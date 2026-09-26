"""
Verificación de las piezas de app/services/visits_service.py que se
pueden probar SIN servidor (plan: docs/Plan_Endpoint_Visits.txt, 8.1).

Bloque C (esta versión):
  1. Cambio de hora. combinar_fecha_hora_madrid() con fechas FIJAS, no
     relativas a hoy, porque lo que se prueba es el calendario:
       2026-10-23 08:30 -> +02:00 (horario de verano)
       2026-10-26 08:30 -> +01:00 (horario de invierno; el cambio de hora
                                   es el domingo 25/10/2026)
     Se comprueba también el instante en UTC, que es lo que guarda
     Postgres: 06:30 y 07:30. Con un desfase fijo +02:00 escrito a mano,
     el caso del 26/10 daría 06:30 en UTC, una hora antes de lo pedido.

Bloque D añadirá aquí el caso de la configuración incompleta (503).
"""

import sys
from datetime import date, time, timedelta, timezone
from pathlib import Path

# El paquete "app" está en la raíz del repositorio (misma técnica que los
# demás check_*.py).
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.visits_service import combinar_fecha_hora_madrid

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"  [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"  [FALLO] {titulo}  {detalle}")


print("=" * 78)
print("1. CAMBIO DE HORA (fechas fijas, Europe/Madrid)")
print("=" * 78)
# (día, hora, desfase esperado, instante esperado en UTC)
CASOS = [
    (date(2026, 10, 23), time(8, 30), timedelta(hours=2), "2026-10-23T06:30:00+00:00"),
    (date(2026, 10, 26), time(8, 30), timedelta(hours=1), "2026-10-26T07:30:00+00:00"),
]
for dia, hora, desfase, utc_esperado in CASOS:
    resultado = combinar_fecha_hora_madrid(dia, hora)
    # utcoffset(): cuánto se separa esa hora local de UTC en ESE día.
    # astimezone(timezone.utc): el mismo instante expresado en UTC.
    en_utc = resultado.astimezone(timezone.utc).isoformat()
    print(f"  {dia} {hora:%H:%M} -> {resultado.isoformat()}  | UTC {en_utc}")
    comprobar(f"{dia}: desfase {desfase}", resultado.utcoffset() == desfase, f"(obtenido {resultado.utcoffset()})")
    comprobar(f"{dia}: en UTC {utc_esperado}", en_utc == utc_esperado)
    comprobar(f"{dia}: la hora local sigue siendo {hora:%H:%M}", (resultado.hour, resultado.minute) == (hora.hour, hora.minute))

total = ok + len(fallos)
print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{total} correctas")
print("=" * 78)
if fallos:
    print("FALLOS:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
