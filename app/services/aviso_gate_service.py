"""
Lógica de negocio de GET /gate-avisos/{oportunidad_id}: los datos de un
caso con Gate para el aviso a administración (WF2) y para el formulario de
POST /gate-decisions (plan: docs/Plan_Endpoint_Aviso_Gate.txt).

SOLO LECTURA (pre-decisión 8): usa get_db_connection(), que termina con
rollback, y no escribe nada en ninguna tabla, tampoco en logs.

Dos consultas (P5): la primera es la "puerta" y no lee ningún dato
personal; la segunda solo se ejecuta si la oportunidad tiene Gate. Así, en
un caso sin Gate, el servicio ni siquiera llega a leer el nombre, el email
o el teléfono (minimización, RGPD).

Como todo services/, este archivo NO importa fastapi ni fastmcp. Cuando no
puede devolver el aviso, lanza una excepción con un "motivo" (código
estable) y un "mensaje"; el adaptador app/api/aviso_gate.py decide el
código HTTP de cada una.
"""

# datetime: para anotar el tipo de las fechas que se convierten a Madrid.
from datetime import datetime

# Decimal: los importes y el IVA llegan de Postgres como Decimal exacto, y
# las cuentas se hacen con Decimal, nunca con float.
from decimal import Decimal

# La conexión de SOLO LECTURA del pool: al salir del "with" hace rollback y
# devuelve la conexión, así que es imposible que este servicio confirme
# una escritura.
from app.db.connection import get_db_connection

# El esquema de la respuesta y sus piezas (app/schemas/aviso_gate.py).
from app.schemas.aviso_gate import (
    AvisoGateResponse,
    ContactoAviso,
    DecisionAviso,
    OrigenContacto,
    PresupuestoAviso,
    ReformaAviso,
)

# El MISMO redondeo que usa el cálculo del presupuesto (céntimos,
# ROUND_HALF_UP) y la constante Decimal("100"). Importarlos, y no copiarlos,
# garantiza que la deducción del sin IVA redondea exactamente igual que el
# cálculo (plan, 2.1).
from app.services.estimate_service import CIEN, redondear

# ZONA_MADRID: la zona horaria Europe/Madrid. RechazoNegocio: la base común
# de los rechazos de negocio de todos los endpoints (guarda motivo y
# mensaje).
from app.services.reglas_visita import ZONA_MADRID, RechazoNegocio


# ======================================================================
# Excepciones: una clase por FAMILIA de rechazo
# ======================================================================


class AvisoGateRechazado(RechazoNegocio):
    """Base de los rechazos propios de GET /gate-avisos (motivo y mensaje los guarda RechazoNegocio)."""


class OportunidadNoEncontrada(AvisoGateRechazado):
    """No existe ninguna oportunidad con ese id (-> 404)."""


class EstadoNoPermiteAviso(AvisoGateRechazado):
    """La oportunidad no tiene un caso de Gate que avisar (-> 409). Motivos: sin_presupuesto y sin_gate."""


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


# ======================================================================
# La operación completa
# ======================================================================


def obtener_aviso(oportunidad_id: int) -> AvisoGateResponse:
    """
    Devuelve los datos del caso con Gate de esa oportunidad.

    Orden de las comprobaciones (plan, 1.7):
      404 oportunidad_no_encontrada
      409 sin_presupuesto
      409 sin_gate
      200 con el aviso
    No depende del estado: un caso ya decidido sigue devolviendo su aviso,
    con su decisión.
    """
    # get_db_connection(): conexión de solo lectura. Todo lo de dentro del
    # "with" ocurre en una transacción que se deshace al salir.
    with get_db_connection() as conn:
        # El cursor envía las consultas y lee sus resultados.
        cursor = conn.cursor()

        # --------------------------------------------------------------
        # 1. La puerta: ¿existe, tiene presupuesto y tiene Gate?
        # --------------------------------------------------------------
        # SQL: el id de la oportunidad, y el id y requiere_aprobacion de su
        # presupuesto. LEFT JOIN ("une si existe"): si no hay presupuesto,
        # la fila sale igual con p.id NULL; con un JOIN normal desaparecería
        # y parecería que la oportunidad no existe. Esta consulta NO lee
        # ningún dato personal.
        cursor.execute(
            """
            SELECT o.id, p.id, p.requiere_aprobacion
            FROM oportunidades o
            LEFT JOIN presupuestos p ON p.oportunidad_id = o.id
            WHERE o.id = %s;
            """,
            (oportunidad_id,),
        )
        # fetchone(): la única fila, o None si no hay ninguna.
        puerta = cursor.fetchone()
        # Sin fila: la oportunidad no existe (404).
        if puerta is None:
            raise OportunidadNoEncontrada(
                "oportunidad_no_encontrada",
                f"No existe ninguna oportunidad con id {oportunidad_id}.",
            )
        # Desempaquetado: una variable por columna; el "_" es el id de la
        # oportunidad, que ya se conoce.
        _, presupuesto_id, requiere_aprobacion = puerta
        # Sin presupuesto no hay Gate posible (409).
        if presupuesto_id is None:
            raise EstadoNoPermiteAviso(
                "sin_presupuesto",
                "La oportunidad todavía no tiene presupuesto calculado, así que no hay ningún Gate que avisar.",
            )
        # Presupuesto sin Gate: nunca se devuelven sus datos (pre-decisión 4).
        if not requiere_aprobacion:
            raise EstadoNoPermiteAviso(
                "sin_gate",
                "El presupuesto de esta oportunidad no requiere aprobación (no tiene Gate): no hay nada que avisar.",
            )

        # --------------------------------------------------------------
        # 2. El caso: solo se llega aquí si hay Gate
        # --------------------------------------------------------------
        # SQL, por partes:
        #   - "->" saca un objeto de dentro del JSONB (el de 'contacto') y
        #     "->>" saca un valor como TEXTO.
        #   - "datos_estructurados ? 'contacto'": el operador ? de JSONB
        #     pregunta si el objeto TIENE esa clave (true/false). Así se
        #     distingue "sin clave" (origen no_disponible, P1) de una clave
        #     presente.
        #   - "::numeric" y "::boolean" convierten ese texto DENTRO de
        #     Postgres: m2 llega como Decimal exacto, sin pasar por float.
        #   - JOIN normal con leads y presupuestos: siempre existen (claves
        #     foráneas NOT NULL, y la puerta ya comprobó el presupuesto).
        #   - Sin JOIN con clientes: la ficha no se lee nunca (P1).
        #   - LEFT JOIN con umbrales_gate, decisiones_gate y visitas: pueden
        #     no existir (sin decisión, o descarte sin visita); entonces sus
        #     columnas llegan NULL en vez de perderse la fila.
        #   - Como mucho UNA fila: presupuestos y decisiones_gate tienen
        #     UNIQUE (oportunidad_id).
        #   - NO lee el informe de la decisión ni lead_token (D2, D3).
        cursor.execute(
            """
            SELECT o.estado,
                   o.tipo_reforma,
                   l.created_at,
                   l.datos_estructurados ? 'contacto',
                   l.datos_estructurados -> 'contacto' ->> 'nombre',
                   l.datos_estructurados -> 'contacto' ->> 'email',
                   l.datos_estructurados -> 'contacto' ->> 'telefono',
                   (l.datos_estructurados ->> 'm2')::numeric,
                   l.datos_estructurados ->> 'nivel_acabados',
                   (l.datos_estructurados ->> 'incluye_cambios_estructurales')::boolean,
                   l.fotos_urls,
                   p.created_at,
                   p.motivo_gate,
                   p.iva_pct_aplicado,
                   p.importe_min_con_iva,
                   p.importe_max_con_iva,
                   u.umbral,
                   d.decision,
                   d.created_at,
                   d.motivo,
                   v.fecha_propuesta
            FROM oportunidades o
            JOIN leads l                ON l.id = o.lead_id
            JOIN presupuestos p         ON p.oportunidad_id = o.id
            LEFT JOIN umbrales_gate u   ON u.tipo_reforma = o.tipo_reforma
            LEFT JOIN decisiones_gate d ON d.oportunidad_id = o.id
            LEFT JOIN visitas v         ON v.id = d.visita_id
            WHERE o.id = %s;
            """,
            (oportunidad_id,),
        )
        # Desempaquetado: una variable por columna del SELECT, en el mismo
        # orden. El paréntesis permite repartirlo en varias líneas.
        (
            estado,
            tipo_reforma,
            fecha_solicitud,
            tiene_contacto,
            contacto_nombre,
            contacto_email,
            contacto_telefono,
            m2,
            nivel_acabados,
            cambios_estructurales,
            fotos_urls,
            fecha_presupuesto,
            motivo_gate,
            iva_pct,
            importe_min_con_iva,
            importe_max_con_iva,
            umbral_vigente,
            decision,
            fecha_decision,
            motivo_descarte,
            fecha_visita,
        ) = cursor.fetchone()
        # Se cierra el cursor; la conexión la devuelve el "with" al pool.
        cursor.close()

    # A partir de aquí ya no se usa la base de datos: solo se construye la
    # respuesta con lo leído.

    # Contacto (P1): el de la solicitud si el lead tiene la clave; si no,
    # los tres a None con origen no_disponible. Nunca la ficha de clientes.
    if tiene_contacto:
        contacto = ContactoAviso(
            nombre=contacto_nombre,
            email=contacto_email,
            telefono=contacto_telefono,
            origen=OrigenContacto.SOLICITUD,
        )
    else:
        contacto = ContactoAviso(
            nombre=None, email=None, telefono=None, origen=OrigenContacto.NO_DISPONIBLE
        )

    # Decisión (D3): None si no hay fila en decisiones_gate; si la hay, sus
    # datos sin el informe, con las fechas en hora de Madrid.
    if decision is None:
        decision_aviso = None
    else:
        decision_aviso = DecisionAviso(
            decision=decision,
            fecha_decision=a_madrid(fecha_decision),
            motivo=motivo_descarte,
            fecha_visita=a_madrid(fecha_visita),
        )

    # La respuesta completa. Pydantic comprueba cada campo con el esquema
    # (y extra="forbid" impide añadir ninguno que no esté declarado).
    return AvisoGateResponse(
        oportunidad_id=oportunidad_id,
        estado_oportunidad=estado,
        fecha_solicitud=a_madrid(fecha_solicitud),
        contacto=contacto,
        reforma=ReformaAviso(
            tipo_reforma=tipo_reforma,
            m2=m2,
            nivel_acabados=nivel_acabados,
            incluye_cambios_estructurales=cambios_estructurales,
        ),
        # psycopg2 convierte el JSONB en una lista de Python; si la columna
        # fuera NULL, lista vacía (plan, 1.5).
        fotos=fotos_urls if fotos_urls is not None else [],
        presupuesto=PresupuestoAviso(
            fecha_presupuesto=a_madrid(fecha_presupuesto),
            motivo_gate=motivo_gate,
            iva_pct_aplicado=iva_pct,
            importe_min_con_iva=importe_min_con_iva,
            importe_max_con_iva=importe_max_con_iva,
            # Deducidos, con el IVA de ESTE presupuesto (plan, 2.1).
            importe_min_sin_iva=deducir_sin_iva(importe_min_con_iva, iva_pct),
            importe_max_sin_iva=deducir_sin_iva(importe_max_con_iva, iva_pct),
            umbral_gate_vigente=umbral_vigente,
        ),
        decision=decision_aviso,
    )
