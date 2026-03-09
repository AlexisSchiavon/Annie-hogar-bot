# Annie Hogar Bot

Asistente virtual de ventas para WhatsApp de una tienda de muebles y colchones en Colombia. Atiende clientes, muestra el catálogo en tiempo real, agenda visitas a la tienda y notifica al propietario sobre oportunidades clave.

---

## Tabla de contenidos

1. [Arquitectura](#arquitectura)
2. [Estructura del proyecto](#estructura-del-proyecto)
3. [Desarrollo local con Docker Compose](#desarrollo-local-con-docker-compose)
4. [Variables de entorno](#variables-de-entorno)
5. [Configurar Google Service Account](#configurar-google-service-account)
6. [Importar workflows en n8n](#importar-workflows-en-n8n)
7. [Despliegue en EasyPanel](#despliegue-en-easypanel)
8. [Actualizar catálogo](#actualizar-catálogo)
9. [Endpoints de la API](#endpoints-de-la-api)

---

## Arquitectura

```
WhatsApp
   │
   ▼
ManyChat  ──────────────────────────────────────────────────────┐
   │  (webhook entrante)                                        │
   ▼                                                            │
n8n (orquestador)                                               │
   │                                                            │
   ├── POST /chat ──────────────────► FastAPI (Python 3.11)     │
   │                                      │                     │
   │                                      ├── OpenAI GPT-4o-mini│
   │                                      │   (tool calling)    │
   │                                      ├── PostgreSQL 16     │
   │                                      │   (leads, conv.)    │
   │                                      └── Redis 7           │
   │                                          (sesiones, caché) │
   │                                                            │
   ├── Crons (reminders, followups, summary)                    │
   │                                                            │
   └── Respuesta ManyChat ──────────────────────────────────────┘
                              (envía mensaje al cliente)
```

**Flujo principal:**

1. Cliente escribe por WhatsApp → ManyChat recibe el mensaje
2. n8n reenvía al endpoint `/chat` de FastAPI
3. FastAPI procesa con GPT-4o-mini usando tool calling (hasta 5 rondas)
4. Las herramientas consultan el catálogo en Redis/Google Sheets, guardan leads en Postgres, agendan citas
5. FastAPI devuelve la respuesta a n8n → n8n usa ManyChat API para responder al cliente

**Componentes:**

| Componente | Tecnología | Rol |
|---|---|---|
| Bot / IA | OpenAI GPT-4o-mini | Procesamiento de lenguaje natural + tool calling |
| API | FastAPI + uvicorn | Endpoints REST, lógica de negocio |
| Base de datos | PostgreSQL 16 + asyncpg | Leads, conversaciones, citas, follow-ups |
| Caché / sesiones | Redis 7 | Sesiones (TTL 2h), catálogo (TTL 15min), rate limit |
| Catálogo | Google Sheets + gspread | Fuente de verdad para productos y precios |
| Orquestador | n8n | Webhooks, crons, integración ManyChat |
| WhatsApp | ManyChat | Middleware de mensajería |

---

## Estructura del proyecto

```
Annie-hogar-bot/
├── app/
│   ├── main.py                  # FastAPI: todos los endpoints
│   ├── config.py                # Pydantic Settings (singleton)
│   ├── dependencies.py          # DI: verify_api_key, get_db, get_redis
│   ├── db/
│   │   ├── postgres.py          # Engine asyncpg, get_db_session()
│   │   └── redis_client.py      # Helpers de sesión, caché, rate limit
│   ├── models/
│   │   ├── database.py          # SQLAlchemy models (Lead, Conversation, etc.)
│   │   └── schemas.py           # Pydantic schemas request/response
│   ├── services/
│   │   ├── conversation.py      # Motor principal con tool calling loop
│   │   ├── catalog.py           # Lee Google Sheets, cachea en Redis
│   │   ├── notifications.py     # Alertas a Javier vía n8n
│   │   ├── reminder.py          # Recordatorios 24h antes de cita
│   │   ├── followup.py          # Seguimientos a 24h/72h/168h
│   │   └── summary.py           # Resumen diario
│   ├── tools/
│   │   ├── definitions.py       # 4 tools OpenAI: buscar, agendar, calificar, notificar
│   │   └── executor.py          # Ejecutor de tool calls
│   └── prompts/
│       ├── system_prompt.py     # System prompt dinámico por lead
│       └── templates.py         # Todos los textos salientes
├── n8n/                         # Workflows n8n (JSON importables)
│   ├── main_webhook.json        # WF1: entrada de mensajes WhatsApp
│   ├── cron_reminders.json      # WF2: recordatorios de citas (cada 1h)
│   ├── cron_followups.json      # WF3: seguimientos (cada 6h)
│   ├── cron_summary.json        # WF4: resumen diario a Javier
│   ├── panel_control.json       # WF5: panel admin + webhook notify-javier
│   ├── panel_takeover.json      # WF6: activar/desactivar human takeover
│   └── panel_leads.json         # WF7: consultar leads recientes
├── sql/
│   └── init.sql                 # CREATE TABLE + índices + trigger updated_at
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Desarrollo local con Docker Compose

### Requisitos previos

- Docker Desktop 24+ con Docker Compose v2
- Credenciales de Google Service Account (ver [sección dedicada](#configurar-google-service-account))
- API key de OpenAI
- API key de ManyChat

### Pasos

**1. Clonar y preparar el entorno:**

```bash
git clone <repo-url>
cd Annie-hogar-bot
cp .env.example .env
```

**2. Editar `.env` con tus valores reales** (ver sección [Variables de entorno](#variables-de-entorno)).

**3. Levantar todos los servicios:**

```bash
docker compose up -d
```

Esto levanta cuatro contenedores:
- `annie-fastapi` en `http://localhost:8000`
- `annie-postgres` en `localhost:5432`
- `annie-redis` en `localhost:6379`
- `annie-n8n` en `http://localhost:5678`

El esquema de base de datos se aplica automáticamente desde `sql/init.sql` al crear el contenedor de Postgres por primera vez.

**4. Verificar que todo está sano:**

```bash
curl http://localhost:8000/health
```

Respuesta esperada:
```json
{"status": "ok", "postgres": "ok", "redis": "ok", "version": "1.0.0"}
```

**5. Acceder a n8n:**

Abre `http://localhost:5678` → usuario/contraseña definidos en `N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD`.

**Comandos útiles:**

```bash
# Ver logs del bot
docker compose logs -f fastapi

# Reiniciar solo el bot (tras cambios en código)
docker compose restart fastapi

# Detener todo
docker compose down

# Detener y borrar volúmenes (reset completo)
docker compose down -v
```

---

## Variables de entorno

Copia `.env.example` a `.env` y rellena cada valor:

### Base de datos

| Variable | Descripción | Ejemplo |
|---|---|---|
| `DATABASE_URL` | URL asyncpg de PostgreSQL | `postgresql+asyncpg://annie:annie_pass@postgres:5432/annie` |
| `POSTGRES_USER` | Usuario de Postgres | `annie` |
| `POSTGRES_PASSWORD` | Contraseña de Postgres | `annie_pass` |
| `POSTGRES_DB` | Nombre de la base de datos | `annie` |

> En producción (EasyPanel), `postgres` en el host se reemplaza por la IP o nombre del servicio Postgres.

### Redis

| Variable | Descripción | Ejemplo |
|---|---|---|
| `REDIS_URL` | URL de conexión Redis | `redis://redis:6379/0` |

### OpenAI

| Variable | Descripción | Ejemplo |
|---|---|---|
| `OPENAI_API_KEY` | API key de OpenAI | `sk-proj-xxx...` |
| `OPENAI_MODEL` | Modelo a usar | `gpt-4o-mini` |

### Google

| Variable | Descripción | Ejemplo |
|---|---|---|
| `GOOGLE_SHEETS_ID` | ID de la hoja de catálogo | `1aBcDeFg...` (extraído de la URL) |
| `GOOGLE_DRIVE_FOLDER_ID` | ID de la carpeta de Drive (para PDFs futuros) | `1aBcDeFg...` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON completo del service account **en una sola línea** | `{"type":"service_account",...}` |

> El ID del Google Sheet está en su URL: `https://docs.google.com/spreadsheets/d/`**`ID_AQUI`**`/edit`

### ManyChat / WhatsApp

| Variable | Descripción | Ejemplo |
|---|---|---|
| `MANYCHAT_API_KEY` | API key de ManyChat | `xxx...` |
| `MANYCHAT_FLOW_NS` | Namespace del flow de respuesta | `content20240101000000_000000` |

### Negocio

| Variable | Descripción | Ejemplo |
|---|---|---|
| `JAVIER_PHONE` | Teléfono del dueño (con código país, sin +) | `573001234567` |
| `BOT_NAME` | Nombre del bot en el system prompt | `Annie Hogar` |
| `STORE_ADDRESS` | Dirección física de la tienda | `Calle 50 # 20-30, Medellín` |
| `STORE_HOURS` | Horarios de atención | `Lun-Sáb 9am-7pm, Dom 10am-4pm` |

### Seguridad

| Variable | Descripción |
|---|---|
| `API_SECRET_KEY` | Clave para autenticar llamadas entre n8n y FastAPI (header `X-API-Key`) |

Genera una clave segura:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### n8n

| Variable | Descripción | Dev local | Producción |
|---|---|---|---|
| `N8N_WEBHOOK_URL` | URL base de n8n (usada por FastAPI para notificaciones) | `http://n8n:5678` | `https://n8n.tudominio.com` |
| `N8N_BASIC_AUTH_USER` | Usuario UI de n8n | `admin` | cambia esto |
| `N8N_BASIC_AUTH_PASSWORD` | Contraseña UI de n8n | — | cambia esto |

---

## Configurar Google Service Account

El bot usa un Service Account de Google para leer la hoja de catálogo sin autenticación interactiva.

### 1. Crear el Service Account

1. Ir a [Google Cloud Console](https://console.cloud.google.com/) → selecciona o crea un proyecto
2. **APIs & Services → Credentials → Create Credentials → Service account**
3. Dale un nombre (p. ej. `annie-bot`) y crea
4. En la lista de Service Accounts, haz clic en el que creaste → pestaña **Keys**
5. **Add Key → Create new key → JSON** → descarga el archivo

### 2. Habilitar las APIs necesarias

En **APIs & Services → Library**, habilita:
- **Google Sheets API**
- **Google Drive API**

### 3. Compartir el Google Sheet con el Service Account

1. Abre el archivo `.json` descargado y copia el valor de `client_email`
   (tiene el formato `nombre@proyecto.iam.gserviceaccount.com`)
2. Abre tu Google Sheet de catálogo
3. **Compartir → pega el email del service account → rol Lector** (o Editor si necesitas escribir)

### 4. Agregar el JSON al `.env`

El JSON debe estar **en una sola línea** (sin saltos de línea reales):

```bash
# En macOS/Linux:
cat service-account.json | tr -d '\n' | pbcopy
# Pega el resultado como valor de GOOGLE_SERVICE_ACCOUNT_JSON en .env
```

O usa Python:
```bash
python -c "import json,sys; print(json.dumps(json.load(open('service-account.json'))))" | pbcopy
```

El resultado en `.env` debe verse así (en una sola línea):
```
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"...","private_key":"-----BEGIN RSA PRIVATE KEY-----\nMII..."}
```

### 5. Estructura del Google Sheet de catálogo

La hoja debe tener una primera fila con encabezados. Nombres de columnas reconocidos:

| Columna (ES) | Columna (EN) | Descripción |
|---|---|---|
| `nombre` | `name` | Nombre del producto |
| `categoria` | `category` | Sala, Comedor, Alcoba, Colchones, Oficina, Exterior, Otro |
| `precio` | `price` | Precio en COP (puede incluir `$` y comas) |
| `descripcion` | `description` | Descripción del producto |
| `disponible` | `available` | `si`/`no` o `true`/`false` |
| Otras columnas | — | Se guardan en el campo `extra` del producto |

---

## Importar workflows en n8n

Los 7 workflows están en la carpeta `n8n/` como archivos JSON listos para importar.

### Pasos

1. Accede a n8n (`http://localhost:5678` en local o tu dominio en producción)
2. Menú superior izquierdo → **Workflows → Import from file**
3. Importa cada archivo en este orden:

| Archivo | Nombre | Descripción |
|---|---|---|
| `panel_control.json` | Panel de Control | Panel admin + webhook `/notify-javier` (importar primero) |
| `main_webhook.json` | Webhook Principal | Entrada de mensajes desde ManyChat |
| `cron_reminders.json` | Recordatorios de Citas | Cron cada 1h — recuerda citas del día siguiente |
| `cron_followups.json` | Seguimientos | Cron cada 6h — follow-ups a leads silenciosos |
| `cron_summary.json` | Resumen Diario | Cron 8pm Colombia — resumen para Javier |
| `panel_takeover.json` | Panel Takeover | Activa/desactiva atención humana por teléfono |
| `panel_leads.json` | Panel Leads | Consulta leads recientes con filtros |

4. Después de importar cada workflow, **actívalo** con el toggle en la esquina superior derecha

### Credenciales requeridas en n8n

En cada workflow que use HTTP Request, configura las credenciales:
- **Header Auth**: nombre `X-API-Key`, valor = tu `API_SECRET_KEY`
- **ManyChat**: header `Authorization: Bearer {MANYCHAT_API_KEY}`

---

## Despliegue en EasyPanel

EasyPanel permite desplegar aplicaciones Docker con un panel visual. El bot requiere 4 servicios.

### 1. Crear el proyecto en EasyPanel

1. Accede a tu servidor EasyPanel → **Projects → New Project** → nombre `annie-hogar`

### 2. Desplegar PostgreSQL

1. En el proyecto → **New Service → Postgres**
2. Configura:
   - Database: `annie`
   - Username: `annie`
   - Password: (genera una fuerte)
3. Guarda y anota el **host interno** (p. ej. `annie-hogar_postgres`)

**Inicializar el esquema:**
Después de que Postgres esté corriendo, abre la consola del contenedor y ejecuta:
```bash
psql -U annie -d annie < /path/to/sql/init.sql
```
O copia y pega el contenido de `sql/init.sql` en la consola de la DB en EasyPanel.

### 3. Desplegar Redis

1. **New Service → Redis**
2. Sin contraseña (o con contraseña si lo requieres, ajusta `REDIS_URL`)
3. Anota el **host interno** (p. ej. `annie-hogar_redis`)

### 4. Desplegar n8n

1. **New Service → App** → imagen `n8nio/n8n:latest`
2. Variables de entorno:
   ```
   N8N_HOST=0.0.0.0
   N8N_PORT=5678
   N8N_PROTOCOL=https
   WEBHOOK_URL=https://n8n.tudominio.com
   DB_TYPE=postgresdb
   DB_POSTGRESDB_HOST=annie-hogar_postgres
   DB_POSTGRESDB_PORT=5432
   DB_POSTGRESDB_DATABASE=annie
   DB_POSTGRESDB_USER=annie
   DB_POSTGRESDB_PASSWORD=<tu-password>
   N8N_BASIC_AUTH_ACTIVE=true
   N8N_BASIC_AUTH_USER=admin
   N8N_BASIC_AUTH_PASSWORD=<password-seguro>
   ```
3. Exponer el puerto `5678` → configura el dominio `n8n.tudominio.com` con HTTPS en EasyPanel
4. Volumen persistente: `/home/node/.n8n`

### 5. Desplegar FastAPI

1. **New Service → App** → conecta tu repositorio Git (o usa imagen Docker)
2. Si usas imagen: EasyPanel construye desde el `Dockerfile` en la raíz del repo
3. Variables de entorno — completa todas las de la sección [Variables de entorno](#variables-de-entorno), ajustando los hosts:
   ```
   DATABASE_URL=postgresql+asyncpg://annie:<pass>@annie-hogar_postgres:5432/annie
   REDIS_URL=redis://annie-hogar_redis:6379/0
   N8N_WEBHOOK_URL=https://n8n.tudominio.com
   # ... resto de variables
   ```
4. Exponer el puerto `8000` → configura el dominio `api.tudominio.com` con HTTPS
5. (Opcional) Health check: `GET /health`

### 6. Configurar ManyChat

1. En ManyChat → **Settings → API** → copia tu API key
2. En tu Flow de respuesta → anota el namespace (campo `MANYCHAT_FLOW_NS`)
3. En el webhook de entrada de ManyChat → apunta al webhook de n8n:
   `https://n8n.tudominio.com/webhook/manychat-chat`

### 7. Verificar el despliegue

```bash
curl https://api.tudominio.com/health
# → {"status":"ok","postgres":"ok","redis":"ok","version":"1.0.0"}
```

---

## Actualizar catálogo

El catálogo se lee desde Google Sheets y se cachea en Redis con un TTL de 15 minutos. Hay dos formas de actualizar:

### Automático (cada 15 minutos)

El caché expira solo. La próxima consulta al bot recargará el catálogo desde Sheets.

### Manual (forzado inmediato)

```bash
curl -X POST https://api.tudominio.com/catalog/refresh \
  -H "X-API-Key: <tu-api-secret-key>"
```

Respuesta:
```json
{"products_count": 42, "refreshed_at": "2025-03-08T20:00:00Z"}
```

### Desde el panel de n8n

En el workflow `panel_control.json` hay un formulario de administración que incluye el botón **Recargar catálogo** que llama a este endpoint.

### Actualizar el Google Sheet

1. Abre el Google Sheet de catálogo
2. Edita, agrega o elimina filas de productos
3. El bot tomará los cambios en el siguiente ciclo de caché (máx. 15 min) o forzando el refresh manual

### PDFs de productos (futuro)

Los PDFs se almacenarán en la carpeta de Google Drive identificada por `GOOGLE_DRIVE_FOLDER_ID`. Actualmente el campo está reservado para una integración futura de búsqueda en documentos.

---

## Endpoints de la API

Todos los endpoints (excepto `/health`) requieren el header `X-API-Key: <API_SECRET_KEY>`.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Health check (sin auth) |
| `POST` | `/chat` | Conversación principal con el bot |
| `POST` | `/chat/takeover` | Activar/desactivar atención humana |
| `POST` | `/reminders/check` | Enviar recordatorios de citas (cron 1h) |
| `POST` | `/followups/check` | Enviar seguimientos a leads (cron 6h) |
| `POST` | `/summary/daily` | Generar y enviar resumen diario (cron 8pm) |
| `POST` | `/catalog/refresh` | Forzar recarga del catálogo desde Sheets |
| `GET` | `/catalog/products` | Listar todos los productos del catálogo |
| `GET` | `/leads/recent?limit=N` | Últimos N leads registrados |

**Ejemplo de llamada a `/chat`:**

```bash
curl -X POST https://api.tudominio.com/chat \
  -H "X-API-Key: <tu-api-secret-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "573001234567",
    "message": "Hola, busco un sofá para sala",
    "name": "Carlos"
  }'
```
