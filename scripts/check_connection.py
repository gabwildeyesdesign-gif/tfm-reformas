"""
Verifica la conexion a Supabase y hace un chequeo de salud de todas las
tablas del schema 'public': confirma que existen y cuenta sus filas.
"""

import os
import sys

import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def obtener_tablas(cursor):
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name;
        """
    )
    return [fila[0] for fila in cursor.fetchall()]


def contar_filas(cursor, nombre_tabla):
    consulta = sql.SQL("SELECT COUNT(*) FROM {}").format(
        sql.Identifier(nombre_tabla)
    )
    cursor.execute(consulta)
    return cursor.fetchone()[0]


def main():
    if not DATABASE_URL:
        print("ERROR: no se encontro DATABASE_URL en el archivo .env")
        sys.exit(1)

    try:
        conexion = psycopg2.connect(DATABASE_URL)
    except psycopg2.OperationalError as error:
        print(f"ERROR: no se pudo conectar a Supabase -> {error}")
        sys.exit(1)

    print("Conexion a Supabase establecida correctamente.\n")

    cursor = conexion.cursor()
    tablas = obtener_tablas(cursor)

    if not tablas:
        print("No se encontraron tablas en el schema 'public'.")
        cursor.close()
        conexion.close()
        return

    resultados = []
    for nombre_tabla in tablas:
        try:
            total_filas = contar_filas(cursor, nombre_tabla)
            resultados.append((nombre_tabla, total_filas, "OK"))
        except Exception as error:
            conexion.rollback()
            resultados.append((nombre_tabla, str(error).strip(), "ERROR"))

    ancho_nombre = max(len(nombre) for nombre, _, _ in resultados) + 2
    print(f"{'TABLA':<{ancho_nombre}}{'FILAS':<12}{'ESTADO'}")
    print("-" * (ancho_nombre + 20))
    for nombre_tabla, dato, estado in resultados:
        print(f"{nombre_tabla:<{ancho_nombre}}{str(dato):<12}{estado}")

    ok_count = sum(1 for _, _, estado in resultados if estado == "OK")
    total = len(resultados)
    print(f"\nResumen: {ok_count}/{total} tablas verificadas correctamente.")

    cursor.close()
    conexion.close()


if __name__ == "__main__":
    main()
