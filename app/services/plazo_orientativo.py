"""
Plazo orientativo de ejecución que el Agente 2 puede dar al cliente (D18.7;
plan docs/Plan_Agente2_Calendar.txt, sección 3.3 y Bloque B2).

La regla de D18.7 la aplica el BACKEND, no el agente: el plazo solo existe
si el tipo es bano o cocina, NO hay Gate y la reforma NO incluye cambios
estructurales (false, no null: un dato que falta no se adivina, D4). En
cualquier otro caso, el plazo lo da una persona y aquí sale None.

Los números viven en reglas_negocio (paso13), nunca en el código:
  bano:   obra  semanas_obra_bano_min / _max
  cocina: obra  semanas_obra_cocina_min / _max
          y fabricación de muebles, ANTES del montaje,
                semanas_fabricacion_cocina_min / _max

Este archivo NO importa fastapi ni fastmcp (regla de services/). Todo es
puro (sin base de datos) salvo leer_reglas_plazo, que recibe el cursor de
quien llama.
"""

# Decimal: el tipo con el que psycopg2 entrega las columnas NUMERIC.
from decimal import Decimal

# Los dos objetos del contrato (app/schemas/leads.py).
from app.schemas.leads import PlazoOrientativo, TramoSemanas

# ConfiguracionIncompleta: el rechazo "falta o no es válida una regla"
# (-> 503), el mismo que usan /visits, /gate-decisions y el listado.
from app.services.reglas_visita import ConfiguracionIncompleta

# ======================================================================
# Constantes
# ======================================================================

# Las reglas de cada tipo de reforma que tiene plazo: para cada tramo
# (obra, fabricación de muebles), la pareja (clave mínima, clave máxima).
# Un tipo que no está aquí NO tiene plazo orientativo (integral_vivienda,
# parcial_acabados: D18.7). Las funciones leen este diccionario en el
# momento de ejecutarse, así que una prueba puede cambiar una clave EN
# MEMORIA para provocar el 503 sin tocar la tabla (como check_visits_service).
CLAVES_PLAZO = {
    "bano": {
        "obra": ("semanas_obra_bano_min", "semanas_obra_bano_max"),
    },
    "cocina": {
        "obra": ("semanas_obra_cocina_min", "semanas_obra_cocina_max"),
        "fabricacion": ("semanas_fabricacion_cocina_min", "semanas_fabricacion_cocina_max"),
    },
}

# Límites de un plazo en semanas: al menos una, como mucho un año. Son los
# mismos que comprueba check_migracion_agente2.
MIN_SEMANAS = 1
MAX_SEMANAS = 52

# Mensaje del 503 (llega a n8n; el router no debe enseñárselo al cliente).
MENSAJE_CONFIGURACION = (
    "No se puede calcular el plazo orientativo: falta o no es válida una regla "
    "de reglas_negocio (ver 'faltan'). No se ha escrito nada."
)


# ======================================================================
# Funciones puras (sin base de datos)
# ======================================================================


def plazo_aplicable(
    tipo_reforma: str | None,
    tiene_presupuesto: bool,
    requiere_aprobacion: bool | None,
    cambios_estructurales: bool | None,
) -> bool:
    """
    True si D18.7 permite dar un plazo: tipo con reglas de plazo, con
    presupuesto, sin Gate y sin cambios estructurales.

    Cada condición va en su propia línea para que una prueba en negativo
    pueda quitar UNA (N2 del plan).
    """
    # Sin presupuesto no hay nada que asesorar todavía.
    if not tiene_presupuesto:
        return False
    # Con Gate, el plazo lo da el técnico (D18.7). "is not False": solo
    # vale un False explícito; None (sin dato) tampoco da plazo.
    if requiere_aprobacion is not False:
        return False
    # Con cambios estructurales, o sin saberlo (None), tampoco (D18.7, D4).
    if cambios_estructurales is not False:
        return False
    # Y solo los tipos con reglas de plazo (bano y cocina).
    return tipo_reforma in CLAVES_PLAZO


def claves_necesarias(tipo_reforma: str) -> list[str]:
    """Las claves de reglas_negocio que hacen falta para ese tipo, en orden."""
    # Se aplanan las parejas de todos los tramos del tipo en una lista.
    return [clave for pareja in CLAVES_PLAZO[tipo_reforma].values() for clave in pareja]


def problema_semanas(clave: str, valor: Decimal | None) -> str | None:
    """
    Valida UNA regla de semanas: None si es válida, o el texto del problema.
    Válida = un ENTERO entre MIN_SEMANAS y MAX_SEMANAS (2.50, 0 o 53 no).
    Mismo formato de texto que problema_horas (condicion_seguimiento.py).
    """
    # La fila no existe.
    if valor is None:
        return f"{clave}: no existe"
    # Con decimales (to_integral_value() redondea: si cambia, los tenía) o
    # fuera de los límites.
    if valor != valor.to_integral_value() or not (MIN_SEMANAS <= valor <= MAX_SEMANAS):
        return f"{clave}: valor no válido ({valor}); debe ser un entero entre {MIN_SEMANAS} y {MAX_SEMANAS}"
    # Válida.
    return None


def construir_plazo(tipo_reforma: str, leidas: dict[str, Decimal]) -> tuple[PlazoOrientativo | None, list[str]]:
    """
    Construye el plazo de un tipo con las reglas leídas. Devuelve
    (plazo, problemas): con algún problema, plazo es None y quien llama
    lanza el 503. No se adivina ningún valor por defecto.

    leidas: clave -> valor, tal como llegan de reglas_negocio.
    """
    # Los problemas de todas las reglas, para nombrarlas todas en el 503.
    problemas: list[str] = []
    # Los tramos válidos: nombre del tramo -> TramoSemanas.
    tramos: dict[str, TramoSemanas] = {}
    # Cada tramo del tipo (obra; en cocina, también fabricación).
    for tramo, (clave_min, clave_max) in CLAVES_PLAZO[tipo_reforma].items():
        # Los dos valores (None si la fila no se leyó).
        valor_min, valor_max = leidas.get(clave_min), leidas.get(clave_max)
        # Los problemas de cada regla por separado.
        nuevos = [p for p in (problema_semanas(clave_min, valor_min),
                              problema_semanas(clave_max, valor_max)) if p is not None]
        # Las dos válidas pero al revés (mínimo mayor que máximo).
        if not nuevos and valor_min > valor_max:
            nuevos.append(f"{clave_min} ({valor_min}) es mayor que {clave_max} ({valor_max})")
        # Se acumulan los problemas de este tramo.
        problemas += nuevos
        # Sin problemas, el tramo se guarda como enteros (llegan como 2.00).
        if not nuevos:
            tramos[tramo] = TramoSemanas(min=int(valor_min), max=int(valor_max))
    # Con algún problema, ningún plazo: el 503 lo decide quien llama.
    if problemas:
        return None, problemas
    # El objeto del contrato; fabricación solo existe en cocina (None en baño).
    return PlazoOrientativo(
        obra_semanas=tramos["obra"],
        fabricacion_muebles_semanas=tramos.get("fabricacion"),
    ), []


# ======================================================================
# Con base de datos (recibe el cursor de quien llama)
# ======================================================================


def leer_plazo(cursor, tipo_reforma: str) -> PlazoOrientativo:
    """
    Lee las reglas del tipo con el cursor de quien llama y devuelve el
    plazo. Solo se llama si plazo_aplicable() es True.

    Excepción: ConfiguracionIncompleta (-> 503) si falta o no es válida
    alguna regla. No escribe nada (tampoco logs).
    """
    # Las claves se calculan AHORA (no al importar), por la prueba del 503.
    claves = claves_necesarias(tipo_reforma)
    # SQL: clave y valor de las filas cuya clave está en la lista; "=
    # ANY(%s)" es "igual a alguno de la lista". Datos de configuración.
    cursor.execute("SELECT clave, valor FROM reglas_negocio WHERE clave = ANY(%s);", (claves,))
    # dict(...) sobre pares (clave, valor) crea el diccionario.
    plazo, problemas = construir_plazo(tipo_reforma, dict(cursor.fetchall()))
    # Con algún problema: 503, sin escribir nada.
    if problemas:
        raise ConfiguracionIncompleta("configuracion_incompleta", MENSAJE_CONFIGURACION, problemas)
    # Sin problemas, el plazo.
    return plazo
