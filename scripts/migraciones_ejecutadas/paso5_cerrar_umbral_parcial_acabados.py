"""
MIGRACIÓN DE DATOS YA EJECUTADA - NO ES UN SCRIPT DE VERIFICACIÓN.

Ejecutada el 2026-09-20 contra la base de datos real.

QUÉ HACE: cierra el umbral del Gate de parcial_acabados. El valor NO
cambia (sigue siendo 10.000 €); lo que cambia es su estado: deja de
estar marcado como provisional.

POR QUÉ: cuando se creó la tabla (paso4), los 10.000 € de
parcial_acabados eran el valor heredado del umbral global antiguo, no
una decisión de negocio, y quedaron marcados con provisional = true a la
espera de cerrar D10. Gabi ha cerrado esa decisión el 2026-09-20: el
umbral de parcial_acabados es 10.000 €.

CONSECUENCIA EN EL SISTEMA: el cálculo del Gate no cambia en absoluto,
porque provisional nunca intervino en él (se leía solo para dejar
constancia en el log). Lo que cambia es que, a partir de ahora, los logs
de presupuestos de parcial_acabados llevarán "umbral_provisional": false
en vez de true. Los presupuestos ya calculados conservan su valor
original, que es justo para lo que servía esa marca: saber con qué
estado del umbral se decidió cada uno.

Es idempotente: si ya está cerrado, no hace nada.

Verificación: scripts/check_migracion_umbrales_gate.py (17/17, con
parcial_acabados esperado ahora como provisional=False).
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

RAIZ_REPO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_REPO / ".env")

DESCRIPCION_CERRADA = (
    "D9/D10, cerrado el 2026-09-20: el umbral de parcial_acabados es 10.000 €. "
    "Mismo valor que tenía como provisional, ahora como decisión tomada. "
    "Razón: parcial_acabados es por definición una intervención puntual; si el "
    "alcance creciera hasta cubrir una vivienda completa, dejaría de "
    "clasificarse como tal. Bajo esa definición, 10.000 € es un techo "
    "razonable. DEFECTO OPERATIVO CONOCIDO: esa regla es una definición de "
    "negocio, no una restricción técnica — nada impide hoy crear un lead de "
    "parcial_acabados con 70 m² (22.540 € en nivel medio). Queda como mejora "
    "pendiente un tope de m² por categoría o un flujo de captura que lo impida."
)

cn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = cn.cursor()

print("=== ANTES ===")
cur.execute(
    "SELECT umbral, provisional FROM umbrales_gate WHERE tipo_reforma = 'parcial_acabados';"
)
print(f"  parcial_acabados -> {cur.fetchone()}")

try:
    # El WHERE hace el script repetible: solo actualiza si queda algo por
    # cambiar, así que ejecutarlo dos veces deja cur.rowcount en 0 y no
    # mueve la fecha_actualizacion sin motivo.
    #
    # "AND provisional" equivale a "AND provisional = true". La segunda
    # condición, con IS DISTINCT FROM, compara la descripción guardada
    # con la de este archivo. Se usa IS DISTINCT FROM y no "<>" porque en
    # SQL cualquier comparación con NULL da "desconocido", no "verdadero":
    # si la descripción fuera NULL, "<>" no la seleccionaría nunca. IS
    # DISTINCT FROM trata NULL como un valor más.
    #
    # Está así para que este archivo siga siendo evidencia FIEL de lo que
    # hay en la base de datos: si se corrige el texto aquí, volver a
    # ejecutarlo lo pone al día en vez de dejar los dos desincronizados.
    #
    # NO se toca la columna umbral: el valor ya era 10.000 € y sigue
    # siéndolo. Esta migración cambia el ESTADO de la decisión, no la
    # cifra.
    cur.execute(
        """
        UPDATE umbrales_gate
        SET provisional = false,
            descripcion = %s,
            fecha_actualizacion = CURRENT_DATE
        WHERE tipo_reforma = 'parcial_acabados'
          AND (provisional OR descripcion IS DISTINCT FROM %s);
        """,
        (DESCRIPCION_CERRADA, DESCRIPCION_CERRADA),
    )
    print(f"\n  Filas actualizadas: {cur.rowcount}")
    cn.commit()
    print("  commit() ejecutado")
except Exception:
    cn.rollback()
    print("  ERROR: rollback() ejecutado, no se ha cambiado nada")
    raise

print("\n=== DESPUÉS ===")
cur.execute("SELECT tipo_reforma, umbral, provisional FROM umbrales_gate ORDER BY tipo_reforma;")
for t, u, p in cur.fetchall():
    print(f"  {t:<20} {u:>10}  provisional={p}")

# Cuántos presupuestos se calcularon mientras el umbral era provisional.
# Es justo la consulta para la que se creó la marca en el log: permite
# revisarlos si alguna vez se cambia la cifra.
cur.execute(
    """
    SELECT count(*) FROM logs
    WHERE accion = 'presupuesto_calculado'
      AND (detalle ->> 'umbral_provisional')::boolean IS TRUE;
    """
)
print(f"\n  Presupuestos calculados con el umbral aún provisional: {cur.fetchone()[0]}")

cur.close()
cn.close()
