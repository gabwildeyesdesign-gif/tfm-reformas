"""
Lógica de negocio del cálculo de presupuestos (POST /calculate-estimate
y la tool MCP calculate_estimate).

Como leads_service.py, este archivo NO importa fastapi ni fastmcp. Es la
única copia del cálculo: la puerta REST (app/api/estimates.py) y la
puerta MCP (app/mcp_server/server.py) llaman a las MISMAS funciones de
aquí, así que es imposible que el agente de IA y una prueba manual
obtengan cifras distintas (documento de arquitectura, sección 4.3).

Contrato: Adenda, punto 1.1. Resumen:
  - Entrada: solo oportunidad_id. Los datos de negocio se leen de la base
    de datos, nunca los aporta quien llama.
  - importe_min_sin_iva = precio_m2 × m2 (+ recargo_informe_tecnico si
    hay cambios estructurales)
    importe_max_sin_iva = importe_min_sin_iva × (1 + margen_empresa_pct / 100)
  - importe_*_con_iva  = importe_*_sin_iva × (1 + iva_estandar_pct / 100),
    redondeado a céntimos (D14). Es lo que se devuelve y lo que se guarda.
  - Gate HITL evaluado contra importe_max_SIN_iva, con ">" estricto: el
    umbral mide riesgo comercial, y el IVA no se queda en la empresa.
  - Estado de la oportunidad: 'presupuesto_enviado' sin Gate,
    'pendiente_aprobacion' con Gate (D8).
  - Idempotente: una segunda llamada devuelve el presupuesto que ya
    existe, sin recalcular ni escribir nada.

Organización del archivo:
  1. Constantes.
  2. Excepciones propias (errores de negocio que cada puerta traduce).
  3. Funciones PURAS: calculan sin tocar la base de datos, así que se
     pueden probar sin Supabase.
  4. calculate_estimate(): la función que orquesta lecturas, cálculo y
     escrituras dentro de una transacción.
"""

# ROUND_HALF_UP es el modo de redondeo "de toda la vida": 0,5 se redondea
# hacia arriba (2,345 -> 2,35). Hay que indicarlo explícitamente porque
# el modo por defecto de Decimal es ROUND_HALF_EVEN, el "redondeo del
# banquero", que manda el 0,5 al número PAR más cercano (2,345 -> 2,34).
# Ese modo evita sesgos en estadística, pero en un presupuesto que lee un
# cliente lo esperado es el redondeo escolar.
from decimal import ROUND_HALF_UP, Decimal

# Json: el mismo adaptador de psycopg2 que usa leads_service.py para
# escribir un diccionario de Python en una columna JSONB (aquí,
# logs.detalle).
from psycopg2.extras import Json

from app.db.connection import get_transactional_connection
from app.schemas.common import MotivoGate
from app.schemas.estimates import EstimateResponse

# ======================================================================
# 1. CONSTANTES
# ======================================================================
# Los estados se escriben como constantes, y no como textos sueltos
# repetidos dentro del SQL, para que un error tipográfico ('pendiente_
# aprovacion') falle en un solo sitio visible. Los tres existen en el
# CHECK oportunidades_estado_check (migración paso3).
ESTADO_INICIAL = "nueva"
ESTADO_SIN_GATE = "presupuesto_enviado"
ESTADO_CON_GATE = "pendiente_aprobacion"

# Valor especial de status cuando no se puede calcular. NO es un estado
# de la tabla oportunidades (la oportunidad sigue en 'nueva'): es la
# respuesta que fija la Adenda para el fallback.
STATUS_REQUIERE_REVISION = "requiere_revision"

# Claves de reglas_negocio que usa el cálculo, copiadas de la tabla real.
CLAVE_MARGEN = "margen_empresa_pct"
CLAVE_RECARGO = "recargo_informe_tecnico"

# Tipo de IVA que se aplica al precio mostrado al cliente (D14). Se lee de
# reglas_negocio en CADA cálculo nuevo, exactamente igual que el margen o
# el recargo, y nunca se escribe el 21 a mano en el código: es un
# parámetro de negocio que puede cambiar por una reforma fiscal, y debe
# poder cambiarse sin tocar el código ni reiniciar el servidor.
CLAVE_IVA = "iva_estandar_pct"

# OJO: el umbral del Gate YA NO se lee de reglas_negocio.
#
# Hasta D9 había una sola clave, 'umbral_aprobacion_manual', con un valor
# único (10.000 €) para las cuatro categorías. Pero los precios por m² van
# de 130 € (parcial_acabados/basico) a 1.700 € (bano/alto): más de 13
# veces de diferencia. Con un solo número, una cocina de tamaño
# perfectamente normal (10 m²) activaba el Gate, mientras que subirlo
# estropeaba el objetivo contrario en baño de gama alta. Un único valor no
# puede cumplir dos objetivos de negocio distintos a la vez.
#
# Desde D9, cada categoría tiene su propio umbral en la tabla
# umbrales_gate, cuya clave primaria ES tipo_reforma. La fila antigua de
# reglas_negocio sigue existiendo, marcada como OBSOLETA en su
# descripción, pero NADIE la lee: editarla no tiene ningún efecto.

# Decimal("0.01") indica "redondear a dos decimales" (céntimos), igual
# que las columnas NUMERIC(10,2) de presupuestos. Se construye desde el
# TEXTO "0.01" y no desde el float 0.01, que ya sería inexacto.
DOS_DECIMALES = Decimal("0.01")
CIEN = Decimal("100")

# Valores para la tabla logs. 'oportunidad' es un entity_type NUEVO: se
# ha añadido al registro de valores de la sección 5 de
# docs/TFM_Decisiones_Modelo_Datos_Leads.txt, como ese documento exige,
# para que no aparezcan variantes como 'Oportunidad' u 'opportunity'.
LOG_ENTITY_TYPE_OPORTUNIDAD = "oportunidad"
ACCION_CALCULADO = "presupuesto_calculado"
ACCION_REQUIERE_REVISION = "presupuesto_requiere_revision"


# ======================================================================
# 2. EXCEPCIONES PROPIAS
# ======================================================================
# Una excepción propia es una clase que hereda de Exception (la clase
# base de los errores "normales" de Python). No necesita cuerpo: basta
# con que exista con un nombre que diga QUÉ ha pasado.
#
# Por qué no lanzar directamente HTTPException(404): services/ no puede
# importar fastapi (regla de arquitectura), y además la puerta MCP no
# habla HTTP, sino que devuelve un ToolError. Con excepciones propias,
# el servicio dice qué ha pasado en términos de negocio y cada puerta lo
# traduce a su idioma: REST a 404/409 y MCP a ToolError.


class OportunidadNoEncontrada(Exception):
    """No existe ninguna oportunidad con el id recibido."""


class EstadoNoPermiteCalculo(Exception):
    """
    La oportunidad no tiene presupuesto y no está en estado 'nueva', así
    que calcularle uno ahora rompería el orden del proceso comercial.

    Ejemplo real: una oportunidad marcada a mano como 'perdida' desde el
    panel de Supabase. Sin esta protección, calcularle un presupuesto la
    devolvería a 'presupuesto_enviado' sin que nadie lo notara (Notas
    técnicas N0, nota B; decisión P5 del plan).
    """


# ======================================================================
# 3. FUNCIONES PURAS (sin base de datos)
# ======================================================================
# "Pura" significa que el resultado depende SOLO de los parámetros: no
# lee la base de datos, ni la hora, ni ninguna variable global. Dos
# ventajas concretas aquí: se pueden verificar con cifras calculadas a
# mano sin Supabase (scripts/check_estimate_service.py, primera parte), y
# serán las primeras candidatas de la suite de pytest prevista.


def redondear(importe: Decimal) -> Decimal:
    """
    Redondea un importe a céntimos con redondeo escolar.

    quantize(DOS_DECIMALES, ...) significa "deja este número con el mismo
    número de decimales que 0.01". Así, 6228.9175 pasa a 6228.92.

    Por qué redondear en Python y no dejar que lo haga Postgres al
    guardar en NUMERIC(10,2): si Postgres redondeara, el importe devuelto
    al cliente (sin redondear) y el guardado en la tabla (redondeado)
    podrían no coincidir. Redondeando aquí, el valor que se guarda, el
    que se devuelve y el que se compara con el umbral son exactamente el
    mismo.
    """
    return importe.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def calcular_importes(
    precio_m2: Decimal,
    m2: Decimal,
    incluye_cambios_estructurales: bool,
    recargo_informe_tecnico: Decimal,
    margen_empresa_pct: Decimal,
) -> tuple[Decimal, Decimal]:
    """
    Aplica la fórmula de la Adenda 1.1(b) y devuelve el par
    (importe_min_sin_iva, importe_max_sin_iva), los dos ya redondeados a
    céntimos. SIN IVA: el impuesto lo añade después aplicar_iva (D14).
    Esta función calcula lo que cobra la empresa por la obra; el IVA es
    un recargo fiscal posterior que no depende de tarifas ni de margen.

    Por qué el margen AMPLÍA el rango y no se suma como beneficio: los
    precio_m2 de tarifas_base ya son precios de VENTA de mercado, con el
    margen de la empresa dentro. Sumar otro margen encima lo contaría dos
    veces y sacaría la oferta del mercado. margen_empresa_pct define
    hasta dónde puede subir el precio si aparecen complicaciones: un
    techo de negociación, no un recargo oculto.

    Ejemplo: baño medio (1150.00 €/m²), 6 m², con cambios estructurales
    (recargo 600.00), margen 15.00:
        importe_min_sin_iva = 1150.00 × 6 + 600.00 = 7500.00
        importe_max_sin_iva = 7500.00 × (1 + 15.00 / 100) = 8625.00
    (con el 21 % de IVA, al cliente se le mostraría 9075.00 - 10436.25)
    """
    # "Expresión condicional" de Python: VALOR_SI_SÍ if CONDICIÓN else
    # VALOR_SI_NO. Es un if/else en una sola línea que produce un valor.
    # Se usa Decimal("0") y no el entero 0 para que la suma sea, de
    # principio a fin, entre Decimal.
    recargo = recargo_informe_tecnico if incluye_cambios_estructurales else Decimal("0")

    # El recargo va DENTRO de importe_min: es un coste real y seguro
    # (el informe técnico hay que pagarlo sí o sí), no una estimación.
    importe_min = redondear(precio_m2 * m2 + recargo)

    # Se multiplica importe_min YA redondeado. Así, quien recalcule
    # importe_max a partir de la cifra guardada en la tabla obtiene
    # exactamente la misma cifra, sin diferencias de céntimos.
    importe_max = redondear(importe_min * (1 + margen_empresa_pct / CIEN))

    # Una tupla (min, max): quien llama la separa en dos variables en
    # una sola línea, como hace leads_service.py con "oportunidad_id,
    # estado = cursor.fetchone()".
    return importe_min, importe_max


def aplicar_iva(importe_sin_iva: Decimal, iva_pct: Decimal) -> Decimal:
    """
    Convierte un importe SIN IVA en el precio final CON IVA (D14).

    La cuenta es la de toda la vida: al precio base se le suma el
    porcentaje de impuesto. Multiplicar por (1 + iva/100) es la forma
    corta de hacer esas dos cosas a la vez:

        precio_final = base + base × (iva/100)
                     = base × (1 + iva/100)

    Con el 21 %:  1 + 21/100 = 1,21.  Un importe de 6.900,00 € queda en
    6.900,00 × 1,21 = 8.349,00 €.

    Por qué se divide entre 100 y no se escribe 0,21 directamente: el
    valor llega de reglas_negocio como un PORCENTAJE (21), que es como lo
    entiende una persona que edite la tabla. Convertirlo a proporción es
    trabajo del código, no de quien administra los datos.

    Por qué CIEN es un Decimal y no el entero 100: para que toda la
    operación ocurra entre Decimal y no se cuele un float por el camino,
    que es lo que introduciría errores de céntimos.

    El resultado se redondea a dos decimales igual que los demás
    importes, porque es el valor que se guarda en una columna
    NUMERIC(10,2) y el que se le muestra al cliente: el número mostrado,
    el guardado y el comparable tienen que ser el mismo.
    """
    return redondear(importe_sin_iva * (1 + iva_pct / CIEN))


def evaluar_gate(
    importe_max: Decimal,
    incluye_cambios_estructurales: bool,
    umbral: Decimal,
) -> MotivoGate | None:
    """
    Decide si el presupuesto necesita aprobación humana y por qué
    (Adenda 1.1c). Devuelve el motivo, o None si no hace falta.

        estructural  importe_max > umbral   resultado
        -----------  ---------------------  -----------------------
        sí           sí                     AMBOS
        sí           no                     CAMBIOS_ESTRUCTURALES
        no           sí                     IMPORTE_SUPERIOR_UMBRAL
        no           no                     None (sin Gate)

    Se compara importe_max y no importe_min porque el Gate existe para
    frenar el riesgo antes de comprometerse, y el riesgo es el peor caso
    (el techo del rango). Con ">" estricto, un importe_max de 10000.00
    exactos NO activa el Gate con un umbral de 10000.00.

    CAMBIO DE D9, y por qué esta función NO cambia
    El umbral ya no es un valor global: depende de tipo_reforma (13.000 €
    en baño y cocina, 10.000 € en integral_vivienda y, provisionalmente,
    en parcial_acabados). Aun así, aquí no hubo que tocar nada, y eso es
    una consecuencia buscada de que la función sea PURA: recibe el umbral
    como parámetro en vez de ir a buscarlo, así que le da igual de dónde
    salga. Lo único que cambió es QUIÉN se lo pasa: calculate_estimate,
    que antes leía una clave de reglas_negocio y ahora busca la fila de
    umbrales_gate que corresponde a la categoría.

    Si esta función hubiera leído la base de datos por su cuenta, D9
    habría obligado a reescribirla y a rehacer sus pruebas. Es el
    argumento práctico a favor de separar el cálculo del acceso a datos.
    """
    supera_umbral = importe_max > umbral

    if incluye_cambios_estructurales and supera_umbral:
        return MotivoGate.AMBOS
    if incluye_cambios_estructurales:
        return MotivoGate.CAMBIOS_ESTRUCTURALES
    if supera_umbral:
        return MotivoGate.IMPORTE_SUPERIOR_UMBRAL
    return None


def ocultar_importes_para_agente(respuesta: EstimateResponse) -> EstimateResponse:
    """
    Versión de la respuesta que se le entrega al AGENTE DE IA por la
    puerta MCP. Si el presupuesto tiene cambios estructurales (solos o en
    'ambos'), los importes se sustituyen por None. En cualquier otro caso
    la respuesta sale intacta.

    Por qué: un presupuesto con cambios estructurales depende de un
    informe técnico que todavía no existe. La cifra no debe llegar al
    cliente hasta que una persona la revise. Una instrucción en el prompt
    ("no digas el importe") no es un control fiable, porque un LLM puede
    saltársela. La única garantía es que el agente nunca reciba la cifra.

    Por qué None explícito y no quitar los campos: el esquema de salida
    es el mismo para las dos puertas, y un campo presente con valor null
    le dice al agente "este dato existe y no te lo doy". motivo_gate le
    explica el motivo.

    Por qué vive aquí, en services/, y no dentro de la tool MCP: es una
    regla de seguridad del negocio. Aquí queda a la vista, se puede
    probar sin servidor, y la puerta REST (uso de personas de confianza)
    simplemente no la llama.

    model_copy(update=...) es un método de Pydantic que crea una COPIA
    del objeto con los campos indicados cambiados. El original no se
    modifica.
    """
    if respuesta.motivo_gate in (MotivoGate.CAMBIOS_ESTRUCTURALES, MotivoGate.AMBOS):
        return respuesta.model_copy(
            update={"importe_min_con_iva": None, "importe_max_con_iva": None}
        )
    return respuesta


def _texto(valor):
    """
    Convierte un Decimal a texto para guardarlo en logs.detalle (JSONB),
    y deja cualquier otro valor tal cual.

    Hace falta porque Json() usa el conversor JSON estándar de Python, que
    no sabe convertir Decimal: lanzaría "TypeError: Object of type Decimal
    is not JSON serializable". Se guarda como texto ("8625.00") y no como
    float para no perder la exactitud justo en el registro de auditoría.

    El guion bajo inicial (_texto) es una convención de Python: significa
    "función interna de este archivo, no la uses desde fuera". Python no
    lo impide, es un aviso para quien lee.
    """
    return str(valor) if isinstance(valor, Decimal) else valor


# ======================================================================
# 4. FUNCIÓN PRINCIPAL
# ======================================================================


def calculate_estimate(oportunidad_id: int) -> EstimateResponse:
    """
    Calcula (o recupera) el presupuesto de una oportunidad.

    Todo ocurre dentro de UNA transacción, con get_transactional_connection(),
    el mismo context manager de escritura que create_lead():
      - Si todo va bien: commit() al salir del with.
      - Si salta cualquier excepción: rollback(). Por ejemplo, si el
        UPDATE de estado falla después del INSERT del presupuesto, el
        presupuesto tampoco queda guardado: no puede existir un
        presupuesto con la oportunidad todavía en 'nueva'.
    No se usa get_db_connection() (la de solo lectura) porque esta función
    ESCRIBE. Aquella hace rollback() al salir, así que el presupuesto se
    perdería sin ningún error visible (el bug que motivó D6).

    Orden de los pasos, y por qué ese orden:
      1. Leer la oportunidad, sus datos y su presupuesto si ya tiene uno
         (una sola consulta).
      2. Si ya tiene presupuesto, devolverlo tal cual. Va ANTES que
         cualquier comprobación de tarifas o de estado, para que un
         reintento devuelva siempre lo mismo aunque entretanto se haya
         borrado una tarifa o la oportunidad haya avanzado.
      3. Proteger el orden de estados: solo se calcula desde 'nueva'.
      4. Leer la tarifa y las reglas (una sola consulta). Si falta algo,
         fallback: se registra en logs y se devuelve 'requiere_revision'.
      5. Calcular (funciones puras).
      6. Escribir: presupuesto, estado y log de auditoría.

    Lanza:
        OportunidadNoEncontrada: no existe la oportunidad.
        EstadoNoPermiteCalculo: sin presupuesto y fuera de 'nueva'.
    """
    # "with ... as conn" pide una conexión al pool y la guarda en conn.
    # Al salir del bloque (por el final, por un return o por una
    # excepción) el context manager hace commit o rollback y devuelve la
    # conexión al pool. Sin with habría que escribir a mano un
    # try/except/finally en cada salida, y olvidar uno solo dejaría una
    # conexión prestada para siempre: el pool (máximo 10) se agotaría.
    with get_transactional_connection() as conn:
        cursor = conn.cursor()

        # --------------------------------------------------------------
        # PASO 1: la oportunidad, sus datos y su presupuesto, de una vez
        # --------------------------------------------------------------
        # SQL nuevo en el proyecto:
        #
        # ->> extrae una clave de un JSONB como TEXTO. Por ejemplo,
        # '{"m2": 8.7}'::jsonb ->> 'm2' da '8.7'. Después, ::numeric
        # convierte ese texto en NUMERIC dentro de Postgres, y psycopg2
        # lo entrega a Python como Decimal('8.7'), exacto.
        #
        # Por qué no leer el JSONB entero y convertir en Python: psycopg2
        # convierte el JSONB en un diccionario de Python, y cualquier
        # número con decimales del JSON se convierte en float
        # (comprobado: 8.7 llega como float). Pasar ese float a Decimal
        # arrastra su error binario, porque Decimal(8.7) vale
        # 8.699999999999999289... Con ->> y ::numeric el número va de
        # texto a NUMERIC sin pasar nunca por float. Lo mismo con
        # ::boolean para el indicador de cambios estructurales.
        #
        # Si una clave no existe en el JSON, ->> devuelve NULL (None en
        # Python), no un error. Se trata más abajo como datos
        # incompletos.
        #
        # LEFT JOIN presupuestos: une la oportunidad con su presupuesto
        # SI LO TIENE. Un JOIN normal descartaría la oportunidad si no
        # tiene presupuesto. Con LEFT JOIN la fila sale igualmente y las
        # columnas de presupuestos vienen a NULL. Como hay un UNIQUE
        # sobre presupuestos.oportunidad_id (migración paso3), como mucho
        # puede salir un presupuesto: nunca se multiplican las filas.
        #
        # Así, un reintento, que es el caso más frecuente de llamada
        # repetida, se resuelve con UN solo viaje a Supabase en vez de
        # dos.
        #
        # tipo_reforma sale de la COLUMNA de oportunidades y no del JSON:
        # D3 define el JSON como la evidencia inmutable de lo que llegó y
        # la columna como el dato de trabajo (el que se consulta y, en N1,
        # el que el agente podría corregir). Los otros tres datos solo
        # existen en el JSON.
        cursor.execute(
            """
            SELECT o.estado,
                   o.tipo_reforma,
                   l.datos_estructurados ->> 'nivel_acabados',
                   (l.datos_estructurados ->> 'm2')::numeric,
                   (l.datos_estructurados ->> 'incluye_cambios_estructurales')::boolean,
                   p.id,
                   -- D14: las columnas se llaman ahora *_con_iva. Lo que
                   -- guardan es el precio final que se le enseñó al
                   -- cliente, con el IVA ya aplicado.
                   p.importe_min_con_iva,
                   p.importe_max_con_iva,
                   p.motivo_gate,
                   p.requiere_aprobacion
            FROM oportunidades o
            JOIN leads l ON l.id = o.lead_id
            LEFT JOIN presupuestos p ON p.oportunidad_id = o.id
            WHERE o.id = %s;
            """,
            (oportunidad_id,),
        )
        fila = cursor.fetchone()

        # fetchone() devuelve None si la consulta no encontró ninguna
        # fila: la oportunidad no existe. Se lanza la excepción de
        # negocio. Al salir del with por una excepción, el context
        # manager hace rollback (aquí no había nada que deshacer) y
        # devuelve la conexión.
        if fila is None:
            raise OportunidadNoEncontrada(f"No existe la oportunidad {oportunidad_id}")

        # "Desempaquetado": la tupla de 10 valores se reparte en 10
        # variables, en el mismo orden que el SELECT.
        (
            estado,
            tipo_reforma,
            nivel_acabados,
            m2,
            incluye_cambios_estructurales,
            presupuesto_id,
            importe_min_con_iva,
            importe_max_con_iva,
            motivo_gate,
            requiere_aprobacion,
        ) = fila

        # --------------------------------------------------------------
        # PASO 2: ¿ya tiene presupuesto? Se devuelve tal cual.
        # --------------------------------------------------------------
        # Es la idempotencia: llamar dos veces produce el mismo resultado
        # que llamar una. Sin recalcular (las tarifas podrían haber
        # cambiado y la cifra ya comunicada no debe moverse), sin tocar
        # el estado y con creado=False para que n8n no avise dos veces.
        #
        # El return sale del with de forma normal, así que el context
        # manager hace commit. No se había escrito nada, así que el
        # commit solo cierra la transacción de lectura.
        #
        # ESTA RAMA NO TIENE NINGUNA LÓGICA DE IVA, y es deliberado
        # (D14). Los importes se devuelven tal como están guardados, sin
        # leer siquiera reglas_negocio.iva_estandar_pct. Motivo: si aquí
        # se recalculara el IVA, habría DOS sitios que deciden el precio
        # —el cálculo nuevo y la relectura— y bastaría con que alguien
        # cambiara el tipo de IVA en la tabla para que un presupuesto ya
        # comunicado al cliente devolviera de pronto otra cifra. Con el
        # importe final guardado, una segunda llamada devuelve
        # exactamente el mismo número que la primera, para siempre. El
        # tipo que se aplicó queda archivado en
        # presupuestos.iva_pct_aplicado por si hace falta justificarlo.
        if presupuesto_id is not None:
            return EstimateResponse(
                presupuesto_id=presupuesto_id,
                oportunidad_id=oportunidad_id,
                importe_min_con_iva=importe_min_con_iva,
                importe_max_con_iva=importe_max_con_iva,
                # motivo_gate llega de la base de datos como texto
                # ('ambos') o None; Pydantic lo convierte solo al enum
                # MotivoGate porque el campo está declarado de ese tipo.
                motivo_gate=motivo_gate,
                requiere_aprobacion=requiere_aprobacion,
                status=estado,
                creado=False,
            )

        # --------------------------------------------------------------
        # PASO 3: protección del orden de estados (primera línea)
        # --------------------------------------------------------------
        # Solo una oportunidad 'nueva' puede recibir su primer
        # presupuesto. Esta comprobación da un error claro sin gastar más
        # consultas. La protección definitiva, que también cubre el caso
        # de que otro proceso cambie el estado mientras tanto, es el
        # WHERE estado = 'nueva' del UPDATE del paso 6.
        if estado != ESTADO_INICIAL:
            raise EstadoNoPermiteCalculo(
                f"La oportunidad {oportunidad_id} está en estado '{estado}' "
                f"y no tiene presupuesto; solo se calcula desde '{ESTADO_INICIAL}'"
            )

        # --------------------------------------------------------------
        # PASO 4: tarifa y reglas de negocio, en UNA consulta
        # --------------------------------------------------------------
        # SQL nuevo en el proyecto: cada (SELECT ...) entre paréntesis
        # dentro de la lista de columnas es una "subconsulta escalar".
        # Devuelve un solo valor, o NULL si no encuentra fila. Así se
        # piden cuatro datos de dos tablas distintas en un solo viaje a
        # Supabase, en vez de dos o cuatro. Y una tarifa o regla que falte
        # no rompe la consulta: simplemente llega como None, que es
        # justo la señal que necesita el fallback.
        #
        # Si tipo_reforma o nivel_acabados son None (columna NULL o clave
        # ausente en el JSON), "tipo_reforma = NULL" nunca es verdadero en
        # SQL, así que precio_m2 llega como None. Cae en el mismo
        # fallback que una tarifa inexistente, sin un caso especial.
        #
        # Las tarifas y reglas se leen en CADA llamada, sin guardarlas en
        # memoria: se pueden editar en Supabase sin reiniciar el servidor,
        # y ese es el motivo de que estén en tablas y no en el código.
        #
        # CAMBIO DE D9: el umbral ya no es una cuarta clave de
        # reglas_negocio, sino una búsqueda en umbrales_gate POR
        # tipo_reforma. La técnica es la misma que ya se usaba (una
        # subconsulta escalar más en la misma lista), así que el número
        # de viajes a Supabase no cambia: sigue siendo UNA consulta para
        # todos los parámetros del cálculo. Lo único que cambia es de qué
        # tabla sale el umbral y con qué condición se busca.
        #
        # Se traen DOS columnas de umbrales_gate (el umbral y su marca
        # 'provisional'), y por eso hay dos subconsultas contra esa
        # tabla: una subconsulta escalar devuelve un solo valor por
        # definición. Son dos búsquedas por clave primaria en una tabla
        # de 4 filas, dentro de la misma consulta: el coste es
        # inapreciable. 'provisional' no interviene en el cálculo; se lee
        # para dejar constancia en el log de auditoría de que ese
        # presupuesto se decidió con un umbral que todavía no es
        # definitivo (hoy, parcial_acabados).
        cursor.execute(
            """
            SELECT
                (SELECT precio_m2 FROM tarifas_base
                  WHERE tipo_reforma = %s AND nivel_acabados = %s),
                (SELECT valor FROM reglas_negocio WHERE clave = %s),
                (SELECT valor FROM reglas_negocio WHERE clave = %s),
                -- D14: el tipo de IVA se lee aquí, en la misma consulta
                -- que el resto de parámetros del cálculo, así que no
                -- cuesta ningún viaje extra a la base de datos.
                (SELECT valor FROM reglas_negocio WHERE clave = %s),
                (SELECT umbral FROM umbrales_gate WHERE tipo_reforma = %s),
                (SELECT provisional FROM umbrales_gate WHERE tipo_reforma = %s);
            """,
            (
                tipo_reforma,
                nivel_acabados,
                CLAVE_MARGEN,
                CLAVE_RECARGO,
                CLAVE_IVA,
                tipo_reforma,
                tipo_reforma,
            ),
        )
        precio_m2, margen, recargo, iva_pct, umbral, umbral_provisional = cursor.fetchone()

        # Qué falta, en lenguaje de negocio, para dejarlo en el log.
        # Una lista vacía significa que no falta nada.
        faltan = []
        if precio_m2 is None:
            faltan.append("tarifa")
        if m2 is None or incluye_cambios_estructurales is None:
            faltan.append("datos_lead")
        # zip() recorre dos secuencias a la vez, emparejando sus elementos:
        # (CLAVE_MARGEN, margen), (CLAVE_RECARGO, recargo)... Así se añade
        # a la lista el NOMBRE de cada parámetro cuyo valor es None.
        #
        # "umbral_gate" entra en esta misma lista, y no en un caso aparte,
        # por una razón de diseño: un umbral que falta es exactamente el
        # mismo tipo de problema que una tarifa o una regla que faltan
        # ("el sistema no tiene el dato que necesita para calcular"), así
        # que merece la misma respuesta ya probada: registrar la
        # incidencia y devolver 'requiere_revision'.
        #
        # ¿PUEDE FALTAR DE VERDAD? Razonado, no supuesto:
        #   - Si tipo_reforma es NULL, la subconsulta no encuentra fila y
        #     umbral llega None. Pero en ese caso precio_m2 también es
        #     None, así que la llamada ya iba a 'requiere_revision' por
        #     "tarifa". Nada cambia.
        #   - Si tipo_reforma tiene uno de los 4 valores del CHECK, hoy
        #     SIEMPRE hay umbral: la verificación U6 de la migración
        #     comprueba, contra la base de datos real, que ninguna
        #     categoría con tarifa se queda sin umbral.
        #   - Pero "hoy" no es "siempre": los umbrales son datos
        #     editables sin redeploy, y un DELETE en el panel de Supabase
        #     dejaría una categoría huérfana. La base de datos no puede
        #     impedirlo (una clave foránea obliga a que lo que hay
        #     APUNTE a algo existente, no a que exista una fila por cada
        #     categoría posible).
        #   - La alternativa —usar un valor por defecto, por ejemplo los
        #     10.000 € antiguos— se descarta por el mismo principio que
        #     rige el fallback entero: no inventar. Un umbral inventado
        #     no da error, da presupuestos mal clasificados en silencio,
        #     que es peor. Mejor un caso a revisión humana, con la causa
        #     escrita en logs.
        # El IVA entra en esta misma lista: si la regla no estuviera, no
        # se puede calcular el precio que se le muestra al cliente, y
        # NUNCA se usa un 21 de reserva. Inventar un tipo impositivo
        # daría un precio equivocado sin avisar a nadie; mejor un caso a
        # revisión humana con la causa escrita en el log.
        for clave, valor in zip(
            (CLAVE_MARGEN, CLAVE_RECARGO, CLAVE_IVA, "umbral_gate"),
            (margen, recargo, iva_pct, umbral),
        ):
            if valor is None:
                faltan.append(clave)

        if faltan:
            # ----------------------------------------------------------
            # FALLBACK (Adenda, punto 5; decisiones 7 y P6 del plan)
            # ----------------------------------------------------------
            # "No se inventa un precio". Se deja rastro en logs con lo que
            # faltaba, y no se toca ni presupuestos ni el estado. La
            # oportunidad sigue en 'nueva', así que cuando alguien corrija
            # el dato se podrá volver a calcular con normalidad.
            #
            # Se responde con normalidad (un 200 por REST) y no con un
            # error 5xx, a propósito: la Adenda manda a n8n REINTENTAR los
            # 5xx. Reintentar no arreglaría una tarifa que falta, solo
            # haría esperar más al cliente.
            cursor.execute(
                """
                INSERT INTO logs (entity_type, entity_id, accion, detalle)
                VALUES (%s, %s, %s, %s);
                """,
                (
                    LOG_ENTITY_TYPE_OPORTUNIDAD,
                    oportunidad_id,
                    ACCION_REQUIERE_REVISION,
                    Json(
                        {
                            "faltan": faltan,
                            "tipo_reforma": tipo_reforma,
                            "nivel_acabados": nivel_acabados,
                            "m2": _texto(m2),
                        }
                    ),
                ),
            )
            # El return sale del with de forma normal, así que hay commit:
            # la fila de logs SÍ queda guardada. Es la única escritura de
            # este camino.
            return EstimateResponse(
                presupuesto_id=None,
                oportunidad_id=oportunidad_id,
                importe_min_con_iva=None,
                importe_max_con_iva=None,
                motivo_gate=None,
                requiere_aprobacion=False,
                status=STATUS_REQUIERE_REVISION,
                creado=False,
            )

        # --------------------------------------------------------------
        # PASO 5: el cálculo (funciones puras de arriba)
        # --------------------------------------------------------------
        # Primero, el precio SIN IVA: es la base imponible, lo que la
        # empresa cobra por la obra. Es también el número con el que se
        # razona el negocio (tarifas, margen, recargo del informe).
        importe_min_sin_iva, importe_max_sin_iva = calcular_importes(
            precio_m2, m2, incluye_cambios_estructurales, recargo, margen
        )

        # Después, el precio que se le enseña al cliente (D14): el mismo
        # importe con el IVA ya sumado. Se aplica a los DOS extremos del
        # rango, porque el impuesto no depende de dónde caiga el precio
        # dentro de la horquilla.
        importe_min_con_iva = aplicar_iva(importe_min_sin_iva, iva_pct)
        importe_max_con_iva = aplicar_iva(importe_max_sin_iva, iva_pct)

        # EL GATE SE EVALÚA CONTRA EL IMPORTE SIN IVA, a propósito.
        #
        # El umbral de umbrales_gate expresa cuánto riesgo comercial
        # asume la empresa antes de pedir que una persona revise el
        # presupuesto. Ese riesgo es el valor de la obra, no el dinero
        # que se recauda para Hacienda: el IVA no se queda en la empresa,
        # se ingresa. Comparar contra el precio con IVA haría que el Gate
        # saltara un 21 % antes, sin que el trabajo comprometido hubiera
        # crecido ni un euro, y además cambiaría el significado de unos
        # umbrales que se calibraron (D9) con cifras sin IVA.
        #
        # Consecuencia visible, y es la esperada: una cocina típica de
        # 10 m² da 11.500 € sin IVA y 13.915 € con IVA. Con el umbral de
        # cocina en 13.000 €, NO activa el Gate, porque se compara el
        # primer número. Si se comparase el segundo, sí lo activaría.
        #
        # TODO: la Adenda deja abierta la pregunta de si el umbral
        # debería compararse contra el importe CON IVA, que es el que ve
        # el cliente. No se cambia aquí: cambiarlo sin recalibrar los
        # umbrales de D9 movería el comportamiento del Gate en las cuatro
        # categorías a la vez. Queda señalado para decidirlo aparte.
        motivo = evaluar_gate(importe_max_sin_iva, incluye_cambios_estructurales, umbral)
        requiere_aprobacion = motivo is not None
        nuevo_estado = ESTADO_CON_GATE if requiere_aprobacion else ESTADO_SIN_GATE

        # --------------------------------------------------------------
        # PASO 6a: guardar el presupuesto (idempotente ante carreras)
        # --------------------------------------------------------------
        # ON CONFLICT (oportunidad_id) DO NOTHING: si ya existe un
        # presupuesto para esta oportunidad (el UNIQUE de la migración
        # paso3), Postgres no da error ni escribe nada. Y en ese caso
        # RETURNING no devuelve ninguna fila.
        #
        # ¿Cómo puede existir, si el paso 2 ya comprobó que no había? Por
        # dos llamadas SIMULTÁNEAS (el agente reintenta mientras la
        # primera llamada aún no ha terminado): las dos pasan el paso 2
        # con la tabla vacía. La segunda se queda esperando en este
        # INSERT hasta que la primera confirma, y entonces recibe "cero
        # filas" en vez de un UniqueViolation (que acabaría en un 500).
        #
        # COMPARACIÓN CON D2 (clientes.email en leads_service.py): allí
        # se usa ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
        # justamente para NO usar DO NOTHING, porque se necesitaba el id
        # del cliente en los dos casos. Aquí se hace lo contrario, y es a
        # propósito:
        #   - Aquí SÍ importa distinguir "lo acabo de crear" de "ya
        #     existía": solo en el primer caso se cambia el estado y se
        #     marca creado=True. DO NOTHING da esa señal gratis (hay fila
        #     o no la hay). El truco de D2 devolvería una fila idéntica en
        #     los dos casos.
        #   - DO UPDATE reescribe la fila existente. Postgres crea una
        #     versión nueva de la fila aunque los valores sean los mismos.
        #     Un presupuesto ya emitido, quizá pendiente de aprobación, no
        #     debe recibir ninguna escritura.
        # Lo que las dos técnicas comparten es la idea de fondo: que la
        # restricción UNIQUE resuelva la carrera dentro de UNA sentencia,
        # en vez de "SELECT y, si no hay, INSERT", que D2 ya descartó por
        # abrir una condición de carrera.
        #
        # duracion_estimada_dias y aprobado_por se escriben NULL a mano:
        # la duración no tiene fórmula en N0 (limitación documentada en
        # la Adenda 1.1b) y aprobado_por lo escribirá POST
        # /gate-decisions. Escritos explícitos, la intención queda
        # visible en el código, con el mismo criterio que datos_completos
        # en create_lead.
        cursor.execute(
            """
            INSERT INTO presupuestos (
                oportunidad_id, importe_min_con_iva, importe_max_con_iva,
                iva_pct_aplicado,
                duracion_estimada_dias, requiere_aprobacion, aprobado_por,
                motivo_gate, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, NULL, %s, NULL, %s, now(), now())
            ON CONFLICT (oportunidad_id) DO NOTHING
            RETURNING id;
            """,
            (
                oportunidad_id,
                # Se guarda el precio CON IVA, que es el que se le
                # comunica al cliente, y junto a él el tipo aplicado. Así
                # el presupuesto queda congelado: un cambio futuro de
                # iva_estandar_pct no reescribe retroactivamente lo que
                # ya se ofreció. El importe sin IVA no se pierde: queda
                # en el log de auditoría del paso 6c.
                importe_min_con_iva,
                importe_max_con_iva,
                iva_pct,
                requiere_aprobacion,
                # .value da el texto del enum ('ambos'). Si no hay Gate,
                # motivo es None y se guarda NULL. Otra expresión
                # condicional en una línea.
                motivo.value if motivo is not None else None,
            ),
        )
        insertado = cursor.fetchone()

        if insertado is None:
            # Carrera perdida: otra llamada simultánea creó el
            # presupuesto primero y ya lo ha confirmado. Se lee y se
            # devuelve el SUYO, junto con el estado que ella dejó. No se
            # toca el estado: ya lo cambió la ganadora. Esta consulta se
            # ejecuta después de que la otra transacción haya confirmado,
            # así que ya ve sus datos (nivel de aislamiento READ
            # COMMITTED, el de Postgres por defecto).
            cursor.execute(
                """
                SELECT p.id, p.importe_min_con_iva, p.importe_max_con_iva,
                       p.motivo_gate, p.requiere_aprobacion, o.estado
                FROM presupuestos p
                JOIN oportunidades o ON o.id = p.oportunidad_id
                WHERE p.oportunidad_id = %s;
                """,
                (oportunidad_id,),
            )
            (
                presupuesto_id,
                importe_min_con_iva,
                importe_max_con_iva,
                motivo_gate,
                requiere_aprobacion,
                estado,
            ) = cursor.fetchone()
            # Igual que en el paso 2: se devuelve lo guardado por la
            # llamada que ganó la carrera, sin recalcular ni volver a
            # aplicar el IVA. Los importes que esta llamada había
            # calculado se descartan.
            return EstimateResponse(
                presupuesto_id=presupuesto_id,
                oportunidad_id=oportunidad_id,
                importe_min_con_iva=importe_min_con_iva,
                importe_max_con_iva=importe_max_con_iva,
                motivo_gate=motivo_gate,
                requiere_aprobacion=requiere_aprobacion,
                status=estado,
                creado=False,
            )

        presupuesto_id = insertado[0]

        # --------------------------------------------------------------
        # PASO 6b: cambiar el estado, con protección del orden
        # --------------------------------------------------------------
        # El "AND estado = 'nueva'" del WHERE es la guarda definitiva
        # (decisión P5): el UPDATE solo afecta a la fila si SIGUE en
        # 'nueva' en este preciso momento. Si otro proceso la cambió
        # entre el paso 1 y aquí, el UPDATE no toca nada y RETURNING no
        # devuelve fila. Se lanza entonces EstadoNoPermiteCalculo, y el
        # rollback del context manager deshace también el INSERT del
        # presupuesto de arriba.
        #
        # RETURNING estado devuelve el valor que Postgres acaba de
        # escribir, igual que en create_lead (D4). El status de la
        # respuesta es el dato real, no una copia escrita en Python.
        #
        # datos_completos, confianza_ia y prioridad NO aparecen en el SET:
        # quedan exactamente como estaban (decisión 4 del plan). Los
        # rellena el agente en otro punto del flujo.
        cursor.execute(
            """
            UPDATE oportunidades
            SET estado = %s, updated_at = now()
            WHERE id = %s AND estado = %s
            RETURNING estado;
            """,
            (nuevo_estado, oportunidad_id, ESTADO_INICIAL),
        )
        actualizado = cursor.fetchone()
        if actualizado is None:
            raise EstadoNoPermiteCalculo(
                f"El estado de la oportunidad {oportunidad_id} cambió durante el cálculo"
            )
        estado_final = actualizado[0]

        # --------------------------------------------------------------
        # PASO 6c: registro de auditoría del cálculo (decisión P7)
        # --------------------------------------------------------------
        # Se guardan las tarifas y reglas USADAS en este cálculo. leads
        # conserva los datos de entrada del cliente de forma inmutable
        # (D3), pero tarifas_base y reglas_negocio se pueden editar. Sin
        # esta foto, dentro de seis meses no se podría explicar por qué
        # salió esta cifra. Va en la misma transacción: si el log fallara,
        # tampoco quedaría el presupuesto, y no puede existir un
        # presupuesto sin su explicación.
        cursor.execute(
            """
            INSERT INTO logs (entity_type, entity_id, accion, detalle)
            VALUES (%s, %s, %s, %s);
            """,
            (
                LOG_ENTITY_TYPE_OPORTUNIDAD,
                oportunidad_id,
                ACCION_CALCULADO,
                Json(
                    {
                        "presupuesto_id": presupuesto_id,
                        "tipo_reforma": tipo_reforma,
                        "nivel_acabados": nivel_acabados,
                        "m2": _texto(m2),
                        "incluye_cambios_estructurales": incluye_cambios_estructurales,
                        "precio_m2": _texto(precio_m2),
                        "recargo_informe_tecnico": _texto(recargo),
                        "margen_empresa_pct": _texto(margen),
                        # Desde D9, el umbral depende de la categoría, así
                        # que se guarda el valor CONCRETO que se usó en
                        # este cálculo (y de qué categoría salía, que ya
                        # está más arriba en 'tipo_reforma').
                        "umbral_gate": _texto(umbral),
                        # Si el umbral usado todavía es provisional
                        # (parcial_acabados, pendiente de cerrar D10), el
                        # log lo dice. Así, el día que se fije el valor
                        # definitivo, se puede localizar con una consulta
                        # qué presupuestos se calcularon con el
                        # provisional, en vez de tener que adivinarlo por
                        # la fecha.
                        "umbral_provisional": umbral_provisional,
                        # Se guardan los CUATRO importes. Los que llevan
                        # IVA son los que vio el cliente; los de sin IVA
                        # son los que explican el cálculo (tarifa, margen,
                        # recargo) y con los que se decidió el Gate. Sin
                        # ambos pares, dentro de un año no se podría
                        # reconstruir por qué salió esa cifra NI por qué
                        # el Gate saltó o no saltó.
                        "importe_min_sin_iva": _texto(importe_min_sin_iva),
                        "importe_max_sin_iva": _texto(importe_max_sin_iva),
                        "importe_min_con_iva": _texto(importe_min_con_iva),
                        "importe_max_con_iva": _texto(importe_max_con_iva),
                        # El tipo aplicado, para poder rehacer la cuenta
                        # aunque la regla cambie después.
                        "iva_pct_aplicado": _texto(iva_pct),
                        "motivo_gate": motivo.value if motivo is not None else None,
                        "estado": estado_final,
                    }
                ),
            ),
        )

        cursor.close()

    # Aquí ya se ha salido del with, así que el commit está hecho: el
    # presupuesto, el estado nuevo y el log están guardados de verdad.
    return EstimateResponse(
        presupuesto_id=presupuesto_id,
        oportunidad_id=oportunidad_id,
        # Al cliente se le devuelve el precio final, con IVA. El desglose
        # sin IVA se ha quedado en el log de auditoría.
        importe_min_con_iva=importe_min_con_iva,
        importe_max_con_iva=importe_max_con_iva,
        motivo_gate=motivo,
        requiere_aprobacion=requiere_aprobacion,
        status=estado_final,
        creado=True,
    )
