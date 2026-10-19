"""
VUELTA ATRÁS DE paso15 - ESCRITA, SIN EJECUTAR. Solo si Gabi lo decide.

Quita de leads el CHECK chk_leads_resumen_solicitud_longitud y la columna
resumen_solicitud que añade paso15_resumen_solicitud.py (plan:
docs/Plan_Resumen_Solicitud_D28.txt, sección 4.8).

OJO: borrar la columna borra los resúmenes que haya guardados, y eso no tiene
vuelta atrás. Por eso, si alguna fila tiene resumen_solicitud con valor, el
script se PARA sin tocar nada, salvo que se le pase además --borrar-resumenes
(y eso, solo tras decirlo Gabi).

ANTES de ejecutarla, el código que lee la columna (Bloque B2) tiene que haber
salido del uvicorn del 8000: si no, GET /gate-avisos y la ficha darían 500.
Es el orden inverso de expand-contract.

Una sola transacción, con lock_timeout de 5 s, como la migración.
Idempotente: si ya no existen ni la columna ni el CHECK, lo dice y no toca
nada.

Freno: sin el argumento --confirmo-vuelta-atras-paso15 no se conecta a nada
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

# Los mismos nombres que la migración, escritos aquí (sin depender de app/).
COLUMNA = "resumen_solicitud"
# El CHECK que se quita.
RESTRICCION = "chk_leads_resumen_solicitud_longitud"


def main():
    """La vuelta atrás entera: frenos, foto, comprobación y DROP."""
    # Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
    sys.stdout.reconfigure(encoding="utf-8")
    # El freno: sin la bandera, se para aquí sin conectarse a nada.
    if "--confirmo-vuelta-atras-paso15" not in sys.argv:
        # Se explica por qué no hace nada.
        print("No se ejecuta: falta --confirmo-vuelta-atras-paso15 (solo si Gabi lo decide).")
        # Código 2: "no se ha hecho nada a propósito".
        sys.exit(2)
    # La segunda llave, solo para el caso en que haya resúmenes guardados.
    borrar_resumenes = "--borrar-resumenes" in sys.argv

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
        # SQL: ¿existe la columna?
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'leads' AND column_name = %s;",
            (COLUMNA,),
        )
        # True o False.
        hay_columna = cur.fetchone()[0] == 1
        # SQL: ¿existe el CHECK?
        cur.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid = 'public.leads'::regclass AND conname = %s;",
            (RESTRICCION,),
        )
        # True o False.
        hay_check = cur.fetchone()[0] == 1
        # Se enseña el estado.
        print(f"  ANTES: columna {COLUMNA}: {hay_columna}; {RESTRICCION}: {hay_check}")
        # Nada que quitar: no se toca nada.
        if not hay_columna and not hay_check:
            # Se dice.
            print("  ya no existen: no se toca nada")
            # Se cierra la lectura.
            cn.rollback()
            # Código 0.
            sys.exit(0)
        # Con la columna: ¿hay resúmenes guardados?
        if hay_columna:
            # SQL: cuántas filas tienen resumen (solo el número, nunca el texto).
            cur.execute(f"SELECT count(*) FROM leads WHERE {COLUMNA} IS NOT NULL;")
            # El recuento.
            con_resumen = cur.fetchone()[0]
            # Se enseña.
            print(f"  filas con resumen guardado: {con_resumen}")
            # Con resúmenes y sin la segunda llave: se para.
            if con_resumen and not borrar_resumenes:
                # Se explica.
                print("  NO SE HACE: borrar la columna borraría esos resúmenes; "
                      "hace falta además --borrar-resumenes, y solo si Gabi lo decide")
                # Nada cambiado.
                cn.rollback()
                # Código 1.
                sys.exit(1)
        # SQL: esperar un bloqueo como mucho 5 s, solo en esta transacción.
        cur.execute("SET LOCAL lock_timeout = '5s';")
        # Primero el CHECK y después la columna. IF EXISTS: sin error si una
        # de las dos ya no está.
        # SQL: el CHECK.
        cur.execute(f"ALTER TABLE leads DROP CONSTRAINT IF EXISTS {RESTRICCION};")
        # SQL: la columna.
        cur.execute(f"ALTER TABLE leads DROP COLUMN IF EXISTS {COLUMNA};")
        # Definitivo.
        cn.commit()
        # Constancia en la salida.
        print("  DROP CONSTRAINT y DROP COLUMN ejecutados; commit() ejecutado")
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
