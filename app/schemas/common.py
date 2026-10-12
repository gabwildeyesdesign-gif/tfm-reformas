"""
Tipos y constantes compartidos por varios esquemas.

Viven aquí, y no dentro de leads.py, porque los mismos valores los va a
necesitar también el esquema de calculate-estimate en el siguiente bloque.
Si estuvieran en leads.py, el esquema de presupuestos tendría que importar
del de leads: una dependencia entre dos dominios distintos que no
significa nada en el negocio.
"""

# "enum" (de "enumeration") es un módulo de la librería estándar de Python.
# Sirve para definir un conjunto CERRADO de valores con nombre: en vez de
# "esto es un texto cualquiera", decimos "esto es uno de estos cuatro".
from enum import Enum

# re: expresiones regulares, para exigir el formato EXACTO de fecha y hora
# (validadores compartidos, al final de este archivo).
import re


class TipoReforma(str, Enum):
    """
    Tipos de reforma admitidos.

    ¡OJO! Los valores de la derecha NO son decorativos: son exactamente
    los que están almacenados en la columna tarifas_base.tipo_reforma de
    Supabase, verificados con una consulta real (SELECT DISTINCT), no
    copiados de la documentación. La tabla tiene además un CHECK que
    solo admite estos cuatro.

    Por eso van SIN eñe y SIN tildes: en la base de datos es 'bano', no
    'baño'. Si aquí escribiéramos 'baño', el lead se guardaría sin error
    (la columna oportunidades.tipo_reforma no tiene CHECK), pero después
    calculate-estimate buscaría 'baño' en tarifas_base, no encontraría
    tarifa, y TODOS los leads de baño acabarían marcados como
    'requiere_revision' por un fallo imposible de ver a simple vista.

    Heredamos de (str, Enum) y no solo de Enum. Eso hace que cada miembro
    SEA además un texto de Python de pleno derecho: se puede comparar con
    "bano", psycopg2 lo envía a Postgres como texto sin conversión
    manual, y FastAPI lo escribe en el JSON como "bano" y no como un
    objeto raro. Python 3.12 ofrece también StrEnum, que hace algo muy
    parecido; se usa (str, Enum) porque es la forma que aparece en la
    inmensa mayoría de la documentación y los ejemplos que vas a
    encontrar mientras aprendes.
    """

    # A la izquierda, el NOMBRE con el que se usa desde Python
    # (TipoReforma.BANO). A la derecha, el VALOR real que viaja por el
    # JSON y que se guarda en la base de datos.
    BANO = "bano"
    COCINA = "cocina"
    INTEGRAL_VIVIENDA = "integral_vivienda"
    PARCIAL_ACABADOS = "parcial_acabados"


class NivelAcabados(str, Enum):
    """
    Niveles de acabados admitidos.

    Mismo criterio que TipoReforma: valores verificados contra
    tarifas_base.nivel_acabados en Supabase. Son 'basico' (sin tilde),
    'medio' y 'alto'. La combinación de estos 3 niveles con los 4 tipos
    de reforma da las 12 filas de tarifas_base, que tiene además un
    UNIQUE (tipo_reforma, nivel_acabados) para que no haya duplicados.
    """

    # Igual que en TipoReforma: a la izquierda el nombre en Python, a la
    # derecha el valor real de la base de datos (sin tildes).
    BASICO = "basico"
    MEDIO = "medio"
    ALTO = "alto"


class MotivoGate(str, Enum):
    """
    Motivos por los que un presupuesto necesita la aprobación de una
    persona (Gate HITL) antes de llegar al cliente.

    Los valores de la derecha están copiados del CHECK real
    presupuestos_motivo_gate_check de Supabase (docs/schema_actual.sql),
    no de la documentación. Si aquí se escribiera un valor que el CHECK
    no admite, el INSERT del presupuesto fallaría en la base de datos.

    Vive en common.py, y no en estimates.py, por el mismo motivo que
    TipoReforma: lo van a necesitar dos dominios distintos. Hoy lo usa
    calculate-estimate, que DECIDE el motivo, y mañana lo leerá POST
    /gate-decisions, que RESUELVE el Gate.

    Cuando NO hay Gate no existe un cuarto valor "ninguno": el campo
    motivo_gate vale None (NULL en la base de datos). Así "no hay motivo"
    se representa con la ausencia de valor, que es lo que significa, y
    no con un texto que habría que añadir también al CHECK.
    """

    # El importe está bajo el umbral, pero la reforma toca estructura:
    # hace falta un informe técnico, y un técnico debe revisarlo.
    CAMBIOS_ESTRUCTURALES = "cambios_estructurales"
    # Sin cambios estructurales, pero importe_max supera el umbral de
    # reglas_negocio.umbral_aprobacion_manual.
    IMPORTE_SUPERIOR_UMBRAL = "importe_superior_umbral"
    # Las dos cosas a la vez.
    AMBOS = "ambos"


class EstadoOportunidad(str, Enum):
    """
    Estados posibles de una oportunidad (columna oportunidades.estado).

    Los valores de la derecha están copiados del CHECK real
    oportunidades_estado_check de Supabase (docs/schema_actual.sql), en el
    mismo orden, no de la documentación. Mismo criterio que TipoReforma y
    MotivoGate: si aquí faltara un valor que la base de datos sí admite,
    leer una oportunidad en ese estado daría un 500 al construir la
    respuesta.

    Se añadió el 2026-10-03 para GET /gate-avisos (corrección del tutor
    sobre el Bloque B). Están los OCHO y no solo los tres que espera ese
    endpoint: tras la visita, un caso con Gate puede pasar A MANO a
    'ganada' (N0), y con una lista de tres el aviso fallaría. Los
    servicios que hoy escriben los estados como textos sueltos
    (gate_decisions_service, desde el 2026-10-11 resultados_llamada_service;
    visits_service y estimate_service) todavía no
    lo usan: queda para otra rama.

    (str, Enum): cada valor es también un texto normal, igual que
    TipoReforma.
    """

    # Recién creada por POST /leads.
    NUEVA = "nueva"
    # Está en el CHECK pero NO se escribe en N0: la cualificación ocurre
    # dentro del mismo ciclo síncrono que el cálculo, y un estado que nadie
    # puede observar no aporta nada. Se conserva para N1+
    # (docs/TFM_Decisiones_Modelo_Datos_Leads.txt, "POR QUÉ 'cualificada'
    # NO TIENE UNA TRANSICIÓN PERSISTIDA PROPIA EN N0"). También D21.1
    # (Decisiones_D21_Gate_Resultado_Llamada_Administracion.md, pendiente
    # de copiar al repositorio).
    CUALIFICADA = "cualificada"
    # Presupuesto con Gate, esperando la llamada de administración.
    PENDIENTE_APROBACION = "pendiente_aprobacion"
    # Visita confirmada (por POST /gate-decisions con visita_acordada).
    VISITA_AGENDADA = "visita_agendada"
    # Presupuesto sin Gate, ya comunicado al cliente.
    PRESUPUESTO_ENVIADO = "presupuesto_enviado"
    # Seguimiento de 48 h pendiente (D13; lo escribirá el barrido de WF3).
    SEGUIMIENTO_PENDIENTE = "seguimiento_pendiente"
    # Estados finales. 'ganada' se escribe a mano en N0; 'perdida', también
    # por POST /gate-decisions con descartar.
    GANADA = "ganada"
    PERDIDA = "perdida"


class EstadoVisita(str, Enum):
    """
    Estados posibles de una visita (columna visitas.estado).

    Copiados del CHECK real visitas_estado_check (docs/schema_actual.sql),
    en el mismo orden. Mismo criterio que EstadoOportunidad: si aquí
    faltara un valor que la base de datos admite, leer una visita en ese
    estado daría un 500.

    Se añadió el 2026-10-10 para la ficha (GET
    /oportunidades/{id}/ficha, P10 de docs/Plan_Endpoint_Ficha_Oportunidad.txt),
    que devuelve TODAS las visitas. No sustituye a EstadoVisitaListado
    (app/schemas/listado_llamadas.py), que tiene solo las dos activas a
    propósito.
    """

    # Pedida por el cliente desde el chat (POST /visits).
    SOLICITADA = "solicitada"
    # Acordada por teléfono con administración (POST /gate-decisions).
    CONFIRMADA = "confirmada"
    # Hecha. En N0 se escribe a mano.
    COMPLETADA = "completada"
    # Sustituida por otra visita del mismo cliente.
    CANCELADA = "cancelada"


# Número máximo de fotos que se aceptan en un lead.
#
# PROVISIONAL — PENDIENTE (limitación documentada en
# docs/TFM_Decisiones_Modelo_Datos_Leads.txt, apartado 6):
# este valor debería leerse de la tabla reglas_negocio (clave
# 'max_fotos_lead'), igual que se hace con el umbral del Gate o el margen
# de empresa, para poder cambiarlo sin tocar código.
#
# VERIFICADO contra Supabase (2026-09-18): la fila YA EXISTE, con clave
# 'max_fotos_lead' y valor 5.00 (insertada el 2026-09-17). Lo que falta
# es que services/ la lea; hasta entonces, el límite real lo aplica esta
# constante, y los dos valores coinciden a mano. Aviso para cuando se
# implemente la lectura: la columna valor es NUMERIC(10,2), así que
# vuelve como Decimal('5.00') y hay que convertirla a int.
#
# Se define como constante compartida (y no como un 5 suelto repetido
# en dos modelos) para que el día que se sustituya por la lectura real
# solo haya que cambiar un sitio.
MAX_FOTOS_LEAD = 5

# Superficie máxima admitida en un lead, en metros cuadrados.
#
# POR QUÉ EXISTE ESTE TOPE. Hasta ahora m2 solo exigía "mayor que cero".
# Verificado con ejecución real: un lead de 60.000 m² se aceptaba sin
# problema (201) y el fallo aparecía DESPUÉS, al calcular, porque
# importe_max no cabía en presupuestos.importe_max, que es NUMERIC(10,2)
# (máximo 99.999.999,99 €). El cliente recibía un 500 genérico por un
# dato que se podría haber rechazado en la puerta.
#
# POR QUÉ 500 Y NO OTRO NÚMERO. El desbordamiento real empieza en unos
# 51.151 m² (con la tarifa más cara, bano/alto a 1.700 €/m²), así que 500
# deja un margen de cien veces. Y sigue siendo holgado para el negocio:
# el Informe de decisiones trabaja con viviendas de 45 a 150 m².
#
# Igual que MAX_FOTOS_LEAD, se define como constante compartida para que
# el día que se lea de reglas_negocio solo haya que cambiar un sitio. El
# mismo valor está replicado en el CHECK chk_leads_m2_rango de la base de
# datos (defensa doble, mismo criterio que D5): si se cambia aquí, hay
# que cambiarlo también allí.
MAX_M2_LEAD = 500

# Longitud admitida de lead_token, el identificador de la conversación del
# chat. Es la clave de idempotencia de POST /leads y la clave de búsqueda
# de GET /leads/session/{lead_token}.
#
# MAX_LEAD_TOKEN = 100 replica el ancho de la columna leads.lead_token
# (VARCHAR(100)): un token más largo no cabría y daría un 500 en vez de un
# 422. MIN_LEAD_TOKEN = 1 rechaza el texto vacío. Un UUID ocupa 36
# caracteres.
#
# Constantes compartidas, y no números sueltos, porque las usan dos sitios
# que tienen que coincidir: el cuerpo de POST /leads (LeadCreate) y el
# parámetro de ruta de GET /leads/session. Si un día cambia el ancho de la
# columna, se cambia aquí y los dos quedan iguales.
MIN_LEAD_TOKEN = 1
MAX_LEAD_TOKEN = 100

# Longitud máxima de visitas.texto_cliente: lo que dijo el cliente en el
# chat sobre cuándo le viene bien la visita. Es una frase, no la
# descripción del proyecto: un turno de chat normal ronda los 300
# caracteres. El tope evita que un texto pegado (o un bucle del agente)
# meta decenas de KB en visitas y en logs.
#
# Defensa doble (mismo criterio que MAX_M2_LEAD): el mismo valor está en
# el CHECK chk_visitas_texto_cliente_longitud de la base de datos
# (migración paso9). Si se cambia aquí, hay que cambiarlo también allí.
#
# Nota 2026-10-12 (D26.7, plan del Agente 2): baja de 1000 a 300. Durante
# un tiempo el CHECK sigue en 1000: endurecerlo antes del merge haría que
# POST /visits de main diera 500 con textos de 301 a 1000 caracteres (C6
# del plan). Lo baja la migración paso13b, DESPUÉS del merge. Mientras
# tanto Pydantic es más estricto que la base de datos, que es el orden
# seguro.
MAX_TEXTO_VISITA = 300

# Longitud máxima de decisiones_gate.informe: lo que anota administración
# tras la llamada al cliente en un caso de Gate (POST /gate-decisions). Es
# más largo que el texto de una visita porque es un resumen de una
# conversación telefónica, y queda para la auditoría anual.
#
# Defensa doble (mismo criterio que MAX_TEXTO_VISITA): el mismo valor está
# en el CHECK chk_decisiones_gate_informe de la base de datos (migración
# paso10). Si se cambia aquí, hay que cambiarlo también allí.
MAX_INFORME_GATE = 2000


# ======================================================================
# Validadores de formato COMPARTIDOS (fecha, hora y texto)
# ======================================================================
# Los usan VisitaCreate (POST /visits) y GateDecisionCreate (POST
# /gate-decisions), que deben aceptar EXACTAMENTE los mismos formatos. Se
# mudaron aquí desde app/schemas/visits.py el 2026-09-30, sin cambiar ni
# los patrones ni los mensajes de error.
#
# Por qué hacen falta (comprobado con ejecución real el 2026-09-26): sin
# ellos, Pydantic acepta en una fecha "2026-10-01T00:00:00" y números
# enteros, y en una hora "10:00:30.5", el número 36000 y "10:00Z". Este
# último es el peligroso: la Z significa UTC, así que la visita quedaría
# dos horas desplazada. El contrato dice YYYY-MM-DD y HH:MM, y solo eso.
#
# Cada esquema los llama desde un @field_validator(..., mode="before"), es
# decir, ANTES de que Pydantic convierta el valor a date o a time.

# Formatos exactos. ^ y $ obligan a que el texto ENTERO cumpla el patrón
# (no basta con que lo contenga).
#   \d{4}-\d{2}-\d{2}       -> "2026-10-23"
#   ([01]\d|2[0-3]):[0-5]\d -> de "00:00" a "23:59"; "9:00" no vale, "09:00" sí
PATRON_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PATRON_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def fecha_en_formato_exacto(valor):
    """
    Deja pasar solo un texto "YYYY-MM-DD"; si no, ValueError (que Pydantic
    convierte en un 422 que nombra el campo). Devuelve el texto tal cual:
    Pydantic lo convierte después a date, y ahí rechaza los días
    imposibles como "2026-02-30".
    """
    # isinstance comprueba el tipo: si no es texto, o si es texto con otra
    # forma, se rechaza.
    if not isinstance(valor, str) or not PATRON_FECHA.match(valor):
        raise ValueError("la fecha debe tener el formato YYYY-MM-DD, por ejemplo 2026-10-23")
    # Formato correcto: se devuelve sin tocar para que Pydantic siga.
    return valor


def hora_en_formato_exacto(valor):
    """Deja pasar solo un texto "HH:MM" (24 horas); si no, ValueError."""
    # Mismo esquema que la fecha: si no es texto o no cumple PATRON_HORA,
    # ValueError, que Pydantic convierte en un 422 que nombra el campo.
    if not isinstance(valor, str) or not PATRON_HORA.match(valor):
        raise ValueError("la hora debe tener el formato HH:MM (24 horas), por ejemplo 08:30 o 17:00")
    # Formato correcto: se devuelve sin tocar; Pydantic lo convierte a time.
    return valor


def texto_sin_espacios_en_los_extremos(valor):
    """
    Quita espacios, tabuladores y saltos de línea del principio y del
    final (.strip()). Un texto hecho solo de espacios queda vacío y lo
    rechaza el min_length=1 del campo. Si no es texto, se deja pasar tal
    cual para que Pydantic dé su propio error de tipo.
    """
    # Expresión condicional en una línea: "A if condición else B". Si es
    # texto, se devuelve recortado; si no, el valor tal cual llegó.
    return valor.strip() if isinstance(valor, str) else valor


# ======================================================================
# Filtro de datos de contacto en el texto que escribe un agente (D26.7)
# ======================================================================
# El texto que escribe el Agente 2 (visitas.texto_cliente) lo leen
# administración, la ficha y la lista diaria. No debe llevar el email ni el
# teléfono del cliente: ya están en la solicitud, y copiarlos en texto
# libre los esparce por sitios que no los esperan. Este filtro los rechaza
# con un 422 y el agente reescribe el texto sin ellos.
#
# Vive aquí, y no en visits.py, porque la rama 2 del plan lo reutilizará
# para el resumen de la solicitud (LeadCreate.resumen): un solo sitio.
#
# Solo en Pydantic, nunca en un CHECK: una expresión regular escrita en SQL
# divergiría con el tiempo de la de Python (P3 del plan).
#
# Límites conocidos (R1 y R2 del plan): no detecta una dirección ni datos
# de salud escritos en prosa, ni un número dictado en palabras ("seis seis
# seis..."). Y puede rechazar un texto inocente con 9 o más cifras
# seguidas, por ejemplo "referencia 123 456 789"; el agente lo reescribe.

# Forma de email, la misma que pide el Agente 1 ("Validating contact data"
# de prompts/agente1_captura.md): texto, una @, un dominio, un punto y un
# final de al menos 2 letras. Se BUSCA dentro del texto (re.search), no se
# exige que el texto entero lo sea.
#   [^\s@]+    -> uno o más caracteres que no son espacio ni @ (el usuario)
#   @          -> la arroba
#   [^\s@]+    -> el dominio (puede llevar puntos: "correo.empresa")
#   \.         -> un punto literal
#   [^\W\d_]{2,} -> 2 o más LETRAS (\W es "no letra ni cifra ni _"; quitar
#                 también \d y _ deja solo letras, con tildes incluidas)
PATRON_EMAIL_EN_TEXTO = re.compile(r"[^\s@]+@[^\s@]+\.[^\W\d_]{2,}")

# Forma de teléfono: un tramo de 9 o más cifras, con o sin separadores
# entre ellas. El Agente 1 admite de 9 a 15 cifras con espacios, guiones,
# puntos y paréntesis; aquí no hay máximo, para que un número más largo
# (16 cifras) también se detecte. El "+" del prefijo no hace falta:
# "+34 666 777 444" ya tiene 11 cifras.
#   (?<![\d:/])  -> el tramo no empieza pegado a una cifra, ":" o "/"
#   \d           -> una cifra
#   (?:[\s.\-()]{0,3}\d){8,} -> 8 o más veces: hasta 3 separadores
#                   (espacio, punto, guion o paréntesis) y otra cifra
#   (?![\d:/])   -> y no termina pegado a una cifra, ":" o "/"
# La barra "/" y los dos puntos ":" NO son separadores y además cortan el
# tramo por los dos lados: así una hora ("9:30") o una fecha ("19/10/2026")
# no cuentan, y tampoco "2026-10-19 10:00", donde sin esos bordes las
# cifras de la fecha y el "10" de la hora sumarían 10.
PATRON_TELEFONO_EN_TEXTO = re.compile(r"(?<![\d:/])\d(?:[\s.\-()]{0,3}\d){8,}(?![\d:/])")

# Mensajes del 422. Los lee el agente (n8n le pasa el cuerpo del error), así
# que dicen qué hacer. NO repiten el dato encontrado. OJO: el 422 de
# Pydantic sí devuelve el texto completo en "input" (K3 del plan, aceptado
# y documentado como R3).
# Mensaje cuando el texto tiene forma de email.
MENSAJE_EMAIL_EN_TEXTO = (
    "el texto parece contener un email: escríbelo sin datos de contacto "
    "(administración ya tiene el email y el teléfono del cliente)"
)
# Mensaje cuando el texto tiene forma de teléfono.
MENSAJE_TELEFONO_EN_TEXTO = (
    "el texto parece contener un teléfono (9 o más cifras seguidas): "
    "escríbelo sin datos de contacto y sin números largos "
    "(administración ya tiene el email y el teléfono del cliente)"
)


# El filtro: lo llama VisitaCreate desde un @field_validator (visits.py).
def texto_sin_datos_de_contacto(valor):
    """
    Deja pasar un texto sin forma de email ni de teléfono; si tiene alguna,
    ValueError (que Pydantic convierte en un 422 que nombra el campo).
    Se llama DESPUÉS de que Pydantic haya comprobado el tipo y la longitud,
    así que valor ya es un str recortado. Devuelve el texto sin tocar.
    """
    # re.search busca el patrón en cualquier punto del texto. Si encuentra
    # un email, se rechaza con su mensaje (se mira antes que el teléfono:
    # si hay los dos, basta con un mensaje).
    if PATRON_EMAIL_EN_TEXTO.search(valor):
        raise ValueError(MENSAJE_EMAIL_EN_TEXTO)
    # Igual con la forma de teléfono.
    if PATRON_TELEFONO_EN_TEXTO.search(valor):
        raise ValueError(MENSAJE_TELEFONO_EN_TEXTO)
    # Sin datos de contacto: el texto se devuelve tal cual.
    return valor
