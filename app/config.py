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
# Si se piden más conexiones de las que el pool tiene disponibles y ya
# llegó a este límite, la petición que pide una conexión de más espera
# o falla, según cómo se use el pool.
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
