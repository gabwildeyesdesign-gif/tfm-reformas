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
