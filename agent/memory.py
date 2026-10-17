# agent/memory.py — Memoria del agente ELI
# Generado por AgentKit
#
# Tres tablas:
#   mensajes            → historial de conversación por número de teléfono
#   mensajes_procesados → IDs ya atendidos (YCloud puede reenviar el mismo evento)
#   pausas              → números donde un humano tomó la conversación (el bot no responde)

import os
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, delete, Integer
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./elim.db")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _ahora() -> datetime:
    """Hora actual en UTC sin zona (formato que guardan las columnas DateTime)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" o "assistant"
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


class MensajeProcesado(Base):
    __tablename__ = "mensajes_procesados"

    mensaje_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_ahora)


class Pausa(Base):
    __tablename__ = "pausas"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    hasta: Mapped[datetime] = mapped_column(DateTime)
    motivo: Mapped[str] = mapped_column(String(200), default="")


async def inicializar_db():
    """Crea las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── Historial ────────────────────────────────────────────────────────────────

async def guardar_mensaje(telefono: str, role: str, content: str):
    """Guarda un mensaje en el historial de conversación."""
    async with async_session() as session:
        session.add(Mensaje(telefono=telefono, role=role, content=content, timestamp=_ahora()))
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    """Recupera los últimos N mensajes de una conversación en orden cronológico."""
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.id.desc())
            .limit(limite)
        )
        mensajes = list((await session.execute(query)).scalars().all())
        mensajes.reverse()
        return [{"role": m.role, "content": m.content} for m in mensajes]


async def limpiar_historial(telefono: str):
    """Borra todo el historial de una conversación."""
    async with async_session() as session:
        await session.execute(delete(Mensaje).where(Mensaje.telefono == telefono))
        await session.commit()


# ── Duplicados ───────────────────────────────────────────────────────────────

async def marcar_procesado(mensaje_id: str) -> bool:
    """Registra el ID. Retorna False si ya se había procesado (evento repetido)."""
    if not mensaje_id:
        return True
    async with async_session() as session:
        session.add(MensajeProcesado(mensaje_id=mensaje_id, timestamp=_ahora()))
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


# ── Pausas por atención humana ───────────────────────────────────────────────

async def pausar_bot(telefono: str, horas: float, motivo: str = ""):
    """El bot deja de responder a este número durante `horas`."""
    hasta = _ahora() + timedelta(hours=horas)
    async with async_session() as session:
        pausa = await session.get(Pausa, telefono)
        if pausa:
            pausa.hasta = hasta
            pausa.motivo = motivo[:200]
        else:
            session.add(Pausa(telefono=telefono, hasta=hasta, motivo=motivo[:200]))
        await session.commit()


async def bot_pausado(telefono: str) -> bool:
    """True si un humano está atendiendo este número."""
    async with async_session() as session:
        pausa = await session.get(Pausa, telefono)
        return bool(pausa and pausa.hasta > _ahora())


async def reanudar_bot(telefono: str):
    """Quita la pausa de un número."""
    async with async_session() as session:
        await session.execute(delete(Pausa).where(Pausa.telefono == telefono))
        await session.commit()
