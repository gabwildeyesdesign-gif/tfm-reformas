"""
BLOQUE 2 - Verificación real de app/services/estimate_service.py.

Dos partes:

  A) Funciones PURAS, sin base de datos: calcular_importes, evaluar_gate
     y ocultar_importes_para_agente. Los valores esperados se calcularon
     con Decimal ANTES de escribir el servicio (plan, sección 5), no
     copiando lo que devuelve el código.

  B) calculate_estimate() contra la base de datos REAL de Supabase:
     crea leads de verdad con create_lead() (el camino real de POST
     /leads), calcula y comprueba con SELECT, desde una conexión aparte,
     lo que ha quedado escrito en presupuestos, oportunidades y logs.

Datos de prueba: clientes con email check-estimate-*@example.com. Se
borran al empezar (por si una ejecución anterior se cortó) y al
terminar, en un finally, respetando el orden de las claves foráneas.

Lo que NO se prueba aquí, y por qué: el fallback por una regla que falte
en reglas_negocio. Provocarlo exigiría borrar (y confirmar el borrado) de
una regla real que usa el sistema entero. El camino de código es el
mismo que el de "falta la tarifa" (la lista 'faltan'), que sí se prueba
en B9 y B10.
"""

import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]
# sys.path es la lista de carpetas donde Python busca los módulos al
# hacer import. Se añade la raíz del repositorio para que
# "from app.services..." funcione al ejecutar este archivo desde
# scripts/. Mismo patrón que check_create_lead_service.py.
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2

from app.config import DATABASE_URL
from app.db import connection as db
from app.schemas.common import MotivoGate
from app.schemas.estimates import EstimateResponse
from app.schemas.leads import LeadCreate
from app.services.estimate_service import (
    EstadoNoPermiteCalculo,
    OportunidadNoEncontrada,
    calcular_importes,
    calculate_estimate,
    evaluar_gate,
    ocultar_importes_para_agente,
)
from app.services.leads_service import create_lead

PATRON_EMAIL = "check-estimate-%@example.com"
D = Decimal  # alias corto para que las tablas de casos se lean mejor

ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Cuenta un acierto o apunta un fallo, e imprime la línea."""
    global ok
    if condicion:
        ok += 1
        print(f"    [OK]    {titulo}  {detalle}")
    else:
        fallos.append(f"{titulo} {detalle}")
        print(f"    [FALLO] {titulo}  {detalle}")


# ======================================================================
print("=" * 78)
print("A) FUNCIONES PURAS (sin base de datos)")
print("=" * 78)
REC, MAR = D("600.00"), D("15.00")
# Desde D9 hay dos umbrales distintos en la tabla umbrales_gate: 13.000 €
# para bano y cocina, y 10.000 € para integral_vivienda y
# parcial_acabados (este último, provisional hasta cerrar D10).
UMB_10, UMB_13 = D("10000.00"), D("13000.00")

print("\n  calcular_importes(precio_m2, m2, estructural, recargo 600, margen 15):")
casos_importes = [
    ("bano medio, 6 m², sin estructural", D("1150.00"), D("6"), False, D("6900.00"), D("7935.00")),
    ("bano medio, 6 m², con estructural", D("1150.00"), D("6"), True, D("7500.00"), D("8625.00")),
    ("integral medio, 80 m², sin", D("800.00"), D("80"), False, D("64000.00"), D("73600.00")),
    ("integral medio, 80 m², con", D("800.00"), D("80"), True, D("64600.00"), D("74290.00")),
    ("bano medio, 8.7 m² (trampa float)", D("1150.00"), D("8.7"), False, D("10005.00"), D("11505.75")),
    ("cocina basico, 8.333 m² (redondeo)", D("650.00"), D("8.333"), False, D("5416.45"), D("6228.92")),
]
for nombre, precio, m2, estr, esp_min, esp_max in casos_importes:
    mn, mx = calcular_importes(precio, m2, estr, REC, MAR)
    comprobar(nombre, (mn, mx) == (esp_min, esp_max), f"-> {mn} / {mx}")

print("\n  evaluar_gate(importe_max, estructural, umbral):")
# evaluar_gate NO cambió con D9: sigue recibiendo el umbral como
# parámetro, así que aquí se prueba con los DOS umbrales que existen hoy.
# Que la misma función dé resultados distintos según el umbral recibido
# es justo lo que permitió que D9 no tuviera que tocar el cálculo.
casos_gate = [
    (D("7935.00"), False, UMB_10, None),
    (D("8625.00"), True, UMB_10, MotivoGate.CAMBIOS_ESTRUCTURALES),
    (D("73600.00"), False, UMB_10, MotivoGate.IMPORTE_SUPERIOR_UMBRAL),
    (D("74290.00"), True, UMB_10, MotivoGate.AMBOS),
    (D("10000.00"), False, UMB_10, None),  # límite exacto: ">" no salta
    (D("10000.01"), False, UMB_10, MotivoGate.IMPORTE_SUPERIOR_UMBRAL),
    # Con el umbral nuevo de baño y cocina: lo que antes disparaba el
    # Gate (11.505,75 €) ya no lo dispara, y el límite exacto tampoco.
    (D("11505.75"), False, UMB_13, None),
    (D("13000.00"), False, UMB_13, None),
    (D("13000.01"), False, UMB_13, MotivoGate.IMPORTE_SUPERIOR_UMBRAL),
    (D("15870.00"), False, UMB_13, MotivoGate.IMPORTE_SUPERIOR_UMBRAL),
]
for importe, estr, umbral, esperado in casos_gate:
    obtenido = evaluar_gate(importe, estr, umbral)
    comprobar(f"{importe} estructural={estr} umbral={umbral}",
              obtenido == esperado, f"-> {obtenido}")

print("\n  ocultar_importes_para_agente:")
for motivo, debe_ocultar in [
    (MotivoGate.CAMBIOS_ESTRUCTURALES, True),
    (MotivoGate.AMBOS, True),
    (MotivoGate.IMPORTE_SUPERIOR_UMBRAL, False),
    (None, False),
]:
    original = EstimateResponse(
        presupuesto_id=1, oportunidad_id=1, importe_min=D("1.00"), importe_max=D("2.00"),
        motivo_gate=motivo, requiere_aprobacion=motivo is not None,
        status="x", creado=True,
    )
    r = ocultar_importes_para_agente(original)
    ocultos = r.importe_min is None and r.importe_max is None
    comprobar(
        f"motivo={motivo.value if motivo else None}",
        ocultos == debe_ocultar and original.importe_min == D("1.00"),
        f"-> ocultos={ocultos} (original intacto: {original.importe_min})",
    )


# ======================================================================
# Utilidades para la parte B
# ======================================================================
def limpiar(cur, cn):
    """Borra todo lo de prueba, en orden inverso a las claves foráneas."""
    cur.execute(
        """
        SELECT o.id FROM oportunidades o
        JOIN leads l ON l.id = o.lead_id
        JOIN clientes c ON c.id = l.cliente_id
        WHERE c.email LIKE %s;
        """,
        (PATRON_EMAIL,),
    )
    ids = [f[0] for f in cur.fetchall()]
    cur.execute(
        "DELETE FROM logs WHERE entity_type = 'oportunidad' AND entity_id = ANY(%s);", (ids,)
    )
    cur.execute("DELETE FROM presupuestos WHERE oportunidad_id = ANY(%s);", (ids,))
    cur.execute("DELETE FROM oportunidades WHERE id = ANY(%s);", (ids,))
    cur.execute(
        "DELETE FROM leads WHERE cliente_id IN (SELECT id FROM clientes WHERE email LIKE %s);",
        (PATRON_EMAIL,),
    )
    cur.execute("DELETE FROM clientes WHERE email LIKE %s;", (PATRON_EMAIL,))
    cn.commit()


def nuevo_lead(sufijo, tipo, nivel, m2, estructural):
    """Crea un lead por el camino real y devuelve su oportunidad_id."""
    r = create_lead(
        LeadCreate(
            nombre=f"Prueba {sufijo}",
            email=f"check-estimate-{sufijo}@example.com",
            telefono="600000000",
            tipo_reforma=tipo,
            m2=m2,
            nivel_acabados=nivel,
            incluye_cambios_estructurales=estructural,
            lead_token=f"tok-{sufijo}",
        )
    )
    return r.oportunidad_id


def estado_bd(cur, cn, oportunidad_id):
    """Lee desde la conexión aparte lo que ha quedado guardado de verdad."""
    cur.execute(
        """
        SELECT o.estado, o.datos_completos, o.confianza_ia, o.prioridad,
               (SELECT count(*) FROM presupuestos p WHERE p.oportunidad_id = o.id),
               (SELECT count(*) FROM logs g WHERE g.entity_type = 'oportunidad'
                  AND g.entity_id = o.id AND g.accion = 'presupuesto_calculado'),
               (SELECT count(*) FROM logs g WHERE g.entity_type = 'oportunidad'
                  AND g.entity_id = o.id AND g.accion = 'presupuesto_requiere_revision')
        FROM oportunidades o WHERE o.id = %s;
        """,
        (oportunidad_id,),
    )
    fila = cur.fetchone()
    cn.commit()
    claves = ("estado", "datos_completos", "confianza_ia", "prioridad",
              "n_presupuestos", "n_logs_calculado", "n_logs_revision")
    return dict(zip(claves, fila))


def presupuesto_bd(cur, cn, oportunidad_id):
    cur.execute(
        """
        SELECT id, importe_min, importe_max, motivo_gate, requiere_aprobacion,
               duracion_estimada_dias, aprobado_por
        FROM presupuestos WHERE oportunidad_id = %s;
        """,
        (oportunidad_id,),
    )
    fila = cur.fetchone()
    cn.commit()
    return fila


def caso_calculo(titulo, sufijo, tipo, nivel, m2, estr, esp_min, esp_max, esp_motivo, esp_estado):
    """Crea un lead, calcula y comprueba respuesta y base de datos."""
    print(f"\n  {titulo}")
    op = nuevo_lead(sufijo, tipo, nivel, m2, estr)
    r = calculate_estimate(op)
    print(f"    respuesta: {r.model_dump()}")
    pres = presupuesto_bd(cur, cn, op)
    bd = estado_bd(cur, cn, op)
    comprobar("creado=True", r.creado is True)
    comprobar("importes esperados", (r.importe_min, r.importe_max) == (esp_min, esp_max),
              f"({r.importe_min} / {r.importe_max})")
    comprobar("motivo_gate esperado", r.motivo_gate == esp_motivo, f"({r.motivo_gate})")
    comprobar("requiere_aprobacion coherente", r.requiere_aprobacion == (esp_motivo is not None))
    comprobar("status = estado esperado", r.status == esp_estado, f"({r.status})")
    comprobar("fila en presupuestos = respuesta",
              pres is not None and pres[0] == r.presupuesto_id
              and (pres[1], pres[2]) == (esp_min, esp_max)
              and pres[3] == (esp_motivo.value if esp_motivo else None)
              and pres[4] == r.requiere_aprobacion,
              f"({pres})")
    comprobar("duracion_estimada_dias y aprobado_por NULL", pres[5] is None and pres[6] is None)
    comprobar("estado en la BD = status", bd["estado"] == r.status, f"({bd['estado']})")
    comprobar("datos_completos, confianza_ia y prioridad intactos",
              (bd["datos_completos"], bd["confianza_ia"], bd["prioridad"]) == (False, None, None))
    comprobar("1 presupuesto y 1 log de cálculo",
              (bd["n_presupuestos"], bd["n_logs_calculado"]) == (1, 1),
              f"({bd['n_presupuestos']}, {bd['n_logs_calculado']})")
    return op, r


# ======================================================================
db.init_pool()
# Conexión APARTE, fuera del pool, para comprobar lo guardado: así se ve
# solo lo que el servicio confirmó de verdad (lo no confirmado de otra
# conexión es invisible).
cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor()
limpiar(cur, cn)

try:
    print("\n" + "=" * 78)
    print("B) calculate_estimate() CONTRA LA BASE DE DATOS REAL")
    print("=" * 78)

    op1, r1 = caso_calculo("B1 baño medio 6 m², sin estructural -> sin Gate",
                           "b1", "bano", "medio", 6, False,
                           D("6900.00"), D("7935.00"), None, "presupuesto_enviado")

    # El log de auditoría guarda las tarifas y reglas usadas.
    cur.execute(
        """SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s
           AND accion = 'presupuesto_calculado';""",
        (op1,),
    )
    detalle = cur.fetchone()[0]
    cn.commit()
    print(f"    detalle del log: {detalle}")
    # El umbral que guarda el log es ahora el de la CATEGORÍA (D9): baño
    # -> 13.000 €, no el antiguo global de 10.000 €. Y queda anotado si
    # ese umbral era provisional (aquí no lo es; solo lo es hoy el de
    # parcial_acabados).
    comprobar("log guarda precio_m2, margen, recargo y el umbral de la categoría",
              (detalle["precio_m2"], detalle["margen_empresa_pct"],
               detalle["recargo_informe_tecnico"], detalle["umbral_gate"],
               detalle["umbral_provisional"])
              == ("1150.00", "15.00", "600.00", "13000.00", False),
              f"(umbral_gate={detalle['umbral_gate']}, "
              f"provisional={detalle['umbral_provisional']})")

    print("\n  B2 segunda llamada a la misma oportunidad (idempotencia)")
    r2 = calculate_estimate(op1)
    bd = estado_bd(cur, cn, op1)
    comprobar("mismo presupuesto_id", r2.presupuesto_id == r1.presupuesto_id)
    comprobar("creado=False", r2.creado is False)
    comprobar("mismos importes, motivo y status",
              (r2.importe_min, r2.importe_max, r2.motivo_gate, r2.status)
              == (r1.importe_min, r1.importe_max, r1.motivo_gate, r1.status))
    comprobar("sigue habiendo 1 presupuesto y 1 log (nada escrito)",
              (bd["n_presupuestos"], bd["n_logs_calculado"]) == (1, 1))

    op3, r3 = caso_calculo("B3 baño medio 6 m², CON estructural -> cambios_estructurales",
                           "b3", "bano", "medio", 6, True,
                           D("7500.00"), D("8625.00"), MotivoGate.CAMBIOS_ESTRUCTURALES,
                           "pendiente_aprobacion")
    oculto = ocultar_importes_para_agente(r3)
    comprobar("versión para el agente sin importes",
              oculto.importe_min is None and oculto.importe_max is None
              and oculto.presupuesto_id == r3.presupuesto_id)

    caso_calculo("B4 integral medio 80 m², CON estructural -> ambos",
                 "b4", "integral_vivienda", "medio", 80, True,
                 D("64600.00"), D("74290.00"), MotivoGate.AMBOS, "pendiente_aprobacion")

    caso_calculo("B5 integral medio 80 m², sin estructural -> importe_superior_umbral",
                 "b5", "integral_vivienda", "medio", 80, False,
                 D("64000.00"), D("73600.00"), MotivoGate.IMPORTE_SUPERIOR_UMBRAL,
                 "pendiente_aprobacion")

    # m2=8.7 entra como float en LeadCreate, viaja como 8.7 dentro del
    # JSONB y el servicio lo lee con ->> ::numeric. Si en algún punto se
    # colara un float, importe_min no sería 10005.00 exacto.
    #
    # CAMBIO DE D9: este caso esperaba antes Gate por importe y estado
    # 'pendiente_aprobacion', porque 11.505,75 € superaba el umbral
    # global de 10.000 €. Con el umbral de baño en 13.000 €, el mismo
    # presupuesto queda por DEBAJO y ya no activa el Gate. No es que la
    # prueba se haya relajado: es exactamente el comportamiento que D9
    # quería para baño y cocina, y aquí se verifica de verdad contra la
    # base de datos. El Gate por importe en baño se sigue probando en
    # B6b, con una superficie que sí supera los 13.000 €.
    caso_calculo("B6 baño medio 8.7 m² (m2 float; con D9 ya NO activa Gate)",
                 "b6", "bano", "medio", 8.7, False,
                 D("10005.00"), D("11505.75"), None,
                 "presupuesto_enviado")

    # Prueba nueva de D9: el Gate por importe sigue existiendo en baño,
    # pero ahora salta en el umbral nuevo. 1150 × 12 = 13.800;
    # 13.800 × 1,15 = 15.870 > 13.000.
    caso_calculo("B6b baño medio 12 m² -> supera el umbral NUEVO (13.000)",
                 "b6b", "bano", "medio", 12, False,
                 D("13800.00"), D("15870.00"), MotivoGate.IMPORTE_SUPERIOR_UMBRAL,
                 "pendiente_aprobacion")

    # El caso de negocio que motivó D9: una cocina de tamaño típico
    # (10 m², nivel medio) daba 11.500 € y activaba el Gate con el umbral
    # antiguo, contradiciendo lo que la propia fila de tarifas_base
    # declara para esa combinación. Con 13.000 €, pasa sin Gate.
    caso_calculo("B12 cocina medio 10 m² (caso típico de D9) -> sin Gate",
                 "b12", "cocina", "medio", 10, False,
                 D("10000.00"), D("11500.00"), None,
                 "presupuesto_enviado")

    print("\n  B7 oportunidad inexistente")
    try:
        calculate_estimate(2_000_000_000)
        comprobar("lanza OportunidadNoEncontrada", False, "(no lanzó nada)")
    except OportunidadNoEncontrada as e:
        comprobar("lanza OportunidadNoEncontrada", True, f"({e})")

    print("\n  B8 oportunidad en 'perdida' sin presupuesto (orden de estados)")
    op8 = nuevo_lead("b8", "cocina", "medio", 10, False)
    cur.execute("UPDATE oportunidades SET estado = 'perdida' WHERE id = %s;", (op8,))
    cn.commit()
    try:
        calculate_estimate(op8)
        comprobar("lanza EstadoNoPermiteCalculo", False, "(no lanzó nada)")
    except EstadoNoPermiteCalculo as e:
        comprobar("lanza EstadoNoPermiteCalculo", True, f"({e})")
    bd = estado_bd(cur, cn, op8)
    comprobar("0 presupuestos y estado sigue 'perdida'",
              (bd["n_presupuestos"], bd["estado"]) == (0, "perdida"))

    print("\n  B9 tipo_reforma NULL -> fallback 'requiere_revision'")
    op9 = nuevo_lead("b9", "cocina", "medio", 10, False)
    cur.execute("UPDATE oportunidades SET tipo_reforma = NULL WHERE id = %s;", (op9,))
    cn.commit()
    r9 = calculate_estimate(op9)
    print(f"    respuesta: {r9.model_dump()}")
    bd = estado_bd(cur, cn, op9)
    comprobar("status requiere_revision, sin importes ni presupuesto_id",
              (r9.status, r9.importe_min, r9.importe_max, r9.presupuesto_id, r9.creado)
              == ("requiere_revision", None, None, None, False))
    comprobar("0 presupuestos, estado sigue 'nueva', 1 log de revisión",
              (bd["n_presupuestos"], bd["estado"], bd["n_logs_revision"]) == (0, "nueva", 1))
    cur.execute(
        """SELECT detalle FROM logs WHERE entity_type = 'oportunidad' AND entity_id = %s
           AND accion = 'presupuesto_requiere_revision';""",
        (op9,),
    )
    detalle9 = cur.fetchone()[0]
    cn.commit()
    # CAMBIO DE D9: con tipo_reforma NULL faltan ahora DOS cosas, no una.
    # Antes solo fallaba la búsqueda de la tarifa, porque el umbral era
    # un valor global que no dependía de la categoría. Ahora el umbral
    # también se busca por tipo_reforma, así que una categoría ausente
    # deja sin encontrar las dos. La respuesta al cliente no cambia
    # (sigue siendo 'requiere_revision'); lo que mejora es el diagnóstico
    # escrito en el log, que ahora nombra los dos datos que faltaban.
    comprobar("el log dice qué faltaba (tarifa y umbral)",
              detalle9["faltan"] == ["tarifa", "umbral_gate"], f"({detalle9})")

    print("\n  B10 lead cuyo JSON no trae m2 -> fallback 'requiere_revision'")
    op10 = nuevo_lead("b10", "cocina", "medio", 10, False)
    # El operador - de JSONB quita una clave del objeto: simula un lead
    # escrito por otro camino que no pasó por la validación de Pydantic.
    cur.execute(
        """UPDATE leads SET datos_estructurados = datos_estructurados - 'm2'
           WHERE id = (SELECT lead_id FROM oportunidades WHERE id = %s);""",
        (op10,),
    )
    cn.commit()
    r10 = calculate_estimate(op10)
    bd = estado_bd(cur, cn, op10)
    comprobar("status requiere_revision y 0 presupuestos",
              (r10.status, bd["n_presupuestos"]) == ("requiere_revision", 0))

    print("\n  B11 carrera forzada: otra transacción crea el presupuesto a la vez")
    # Una conexión aparte inserta un presupuesto para la oportunidad y
    # cambia su estado, SIN confirmar todavía. Mientras tanto, el servicio
    # se ejecuta en otro hilo: su paso 1 no ve ese presupuesto (lo no
    # confirmado es invisible para otras conexiones), así que calcula e
    # intenta el INSERT. Ese INSERT se queda ESPERANDO en el índice
    # UNIQUE hasta que la otra conexión confirma. Entonces ON CONFLICT DO
    # NOTHING devuelve cero filas y el servicio debe devolver el
    # presupuesto de la otra conexión, sin error.
    op11 = nuevo_lead("b11", "bano", "basico", 5, False)
    cn_rival = psycopg2.connect(DATABASE_URL)
    cur_rival = cn_rival.cursor()
    cur_rival.execute(
        """INSERT INTO presupuestos (oportunidad_id, importe_min, importe_max, requiere_aprobacion)
           VALUES (%s, 111.11, 222.22, false) RETURNING id;""",
        (op11,),
    )
    id_rival = cur_rival.fetchone()[0]
    cur_rival.execute(
        "UPDATE oportunidades SET estado = 'presupuesto_enviado' WHERE id = %s;", (op11,)
    )
    resultado_hilo = {}

    def en_hilo():
        # Un hilo es otra línea de ejecución dentro del mismo programa.
        # Aquí permite que el servicio se quede bloqueado esperando a
        # Postgres mientras el hilo principal decide cuándo confirmar.
        try:
            resultado_hilo["r"] = calculate_estimate(op11)
        except Exception as e:  # se guarda para comprobarlo después
            resultado_hilo["error"] = e

    hilo = threading.Thread(target=en_hilo)
    hilo.start()
    time.sleep(3)  # tiempo de sobra para que el servicio llegue al INSERT y se bloquee
    sigue_esperando = hilo.is_alive()
    cn_rival.commit()
    hilo.join(timeout=30)
    cn_rival.close()
    r11 = resultado_hilo.get("r")
    print(f"    respuesta: {r11.model_dump() if r11 else resultado_hilo}")
    comprobar("el servicio estaba bloqueado esperando al rival", sigue_esperando)
    comprobar("sin error", "error" not in resultado_hilo)
    comprobar("devuelve el presupuesto del rival, creado=False",
              r11 is not None and (r11.presupuesto_id, r11.creado,
                                   r11.importe_min, r11.status)
              == (id_rival, False, D("111.11"), "presupuesto_enviado"))
    bd = estado_bd(cur, cn, op11)
    comprobar("1 presupuesto y ningún log de cálculo (el servicio no escribió)",
              (bd["n_presupuestos"], bd["n_logs_calculado"]) == (1, 0))

finally:
    limpiar(cur, cn)
    print("\n  Datos de prueba eliminados.")
    cur.close()
    cn.close()
    db.close_pool()

print("\n" + "=" * 78)
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
for f in fallos:
    print(f"  FALLO: {f}")
print("=" * 78)
sys.exit(0 if not fallos else 1)
