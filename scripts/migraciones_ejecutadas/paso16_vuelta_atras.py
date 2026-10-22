"""
VUELTA ATRÁS DE paso16 - ESCRITA, SIN EJECUTAR. Solo si Gabi lo decide.

Borra la tabla direcciones_obra que crea paso16_direcciones_obra.py (D42.1),
con sus restricciones (se van con ella).

OJO: borrar la tabla borra las direcciones que haya guardadas, y eso no tiene
vuelta atrás. Por eso, si la tabla tiene alguna fila, el script se PARA sin
tocar nada, salvo que se le pase además --borrar-direcciones (y eso, solo
tras decirlo Gabi). Nunca enseña una dirección: solo el recuento.

ANTES de ejecutarla, el código que usa la tabla (el servicio de POST
/resultados-llamada y la ficha) tiene que haber salido del uvicorn del 8000:
si no, los dos darían 500. Es el orden inverso de expand-contract.

Una sola transacción, con lock_timeout de 5 s, como la migración.
Idempotente: si la tabla ya no existe, lo dice y no toca nada.

Freno: sin el argumento --confirmo-vuelta-atras-paso16 no se conecta a nada
(código 2).
"""

# os: leer DATABASE_URL del entorno.
import os
# sys: argumentos, consola y código de salida.
import sys
# Path: rutas de archivos.
from pathlib import Path

# psycopg2: hablar con PostgreSQL.
import psycopg2
# load_dotenv: leer el .env.
from dotenv import load_dotenv

# El mismo nombre que la migración, escrito aquí (sin depender de app/).
TABLA = "direcciones_obra"


def main():
    """La vuelta atrás entera: frenos, foto, comprobación y DROP."""
    # Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
    sys.stdout.reconfigure(encoding="utf-8")
    # El freno: sin la bandera, se para aquí sin conectarse a nada.
    if "--confirmo-vuelta-atras-paso16" not in sys.argv:
        # Se explica por qué no hace nada.
        print("No se ejecuta: falta --confirmo-vuelta-atras-paso16 (solo si Gabi lo decide).")
        # Código 2: "no se ha hecho nada a propósito".
        sys.exit(2)
    # La segunda llave, solo para el caso en que haya direcciones guardadas.
    borrar_direcciones = "--borrar-direcciones" in sys.argv

    # parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
    raiz_repo = Path(__file__).resolve().parents[2]
    # Carga el .env de la raíz (DATABASE_URL); su valor no se imprime nunca.
    load_dotenv(raiz_repo / ".env")

    # Conexión y cursor: una sola transacción.
    cn = psycopg2.connect(os.getenv("DATABASE_URL"))
    # El cursor.
    cur = cn.cursor()

    # try / except: si algo falla, rollback y no se cambia nada.
    try:
        # SQL: ¿existe la tabla? to_regclass da NULL si no existe.
        cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{TABLA}",))
        # True o False.
        existe = cur.fetchone()[0]
        # Se enseña el estado.
        print(f"  ANTES: tabla {TABLA}: {existe}")
        # Nada que quitar: no se toca nada.
        if not existe:
            # Se dice.
            print("  ya no existe: no se toca nada")
            # Se cierra la lectura.
            cn.rollback()
            # Código 0.
            sys.exit(0)
        # SQL: cuántas filas tiene (solo el número, nunca el contenido).
        cur.execute(f"SELECT count(*) FROM {TABLA};")
        # El recuento.
        filas = cur.fetchone()[0]
        # Se enseña.
        print(f"  direcciones guardadas: {filas}")
        # Con direcciones y sin la segunda llave: se para.
        if filas and not borrar_direcciones:
            # Se explica.
            print("  NO SE HACE: borrar la tabla borraría esas direcciones; "
                  "hace falta además --borrar-direcciones, y solo si Gabi lo decide")
            # Nada cambiado.
            cn.rollback()
            # Código 1.
            sys.exit(1)
        # SQL: esperar un bloqueo como mucho 5 s, solo en esta transacción.
        cur.execute("SET LOCAL lock_timeout = '5s';")
        # SQL: la tabla, con sus restricciones. IF EXISTS: sin error si ya no está.
        cur.execute(f"DROP TABLE IF EXISTS {TABLA};")
        # Definitivo.
        cn.commit()
        # Constancia en la salida.
        print("  DROP TABLE ejecutado; commit() ejecutado")
    # SystemExit (los sys.exit de arriba) no es un error: se deja pasar.
    except SystemExit:
        # Se vuelve a lanzar tal cual.
        raise
    # Cualquier error: deshacer todo y volver a lanzarlo para verlo.
    except Exception:
        # Nada cambiado.
        cn.rollback()
        # Se dice.
        print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
        # Y se ve el error original.
        raise
    # Pase lo que pase, se cierra la conexión.
    finally:
        # Cerrada.
        cn.close()


# Solo se ejecuta si se lanza el archivo directamente.
if __name__ == "__main__":
    # La vuelta atrás.
    main()
