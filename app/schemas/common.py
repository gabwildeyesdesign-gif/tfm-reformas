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
