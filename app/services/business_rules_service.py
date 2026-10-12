"""
Reglas de negocio GENERALES para el Agente 2: la tool MCP get_business_rules
(Bloque B3 del plan del Agente 2, docs/Plan_Agente2_Calendar.txt, sección
3.4; D18.4, D18.5.2 y D18.8).

Sin parámetros: un LLM no aporta identificadores (Adenda 1.1a), y estas
reglas son las mismas para todos los clientes. Los datos de la reforma de
cada cliente y su plazo orientativo van por otro camino: GET /leads/session
(Bloque B2).

Devuelve, y solo esto:
  - las reglas de la visita técnica (días, franjas y duración), las mismas
    que aplica POST /visits, leídas con la MISMA función
    (leer_reglas_visita), para que el agente proponga horas válidas;
  - lo que cuesta el informe técnico, con el IVA incluido (D14), calculado
    con la MISMA función que el presupuesto (aplicar_iva);
  - incluye_tipico de cada fila de tarifas_base.

NUNCA precio_m2, margen, el tipo de IVA ni los umbrales del Gate (C4 del
plan). La consulta de tarifas_base ni siquiera lee precio_m2, y el esquema
de salida no tiene dónde ponerlo: dos barreras, como en D18.5.

Solo lectura: una transacción REPEATABLE READ, READ ONLY (Postgres rechaza
cualquier escritura) y ningún log. Si falta o no es válida una regla, lanza
ConfiguracionIncompleta; el adaptador MCP la convierte en un error de la
tool con un [AVISO] en stderr.

Como todo services/, este archivo NO importa fastapi ni fastmcp.
"""

# Decimal: los valores de reglas_negocio llegan como Decimal (NUMERIC).
from decimal import Decimal

# get_db_connection(): una conexión del pool que termina con rollback (para
# lecturas).
from app.db.connection import get_db_connection

# El esquema de la respuesta (app/schemas/business_rules.py).
from app.schemas.business_rules import (
    BusinessRulesResponse,
    FranjaVisita,
    IncluyeTipico,
    ReglasVisita,
)

# Las claves del recargo y del IVA, y la función que aplica el IVA: las
# mismas que usa el cálculo del presupuesto (una sola fuente).
from app.services.estimate_service import CLAVE_IVA, CLAVE_RECARGO, aplicar_iva

# Las reglas de la visita: sus claves, la función que las lee y valida, y
# la excepción del 503 (compartida con /visits, el listado y la sesión).
from app.services.reglas_visita import (
    CLAVE_DURACION,
    CLAVE_MANANA_FIN,
    CLAVE_MANANA_INICIO,
    CLAVE_TARDE_FIN,
    CLAVE_TARDE_INICIO,
    ConfiguracionIncompleta,
    leer_reglas_visita,
)

# El modo de la transacción: la PRIMERA orden (mismo texto que el listado).
SQL_MODO_TRANSACCION = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;"

# Los días de visita: la regla fija de validar_fecha (reglas_visita.py), que
# no está en reglas_negocio. Se dice aquí para que el agente no proponga un
# sábado.
DIAS_VISITA = "de lunes a viernes"

# La clave del recargo que se lee, en una variable propia de este módulo:
# el script de comprobación la cambia en memoria para provocar el error de
# configuración sin tocar ninguna fila de reglas_negocio.
CLAVE_RECARGO_LEIDA = CLAVE_RECARGO

# Mensaje del error de configuración (llega a quien mire el error de la
# tool en n8n, nunca al cliente).
MENSAJE_CONFIGURACION = (
    "No se pueden dar las reglas de negocio: falta o no es válida una fila "
    "de reglas_negocio o de tarifas_base (ver 'faltan'). No se ha escrito nada."
)


# Igual que _hhmm de reglas_visita.py, que es de uso interno de ese módulo
# (guion bajo): aquí se escribe la suya en vez de importar una privada.
def minutos_a_hhmm(minutos: int) -> str:
    """Minutos desde medianoche -> texto "HH:MM" (510 -> "08:30")."""
    # divmod(a, b) devuelve a la vez el cociente y el resto: (8, 30).
    horas, mins = divmod(minutos, 60)
    # ":02d" escribe el número con dos cifras: 8 -> "08".
    return f"{horas:02d}:{mins:02d}"


def problemas_de_precio(recargo: Decimal | None, iva_pct: Decimal | None) -> list[str]:
    """
    Comprueba las dos reglas de precio. Devuelve la lista de problemas
    (vacía si están bien). No se adivina ningún valor: si falta el IVA,
    nunca se usa un 21 de reserva (mismo criterio que estimate_service).
    """
    # La lista empieza vacía y se rellena.
    problemas = []
    # El recargo: tiene que existir y no ser negativo.
    if recargo is None:
        # La fila no existe.
        problemas.append(f"{CLAVE_RECARGO_LEIDA}: no existe")
    # Existe pero es negativo.
    elif recargo < 0:
        # Un informe no puede costar menos de 0 €.
        problemas.append(f"{CLAVE_RECARGO_LEIDA}: valor no válido ({recargo})")
    # El IVA: tiene que existir y estar entre 0 y 100.
    if iva_pct is None:
        # La fila no existe.
        problemas.append(f"{CLAVE_IVA}: no existe")
    # Existe pero fuera de 0..100 (el valor no se enseña al agente, solo
    # en el error para administración).
    elif not (0 <= iva_pct <= 100):
        # Un porcentaje fuera de rango.
        problemas.append(f"{CLAVE_IVA}: valor no válido")
    # Se devuelve la lista.
    return problemas


def get_business_rules() -> BusinessRulesResponse:
    """
    Lee las reglas generales en UNA transacción de solo lectura y las
    devuelve. Lanza ConfiguracionIncompleta si falta o no es válida alguna.
    """
    # get_db_connection(): conexión de solo lectura; al salir del "with"
    # hace rollback y la devuelve al pool, también si hay una excepción.
    with get_db_connection() as conn:
        # El cursor se cierra siempre al salir de este bloque.
        with conn.cursor() as cursor:
            # 1. PRIMERA orden: una sola foto para las tres lecturas
            #    (REPEATABLE READ) y ninguna escritura posible (READ ONLY).
            cursor.execute(SQL_MODO_TRANSACCION)

            # 2. Las reglas de la visita, con la función de POST /visits.
            reglas, problemas = leer_reglas_visita(cursor)

            # 3. El recargo y el IVA. SQL: clave y valor de las dos filas;
            #    "= ANY(%s)" es "igual a alguna de la lista".
            cursor.execute(
                "SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);",
                ([CLAVE_RECARGO_LEIDA, CLAVE_IVA],),
            )
            # dict(...) sobre pares (clave, valor) crea el diccionario.
            precios = dict(cursor.fetchall())
            # .get devuelve None si la fila no se leyó.
            recargo = precios.get(CLAVE_RECARGO_LEIDA)
            # Lo mismo con el IVA.
            iva_pct = precios.get(CLAVE_IVA)
            # Se añaden sus problemas a los de la visita.
            problemas += problemas_de_precio(recargo, iva_pct)

            # 4. tarifas_base SIN precio_m2: la consulta no lo lee. ORDER BY
            #    para que la respuesta salga siempre en el mismo orden.
            cursor.execute(
                """
                SELECT tipo_reforma, nivel_acabados, incluye_tipico
                FROM tarifas_base
                ORDER BY tipo_reforma, nivel_acabados;
                """
            )
            # Lista de tuplas (tipo, nivel, texto).
            tarifas = cursor.fetchall()
            # Sin ninguna fila no hay nada que contar al agente.
            if not tarifas:
                # Se nombra la tabla, como una clave más.
                problemas.append("tarifas_base: sin filas")

            # Con algún problema: error de configuración, sin escribir nada.
            if problemas:
                # La misma excepción que el 503 de los endpoints REST.
                raise ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIGURACION, problemas)

    # 5. La respuesta. Las franjas, en "HH:MM".
    return BusinessRulesResponse(
        # Las reglas de la visita.
        visita=ReglasVisita(
            # La regla fija de los días.
            dias=DIAS_VISITA,
            # La franja de la mañana.
            manana=FranjaVisita(
                inicio=minutos_a_hhmm(reglas[CLAVE_MANANA_INICIO]),
                fin=minutos_a_hhmm(reglas[CLAVE_MANANA_FIN]),
            ),
            # La franja de la tarde.
            tarde=FranjaVisita(
                inicio=minutos_a_hhmm(reglas[CLAVE_TARDE_INICIO]),
                fin=minutos_a_hhmm(reglas[CLAVE_TARDE_FIN]),
            ),
            # La duración, en minutos.
            duracion_min=reglas[CLAVE_DURACION],
        ),
        # El recargo con IVA, con la función del presupuesto (redondeo a
        # céntimos, ROUND_HALF_UP).
        recargo_informe_tecnico_con_iva=aplicar_iva(recargo, iva_pct),
        # Una entrada por fila de tarifas_base, sin precio.
        incluye_tipico=[
            IncluyeTipico(tipo_reforma=tipo, nivel_acabados=nivel, incluye_tipico=texto)
            for tipo, nivel, texto in tarifas
        ],
    )
