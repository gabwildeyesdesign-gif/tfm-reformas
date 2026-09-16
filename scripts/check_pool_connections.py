"""
Script temporal de verificacion (no forma parte del backend final).

Cuenta en pg_stat_activity las conexiones abiertas por el pool del
backend, identificandolas por su application_name en vez de deducirlas
por el numero total. Hay que ejecutarlo con el servidor uvicorn
arrancado y sin trafico: se espera ver exactamente DB_POOL_MIN filas.
"""

import os

import psycopg2
from dotenv import load_dotenv

# Mismo nombre que usa el pool en app/db/connection.py. Lo repetimos aqui
# a proposito, en vez de importarlo desde app/, para que este script no
# dependa del codigo que precisamente esta verificando.
NOMBRE_BACKEND = "reformas-backend-fastapi"

# Nombre distinto para la conexion de ESTE script. Si usaramos el mismo
# nombre que el backend, el script se contaria a si mismo y el resultado
# saldria una fila mas alto de lo real.
NOMBRE_DIAGNOSTICO = "reformas-diagnostico"


def contar_conexiones_backend():
    """
    Abre una conexion aparte (fuera del pool) y lista las filas de
    pg_stat_activity cuyo application_name es el del backend.

    Si no aparece ninguna, muestra ademas que application_name ve
    Postgres para el usuario 'postgres', para poder diagnosticar si el
    pooler de Supabase esta sustituyendo el nombre enviado por el cliente.
    """
    load_dotenv()

    # psycopg2.connect acepta, igual que el pool, parametros extra que
    # combina con la cadena de conexion; aqui le ponemos nuestro nombre
    # de diagnostico.
    conexion = psycopg2.connect(
        os.getenv("DATABASE_URL"),
        application_name=NOMBRE_DIAGNOSTICO,
    )
    cursor = conexion.cursor()

    try:
        # %s es un "marcador de posicion": psycopg2 lo sustituye por el
        # valor de la tupla que pasamos como segundo argumento, escapandolo
        # de forma segura. Nunca se debe meter un valor en SQL pegando
        # textos con f-strings (riesgo de inyeccion SQL).
        cursor.execute(
            """
            SELECT pid, application_name, state, backend_start
            FROM pg_stat_activity
            WHERE application_name = %s
            ORDER BY backend_start;
            """,
            (NOMBRE_BACKEND,),
        )
        filas = cursor.fetchall()

        print(f"Conexiones con application_name = '{NOMBRE_BACKEND}':")
        for pid, nombre, estado, inicio in filas:
            print(f"  pid={pid}  estado={estado}  abierta_desde={inicio}")
        print(f"Total: {len(filas)}")

        if not filas:
            # Diagnostico extra: agrupamos por application_name para ver
            # con que nombre aparecen realmente las conexiones del usuario
            # 'postgres' (el que usa nuestro DATABASE_URL por detras).
            cursor.execute(
                """
                SELECT application_name, state, COUNT(*)
                FROM pg_stat_activity
                WHERE usename = 'postgres'
                GROUP BY application_name, state
                ORDER BY COUNT(*) DESC;
                """
            )
            print("\nNinguna fila. application_name visibles para 'postgres':")
            for nombre, estado, total in cursor.fetchall():
                print(f"  '{nombre}'  estado={estado}  total={total}")
    finally:
        # Cerramos cursor y conexion pase lo que pase, para no dejar una
        # conexion de diagnostico colgada en Supabase.
        cursor.close()
        conexion.close()


if __name__ == "__main__":
    contar_conexiones_backend()
