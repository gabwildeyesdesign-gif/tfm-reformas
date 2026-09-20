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
