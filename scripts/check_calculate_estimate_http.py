"""
BLOQUE 3 - Verificación por HTTP REAL de POST /calculate-estimate.

Levanta el servidor de verdad (uvicorn.Server en un hilo, SIN --reload,
por la regla crítica de CLAUDE.md) y le manda peticiones HTTP reales con
requests, para que pasen por todo el camino: autenticación, validación
de Pydantic, servicio, base de datos y serialización JSON de la
respuesta.

Los leads de prueba se crean con POST /leads por HTTP, no llamando a
Python directamente: así el recorrido cliente -> lead -> oportunidad ->
presupuesto es exactamente el que hará n8n.

Datos de prueba: emails http-estimate-*@example.com, borrados al empezar
y al terminar.
"""

import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
import requests
import uvicorn

from app.config import DATABASE_URL, WEBHOOK_SECRET

PUERTO = 8011
BASE = f"http://127.0.0.1:{PUERTO}"
AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
PATRON_EMAIL = "http-estimate-%@example.com"

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    global ok
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()


def limpiar():
    """Borra los datos de prueba, en orden inverso a las claves foráneas."""
    cur.execute(
        """SELECT o.id FROM oportunidades o JOIN leads l ON l.id = o.lead_id
           JOIN clientes c ON c.id = l.cliente_id WHERE c.email LIKE %s;""",
        (PATRON_EMAIL,),
    )
    ids = [f[0] for f in cur.fetchall()]
    cur.execute("DELETE FROM logs WHERE entity_type = 'oportunidad' AND entity_id = ANY(%s);", (ids,))
    cur.execute("DELETE FROM presupuestos WHERE oportunidad_id = ANY(%s);", (ids,))
    cur.execute("DELETE FROM oportunidades WHERE id = ANY(%s);", (ids,))
    cur.execute(
        "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %s);",
        (PATRON_EMAIL,),
    )
    cur.execute("DELETE FROM clientes WHERE email LIKE %s;", (PATRON_EMAIL,))
    cn.commit()


def contar_presupuestos():
    cur.execute("SELECT count(*) FROM presupuestos;")
    n = cur.fetchone()[0]
    cn.commit()
    return n


def ejecutar_sql(sql, params):
    cur.execute(sql, params)
    cn.commit()


def lead_http(sufijo, tipo, nivel, m2, estructural):
    """Crea un lead por POST /leads y devuelve su oportunidad_id."""
    r = requests.post(
        f"{BASE}/leads",
        headers=AUTH,
        json={
            "nombre": f"Prueba {sufijo}",
            "email": f"http-estimate-{sufijo}@example.com",
            "telefono": "600000000",
            "tipo_reforma": tipo,
            "m2": m2,
            "nivel_acabados": nivel,
            "incluye_cambios_estructurales": estructural,
            "lead_token": f"tok-{sufijo}",
        },
        timeout=20,
    )
    assert r.status_code == 201, r.text
    return r.json()["oportunidad_id"]


def calcular(cuerpo, cabeceras=AUTH):
    """POST /calculate-estimate; devuelve (respuesta, milisegundos)."""
    t0 = time.perf_counter()
    r = requests.post(f"{BASE}/calculate-estimate", headers=cabeceras, json=cuerpo, timeout=20)
    return r, (time.perf_counter() - t0) * 1000


limpiar()

servidor = uvicorn.Server(
    uvicorn.Config("app.main:app", host="127.0.0.1", port=PUERTO, log_level="warning")
)
hilo = threading.Thread(target=servidor.run, daemon=True)
print("=" * 78)
print("ARRANQUE DEL SERVIDOR (uvicorn.Server, sin --reload)")
print("=" * 78)
hilo.start()
inicio = time.time()
while time.time() - inicio < 30:
    try:
        if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
            break
    except requests.exceptions.RequestException:
        time.sleep(0.25)
else:
    print("  El servidor no respondió en 30 s")
    sys.exit(1)
print(f"  Servidor arriba en {time.time() - inicio:.2f} s")

try:
    print("\n" + "=" * 78)
    print("H0 - El contrato publicado en /openapi.json")
    print("=" * 78)
    esquema = requests.get(f"{BASE}/openapi.json", timeout=5).json()
    comprobar("POST /calculate-estimate publicado", "/calculate-estimate" in esquema["paths"])
    peticion = esquema["components"]["schemas"]["EstimateRequest"]
    comprobar("la petición solo admite oportunidad_id",
              list(peticion["properties"]) == ["oportunidad_id"]
              and peticion.get("additionalProperties") is False,
              f"({list(peticion['properties'])}, additionalProperties="
              f"{peticion.get('additionalProperties')})")

    op_ok = lead_http("ok", "bano", "medio", 6, False)
    op_estr = lead_http("estr", "bano", "medio", 6, True)

    print("\n" + "=" * 78)
    print("H1 - Autenticación")
    print("=" * 78)
    antes = contar_presupuestos()
    r, _ = calcular({"oportunidad_id": op_ok}, cabeceras={})
    comprobar("sin X-Webhook-Secret -> 401", r.status_code == 401, f"({r.status_code} {r.text})")
    r, _ = calcular({"oportunidad_id": op_ok}, cabeceras={"X-Webhook-Secret": "malo"})
    comprobar("secreto incorrecto -> 401", r.status_code == 401)
    comprobar("no se ha escrito ningún presupuesto", contar_presupuestos() == antes)

    print("\n" + "=" * 78)
    print("H2 - Validación del cuerpo (422, sin tocar la base de datos)")
    print("=" * 78)
    for nombre, cuerpo in [
        ("lead_id en vez de oportunidad_id", {"lead_id": op_ok}),
        ("oportunidad_id + m2 (campo prohibido)", {"oportunidad_id": op_ok, "m2": 1}),
        ("oportunidad_id + incluye_cambios_estructurales",
         {"oportunidad_id": op_estr, "incluye_cambios_estructurales": False}),
        ("oportunidad_id = 0", {"oportunidad_id": 0}),
        ("oportunidad_id de texto", {"oportunidad_id": "abc"}),
    ]:
        r, _ = calcular(cuerpo)
        comprobar(nombre, r.status_code == 422, f"({r.status_code})")
    comprobar("sigue sin escribirse ningún presupuesto", contar_presupuestos() == antes)

    print("\n" + "=" * 78)
    print("H3 - Oportunidad inexistente -> 404")
    print("=" * 78)
    r, _ = calcular({"oportunidad_id": 2_000_000_000})
    comprobar("404", r.status_code == 404, f"({r.status_code} {r.json()})")

    print("\n" + "=" * 78)
    print("H4 - Caso válido sin Gate, y H5 repetición")
    print("=" * 78)
    r, ms_nuevo = calcular({"oportunidad_id": op_ok})
    j = r.json()
    print(f"    {r.status_code} {j}  ({ms_nuevo:.0f} ms)")
    comprobar("200", r.status_code == 200)
    comprobar("importes como texto exacto en el JSON",
              (j["importe_min_con_iva"], j["importe_max_con_iva"]) == ("8349.00", "9601.35"))
    # Comprobación explícita de la relación con el precio sin IVA (D14).
    # Los dos números se escriben aquí a mano: 6900 x 1,21 = 8349,00 y
    # 7935 x 1,21 = 9601,35. Son DOS valores distintos a propósito, para
    # que no pueda pasar por casualidad.
    comprobar("H4 el JSON trae el precio CON IVA = sin IVA x 1,21",
              (Decimal(j["importe_min_con_iva"]), Decimal(j["importe_max_con_iva"]))
              == ((Decimal("6900.00") * Decimal("1.21")).quantize(Decimal("0.01")),
                  (Decimal("7935.00") * Decimal("1.21")).quantize(Decimal("0.01"))))
    comprobar("sin Gate, presupuesto_enviado, creado=true",
              (j["motivo_gate"], j["requiere_aprobacion"], j["status"], j["creado"])
              == (None, False, "presupuesto_enviado", True))
    r2, ms_repe = calcular({"oportunidad_id": op_ok})
    j2 = r2.json()
    print(f"    {r2.status_code} {j2}  ({ms_repe:.0f} ms)")
    comprobar("repetición: 200, mismo presupuesto_id, creado=false",
              r2.status_code == 200 and j2["presupuesto_id"] == j["presupuesto_id"]
              and j2["creado"] is False)

    print("\n" + "=" * 78)
    print("H6 - Cambios estructurales: la puerta REST SÍ enseña los importes")
    print("=" * 78)
    r, _ = calcular({"oportunidad_id": op_estr})
    j = r.json()
    print(f"    {r.status_code} {j}")
    comprobar("200 con importes visibles",
              r.status_code == 200
              and (j["importe_min_con_iva"], j["importe_max_con_iva"]) == ("9075.00", "10436.25"))
    # Segundo caso con números distintos del anterior: 7500 x 1,21 =
    # 9075,00 y 8625 x 1,21 = 10436,25.
    comprobar("H6 el precio CON IVA vuelve a ser el sin IVA x 1,21",
              (Decimal(j["importe_min_con_iva"]), Decimal(j["importe_max_con_iva"]))
              == ((Decimal("7500.00") * Decimal("1.21")).quantize(Decimal("0.01")),
                  (Decimal("8625.00") * Decimal("1.21")).quantize(Decimal("0.01"))))
    comprobar("motivo cambios_estructurales, pendiente_aprobacion",
              (j["motivo_gate"], j["status"]) == ("cambios_estructurales", "pendiente_aprobacion"))

    print("\n" + "=" * 78)
    print("H6b - Umbral por categoría (D9): cocina típica de 10 m², sin Gate")
    print("=" * 78)
    # Es el caso que motivó D9, comprobado aquí de extremo a extremo por
    # HTTP: 1.000 €/m² × 10 m² × 1,15 = 11.500 €. Con el umbral global
    # antiguo (10.000 €) esta petición habría devuelto
    # motivo_gate='importe_superior_umbral' y estado
    # 'pendiente_aprobacion'; con el umbral de cocina (13.000 €) pasa sin
    # aprobación humana, que es lo que la ficha de tarifas_base declara
    # para esa combinación.
    op_cocina = lead_http("cocina10", "cocina", "medio", 10, False)
    r, _ = calcular({"oportunidad_id": op_cocina})
    j = r.json()
    print(f"    {r.status_code} {j}")
    comprobar("200, 11500.00 y SIN Gate",
              r.status_code == 200
              and (j["importe_min_con_iva"], j["importe_max_con_iva"]) == ("12100.00", "13915.00")
              and j["motivo_gate"] is None and j["requiere_aprobacion"] is False
              and j["status"] == "presupuesto_enviado")
    # El Gate se decide con el importe SIN IVA, y este caso lo demuestra
    # por HTTP: el precio que se devuelve (13.915,00 € con IVA) SUPERA el
    # umbral de cocina (13.000 €), y aun así no hay Gate, porque la
    # comparación se hizo contra los 11.500,00 € sin IVA. Si alguien
    # cambiara esa decisión, esta comprobación fallaría.
    comprobar("H6b el Gate ignora el IVA: 13.915 > 13.000 y aun así sin Gate",
              Decimal(j["importe_max_con_iva"]) > Decimal("13000.00")
              and j["motivo_gate"] is None,
              f"(con IVA {j['importe_max_con_iva']}, sin IVA 11500.00)")

    print("\n" + "=" * 78)
    print("H7 - Oportunidad 'perdida' sin presupuesto -> 409")
    print("=" * 78)
    op_perdida = lead_http("perdida", "cocina", "medio", 10, False)
    ejecutar_sql("UPDATE oportunidades SET estado = 'perdida' WHERE id = %s;", (op_perdida,))
    r, _ = calcular({"oportunidad_id": op_perdida})
    comprobar("409", r.status_code == 409, f"({r.status_code} {r.json()})")

    print("\n" + "=" * 78)
    print("H8 - Sin tarifa -> 200 con status requiere_revision")
    print("=" * 78)
    op_null = lead_http("null", "cocina", "medio", 10, False)
    ejecutar_sql("UPDATE oportunidades SET tipo_reforma = NULL WHERE id = %s;", (op_null,))
    r, _ = calcular({"oportunidad_id": op_null})
    j = r.json()
    print(f"    {r.status_code} {j}")
    comprobar("200, requiere_revision, sin importes",
              r.status_code == 200 and (j["status"], j["importe_min_con_iva"], j["presupuesto_id"])
              == ("requiere_revision", None, None))

    print("\n" + "=" * 78)
    print("H9 - Tiempos medidos (informativo, no es una aserción)")
    print("=" * 78)
    print(f"    cálculo nuevo : {ms_nuevo:.0f} ms (5 consultas + commit = 6 viajes a Supabase)")
    print(f"    reintento     : {ms_repe:.0f} ms (1 consulta + commit = 2 viajes a Supabase)")

finally:
    limpiar()
    print("\n  Datos de prueba eliminados.")
    servidor.should_exit = True
    hilo.join(timeout=15)
    cur.close()
    cn.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
for f in fallos:
    print(f"  FALLO: {f}")
print("=" * 78)
sys.exit(0 if not fallos else 1)
