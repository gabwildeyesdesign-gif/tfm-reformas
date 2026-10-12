"""
MIGRACIÓN DE ESQUEMA - NO ES UN SCRIPT DE VERIFICACIÓN. PREPARADA, SIN APLICAR.

Endurece el CHECK chk_visitas_texto_cliente_longitud de visitas: de 1..1000
caracteres (paso9) a 1..300 (D26.7, P3 del plan del Agente 2,
docs/Plan_Agente2_Calendar.txt, secciones 3.5 y 11). Es la segunda mitad de
la defensa doble: Pydantic (MAX_TEXTO_VISITA = 300) ya lo exige desde la rama
feat/n0-agente2.

CUÁNDO: DESPUÉS del merge de feat/n0-agente2 en main y con el uvicorn del
8000 ya reiniciado con el código nuevo (C6 del plan, expand/contract). Si se
aplicara antes, POST /visits de main seguiría aceptando textos de 301 a 1000
caracteres en Pydantic, la base de datos los rechazaría y el cliente vería un
500. Y SOLO tras la frase de Gabi "confirmo la migración paso13b".

Medición previa (solo lectura, 2026-10-12 21:22): 3 visitas en la tabla, la
más larga de 77 caracteres, 0 por encima de 300. La migración lo vuelve a
medir dentro de su transacción y NO cambia nada si alguna fila lo supera.

Una sola transacción: o se cambia el CHECK entero o nada. Idempotente: si el
CHECK ya es de 1..300, lo dice y no toca nada.

Freno: sin el argumento --confirmo-paso13b no hace nada (código 2).

Vuelta atrás (escrita, sin ejecutar): paso13b_vuelta_atras.py.
Comprobación: scripts/check_migracion_paso13b.py (FALLA antes de migrar).
OJO, después de aplicarla: check_migracion_visitas (prueba 1000 y 1001) y el
CASO 20 de check_listado_llamadas_http (inserta por SQL un texto de 1000)
dejarán de pasar; se cambian en el mismo paso (sección 11 del plan).
"""

# os: el entorno. sys: argumentos, consola y salida. Path: rutas.
import os
# sys: argumentos, consola y código de salida.
import sys
# Path: rutas de archivos.
from pathlib import Path

# psycopg2: hablar con PostgreSQL.
import psycopg2
# load_dotenv: leer el .env.
from dotenv import load_dotenv

# Sin esto, en Windows la consola puede no mostrar tildes ni eñes.
sys.stdout.reconfigure(encoding="utf-8")

# El freno: sin la bandera, se para aquí sin conectarse a nada.
if "--confirmo-paso13b" not in sys.argv:
    # Se explica por qué no hace nada.
    print("No se ejecuta: falta --confirmo-paso13b (solo tras el merge y la frase de Gabi "
          "\"confirmo la migración paso13b\").")
    # Código 2: "no se ha hecho nada a propósito", distinto de un fallo (1).
    sys.exit(2)

# parents[2]: este archivo vive en scripts/migraciones_ejecutadas/.
RAIZ_REPO = Path(__file__).resolve().parents[2]
# Carga el .env de la raíz (DATABASE_URL).
load_dotenv(RAIZ_REPO / ".env")

# El nombre del CHECK (el de paso9) y el máximo nuevo. Escritos aquí a
# propósito: la migración no depende del código de app/.
RESTRICCION = "chk_visitas_texto_cliente_longitud"
# El máximo nuevo, el mismo que MAX_TEXTO_VISITA de app/schemas/common.py.
MAXIMO = 300


def foto(cur, momento):
    """Enseña la definición del CHECK y las longitudes de texto_cliente."""
    # SQL: la definición del CHECK, como la escribe Postgres.
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s;", (RESTRICCION,))
    # La fila, o None si no existe.
    fila = cur.fetchone()
    # Se enseña.
    print(f"  FOTO {momento}: {RESTRICCION} = {fila[0] if fila else 'NO EXISTE'}")
    # SQL: cuántas visitas hay, la más larga y cuántas superan el máximo.
    cur.execute("SELECT count(*), coalesce(max(char_length(texto_cliente)), 0), "
                "count(*) FILTER (WHERE char_length(texto_cliente) > %s) FROM visitas;", (MAXIMO,))
    # Los tres números.
    total, mas_larga, por_encima = cur.fetchone()
    # Se enseñan.
    print(f"  FOTO {momento}: visitas {total}, la más larga {mas_larga}, por encima de {MAXIMO}: {por_encima}")
    # Se devuelven la definición y las que superan el máximo.
    return (fila[0] if fila else None), por_encima


# Conexión y cursor (una sola transacción hasta el commit o el rollback).
cn = psycopg2.connect(os.getenv("DATABASE_URL"))
# El cursor.
cur = cn.cursor()

# try / except: si algo falla, rollback y no se cambia nada.
try:
    # La foto de antes.
    definicion, por_encima = foto(cur, "ANTES")
    # Ya migrada: no se toca nada (idempotente).
    if definicion is not None and f"<= {MAXIMO}" in definicion:
        # Se dice y se sale sin cambios.
        print(f"  el CHECK ya es de 1..{MAXIMO}: no se toca nada")
        # Se cierra la transacción de lectura.
        cn.rollback()
        # Código 0: nada que hacer.
        sys.exit(0)
    # Alguna fila real lo supera: no se migra (habría que decidir qué hacer con ella).
    if por_encima:
        # Se explica y se sale sin cambios.
        print(f"  NO SE MIGRA: {por_encima} visita(s) superan {MAXIMO} caracteres; hay que revisarlas antes")
        # Nada cambiado.
        cn.rollback()
        # Código 1: no se ha podido hacer.
        sys.exit(1)
    # SQL: se cambia el CHECK en la misma sentencia (quitar el viejo y poner
    # el nuevo, con el MISMO nombre). Postgres comprueba las filas existentes
    # al añadirlo; si alguna fallara, error y rollback.
    cur.execute(f"ALTER TABLE visitas DROP CONSTRAINT {RESTRICCION}, "
                f"ADD CONSTRAINT {RESTRICCION} CHECK (char_length(texto_cliente) BETWEEN 1 AND {MAXIMO});")
    # Constancia en la salida.
    print(f"  ALTER TABLE ejecutado: {RESTRICCION} = 1..{MAXIMO}")
    # La foto de después (todavía dentro de la transacción).
    foto(cur, "DESPUÉS")
    # Definitivo.
    cn.commit()
    # Constancia en la salida.
    print("  commit() ejecutado")
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
