"""
Verificacion real de los modelos Pydantic de app/schemas/.

No comprueba que el archivo "se guarde sin error": construye modelos con
datos validos e invalidos y confirma que Pydantic acepta lo que debe
aceptar y RECHAZA lo que debe rechazar.
"""

import sys
from pathlib import Path

# La raiz del repo calculada a partir de la ubicacion de este archivo,
# para poder hacer "import app.schemas..." desde fuera del proyecto.
# Este script vive en scripts/, pero el paquete "app" esta en la raiz del
# repositorio. Al ejecutar "python scripts/<archivo>.py", Python solo anade
# la carpeta del archivo (scripts/) a sys.path, asi que "import app.algo"
# fallaria con ModuleNotFoundError. Se calcula la raiz a partir de la
# ubicacion de este mismo archivo, en vez de escribir una ruta absoluta,
# para que funcione en cualquier maquina y desde cualquier directorio.
# Misma tecnica que scripts/check_graceful_shutdown.py.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
# Los mensajes de error del telefono llevan tildes ("teléfono no válido")
# y uno de los casos usa digitos arabes; sin esto, una consola cp1252
# reventaria al imprimirlos. Misma linea que en los demas check_*.py.
sys.stdout.reconfigure(encoding="utf-8")

from pydantic import ValidationError

from decimal import Decimal

from app.schemas.common import MAX_FOTOS_LEAD, MAX_M2_LEAD, NivelAcabados, TipoReforma
from app.schemas.leads import (
    LeadCreate,
    LeadCreateResponse,
    LeadPhotoUploadResponse,
    PhotoUploadSlot,
)

ok = 0
fallos = []


def debe_aceptar(titulo, modelo, datos, comprobacion=None):
    """Construye el modelo y espera que NO lance error."""
    global ok
    try:
        objeto = modelo(**datos)
    except ValidationError as e:
        fallos.append(f"{titulo}: deberia ACEPTARSE y fue rechazado -> {e}")
        print(f"  [FALLO] {titulo}")
        return
    if comprobacion is not None:
        resultado = comprobacion(objeto)
        if resultado is not True:
            fallos.append(f"{titulo}: aceptado pero {resultado}")
            print(f"  [FALLO] {titulo} -> {resultado}")
            return
    ok += 1
    print(f"  [OK]    {titulo}")


def debe_rechazar(titulo, modelo, datos, texto_esperado=None):
    """Construye el modelo y espera que SI lance error."""
    global ok
    try:
        modelo(**datos)
    except ValidationError as e:
        mensaje = str(e).replace("\n", " ")
        if texto_esperado and texto_esperado not in mensaje:
            fallos.append(f"{titulo}: rechazado, pero sin '{texto_esperado}' -> {mensaje}")
            print(f"  [FALLO] {titulo} (motivo inesperado)")
            return
        ok += 1
        # Primera linea util del error, para ver el motivo real.
        motivo = mensaje.split("[type=")[0].strip()[-110:]
        print(f"  [OK]    {titulo}\n            -> {motivo}")
        return
    fallos.append(f"{titulo}: deberia RECHAZARSE y fue aceptado")
    print(f"  [FALLO] {titulo} FUE ACEPTADO")


LEAD_VALIDO = {
    "nombre": "Gabriela Gomez",
    "email": "gabi@example.com",
    "telefono": "+34600111222",
    "tipo_reforma": "bano",
    "m2": 8.5,
    "nivel_acabados": "medio",
    "incluye_cambios_estructurales": False,
    "fotos": ["leads-temp/tok123/foto1.jpg", "leads-temp/tok123/foto2.jpg"],
    "lead_token": "tok123",
}


def sin_clave(*claves):
    """Copia del lead valido quitando las claves indicadas."""
    copia = dict(LEAD_VALIDO)
    for c in claves:
        copia.pop(c)
    return copia


def con(**cambios):
    """Copia del lead valido cambiando/anadiendo lo que se indique."""
    copia = dict(LEAD_VALIDO)
    copia.update(cambios)
    return copia


print("\n=== A. LO QUE DEBE ACEPTARSE ===")
debe_aceptar(
    "Lead completo con 2 fotos",
    LeadCreate,
    LEAD_VALIDO,
    lambda o: True if o.tipo_reforma is TipoReforma.BANO else f"tipo_reforma={o.tipo_reforma}",
)
debe_aceptar(
    "Lead SIN fotos (campo ausente) -> fotos = []",
    LeadCreate,
    sin_clave("fotos"),
    lambda o: True if o.fotos == [] else f"fotos={o.fotos}",
)
debe_aceptar(
    "Lead con fotos = [] explicita",
    LeadCreate,
    con(fotos=[]),
    lambda o: True if o.fotos == [] else f"fotos={o.fotos}",
)
debe_aceptar(
    "Lead con exactamente MAX_FOTOS_LEAD fotos",
    LeadCreate,
    con(fotos=[f"leads-temp/tok123/f{i}.jpg" for i in range(MAX_FOTOS_LEAD)]),
    lambda o: True if len(o.fotos) == MAX_FOTOS_LEAD else f"len={len(o.fotos)}",
)
# ACTUALIZADO 2026-09-20: m2 pasa de float a Decimal, con tope le=500.
# La asercion antigua exigia isinstance(o.m2, float) y empezo a fallar al
# aplicar el tope. No es un fallo: es el contrato nuevo. Decimal se eligio
# por lo mismo que en los importes -float guarda en binario y pierde
# exactitud- y Pydantic convierte pasando por el texto, asi que "45.5"
# llega como Decimal('45.5') exacto.
debe_aceptar(
    'm2 como texto "45.5" -> Pydantic lo convierte a Decimal exacto',
    LeadCreate,
    con(m2="45.5"),
    lambda o: True if o.m2 == Decimal("45.5") and isinstance(o.m2, Decimal) else f"m2={o.m2!r}",
)
debe_aceptar(
    f"m2 = {MAX_M2_LEAD} exacto (el tope es INCLUSIVO)",
    LeadCreate,
    con(m2=MAX_M2_LEAD),
    lambda o: True if o.m2 == Decimal(MAX_M2_LEAD) else f"m2={o.m2!r}",
)
debe_aceptar(
    "Los 4 tipos y 3 niveles reales de tarifas_base se aceptan",
    LeadCreate,
    con(tipo_reforma="integral_vivienda", nivel_acabados="alto"),
    lambda o: True if o.nivel_acabados is NivelAcabados.ALTO else "enum mal",
)
# --- Telefono (2026-09-24): normalizacion + formato ^\+?[0-9]{9,15}$ ---
# Lo que se comprueba en cada caso no es solo "se acepta", sino el valor
# que queda GUARDADO en el modelo: debe salir ya limpio.
for crudo, esperado in [
    ("+34 666-777-444", "+34666777444"),   # el caso del enunciado
    ("666 777 444", "666777444"),
    ("(+34) 666.777.444", "+34666777444"),
    ("666777444", "666777444"),            # 9 digitos: el minimo
    ("+" + "1" * 15, "+" + "1" * 15),      # 15 digitos: el maximo
    # \s cubre tambien el salto de linea, el tabulador y el espacio duro
    # (U+00A0, el que aparece al copiar un numero de una web): se limpian
    # como cualquier espacio y se guarda el numero limpio.
    ("666777444\n", "666777444"),
    ("666 777 444", "666777444"),
]:
    debe_aceptar(
        f"telefono {crudo!r} -> se guarda normalizado como {esperado!r}",
        LeadCreate,
        con(telefono=crudo),
        # Esta lambda "captura" esperado en el momento de crearla gracias
        # a esperado=esperado; sin ese truco, todas usarian el ultimo
        # valor del bucle.
        lambda o, esperado=esperado: True if o.telefono == esperado else f"telefono={o.telefono!r}",
    )
# --- Email en minusculas (Fase 2) ---
# EmailStr solo baja el DOMINIO; el validador email_en_minusculas baja
# tambien la parte de antes de la @.
debe_aceptar(
    "email 'Pepe@Gmail.com' -> se guarda como 'pepe@gmail.com' (entero en minusculas)",
    LeadCreate,
    con(email="Pepe@Gmail.com"),
    lambda o: True if o.email == "pepe@gmail.com" else f"email={o.email!r}",
)
debe_aceptar(
    "lead_token de 100 caracteres exactos (el limite es inclusivo)",
    LeadCreate,
    con(lead_token="t" * 100),
    lambda o: True if len(o.lead_token) == 100 else f"len={len(o.lead_token)}",
)
debe_aceptar(
    "LeadCreateResponse con creado = False (repeticion con el mismo token)",
    LeadCreateResponse,
    {"lead_id": 1, "cliente_id": 1, "oportunidad_id": 1,
     "status": "pendiente_aprobacion", "creado": False},
    lambda o: True if o.creado is False else f"creado={o.creado!r}",
)
debe_aceptar(
    "PhotoUploadSlot valido",
    PhotoUploadSlot,
    {"path": "leads-temp/tok123/f1.jpg", "signed_upload_url": "https://x/y", "expires_in": 3600},
)
debe_aceptar(
    "LeadPhotoUploadResponse sin slots -> lista vacia",
    LeadPhotoUploadResponse,
    {"lead_token": "tok123"},
    lambda o: True if o.photo_slots == [] else f"slots={o.photo_slots}",
)
debe_aceptar(
    "LeadCreateResponse con los 3 ids enteros + status",
    LeadCreateResponse,
    {"lead_id": 1, "cliente_id": 1, "oportunidad_id": 1, "status": "nueva", "creado": True},
    lambda o: True if o.oportunidad_id == 1 else f"oportunidad_id={o.oportunidad_id}",
)

print("\n=== B. LO QUE DEBE RECHAZARSE ===")
debe_rechazar("m2 = 0", LeadCreate, con(m2=0), "greater_than")
debe_rechazar("m2 negativo (-5)", LeadCreate, con(m2=-5), "greater_than")
# El tope nuevo (MAX_M2_LEAD). Sin el, un lead de 60.000 m2 se aceptaba
# aqui y reventaba despues al calcular el presupuesto, porque importe_max
# no cabe en NUMERIC(10,2). Verificado con ejecucion real antes de poner
# el tope.
debe_rechazar(f"m2 = {MAX_M2_LEAD}.01 (un pelo por encima del tope)", LeadCreate,
              con(m2=float(MAX_M2_LEAD) + 0.01), "less_than_equal")
debe_rechazar("m2 = 60000 (el caso que provocaba un 500)", LeadCreate,
              con(m2=60000), "less_than_equal")
debe_rechazar('tipo_reforma = "bano" CON ENIE ("bano" mal escrito)', LeadCreate, con(tipo_reforma="ba\u00f1o"), "enum")
debe_rechazar('nivel_acabados = "basico" CON TILDE', LeadCreate, con(nivel_acabados="b\u00e1sico"), "enum")
debe_rechazar('tipo_reforma inventado ("tejado")', LeadCreate, con(tipo_reforma="tejado"), "enum")
debe_rechazar("email sin arroba", LeadCreate, con(email="gabi.example.com"))
debe_rechazar("email vacio", LeadCreate, con(email=""))
debe_rechazar("email de mas de 150 caracteres", LeadCreate, con(email="a" * 145 + "@example.com"))
debe_rechazar("nombre vacio", LeadCreate, con(nombre=""), "at least 1")
debe_rechazar("nombre de mas de 150 caracteres", LeadCreate, con(nombre="x" * 151), "at most 150")
debe_rechazar("telefono de mas de 30 caracteres", LeadCreate, con(telefono="9" * 31), "at most 30")
# Telefonos con forma invalida. Se exige que el rechazo lleve el mensaje
# propio del validador ("teléfono no válido"), para no dar por bueno un
# rechazo por otro motivo. Que el error nombre el campo 'telefono' se
# comprueba aparte, en la seccion C.
for malo, motivo in [
    ("telefono666555", "letras delante (el hallazgo original)"),
    ("66677", "solo 5 digitos"),
    ("666abc444", "letras en medio"),
    ("12345678", "8 digitos, uno menos del minimo"),
    ("+" + "1" * 16, "16 digitos, uno mas del maximo"),
    ("++34666777444", "dos signos +"),
    ("34+666777444", "+ en medio"),
    ("٦٦٦٧٧٧٤٤٤", "digitos arabes (trampa de \\d)"),
    ("- . ( )", "solo decoracion: queda vacio"),
]:
    debe_rechazar(f"telefono {malo!r} ({motivo})", LeadCreate, con(telefono=malo),
                  "teléfono no válido")
debe_rechazar(
    f"{MAX_FOTOS_LEAD + 1} fotos (una mas del maximo)",
    LeadCreate,
    con(fotos=[f"f{i}.jpg" for i in range(MAX_FOTOS_LEAD + 1)]),
    "too_long",
)
debe_rechazar("falta incluye_cambios_estructurales", LeadCreate, sin_clave("incluye_cambios_estructurales"), "missing")
debe_rechazar("falta lead_token", LeadCreate, sin_clave("lead_token"), "missing")
debe_rechazar("lead_token vacio", LeadCreate, con(lead_token=""), "at least 1")
# Fase 2: lead_token se guarda en leads.lead_token VARCHAR(100).
debe_rechazar("lead_token de 101 caracteres (ancho de la columna)", LeadCreate,
              con(lead_token="t" * 101), "at most 100")
debe_rechazar(
    "LeadCreateResponse SIN creado (campo obligatorio)",
    LeadCreateResponse,
    {"lead_id": 1, "cliente_id": 1, "oportunidad_id": 1, "status": "nueva"},
    "missing",
)
debe_rechazar('campo de mas: "telefono_movil" (extra=forbid)', LeadCreate, con(telefono_movil="600"), "extra_forbidden")
debe_rechazar("m2 no numerico ('mucho')", LeadCreate, con(m2="mucho"))
debe_rechazar(
    "PhotoUploadSlot con expires_in = 0",
    PhotoUploadSlot,
    {"path": "p", "signed_upload_url": "u", "expires_in": 0},
    "greater_than",
)
debe_rechazar(
    f"LeadPhotoUploadResponse con {MAX_FOTOS_LEAD + 1} slots",
    LeadPhotoUploadResponse,
    {
        "lead_token": "tok",
        "photo_slots": [
            {"path": "p", "signed_upload_url": "u", "expires_in": 60}
            for _ in range(MAX_FOTOS_LEAD + 1)
        ],
    },
    "too_long",
)
debe_rechazar(
    "LeadCreateResponse con lead_id no entero",
    LeadCreateResponse,
    {"lead_id": "no-soy-un-numero", "cliente_id": 1, "oportunidad_id": 1, "status": "nueva", "creado": True},
)

# --- PASO 4: casos nuevos para oportunidad_id (ampliacion del contrato D1) ---
debe_rechazar(
    "LeadCreateResponse SIN oportunidad_id (campo obligatorio)",
    LeadCreateResponse,
    {"lead_id": 1, "cliente_id": 1, "status": "nueva", "creado": True},
    "missing",
)
debe_rechazar(
    "LeadCreateResponse con oportunidad_id = None",
    LeadCreateResponse,
    {"lead_id": 1, "cliente_id": 1, "oportunidad_id": None, "status": "nueva", "creado": True},
)
debe_rechazar(
    "LeadCreateResponse con oportunidad_id no entero",
    LeadCreateResponse,
    {"lead_id": 1, "cliente_id": 1, "oportunidad_id": "abc", "status": "nueva", "creado": True},
)

print("\n=== C. COMPROBACIONES DE COMPORTAMIENTO ===")
a = LeadCreate(**sin_clave("fotos"))
b = LeadCreate(**sin_clave("fotos"))
a.fotos.append("intrusa.jpg")
if b.fotos == []:
    ok += 1
    print("  [OK]    default_factory: cada lead tiene su PROPIA lista (b.fotos sigue vacia)")
else:
    fallos.append("default_factory compartido entre instancias")
    print(f"  [FALLO] listas compartidas -> b.fotos={b.fotos}")

lead = LeadCreate(**LEAD_VALIDO)
serializado = lead.model_dump(mode="json")
if serializado["tipo_reforma"] == "bano" and serializado["nivel_acabados"] == "medio":
    ok += 1
    print("  [OK]    Al serializar a JSON los enums salen como texto plano:")
    print(f"            tipo_reforma={serializado['tipo_reforma']!r} nivel_acabados={serializado['nivel_acabados']!r}")
else:
    fallos.append(f"serializacion de enums inesperada: {serializado}")
    print(f"  [FALLO] serializacion -> {serializado}")

# El error de un telefono invalido debe NOMBRAR el campo: es lo que el
# Agente 1 usa para pedir solo esa correccion. e.errors() devuelve la
# lista de errores como diccionarios; "loc" es la ruta del campo.
try:
    LeadCreate(**con(telefono="telefono666555"))
except ValidationError as e:
    locs = [err["loc"] for err in e.errors()]
    if locs == [("telefono",)]:
        ok += 1
        print(f"  [OK]    El error de telefono invalido nombra el campo: loc={locs}")
    else:
        fallos.append(f"loc inesperado en error de telefono: {locs}")
        print(f"  [FALLO] loc inesperado -> {locs}")
else:
    # Rama else del try: solo se ejecuta si NO salto ninguna excepcion.
    # Sin ella, un validador desactivado haria que esta comprobacion no
    # contara ni como OK ni como FALLO: pasaria en silencio (regla D16).
    fallos.append("telefono666555 NO lanzo ValidationError: la excepcion se ha tragado")
    print("  [FALLO] telefono666555 fue aceptado: no salto ninguna excepcion")

print("\n" + "=" * 60)
print(f"RESULTADO: {ok} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for f in fallos:
        print(" - " + f)
    sys.exit(1)
print("Todas las comprobaciones han pasado.")
