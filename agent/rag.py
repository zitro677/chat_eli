# agent/rag.py — RAG ligero: búsqueda semántica en /knowledge
# Generado por AgentKit
#
# En vez de meter TODA la base de conocimiento en cada mensaje, se divide en
# fragmentos (por secciones "## " de Markdown), se calculan sus embeddings con
# OpenAI una sola vez al arrancar, y para cada mensaje del cliente se envían
# al modelo solo los fragmentos más parecidos a lo que preguntó.
#
# Sin base vectorial ni dependencias nuevas: con ~200 fragmentos, la similitud
# coseno en Python puro tarda milisegundos.

import os
import math
import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger("agentkit")

MODELO_EMBEDDINGS = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
MAX_CARACTERES_FRAGMENTO = 2500  # secciones más largas se parten por párrafos


@dataclass
class Fragmento:
    archivo: str
    titulo: str
    texto: str
    vector: list[float] | None = None


# ── Partir archivos en fragmentos ────────────────────────────────────────────

def _partir_por_parrafos(texto: str) -> list[str]:
    """Divide un texto largo en trozos de hasta MAX_CARACTERES_FRAGMENTO, respetando párrafos."""
    trozos, actual = [], ""
    bloques = []
    for parrafo in texto.split("\n\n"):
        # Un párrafo enorme (ej. una tabla sin líneas en blanco) se parte por líneas
        bloques += parrafo.split("\n") if len(parrafo) > MAX_CARACTERES_FRAGMENTO else [parrafo]
    for parrafo in bloques:
        if actual and len(actual) + len(parrafo) > MAX_CARACTERES_FRAGMENTO:
            trozos.append(actual.strip())
            actual = ""
        actual += parrafo + "\n\n"
    if actual.strip():
        trozos.append(actual.strip())
    return trozos


def _partir_archivo(ruta: str, contenido: str) -> list[Fragmento]:
    """Markdown → un fragmento por sección '## '. CSV/otros → un solo fragmento."""
    contenido = contenido.strip()
    if not ruta.endswith(".md"):
        return [Fragmento(ruta, ruta, t) for t in _partir_por_parrafos(contenido)]

    lineas = contenido.splitlines()
    titulo_doc = lineas[0].lstrip("# ").strip() if lineas and lineas[0].startswith("# ") else ruta

    secciones, actual = [], []
    for linea in lineas:
        if linea.startswith("## ") and actual:
            secciones.append("\n".join(actual))
            actual = []
        actual.append(linea)
    if actual:
        secciones.append("\n".join(actual))

    fragmentos = []
    for seccion in secciones:
        seccion = seccion.strip()
        if not seccion:
            continue
        primera = seccion.splitlines()[0]
        titulo = primera.lstrip("# ").strip() if primera.startswith("#") else titulo_doc
        for trozo in _partir_por_parrafos(seccion):
            # Se antepone el título del documento para que el fragmento tenga contexto propio
            fragmentos.append(Fragmento(ruta, titulo, f"[{titulo_doc}]\n{trozo}"))
    return fragmentos


# ── Utilidades ───────────────────────────────────────────────────────────────

def _coseno(a: list[float], b: list[float]) -> float:
    # Los embeddings de OpenAI vienen normalizados, pero se normaliza igual por seguridad
    punto = sum(x * y for x, y in zip(a, b))
    norma = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return punto / norma if norma else 0.0


def formatear(fragmentos: list[Fragmento]) -> str:
    return "\n\n".join(
        f"<fragmento archivo=\"{f.archivo}\" seccion=\"{f.titulo}\">\n{f.texto}\n</fragmento>"
        for f in fragmentos
    )


# ── Índice ───────────────────────────────────────────────────────────────────

class IndiceRAG:
    """Índice en memoria de los fragmentos de /knowledge con sus embeddings."""

    def __init__(self, client, archivos: list[str], top_k: int = 5, puntaje_minimo: float = 0.25):
        self.client = client
        self.archivos = archivos
        self.top_k = top_k
        self.puntaje_minimo = puntaje_minimo
        self.fragmentos: list[Fragmento] = []
        self.listo = False
        self._candado = asyncio.Lock()

    def _leer_fragmentos(self) -> list[Fragmento]:
        fragmentos = []
        for ruta in self.archivos:
            try:
                with open(os.path.join("knowledge", ruta), "r", encoding="utf-8-sig") as f:
                    fragmentos += _partir_archivo(ruta, f.read())
            except FileNotFoundError:
                logger.warning(f"RAG: archivo no encontrado knowledge/{ruta}")
        return fragmentos

    async def _embeddings(self, textos: list[str]) -> list[list[float]]:
        respuesta = await self.client.embeddings.create(model=MODELO_EMBEDDINGS, input=textos)
        return [d.embedding for d in respuesta.data]

    async def construir(self) -> bool:
        """Calcula los embeddings de todos los fragmentos (una llamada por lote). Idempotente."""
        async with self._candado:
            if self.listo:
                return True
            fragmentos = self._leer_fragmentos()
            if not fragmentos:
                logger.warning("RAG: no hay fragmentos para indexar")
                return False
            lote = 100
            for i in range(0, len(fragmentos), lote):
                grupo = fragmentos[i:i + lote]
                for frag, vector in zip(grupo, await self._embeddings([f.texto for f in grupo])):
                    frag.vector = vector
            self.fragmentos = fragmentos
            self.listo = True
            logger.info(f"RAG: {len(fragmentos)} fragmentos indexados de {len(self.archivos)} archivos")
            return True

    def texto_completo(self) -> str:
        """Respaldo: todos los fragmentos (se usa si la búsqueda falla)."""
        return formatear(self.fragmentos or self._leer_fragmentos())

    async def buscar(self, consulta: str) -> list[Fragmento]:
        """Fragmentos más relevantes para la consulta, en orden de relevancia."""
        if not self.listo and not await self.construir():
            return []
        vector = (await self._embeddings([consulta]))[0]
        puntuados = sorted(
            ((_coseno(vector, f.vector), f) for f in self.fragmentos),
            key=lambda par: par[0],
            reverse=True,
        )
        elegidos = [(p, f) for p, f in puntuados[:self.top_k] if p >= self.puntaje_minimo]
        logger.debug("RAG: " + ", ".join(f"{f.titulo[:40]} ({p:.2f})" for p, f in elegidos))
        return [f for _, f in elegidos]
