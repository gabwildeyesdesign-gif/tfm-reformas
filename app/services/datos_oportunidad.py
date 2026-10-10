"""
Los datos de una oportunidad que comparten GET /gate-avisos y la ficha
(GET /oportunidades/{id}/ficha), escritos UNA sola vez (plan:
docs/Plan_Endpoint_Ficha_Oportunidad.txt, sección 4; D26.6).

Mismo patrón que condicion_seguimiento.py: una tupla de pares (nombre,
expresión SQL) con las columnas del caso, y funciones que construyen las
piezas de la respuesta a partir de lo leído. Así, el contacto (regla P1 de
gate-avisos: el de la solicitud, nunca la ficha de clientes), la reforma,
las fotos y los importes con y sin IVA salen IGUAL en los dos endpoints.

a_madrid y deducir_sin_iva se movieron aquí desde aviso_gate_service.py
(Bloque B1) SIN cambiar su cuerpo; aviso_gate_service las importa de aquí.

Como todo services/, este archivo NO importa fastapi ni fastmcp.
"""

# datetime: para anotar el tipo de las fechas que se convierten a Madrid.
from datetime import datetime

# Decimal: los importes y el IVA llegan de Postgres como Decimal exacto, y
# las cuentas se hacen con Decimal, nunca con float.
from decimal import Decimal

# Las piezas de la respuesta que se construyen aquí (app/schemas/aviso_gate.py).
from app.schemas.aviso_gate import ContactoAviso, OrigenContacto, ReformaAviso

# El MISMO redondeo que usa el cálculo del presupuesto (céntimos,
# ROUND_HALF_UP) y la constante Decimal("100"). Importarlos, y no copiarlos,
# garantiza que la deducción del sin IVA redondea exactamente igual que el
# cálculo (plan de gate-avisos, 2.1).
from app.services.estimate_service import CIEN, redondear

# ZONA_MADRID: la zona horaria Europe/Madrid.
from app.services.reglas_visita import ZONA_MADRID

# ======================================================================
# Las columnas del caso
# ======================================================================

# Las 17 columnas del caso, EN ORDEN, como pares (nombre, expresión SQL).
# Las expresiones usan los alias de las consultas que las incluyen:
#   o = oportunidades, l = leads, p = presupuestos, u = umbrales_gate.
# Son textos FIJOS del programa (ningún dato del usuario entra aquí).
#   - "->" saca un objeto de dentro del JSONB (el de 'contacto') y "->>"
#     saca un valor como TEXTO.
#   - "datos_estructurados ? 'contacto'": el operador ? de JSONB pregunta
#     si el objeto TIENE esa clave (true/false). Así se distingue "sin
#     clave" (origen no_disponible, P1) de una clave presente.
#   - "::numeric" y "::boolean" convierten ese texto DENTRO de Postgres: m2
#     llega como Decimal exacto, sin pasar por float.
#   - Sin ninguna columna de clientes: la ficha de clientes no se lee nunca
#     (P1).
COLUMNAS_CASO = (
    # Estado de la oportunidad y su tipo de reforma.
    ("estado", "o.estado"),
    ("tipo_reforma", "o.tipo_reforma"),
    # Fecha de la solicitud (la del lead).
    ("fecha_solicitud", "l.created_at"),
    # Contacto de la solicitud: si existe la clave, y sus tres valores.
    ("tiene_contacto", "l.datos_estructurados ? 'contacto'"),
    ("contacto_nombre", "l.datos_estructurados -> 'contacto' ->> 'nombre'"),
    ("contacto_email", "l.datos_estructurados -> 'contacto' ->> 'email'"),
    ("contacto_telefono", "l.datos_estructurados -> 'contacto' ->> 'telefono'"),
    # La reforma pedida, leída del JSONB con su tipo exacto.
    ("m2", "(l.datos_estructurados ->> 'm2')::numeric"),
    ("nivel_acabados", "l.datos_estructurados ->> 'nivel_acabados'"),
    ("cambios_estructurales", "(l.datos_estructurados ->> 'incluye_cambios_estructurales')::boolean"),
    # Las rutas de las fotos (JSONB: psycopg2 lo entrega como lista).
    ("fotos_urls", "l.fotos_urls"),
    # El presupuesto: fecha, motivo del Gate, IVA aplicado e importes.
    ("fecha_presupuesto", "p.created_at"),
    ("motivo_gate", "p.motivo_gate"),
    ("iva_pct", "p.iva_pct_aplicado"),
    ("importe_min_con_iva", "p.importe_min_con_iva"),
    ("importe_max_con_iva", "p.importe_max_con_iva"),
    # El umbral VIGENTE de su tipo de reforma (no el aplicado: P2 del plan
    # de la ficha, deuda técnica).
    ("umbral_vigente", "u.umbral"),
)


# ======================================================================
# Funciones puras (sin base de datos): se prueban directamente
# ======================================================================


def deducir_sin_iva(con_iva: Decimal | None, iva_pct: Decimal) -> Decimal | None:
    """
    Importe SIN IVA a partir del importe CON IVA guardado y del IVA
    aplicado en ese cálculo (plan, 2.1, opción a):

        sin_iva = redondear(con_iva / (1 + iva_pct / 100))

    Es exacto: con_iva salió de redondear(sin_iva × k) con k = 1 + iva/100,
    así que su error de redondeo es como mucho 0,005; al dividir por k
    (>= 1) queda en menos de 0,005, y volver a redondear a céntimos
    devuelve el sin_iva original. Comprobado además con los 12
    presupuestos reales el 2026-10-03 (12/12).

    Si con_iva es None, devuelve None: nunca se inventa una cifra (D4).
    """
    # Sin importe con IVA no hay nada de lo que deducir.
    if con_iva is None:
        return None
    # Toda la cuenta entre Decimal: CIEN es Decimal("100"), así que no se
    # cuela ningún float.
    return redondear(con_iva / (1 + iva_pct / CIEN))


def a_madrid(instante: datetime | None) -> datetime | None:
    """
    Pasa un instante (Postgres lo entrega en UTC) a hora de Madrid, con su
    desfase: +02:00 en verano y +01:00 en invierno. astimezone no cambia el
    instante, solo cómo se escribe. None si no hay fecha.
    """
    return instante.astimezone(ZONA_MADRID) if instante is not None else None


def columnas_caso_sql() -> str:
    """
    Las expresiones de COLUMNAS_CASO separadas por comas, para el SELECT
    (sin la palabra SELECT). Se calcula en el momento de llamarla, como
    where_condicion de condicion_seguimiento.
    """
    # ", ".join pone una coma y un espacio entre cada dos expresiones.
    return ", ".join(expresion for _, expresion in COLUMNAS_CASO)


def caso_desde_fila(valores) -> dict:
    """
    Empareja los valores de las columnas de columnas_caso_sql(), en el
    mismo orden, con sus nombres: {"estado": ..., "tipo_reforma": ...}.

    strict=True: si llegan más o menos valores que columnas, zip lanza
    ValueError en vez de desplazar los nombres en silencio.
    """
    # dict(zip(...)): cada nombre con su valor, en orden.
    return dict(zip((nombre for nombre, _ in COLUMNAS_CASO), valores, strict=True))


def contacto_desde(caso: dict) -> ContactoAviso:
    """
    Contacto (P1 de gate-avisos): el de la solicitud si el lead tiene la
    clave; si no, los tres a None con origen no_disponible. Nunca la ficha
    de clientes.
    """
    # Con la clave 'contacto' en la solicitud: sus tres valores.
    if caso["tiene_contacto"]:
        return ContactoAviso(
            nombre=caso["contacto_nombre"],
            email=caso["contacto_email"],
            telefono=caso["contacto_telefono"],
            origen=OrigenContacto.SOLICITUD,
        )
    # Sin la clave: nada inventado.
    return ContactoAviso(nombre=None, email=None, telefono=None, origen=OrigenContacto.NO_DISPONIBLE)


def reforma_desde(caso: dict) -> ReformaAviso:
    """La reforma pedida: tipo, m2, nivel de acabados y cambios estructurales."""
    # Los cuatro valores tal como llegaron de Postgres.
    return ReformaAviso(
        tipo_reforma=caso["tipo_reforma"],
        m2=caso["m2"],
        nivel_acabados=caso["nivel_acabados"],
        incluye_cambios_estructurales=caso["cambios_estructurales"],
    )


def fotos_desde(caso: dict) -> list:
    """
    Las rutas de las fotos: psycopg2 convierte el JSONB en una lista de
    Python; si la columna fuera NULL, lista vacía (plan de gate-avisos, 1.5).
    """
    # Lista vacía cuando no hay fotos guardadas.
    return caso["fotos_urls"] if caso["fotos_urls"] is not None else []


def campos_presupuesto(caso: dict) -> dict:
    """
    Los 8 campos de PresupuestoAviso, con las fechas en hora de Madrid y
    los importes sin IVA DEDUCIDOS con el IVA de ESTE presupuesto (plan de
    gate-avisos, 2.1).

    Devuelve un dict y no el objeto, para que la ficha añada sus campos a
    un modelo heredado sin copiar nada: PresupuestoAviso(**campos).
    """
    # Un diccionario: nombre del campo -> valor.
    return {
        "fecha_presupuesto": a_madrid(caso["fecha_presupuesto"]),
        "motivo_gate": caso["motivo_gate"],
        "iva_pct_aplicado": caso["iva_pct"],
        "importe_min_con_iva": caso["importe_min_con_iva"],
        "importe_max_con_iva": caso["importe_max_con_iva"],
        # Deducidos, nunca leídos de una columna (no existe).
        "importe_min_sin_iva": deducir_sin_iva(caso["importe_min_con_iva"], caso["iva_pct"]),
        "importe_max_sin_iva": deducir_sin_iva(caso["importe_max_con_iva"], caso["iva_pct"]),
        "umbral_gate_vigente": caso["umbral_vigente"],
    }
