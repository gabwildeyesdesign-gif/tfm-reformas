"""
PASO 2 - Verificacion real de leads_service.create_lead().

Tres pruebas:
  A) lead CON fotos
  B) lead SIN fotos
  C) mismo email que A -> mismo cliente_id, pero lead y oportunidad nuevos

Todo se comprueba con SELECT desde una conexion APARTE, fuera del pool,
y con un JOIN por las tres claves foraneas reales.
"""

import sys
from pathlib import Path

# Este script vive en scripts/, pero el paquete "app" esta en la raiz del
# repositorio. Al ejecutar "python scripts/<archivo>.py", Python solo anade
# la carpeta del archivo (scripts/) a sys.path, asi que "import app.algo"
# fallaria con ModuleNotFoundError. Se calcula la raiz a partir de la
# ubicacion de este mismo archivo, en vez de escribir una ruta absoluta,
# para que funcione en cualquier maquina y desde cualquier directorio.
# Misma tecnica que scripts/check_graceful_shutdown.py.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import DATABASE_URL
from app.db import connection as db
from app.schemas.leads import LeadCreate
from app.services.leads_service import create_lead

EMAIL_A = "prueba-a@example.com"
EMAIL_B = "prueba-b@example.com"
EMAILS = (EMAIL_A, EMAIL_B)

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


def limpiar(cur, cn):
    """Borra las filas de prueba respetando el orden de las FK."""
    cur.execute(
        """
        DELETE FROM oportunidades WHERE lead_id IN (
            SELECT l.id FROM leads l JOIN clientes c ON c.id = l.cliente_id
            WHERE c.email = ANY(%s)
        );
        """,
        (list(EMAILS),),
    )
    cur.execute(
        "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email = ANY(%s));",
        (list(EMAILS),),
    )
    cur.execute("DELETE FROM clientes WHERE email = ANY(%s);", (list(EMAILS),))
    cn.commit()


def fila_completa(cur, cn, lead_id):
    """Trae la fila unida por las FK: cliente + lead + oportunidad."""
    cur.execute(
        """
        SELECT c.id   AS cliente_id, c.nombre, c.email, c.telefono,
               l.id   AS lead_id, l.canal, l.mensaje_original,
               l.fotos_urls, l.datos_estructurados,
               o.id   AS oportunidad_id, o.tipo_reforma, o.prioridad,
               o.confianza_ia, o.datos_completos, o.estado
        FROM leads l
        JOIN clientes c      ON c.id = l.cliente_id
        JOIN oportunidades o ON o.lead_id = l.id
        WHERE l.id = %s;
        """,
        (lead_id,),
    )
    fila = cur.fetchone()
    cn.commit()
    return fila


db.init_pool()
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor(cursor_factory=RealDictCursor)

limpiar(cur, cn)
print("Filas de prueba previas eliminadas.\n")

# ==================================================================
print("=" * 78)
print("PRUEBA A - Lead CON fotos")
print("=" * 78)
datos_a = LeadCreate(
    nombre="Ana Martínez",
    email=EMAIL_A,
    telefono="+34600111222",
    tipo_reforma="bano",
    m2=8.5,
    nivel_acabados="medio",
    incluye_cambios_estructurales=False,
    fotos=["leads-temp/tokA/f1.jpg", "leads-temp/tokA/f2.jpg"],
    lead_token="tokA",
)
resp_a = create_lead(datos_a)
print(f"  Respuesta: {resp_a.model_dump()}")

f = fila_completa(cur, cn, resp_a.lead_id)
print("\n  Fila real en la base de datos (JOIN por las 3 FK):")
for k, v in f.items():
    print(f"    {k:<22} = {v!r}")

print("\n  Comprobaciones:")
comprobar("Las 3 filas existen y el JOIN por las FK las une", f is not None)
comprobar("cliente_id de la respuesta == el de la base de datos",
          f["cliente_id"] == resp_a.cliente_id, f"({f['cliente_id']})")
comprobar("oportunidad_id de la respuesta == el de la base de datos",
          f["oportunidad_id"] == resp_a.oportunidad_id, f"({f['oportunidad_id']})")
# 2026-09-24: el canal pasa a ser 'chat_web' (CANAL_CHAT_WEB).
comprobar("canal = 'chat_web'", f["canal"] == "chat_web", f"({f['canal']!r})")
comprobar("mensaje_original es NULL (la conversacion vive en n8n)",
          f["mensaje_original"] is None)
comprobar("fotos_urls guarda las 2 rutas", f["fotos_urls"] == datos_a.fotos,
          f"({f['fotos_urls']})")
comprobar("datos_estructurados.tipo_reforma == 'bano'",
          f["datos_estructurados"]["tipo_reforma"] == "bano")
comprobar("datos_estructurados.nivel_acabados == 'medio'",
          f["datos_estructurados"]["nivel_acabados"] == "medio")
comprobar("datos_estructurados.m2 == 8.5", f["datos_estructurados"]["m2"] == 8.5)
comprobar("datos_estructurados.incluye_cambios_estructurales == False",
          f["datos_estructurados"]["incluye_cambios_estructurales"] is False)
comprobar("oportunidades.tipo_reforma (columna) == 'bano'", f["tipo_reforma"] == "bano")
comprobar("prioridad es NULL", f["prioridad"] is None)
comprobar("confianza_ia es NULL", f["confianza_ia"] is None)
comprobar("datos_completos es False", f["datos_completos"] is False)
comprobar("estado == 'nueva'", f["estado"] == "nueva")
comprobar("status de la respuesta == estado real de la fila",
          resp_a.status == f["estado"], f"('{resp_a.status}')")
comprobar("El cliente guardo el nombre y telefono enviados",
          f["nombre"] == "Ana Martínez" and f["telefono"] == "+34600111222")
# 2026-09-24: el contacto de ESTA llamada se guarda tambien en el lead.
comprobar("datos_estructurados.contacto existe con nombre, email y telefono",
          f["datos_estructurados"].get("contacto") == {
              "nombre": "Ana Martínez", "email": EMAIL_A, "telefono": "+34600111222"},
          f"({f['datos_estructurados'].get('contacto')})")

# ==================================================================
print("\n" + "=" * 78)
print("PRUEBA B - Lead SIN fotos (email distinto)")
print("=" * 78)
datos_b = LeadCreate(
    nombre="Luis Prieto",
    email=EMAIL_B,
    telefono="911223344",
    tipo_reforma="integral_vivienda",
    m2=95,
    nivel_acabados="alto",
    incluye_cambios_estructurales=True,
    lead_token="tokB",
)
print(f"  data.fotos (sin pasar el campo) = {datos_b.fotos!r}")
resp_b = create_lead(datos_b)
print(f"  Respuesta: {resp_b.model_dump()}")

fb = fila_completa(cur, cn, resp_b.lead_id)
print("\n  Fila real en la base de datos:")
for k, v in fb.items():
    print(f"    {k:<22} = {v!r}")

print("\n  Comprobaciones:")
comprobar("Las 3 filas existen y estan enlazadas", fb is not None)
comprobar("fotos_urls es una lista VACIA, no NULL",
          fb["fotos_urls"] == [] and fb["fotos_urls"] is not None,
          f"({fb['fotos_urls']!r})")
comprobar("tipo_reforma = 'integral_vivienda'", fb["tipo_reforma"] == "integral_vivienda")
comprobar("datos_estructurados.incluye_cambios_estructurales == True",
          fb["datos_estructurados"]["incluye_cambios_estructurales"] is True)
comprobar("datos_estructurados.m2 == 95", fb["datos_estructurados"]["m2"] == 95)
comprobar("Es un cliente DISTINTO al de la prueba A",
          fb["cliente_id"] != f["cliente_id"],
          f"(A={f['cliente_id']} B={fb['cliente_id']})")

# ==================================================================
print("\n" + "=" * 78)
print("PRUEBA C - MISMO email que A (cliente repetido, decision D2)")
print("=" * 78)
datos_c = LeadCreate(
    nombre="Ana M.",                 # nombre distinto a proposito
    email=EMAIL_A,                   # MISMO email que la prueba A
    telefono="+34699999999",         # telefono distinto a proposito
    tipo_reforma="cocina",
    m2=12,
    nivel_acabados="basico",
    incluye_cambios_estructurales=False,
    fotos=["leads-temp/tokC/f1.jpg"],
    lead_token="tokC",
)
resp_c = create_lead(datos_c)
print(f"  Respuesta A: {resp_a.model_dump()}")
print(f"  Respuesta C: {resp_c.model_dump()}")

print("\n  Comprobaciones:")
comprobar("MISMO cliente_id que la prueba A (no se duplico el cliente)",
          resp_c.cliente_id == resp_a.cliente_id,
          f"(A={resp_a.cliente_id} C={resp_c.cliente_id})")
comprobar("lead_id NUEVO y distinto",
          resp_c.lead_id != resp_a.lead_id,
          f"(A={resp_a.lead_id} C={resp_c.lead_id})")
comprobar("oportunidad_id NUEVO y distinto",
          resp_c.oportunidad_id != resp_a.oportunidad_id,
          f"(A={resp_a.oportunidad_id} C={resp_c.oportunidad_id})")

cur.execute("SELECT COUNT(*) AS n FROM clientes WHERE email = %s;", (EMAIL_A,))
n_clientes = cur.fetchone()["n"]
cn.commit()
comprobar("Solo hay UNA fila en clientes con ese email", n_clientes == 1, f"(n={n_clientes})")

cur.execute(
    "SELECT COUNT(*) AS n FROM leads WHERE cliente_id = %s;", (resp_a.cliente_id,)
)
n_leads = cur.fetchone()["n"]
cn.commit()
comprobar("Ese cliente tiene 2 leads", n_leads == 2, f"(n={n_leads})")

cur.execute(
    """
    SELECT c.nombre, c.telefono FROM clientes c WHERE c.email = %s;
    """,
    (EMAIL_A,),
)
cliente_tras_c = cur.fetchone()
cn.commit()
print(f"\n  Ficha del cliente tras la 2a llamada: {dict(cliente_tras_c)}")
comprobar("D2: NO se sobrescribio el nombre del cliente existente",
          cliente_tras_c["nombre"] == "Ana Martínez",
          f"('{cliente_tras_c['nombre']}')")
comprobar("D2: NO se sobrescribio el telefono del cliente existente",
          cliente_tras_c["telefono"] == "+34600111222",
          f"('{cliente_tras_c['telefono']}')")

fc = fila_completa(cur, cn, resp_c.lead_id)
comprobar("Pero los datos de ESA llamada si quedaron en el lead nuevo",
          fc["datos_estructurados"]["tipo_reforma"] == "cocina",
          f"({fc['datos_estructurados']})")
# Lo que antes se perdia (hallazgo b): con email repetido, el nombre y el
# telefono NUEVOS no quedaban en ningun sitio. Ahora estan en el lead C,
# y el lead A conserva los suyos: cada lead guarda el contacto de su
# propia llamada.
comprobar("Email repetido: el lead nuevo conserva el contacto NUEVO",
          fc["datos_estructurados"].get("contacto") == {
              "nombre": "Ana M.", "email": EMAIL_A, "telefono": "+34699999999"},
          f"({fc['datos_estructurados'].get('contacto')})")
fa = fila_completa(cur, cn, resp_a.lead_id)
comprobar("Y el lead A sigue con SU contacto original",
          fa["datos_estructurados"].get("contacto", {}).get("telefono") == "+34600111222",
          f"({fa['datos_estructurados'].get('contacto')})")

# ==================================================================
print("\n" + "=" * 78)
print("LIMPIEZA")
print("=" * 78)
limpiar(cur, cn)
for tabla in ("clientes", "leads", "oportunidades"):
    cur.execute(f"SELECT COUNT(*) AS n FROM {tabla};")
    print(f"  {tabla:<16} filas restantes: {cur.fetchone()['n']}")
cn.commit()

db.close_pool()
cur.close()
cn.close()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for x in fallos:
        print("  - " + x)
    sys.exit(1)
print("Todas las comprobaciones han pasado.")
