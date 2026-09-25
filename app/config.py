"""Configuración global de la aplicación (variables de entorno, constantes)."""

import os

from dotenv import load_dotenv

# Carga las variables definidas en el archivo .env como si fueran variables
# de entorno del sistema operativo, para que os.getenv() pueda leerlas.
load_dotenv()

# Cadena de conexión a Supabase (Session pooler), ya usada y verificada en
# check_connection.py. La reutilizamos aquí para no duplicar el nombre de
# la variable en varios sitios del código.
DATABASE_URL = os.getenv("DATABASE_URL")

# Secreto compartido con n8n para autenticar las llamadas al webhook
# POST /leads. n8n lo envía en la cabecera X-Webhook-Secret y FastAPI lo
# compara con este valor.
#
# Aquí es una lectura simple, igual que DATABASE_URL, y a propósito NO
# se comprueba que exista. La comprobación de "obligatorio y no vacío"
# vive en app/api/security.py, que es el único archivo que lo usa. Si
# se hiciera aquí, cualquier script que importe app.config (por ejemplo,
# los de scripts/ que solo necesitan DATABASE_URL) se negaría a
# ejecutarse sin un secreto que no le sirve para nada. Así solo falla al
# arrancar quien de verdad necesita autenticar webhooks: el servidor.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Secreto de la puerta MCP (/mcp), que usa el agente de IA de n8n. Lo
# envía en la cabecera "Authorization: Bearer <secreto>", el formato
# estándar del protocolo MCP.
#
# Es un secreto DISTINTO de WEBHOOK_SECRET a propósito: son dos puertas
# con permisos distintos. Si uno se filtra, se puede cambiar sin tocar el
# otro, y el agente no hereda el acceso a POST /leads ni al revés.
#
# Mismo criterio que WEBHOOK_SECRET: aquí solo se lee. La comprobación de
# "obligatorio y no vacío" vive en app/mcp_server/server.py, el único
# archivo que lo usa.
MCP_SECRET = os.getenv("MCP_SECRET")

# Número mínimo de conexiones que el pool mantiene siempre abiertas, listas
# para usar sin esperar a crear una nueva. os.getenv devuelve siempre texto
# (str) aunque el valor parezca un número, así que hay que convertirlo con
# int(). El segundo argumento de os.getenv ("2") es el valor por defecto:
# se usa solo si la variable DB_POOL_MIN no existe en el .env.
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))

# Número máximo de conexiones que el pool puede llegar a abrir a la vez.
#
# Qué pasa EXACTAMENTE al superar este límite (verificado en el código
# fuente de psycopg2, psycopg2/pool.py, método _getconn): el pool NO
# espera. Lanza inmediatamente PoolError("connection pool exhausted").
# Como PoolError hereda de psycopg2.Error, un endpoint que capture
# psycopg2.Error la recoge igual que un fallo de base de datos.
#
# (Este comentario decía antes que la petición "espera o falla, según
# cómo se use el pool". Era falso y se corrigió: no hay ningún modo de
# uso en el que espere.)
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# ---------------------------------------------------------------------------
# Tiempos límite y keepalives de las conexiones a Postgres
# ---------------------------------------------------------------------------
# Motivo: las conexiones guardadas en el pool morían tras minutos ociosas
# (cortes de red o del pooler entre esta máquina y Supabase), psycopg2 no
# se enteraba, y la primera consulta fallaba con OperationalError o se
# quedaba colgada más de 3 minutos. Estos valores se los pasa init_pool()
# (app/db/connection.py) a cada conexión nueva. Todos se pueden cambiar
# desde el .env sin tocar código; si la variable no existe, se usa el
# valor por defecto que va como segundo argumento de os.getenv.
#
# El sufijo del nombre indica la unidad (_S = segundos, _MS =
# milisegundos), porque libpq y Postgres no usan la misma para todo y
# confundirlas daría un límite mil veces más largo o más corto.

# Segundos máximos para ABRIR una conexión. Sin este parámetro libpq
# espera indefinidamente. Conectar mide ~1,3 s (TLS + Session pooler en
# eu-central-1, medido el 2026-09-25): 10 s dan unas 8 veces de margen.
DB_CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT_S", "10"))

# 1 = activar los keepalives TCP: pequeñas sondas que el sistema operativo
# envía por una conexión ociosa para comprobar que el otro extremo sigue
# ahí. Ya es el valor por defecto de libpq; se escribe para que la
# intención quede a la vista.
DB_KEEPALIVES = int(os.getenv("DB_KEEPALIVES", "1"))

# Segundos sin tráfico tras los que se envía la primera sonda. El defecto
# de Windows es de 2 horas: con 30 s se mantiene viva la entrada del NAT
# del router y del pooler, y una conexión muerta se detecta mientras está
# ociosa, antes de que una petición la use. Coste: un paquete diminuto
# cada 30 s por conexión.
DB_KEEPALIVES_IDLE_S = int(os.getenv("DB_KEEPALIVES_IDLE_S", "30"))

# Segundos entre sondas que no reciben respuesta.
DB_KEEPALIVES_INTERVAL_S = int(os.getenv("DB_KEEPALIVES_INTERVAL_S", "10"))

# Sondas perdidas tras las que se da la conexión por muerta.
# OJO: EN WINDOWS NO TIENE EFECTO. La documentación de libpq solo cita
# Windows para keepalives_idle y keepalives_interval; aquí el número de
# sondas lo fija el sistema operativo (10, según Microsoft). Sí actúa en
# Linux (Docker, producción). Detección de una conexión muerta ociosa:
# ~30 + 3*10 = 60 s en Linux, ~30 + 10*10 = 130 s en Windows.
DB_KEEPALIVES_COUNT = int(os.getenv("DB_KEEPALIVES_COUNT", "3"))

# Milisegundos máximos que Postgres deja correr UNA sentencia antes de
# cancelarla (QueryCanceled). El rol postgres de Supabase trae 2 min
# (medido); las operaciones de N0 tardan milisegundos, así que 30 s
# cortan un cuelgue sin rozar nada legítimo.
# Se aplica con un SET al crear cada conexión, NO con el parámetro
# options="-c statement_timeout=...": el Session pooler de Supabase
# (Supavisor) descarta ese parámetro de arranque (comprobado: seguía
# valiendo 2 min). Ver ConexionConTimeout en app/db/connection.py.
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
