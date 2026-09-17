"""
Lógica de negocio para la gestión de leads.

Esta es la ÚNICA capa con lógica de negocio real del proyecto. Por regla
de disciplina arquitectónica, este archivo NO importa fastapi ni fastmcp:
si lo hiciera, dejaría de ser lógica de negocio pura y pasaría a ser un
adaptador disfrazado. Los adaptadores son app/api/ (puerta REST) y
app/mcp_server/server.py (puerta MCP), y los dos llaman a las funciones
de aquí.

Sí importa Pydantic a través de app/schemas/, porque los esquemas son el
contrato de datos del proyecto, no un framework de entrada.
"""

# Json es un adaptador de psycopg2: envuelve un diccionario o una lista
# de Python y le dice a psycopg2 "esto va a una columna JSONB, conviértelo
# a JSON tú". Sin él habría que llamar a json.dumps() a mano y psycopg2
# guardaría el resultado como TEXTO, no como JSONB.
from psycopg2.extras import Json

from app.db.connection import get_transactional_connection
from app.schemas.leads import LeadCreate, LeadCreateResponse

# Canal fijo de N0: el único disparador implementado es el formulario web
# a través del webhook de n8n. Se escribe como constante y no como texto
# suelto dentro del SQL para que, el día que exista un segundo canal
# (chat, WhatsApp), el sitio donde cambiarlo sea evidente.
CANAL_FORMULARIO_WEB = "formulario_web"


def create_lead(data: LeadCreate) -> LeadCreateResponse:
    """
    Da de alta un lead completo: cliente, lead y oportunidad.

    Las tres filas se escriben dentro de UNA SOLA transacción. O se
    crean las tres, o no se crea ninguna: no puede quedar un cliente sin
    lead, ni un lead sin oportunidad. Eso lo garantiza
    get_transactional_connection(), que confirma al terminar bien y
    deshace si salta cualquier excepción.

    El orden de los tres INSERT lo impone la base de datos:
    leads.cliente_id apunta a clientes.id, y oportunidades.lead_id apunta
    a leads.id. Insertar en otro orden haría fallar la clave foránea.

    Parámetros:
        data: el cuerpo ya validado de POST /leads. Cuando llega aquí,
              Pydantic ya garantizó que el email tiene forma válida, que
              m2 es mayor que cero y que tipo_reforma y nivel_acabados
              son valores existentes en tarifas_base. Esta función NO
              vuelve a comprobar nada de eso.

    Devuelve:
        LeadCreateResponse con los tres identificadores creados y el
        estado real de la oportunidad.
    """
    with get_transactional_connection() as conn:
        # Un cursor es el objeto con el que se envían consultas y se leen
        # resultados. Se crea uno solo y se reutiliza para los tres
        # INSERT: los tres viajan por la misma conexión y, por tanto,
        # pertenecen a la misma transacción.
        cursor = conn.cursor()

        # ------------------------------------------------------------
        # 2a. CLIENTE: se crea, o se reutiliza si el email ya existe.
        # ------------------------------------------------------------
        # clientes.email tiene una restricción UNIQUE. Un INSERT normal
        # fallaría si el cliente ya existe, y ese NO es un caso de error:
        # que alguien pida una segunda reforma es negocio normal.
        #
        # ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email es una
        # actualización deliberadamente vacía: le asigna al email el
        # valor que ya tenía. Se hace así, y no con DO NOTHING, por un
        # motivo concreto: con DO NOTHING, la cláusula RETURNING no
        # devuelve NINGUNA fila cuando hay conflicto, y necesitamos el id
        # en los dos casos (cliente nuevo o cliente reutilizado) sin
        # tener que hacer una segunda consulta.
        #
        # EXCLUDED es el nombre que Postgres le da a la fila que se
        # intentaba insertar y que provocó el conflicto.
        #
        # DECISIÓN DE NEGOCIO (D2): NO se actualizan nombre ni telefono
        # del cliente existente. Los datos de contacto de ESTA llamada
        # quedan reflejados en leads.datos_estructurados, pero nunca
        # sobrescriben la ficha del cliente: un "Gabi" tecleado con
        # prisa no debe pisar un "Gabriela Gómez" ya correcto. Es
        # deliberado, no un descuido.
        cursor.execute(
            """
            INSERT INTO clientes (nombre, email, telefono, created_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING id;
            """,
            (data.nombre, data.email, data.telefono),
        )
        # fetchone() devuelve la fila como una tupla, por ejemplo (7,).
        # El [0] saca el primer (y único) valor de esa tupla.
        cliente_id = cursor.fetchone()[0]

        # ------------------------------------------------------------
        # 2b. LEAD: la evidencia cruda de lo que llegó del formulario.
        # ------------------------------------------------------------
        # fotos_urls: las rutas tal cual, como lista JSON. list(...) crea
        # una lista normal a partir de la de Pydantic; si no había fotos
        # será una lista vacía [], que es un valor JSON perfectamente
        # válido y distinto de NULL ("no mandó fotos" en vez de "no
        # sabemos").
        #
        # datos_estructurados: los cuatro datos del formulario tal como
        # llegaron, sin transformarlos. Es la evidencia inmutable de la
        # petición original.
        #
        # Se usa .value en los dos enums para guardar el texto plano
        # ("bano", "medio") y no el objeto de Python. Aunque TipoReforma
        # hereda de str y probablemente funcionaría igual, escribirlo
        # explícito elimina la duda al leerlo.
        #
        # SÍ, tipo_reforma queda duplicado: aquí dentro del JSON y
        # también en la columna oportunidades.tipo_reforma de más abajo.
        # Es INTENCIONAL y está documentado (decisión D3): el JSON es
        # "lo que llegó" y la columna es "el dato de negocio consultable
        # con SQL normal".
        #
        # mensaje_original se deja en NULL a propósito. NO es un dato que
        # falte: en N0 el disparador es un formulario estructurado, no
        # una conversación, así que no existe texto libre que guardar.
        # La columna queda reservada para un canal futuro (chat,
        # WhatsApp) que N0 no implementa.
        cursor.execute(
            """
            INSERT INTO leads (
                cliente_id, canal, mensaje_original,
                fotos_urls, datos_estructurados, created_at
            )
            VALUES (%s, %s, NULL, %s, %s, now())
            RETURNING id;
            """,
            (
                cliente_id,
                CANAL_FORMULARIO_WEB,
                Json(list(data.fotos)),
                Json(
                    {
                        "tipo_reforma": data.tipo_reforma.value,
                        "nivel_acabados": data.nivel_acabados.value,
                        "m2": data.m2,
                        "incluye_cambios_estructurales": data.incluye_cambios_estructurales,
                    }
                ),
            ),
        )
        lead_id = cursor.fetchone()[0]

        # ------------------------------------------------------------
        # 2c. OPORTUNIDAD: la entidad viva del flujo comercial.
        # ------------------------------------------------------------
        # Se crea aquí, en la misma operación que el lead (decisión D1).
        # Motivo: presupuestos.oportunidad_id y visitas.oportunidad_id
        # son NOT NULL y apuntan a esta tabla, pero ningún otro endpoint
        # del contrato la creaba. Creándola en la entrada, ningún lead
        # queda huérfano del resto de la cadena.
        #
        # Valores y por qué:
        # - tipo_reforma: la columna estructurada que consultará
        #   calculate-estimate. Desde el Paso 1 tiene un CHECK que
        #   rechaza cualquier valor fuera de los cuatro válidos.
        # - prioridad y confianza_ia: NULL. Son campos que rellenará el
        #   agente de IA más adelante, no este endpoint. NULL significa
        #   aquí "todavía no evaluado", que es la verdad.
        # - datos_completos: false EXPLÍCITO, no confiando en el valor
        #   por defecto de la tabla. Significa "todavía no evaluado por
        #   el agente"; será el agente quien lo ponga a true tras
        #   procesar el lead. Escribirlo a mano deja la intención visible
        #   en el código en vez de esconderla en el esquema.
        # - estado: 'nueva' EXPLÍCITO, por el mismo motivo.
        #
        # RETURNING id, estado devuelve también el estado REALMENTE
        # escrito. Así el status de la respuesta no es una etiqueta que
        # escribimos aparte en Python y que podría desincronizarse del
        # dato: es el valor que tiene la fila en la base de datos.
        cursor.execute(
            """
            INSERT INTO oportunidades (
                lead_id, tipo_reforma, prioridad, confianza_ia,
                datos_completos, estado, created_at, updated_at
            )
            VALUES (%s, %s, NULL, NULL, false, 'nueva', now(), now())
            RETURNING id, estado;
            """,
            (lead_id, data.tipo_reforma.value),
        )
        # Aquí la tupla tiene dos valores, así que se desempaquetan los
        # dos a la vez en dos variables.
        oportunidad_id, estado = cursor.fetchone()

        # Cerrar el cursor libera sus recursos. No confirma nada: el
        # commit lo hace get_transactional_connection() al salir del
        # "with" sin excepciones.
        cursor.close()

    # Al llegar a esta línea ya se ha salido del "with", así que el
    # commit se ha ejecutado y las tres filas están guardadas de verdad.
    return LeadCreateResponse(
        lead_id=lead_id,
        cliente_id=cliente_id,
        oportunidad_id=oportunidad_id,
        status=estado,
    )
