# Despliegue de ELI en VPS (Ubuntu 22.04 + Nginx + Certbot)

Guía para desplegar el agente ELI en un VPS InterServer KVM con Ubuntu 22.04, Nginx y Certbot ya instalados.

El proyecto ya trae un `docker-compose.yml` de producción: la app + PostgreSQL, publicada solo en `127.0.0.1` (Nginx es quien la expone por HTTPS).

> IP del VPS: `74.50.80.229`. En los comandos, reemplaza `eli.tudominio.com` por tu subdominio real y `usuario` por tu usuario SSH.

---

## 1. DNS

En el proveedor de tu dominio crea un **registro A**:

```
eli.tudominio.com  →  74.50.80.229
```

Verifica que resuelva antes de seguir con Certbot:

```bash
ping eli.tudominio.com
```

## 2. Instalar Docker (en el VPS, por SSH)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
docker compose version
```

## 3. Clonar el repositorio (privado → deploy key)

Genera una llave solo para este repo:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/elim_deploy -N ""
cat ~/.ssh/elim_deploy.pub
```

Copia la llave pública en **GitHub → chat_eli → Settings → Deploy keys → Add deploy key** (solo lectura). Luego:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-elim
  HostName github.com
  IdentityFile ~/.ssh/elim_deploy
EOF

sudo mkdir -p /opt/elim-agent && sudo chown $USER /opt/elim-agent
git clone git@github-elim:zitro677/chat_eli.git /opt/elim-agent
```

## 4. Copiar el `.env` (no está en git)

Desde **tu PC** (PowerShell):

```powershell
scp "E:\Projects Claude\elim-agent\.env" usuario@74.50.80.229:/opt/elim-agent/.env
```

En el VPS, ajústalo:

```bash
nano /opt/elim-agent/.env
```

- `POSTGRES_PASSWORD=` → una contraseña larga y fuerte.
- `PORT=8000` → verifica antes que esté libre: `ss -ltnp | grep :8000`. Si está ocupado usa otro (ej. `8010`) y usa el mismo en la configuración de Nginx.
- `PUBLIC_BASE_URL=https://eli.tudominio.com` → necesario para responder notas de voz con audio (YCloud descarga el audio de esa URL). Si queda vacío, ELI entiende los audios pero responde en texto.
- `DATABASE_URL` y `ENVIRONMENT` los sobrescribe `docker-compose.yml`; no hace falta cambiarlos.

## 5. Levantar los contenedores

```bash
cd /opt/elim-agent
docker compose up -d --build
docker compose logs -f agent      # debe decir "ELI iniciado"  (Ctrl+C para salir)
curl http://127.0.0.1:8000/       # {"status":"ok","service":"eli-agent"}
```

## 6. Nginx + SSL

```bash
sudo tee /etc/nginx/sites-available/eli <<'EOF'
server {
    listen 80;
    server_name eli.tudominio.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/eli /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d eli.tudominio.com --redirect
curl https://eli.tudominio.com/
```

## 7. Apuntar YCloud al VPS

**YCloud → Developers → Webhooks → editar el endpoint existente**:

- URL: `https://eli.tudominio.com/webhook`
- Evento activo: `whatsapp.inbound_message.received`

Edita el endpoint existente en vez de crear uno nuevo: así conserva el mismo secreto que `YCLOUD_WEBHOOK_SECRET` en el `.env`. Un endpoint nuevo trae otro secreto y el bot rechazaría los mensajes con 401.

## 8. Probar la transferencia desde WhatsApp

Desde un teléfono personal (no el número de ELIM), escribe al WhatsApp del bot:

> Hola, quiero hablar con una persona

Mira los logs en vivo:

```bash
docker compose logs -f agent
```

Resultado esperado:

1. ELI se despide indicando que una persona continuará por este chat.
2. Llega el correo `🔔 ELI — transferencia — <nombre>` a los destinatarios configurados en Make.
3. No se crea fila en la Google Sheet (solo los leads van ahí).
4. El bot queda en pausa para ese número durante `HANDOFF_PAUSE_HOURS` (8 h por defecto). Si un asesor responde desde la app de WhatsApp Business, el bot también se pausa.

Para quitar la pausa de tu número después de la prueba, usa el comando de la tabla de abajo.

---

## Operación diaria

Todos los comandos se ejecutan en `/opt/elim-agent`.

| Tarea | Comando |
|---|---|
| Desplegar cambios | `git pull && docker compose up -d --build` |
| Ver logs | `docker compose logs -f agent` |
| Reiniciar | `docker compose restart agent` |
| Quitar pausa a un número | `docker compose exec agent python -c "import asyncio; from agent.memory import reanudar_bot; asyncio.run(reanudar_bot('+57XXXXXXXXXX'))"` |
| Cambiar base de conocimiento o prompts | editar `knowledge/` o `config/` (montados como volúmenes) → `docker compose restart agent` |

## Firewall (si usas ufw)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

El puerto 8000 queda cerrado hacia afuera porque `docker-compose.yml` lo publica solo en `127.0.0.1`.

## Make (leads y transferencias)

El bot envía todos los eventos a `MAKE_WEBHOOK_URL`. El escenario debe quedar así:

```
                   ┌─[evento = lead]──► Google Sheets   (solo leads)
Webhook ──► Router ┤
                   └─(sin filtro)─────► Gmail            (leads y transferencias)
```

- El escenario debe estar en **ON**. Si se apaga, Make encola los eventos y no escribe ni envía nada.
- Si Make lo desactiva solo, es por errores repetidos: revisa **History** para ver la causa.
