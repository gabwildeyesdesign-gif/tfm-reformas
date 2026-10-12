"""
Verificación SIN servidor ni base de datos del filtro de D26.7 sobre el
texto que escribe el Agente 2 en POST /visits (texto_cliente).

Plan: docs/Plan_Agente2_Calendar.txt, sección 3.2 y Bloque del filtro en
la sección 7. Lo que se prueba vive en app/schemas/common.py
(texto_sin_datos_de_contacto, sus dos patrones y MAX_TEXTO_VISITA) y en
app/schemas/visits.py (VisitaCreate, que lo engancha a texto_cliente).

  S1. Textos que DEBEN pasar: horas, fechas, pisos, metros, códigos
      postales y el borde de 8 cifras.
  S2. Textos con forma de email -> ValueError con el mensaje del email.
  S3. Textos con forma de teléfono (cada separador por separado, fijo,
      móvil, prefijo, 16 cifras y el borde de 9) -> ValueError con el
      mensaje del teléfono.
  S4. Falsos positivos conocidos (R2 del plan): se rechazan HOY; el agente
      los reescribe. Si un día dejan de rechazarse, este caso lo avisa.
  S5. Límites conocidos (R1): textos con datos personales que el filtro NO
      ve. Se comprueba que pasan, para que el límite esté documentado con
      ejecución real y no solo por escrito.
  S6. Mensajes: ninguno repite el dato encontrado; con email y teléfono a
      la vez sale el del email.
  S7. VisitaCreate: longitud máxima 300 (con 300 "á" también), recorte de
      espacios ANTES de medir, 301 -> error de longitud, el 422 nombra
      texto_cliente, y con 301 caracteres Y un email sale solo el error de
      longitud (el filtro va después).

Sale con código 1 si algo falla. Cada comprobación que espera una
excepción tiene su rama else, que marca FALLO si la excepción no sale
(regla de CLAUDE.md, caso D16).
"""

# sys: para la ruta del paquete "app", la salida en UTF-8 y el código de
# salida.
import sys

# Path: para calcular la raíz del repositorio desde este archivo.
from pathlib import Path

# El paquete "app" está en la raíz del repositorio (misma técnica que los
# demás check_*.py). parents[1] es la carpeta de encima de scripts/: en una
# copia de prueba en negativo, es la de la copia, y se importa SU app.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Se pone la primera de la lista de búsqueda de módulos.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes y eñes en la consola de Windows sin UnicodeEncodeError.
sys.stdout.reconfigure(encoding="utf-8")

# ValidationError: la excepción de Pydantic cuando un esquema rechaza datos
# (FastAPI la convierte en el 422).
from pydantic import ValidationError

# Lo que se prueba: la constante de longitud, los dos mensajes y la función.
from app.schemas.common import (
    MAX_TEXTO_VISITA,
    MENSAJE_EMAIL_EN_TEXTO,
    MENSAJE_TELEFONO_EN_TEXTO,
    texto_sin_datos_de_contacto,
)

# El esquema del cuerpo de POST /visits.
from app.schemas.visits import VisitaCreate

# Contadores: comprobaciones correctas y lista de fallos.
ok = 0
fallos = []


def comprobar(titulo, condicion, detalle=""):
    """Imprime [OK] o [FALLO] y lo cuenta."""
    # global: se cambia el contador de fuera de la función.
    global ok
    # Si la condición se cumple, cuenta como correcta.
    if condicion:
        # Una correcta más.
        ok += 1
        # Se enseña sin detalle.
        print(f"  [OK]    {titulo}")
    # Si no se cumple, se anota el fallo con su detalle.
    else:
        # Se guarda para el resumen final.
        fallos.append(f"{titulo} {detalle}")
        # Y se enseña en su sitio.
        print(f"  [FALLO] {titulo} {detalle}")


def rechazo(texto):
    """
    Llama al filtro y devuelve el mensaje del ValueError, o None si el
    texto pasa. Si sale otra excepción, sube (es un error del script o del
    código, no un resultado).
    """
    # try: el filtro lanza ValueError cuando rechaza.
    try:
        # Se llama con el texto tal cual.
        texto_sin_datos_de_contacto(texto)
    # Rechazado: se devuelve el mensaje.
    except ValueError as error:
        # str(error) es el texto del ValueError.
        return str(error)
    # Sin excepción: el texto pasa.
    return None


def cuerpo_visita(texto):
    """Un cuerpo válido de POST /visits con ese texto_cliente."""
    # Fecha y hora con el formato exacto; el token es de prueba (no se
    # consulta ninguna base de datos: solo se valida la forma).
    return {"lead_token": "tok-texto-agente", "fecha": "2026-10-19", "hora": "10:00", "texto_cliente": texto}


def errores_visita(texto):
    """
    Valida el cuerpo con VisitaCreate. Devuelve la lista de errores de
    Pydantic (vacía si pasa). Cada error es un dict con "loc", "type" y
    "msg", lo mismo que FastAPI pone en el 422.
    """
    # try: model_validate lanza ValidationError si algo no encaja.
    try:
        # Validación completa del cuerpo.
        VisitaCreate.model_validate(cuerpo_visita(texto))
    # Rechazado: se devuelven sus errores.
    except ValidationError as error:
        # .errors() da la lista de errores en el formato del 422.
        return error.errors()
    # Sin excepción: ningún error.
    return []


# ----------------------------------------------------------------------
print("\nS1 - Textos que DEBEN pasar")
# Cada uno, con el motivo por el que no es un dato de contacto.
DEBEN_PASAR = [
    # Los tres del enunciado del tutor.
    "a las 9:30",
    "piso 2º B",
    "unos 12 m2",
    # Horas y franjas: ":" no es separador.
    "de 8:30 a 12:30, o por la tarde de 17:00 a 19:00",
    # Fecha con barras y hora: "/" corta el tramo.
    "El lunes 19/10/2026 a las 10:00",
    # Fecha ISO con hora: sin los bordes ":" serían 10 cifras.
    "el 2026-10-19 10:00 o el 2026-10-20 a las 9:30",
    # Código postal (5 cifras) y palabras.
    "Código postal 28001, tercer piso, el portero está de 9 a 14",
    # Varias medidas sueltas.
    "tiene 2 baños de 6 m2 y 8 m2, y la cocina mide 12",
    # Borde: 8 cifras seguidas todavía no son un teléfono.
    "pedido 12345678",
    # Una arroba suelta, sin dominio.
    "nos vemos @ las 9",
]
# Se prueba cada texto con el filtro.
for texto in DEBEN_PASAR:
    # El mensaje del rechazo, o None si pasa.
    mensaje = rechazo(texto)
    # Debe pasar.
    comprobar(f"pasa: {texto!r}", mensaje is None, f"(rechazado: {mensaje})")

# ----------------------------------------------------------------------
print("\nS2 - Forma de email -> rechazado con el mensaje del email")
# Cada uno con algo distinto: normal, con puntos y +, con tildes y
# mayúsculas, y el más corto posible.
EMAILS = [
    "escríbeme a pepe@gmail.com cuando puedas",
    "Pepe.Perez+obra@correo.empresa.es",
    "mi correo es MARÍA@dominio.ES por la tarde",
    "x@y.co",
]
# Se prueba cada texto.
for texto in EMAILS:
    # try / except / else: la rama else marca FALLO si NO sale la excepción.
    try:
        # Debe lanzar ValueError.
        texto_sin_datos_de_contacto(texto)
    # Sale: se comprueba que es el mensaje del email.
    except ValueError as error:
        # Comparación exacta con la constante.
        comprobar(f"rechazado: {texto!r}", str(error) == MENSAJE_EMAIL_EN_TEXTO, f"(mensaje: {error})")
    # No sale: el texto ha pasado.
    else:
        # FALLO explícito.
        comprobar(f"rechazado: {texto!r}", False, "(la excepción se ha tragado: el texto pasa)")

# ----------------------------------------------------------------------
print("\nS3 - Forma de teléfono -> rechazado con el mensaje del teléfono")
# Cada separador por separado, mezclados, fijo de Madrid, prefijo, 16
# cifras y el borde exacto de 9.
TELEFONOS = [
    "llámame al 666 777 444",
    "+34 (666) 777-444",
    "666777444",
    "666.777.444",
    "666-777-444",
    "tel 6 6 6 7 7 7 4 4 4 por la tarde",
    "fijo 91 123 45 67",
    "número 1234567812345678",
    "123456789",
    "llama al 666 777 444 a las 9:30",
]
# Se prueba cada texto.
for texto in TELEFONOS:
    # try / except / else, igual que en S2.
    try:
        # Debe lanzar ValueError.
        texto_sin_datos_de_contacto(texto)
    # Sale: se comprueba que es el mensaje del teléfono.
    except ValueError as error:
        # Comparación exacta con la constante.
        comprobar(f"rechazado: {texto!r}", str(error) == MENSAJE_TELEFONO_EN_TEXTO, f"(mensaje: {error})")
    # No sale: el texto ha pasado.
    else:
        # FALLO explícito.
        comprobar(f"rechazado: {texto!r}", False, "(la excepción se ha tragado: el texto pasa)")

# ----------------------------------------------------------------------
print("\nS4 - Falsos positivos conocidos (R2): se rechazan hoy")
# Inocentes, pero con 9 o más cifras con forma de teléfono. El agente los
# reescribe (por ejemplo, "19 de octubre a las 10").
FALSOS_POSITIVOS = [
    "referencia 123 456 789",
    "el 19.10.2026 10.00",
]
# Se prueba cada texto.
for texto in FALSOS_POSITIVOS:
    # El mensaje del rechazo, o None si pasa.
    mensaje = rechazo(texto)
    # Hoy se rechaza como teléfono.
    comprobar(f"rechazado (falso positivo conocido): {texto!r}", mensaje == MENSAJE_TELEFONO_EN_TEXTO, f"(mensaje: {mensaje})")

# ----------------------------------------------------------------------
print("\nS5 - Límites conocidos (R1): datos personales que el filtro NO ve")
# Una dirección en prosa, salud, un teléfono dictado en palabras, un email
# escrito con "arroba" y uno sin punto en el dominio.
NO_DETECTADOS = [
    "vive en la calle Mayor 5, 3º izquierda",
    "tiene movilidad reducida, mejor por la mañana",
    "su teléfono es seis seis seis siete siete siete",
    "pepe arroba gmail punto com",
    "pepe@gmail",
]
# Se prueba cada texto.
for texto in NO_DETECTADOS:
    # El mensaje del rechazo, o None si pasa.
    mensaje = rechazo(texto)
    # Hoy pasan: el límite queda medido.
    comprobar(f"pasa (límite conocido): {texto!r}", mensaje is None, f"(rechazado: {mensaje})")

# ----------------------------------------------------------------------
print("\nS6 - Mensajes")
# Los datos que aparecen en los textos rechazados de S2 y S3.
DATOS = ["pepe@gmail.com", "@", "666", "777", "444", "123"]
# Ninguno de los dos mensajes contiene un dato ni una cifra.
for nombre, mensaje in (("email", MENSAJE_EMAIL_EN_TEXTO), ("teléfono", MENSAJE_TELEFONO_EN_TEXTO)):
    # Los datos que aparecen en el mensaje (debe quedar vacía).
    repite = [d for d in DATOS if d in mensaje]
    # Debe ser una lista vacía.
    comprobar(f"el mensaje del {nombre} no repite ningún dato", not repite, f"(repite: {repite})")
# Los dos dicen qué hacer: escribirlo sin datos de contacto.
comprobar("los dos mensajes dicen 'escríbelo sin datos de contacto'",
          all("escríbelo sin datos de contacto" in m for m in (MENSAJE_EMAIL_EN_TEXTO, MENSAJE_TELEFONO_EN_TEXTO)))
# Con email y teléfono a la vez, sale el del email (se mira primero).
mensaje = rechazo("pepe@gmail.com o 666 777 444")
# Comparación exacta.
comprobar("email y teléfono a la vez -> mensaje del email", mensaje == MENSAJE_EMAIL_EN_TEXTO, f"(mensaje: {mensaje})")

# ----------------------------------------------------------------------
print("\nS7 - VisitaCreate (lo que verá el agente en el 422)")
# La constante: 300 desde D26.7.
comprobar("MAX_TEXTO_VISITA = 300", MAX_TEXTO_VISITA == 300, f"({MAX_TEXTO_VISITA})")
# 300 caracteres exactos: pasa.
comprobar("300 caracteres -> pasa", errores_visita("x" * 300) == [], f"({errores_visita('x' * 300)})")
# 300 "á" (600 bytes en UTF-8): pasa, porque se cuentan caracteres.
comprobar("300 'á' -> pasa (cuenta caracteres, no bytes)", errores_visita("á" * 300) == [])
# 300 caracteres con espacios alrededor: pasa, porque se recorta antes.
comprobar("300 caracteres con 3 espacios a cada lado -> pasa (recorte antes de medir)",
          errores_visita("   " + "x" * 300 + "   ") == [])
# 301 caracteres: un error, de longitud, en texto_cliente.
errores = errores_visita("x" * 301)
# Un solo error, de tipo string_too_long, en el campo texto_cliente.
comprobar("301 caracteres -> string_too_long en texto_cliente",
          len(errores) == 1 and errores[0]["type"] == "string_too_long" and errores[0]["loc"] == ("texto_cliente",),
          f"({[(e['type'], e['loc']) for e in errores]})")
# Un email: un error en texto_cliente con el mensaje del email.
errores = errores_visita("escríbeme a pepe@gmail.com")
# Pydantic pone "Value error, " delante del mensaje del ValueError.
comprobar("email -> value_error en texto_cliente con el mensaje del email",
          len(errores) == 1 and errores[0]["type"] == "value_error" and errores[0]["loc"] == ("texto_cliente",)
          and errores[0]["msg"] == "Value error, " + MENSAJE_EMAIL_EN_TEXTO,
          f"({[(e['type'], e['loc'], e['msg']) for e in errores]})")
# Un teléfono: igual, con el mensaje del teléfono.
errores = errores_visita("llámame al 666 777 444")
# Mismo formato.
comprobar("teléfono -> value_error en texto_cliente con el mensaje del teléfono",
          len(errores) == 1 and errores[0]["type"] == "value_error" and errores[0]["loc"] == ("texto_cliente",)
          and errores[0]["msg"] == "Value error, " + MENSAJE_TELEFONO_EN_TEXTO,
          f"({[(e['type'], e['loc'], e['msg']) for e in errores]})")
# 301 caracteres que además llevan un email: solo el error de longitud (el
# filtro, mode="after", no llega a ejecutarse).
errores = errores_visita("pepe@gmail.com " + "x" * 286)
# Se comprueba primero que el texto mide 301.
comprobar("301 caracteres con un email -> solo string_too_long (el filtro va después)",
          len("pepe@gmail.com " + "x" * 286) == 301 and [e["type"] for e in errores] == ["string_too_long"],
          f"({[e['type'] for e in errores]})")
# Un texto válido de los del tutor pasa también por el esquema completo.
comprobar("'a las 9:30' -> pasa por VisitaCreate", errores_visita("a las 9:30") == [])

# ----------------------------------------------------------------------
# Resumen: total y lista de fallos.
total = ok + len(fallos)
# Separador.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{total} correctas")
# Separador.
print("=" * 78)
# Si hubo fallos, se listan y se sale con código 1.
if fallos:
    # Cabecera.
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        # El título y su detalle.
        print(f"  - {f}")
    # Código de salida 1: algo ha fallado.
    sys.exit(1)
