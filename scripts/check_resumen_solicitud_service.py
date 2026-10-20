"""
Verificación SIN base de datos del resumen de la solicitud (D28.1; plan
docs/Plan_Resumen_Solicitud_D28.txt, secciones 4.4, 4.6, 5.1 y 7.1).

Qué comprueba:
  S1. evaluar_resumen (app/services/resumen_solicitud.py), un caso por
      comprobación: lo que se guarda (texto recortado o None) y su estado.
      Incluye comillas dobles y saltos de línea en medio (se guardan tal
      cual), 300 y 301 caracteres, el orden de los motivos y tres LÍMITES
      CONOCIDOS D37.1 (importes con guion que el patrón toma por teléfono):
      se esperan con su resultado de HOY; si alguien cambia el patrón, el
      script avisa.
  S2. estado_guardado (la repetición de POST /leads, P3).
  S3. Estructura, sin ejecutar nada de app/services con base de datos:
      introspección de los esquemas de Pydantic y ast sobre los servicios.
      El resumen está en LeadCreate (opcional), en la respuesta de POST
      /leads (estado obligatorio), en la raíz de GET /gate-avisos y de la
      ficha; NUNCA en ReformaAviso, en GET /leads/session ni en GET
      /llamadas-del-dia; COLUMNAS_CASO lo lleva; evaluar_resumen no tiene un
      300 escrito; el [AVISO] de POST /leads no usa el texto del resumen.

Sin base de datos ni servidor: las 4 preguntas de CLAUDE.md no aplican (no
escribe, no hay conexión, un corte no deja nada, no toca el 8000).

Recuento previsto: 38/38 (S1 23, S2 2, S3 13).

Código de salida: 0 si todo está bien, 1 si hay algún fallo.
"""

# ast: leer la estructura de los servicios sin importarlos (no abren nada).
import ast
# sys: sys.path, la salida estándar y el código de salida.
import sys
# typing.get_args: los tipos de dentro de "X | None" o "list[X]".
from typing import get_args
# Path: rutas.
from pathlib import Path

# La raíz del repositorio en sys.path (para "import app..."). En una copia de
# prueba en negativo, es la de la copia, y se importa SU app.
RAIZ_REPO = Path(__file__).resolve().parents[1]
# Primera en la lista de búsqueda de módulos.
sys.path.insert(0, str(RAIZ_REPO))
# Tildes correctas en la consola de Windows.
sys.stdout.reconfigure(encoding="utf-8")

# BaseModel: para reconocer los submodelos al recorrer los campos.
from pydantic import BaseModel

# Lo que se prueba: los esquemas (no abren ninguna conexión) y las dos
# funciones puras del resumen.
# Las respuestas de gate-avisos y de la ficha, y la "reforma" compartida.
from app.schemas.aviso_gate import AvisoGateResponse, ReformaAviso
from app.schemas.ficha_oportunidad import FichaOportunidadResponse
# El máximo de caracteres (el del CHECK de paso15).
from app.schemas.common import MAX_RESUMEN_SOLICITUD
# Los esquemas de POST /leads y de GET /leads/session, y el de la lista diaria.
from app.schemas.leads import EstadoResumenSolicitud, LeadCreate, LeadCreateResponse, LeadSessionResponse
from app.schemas.listado_llamadas import ListadoLlamadasResponse
# Las dos funciones puras del resumen.
from app.services.resumen_solicitud import estado_guardado, evaluar_resumen

# Atajos a los cinco estados, para que las tablas de casos se lean mejor:
# el bueno y el de "no llegó"...
GUARDADO = EstadoResumenSolicitud.GUARDADO
SIN = EstadoResumenSolicitud.SIN_RESUMEN
# ...y los tres descartes.
LONGITUD = EstadoResumenSolicitud.DESCARTADO_LONGITUD
EMAIL = EstadoResumenSolicitud.DESCARTADO_EMAIL
TELEFONO = EstadoResumenSolicitud.DESCARTADO_TELEFONO

# Contadores: comprobaciones correctas y lista de fallos.
ok, fallos = 0, []


def comprobar(titulo, condicion, detalle=""):
    """[OK] o [FALLO], y se cuenta."""
    # global: se cambia el contador de fuera.
    global ok
    # Correcta.
    if condicion:
        # Una más.
        ok += 1
        # Se enseña.
        print(f"  [OK]    {titulo}  {detalle}")
    # Incorrecta.
    else:
        # Para el resumen final.
        fallos.append(f"{titulo} {detalle}")
        # En su sitio.
        print(f"  [FALLO] {titulo}  {detalle}")


def caso(titulo, texto, esperado_texto, esperado_estado):
    """Llama a evaluar_resumen y compara el par (texto guardado, estado)."""
    # try: una excepción también es un FALLO contado, no una traza a medias.
    try:
        # Lo que devuelve la función.
        obtenido = evaluar_resumen(texto)
    # Cualquier error: FALLO con su nombre.
    except Exception as error:
        # Se cuenta y se sale de esta comprobación.
        comprobar(titulo, False, f"(excepción: {type(error).__name__}: {error})")
        return
    # El par esperado.
    esperado = (esperado_texto, esperado_estado)
    # El detalle enseña el estado y la longitud de lo guardado, no el texto;
    # si no llega un par (una versión rota), se enseña tal cual.
    if isinstance(obtenido, tuple) and len(obtenido) == 2:
        # Estado y longitud (None si no se guarda nada).
        detalle = f"(obtenido: {obtenido[1]}, guardado: {None if obtenido[0] is None else len(obtenido[0])} caracteres)"
    # No es un par.
    else:
        # Lo que haya llegado.
        detalle = f"(obtenido: {obtenido!r})"
    # Iguales: correcto.
    comprobar(titulo, obtenido == esperado, detalle)


def campos_recursivos(modelo, vistos=None):
    """Todos los nombres de campo de un modelo y de sus submodelos (también
    dentro de "X | None" y de "list[X]")."""
    # Modelos ya visitados, para no dar vueltas.
    vistos = vistos if vistos is not None else set()
    # Este modelo, una sola vez.
    if modelo in vistos:
        return set()
    # Se apunta.
    vistos.add(modelo)
    # Los nombres de este nivel.
    nombres = set(modelo.model_fields)
    # Cada campo: se buscan submodelos en su tipo.
    for campo in modelo.model_fields.values():
        # El tipo y los de dentro (un nivel de get_args basta en estos esquemas,
        # más el propio tipo; los list[X] | None se abren dos veces).
        pendientes = [campo.annotation]
        # Mientras queden tipos por mirar.
        while pendientes:
            # El siguiente.
            tipo = pendientes.pop()
            # Sus tipos internos, a la cola.
            pendientes.extend(get_args(tipo))
            # Un submodelo: sus campos también cuentan.
            if isinstance(tipo, type) and issubclass(tipo, BaseModel):
                nombres |= campos_recursivos(tipo, vistos)
    # Todos los nombres.
    return nombres


def valor_ast(ruta, nombre):
    """El valor (como objeto de Python) de una asignación de nivel de módulo,
    leído con ast sin importar el archivo. None si no está o no es literal."""
    # El árbol del archivo.
    arbol = ast.parse((RAIZ_REPO / ruta).read_text(encoding="utf-8"))
    # Las sentencias de nivel de módulo.
    for nodo in arbol.body:
        # Una asignación a ese nombre.
        if isinstance(nodo, ast.Assign) and any(getattr(t, "id", None) == nombre for t in nodo.targets):
            # literal_eval: convierte el valor si es un literal (tuplas de textos).
            try:
                return ast.literal_eval(nodo.value)
            # No es un literal: no se puede leer así.
            except ValueError:
                return None
    # No existe.
    return None


# ======================================================================
print("S1. evaluar_resumen: qué se guarda y con qué estado")
# ======================================================================
# Un texto válido de referencia (el ejemplo de D28.1), y uno de 300.
VALIDO = "Cambiar la bañera por un plato de ducha y alicatar hasta el techo"
TRESCIENTOS = "á" * MAX_RESUMEN_SOLICITUD
# Sin resumen: ausente, vacío y solo espacios.
caso("None -> sin_resumen", None, None, SIN)
caso("'' -> sin_resumen", "", None, SIN)
caso("solo espacios, tabulador y salto de línea -> sin_resumen", "  \t\n  ", None, SIN)
# Válidos: tal cual, y recortados solo en los extremos.
caso("texto válido -> guardado tal cual", VALIDO, VALIDO, GUARDADO)
caso("espacios en los extremos -> guardado recortado", f"   {VALIDO}  \n", VALIDO, GUARDADO)
# Comillas dobles y saltos de línea EN MEDIO: se guardan tal cual (encargo 2).
caso('con comillas dobles en medio -> guardado tal cual', 'Quiere un plato "extraplano" y mampara',
     'Quiere un plato "extraplano" y mampara', GUARDADO)
caso("con un salto de línea en medio -> guardado tal cual (solo se recortan los extremos)",
     "  Cambiar la bañera.\nAlicatar hasta el techo.  ", "Cambiar la bañera.\nAlicatar hasta el techo.", GUARDADO)
# Longitud: exactamente 300 se guarda; 301 no; 300 con espacios alrededor sí
# (se mide después de recortar).
caso(f"exactamente {MAX_RESUMEN_SOLICITUD} caracteres ('á') -> guardado", TRESCIENTOS, TRESCIENTOS, GUARDADO)
caso(f"{MAX_RESUMEN_SOLICITUD + 1} caracteres -> descartado_longitud", "x" * (MAX_RESUMEN_SOLICITUD + 1), None, LONGITUD)
caso(f"{MAX_RESUMEN_SOLICITUD} caracteres con espacios alrededor -> guardado (se mide tras recortar)",
     f"  {TRESCIENTOS}  ", TRESCIENTOS, GUARDADO)
# Datos de contacto.
caso("con un email -> descartado_email", "escribir a ana.lopez@correo.es para el presupuesto", None, EMAIL)
caso("con un teléfono '666 777 444' -> descartado_telefono", "llamar al 666 777 444 por la tarde", None, TELEFONO)
caso("con '+34 666-777-444' -> descartado_telefono", "su móvil es +34 666-777-444", None, TELEFONO)
caso("con '(666) 777.444' -> descartado_telefono", "contacto (666) 777.444", None, TELEFONO)
# Orden de los motivos: un solo motivo, el primero que falla.
caso("con email Y teléfono a la vez -> descartado_email (el email se mira antes)",
     "ana@correo.es o el 666 777 444", None, EMAIL)
caso(f"{MAX_RESUMEN_SOLICITUD + 1} caracteres con un email -> descartado_longitud (la longitud va antes)",
     "ana@correo.es " + "x" * (MAX_RESUMEN_SOLICITUD + 1 - len("ana@correo.es ")), None, LONGITUD)
# Sin falsos positivos en lo habitual de un resumen: fechas, horas, medidas.
caso("fecha y hora '19/10/2026 a las 10:00' -> guardado", "visita el 19/10/2026 a las 10:00",
     "visita el 19/10/2026 a las 10:00", GUARDADO)
caso("medidas 'unos 12 m2 y 2 ventanas' -> guardado", "unos 12 m2 y 2 ventanas", "unos 12 m2 y 2 ventanas", GUARDADO)
caso("importe sin guion '15.000 euros' -> guardado", "presupuesto de 15.000 euros", "presupuesto de 15.000 euros",
     GUARDADO)
# LÍMITE CONOCIDO D37.1, no un fallo: un rango de importes con guion junta
# 9 o más cifras con separadores y el patrón de teléfono lo marca. Se espera
# el resultado de HOY (no se cambia el patrón, P6; se corrige en el prompt
# del Agente 1 después del merge). Si alguien cambia el patrón, estos tres
# casos lo dirán.
caso("límite conocido D37.1, no un fallo: '15.000-20.000 euros' -> descartado_telefono",
     "presupuesto 15.000-20.000 euros", None, TELEFONO)
caso("límite conocido D37.1, no un fallo: '8.000 - 10.000 €' -> descartado_telefono",
     "entre 8.000 - 10.000 €", None, TELEFONO)
caso("límite conocido D37.1, no un fallo: 'entre 15.000 y 20.000 euros' -> guardado",
     "entre 15.000 y 20.000 euros", "entre 15.000 y 20.000 euros", GUARDADO)
# Un texto que no es str (lo que nunca debería llegar, porque Pydantic ya
# exige texto): se comprueba solo que el tipo del resultado es el par.
comprobar("evaluar_resumen devuelve siempre un par (texto o None, EstadoResumenSolicitud)",
          all(isinstance(evaluar_resumen(t), tuple) and isinstance(evaluar_resumen(t)[1], EstadoResumenSolicitud)
              for t in (None, "", VALIDO, "x" * 400)))

# ======================================================================
print("\nS2. estado_guardado: la repetición describe lo GUARDADO (P3)")
# ======================================================================
# Con texto en la columna: guardado.
comprobar("columna con texto -> guardado", estado_guardado(VALIDO) == GUARDADO, f"({estado_guardado(VALIDO)})")
# Con NULL: sin_resumen.
comprobar("columna NULL -> sin_resumen", estado_guardado(None) == SIN, f"({estado_guardado(None)})")

# ======================================================================
print("\nS3. Estructura: dónde está el resumen y dónde NO")
# ======================================================================
# El campo de entrada: existe, no es obligatorio y vale None por defecto.
campo = LeadCreate.model_fields.get("resumen_solicitud")
comprobar("LeadCreate.resumen_solicitud existe y NO es obligatorio (el chat publicado no lo envía)",
          campo is not None and not campo.is_required(), f"({campo and campo.is_required()})")
# Su valor por defecto.
comprobar("LeadCreate.resumen_solicitud vale None por defecto", campo is not None and campo.default is None,
          f"({campo and campo.default!r})")
# La respuesta: el estado existe y es obligatorio.
campo = LeadCreateResponse.model_fields.get("resumen_solicitud_estado")
comprobar("LeadCreateResponse.resumen_solicitud_estado existe y es obligatorio",
          campo is not None and campo.is_required())
# Los cinco valores exactos del estado (los del plan, 5.1).
comprobar("EstadoResumenSolicitud tiene exactamente los 5 valores del plan",
          {e.value for e in EstadoResumenSolicitud} == {"guardado", "sin_resumen", "descartado_longitud",
                                                       "descartado_email", "descartado_telefono"},
          f"({sorted(e.value for e in EstadoResumenSolicitud)})")
# En la RAÍZ de gate-avisos y de la ficha (P9), obligatorio (puede ser null).
comprobar("AvisoGateResponse.resumen_solicitud en la raíz",
          "resumen_solicitud" in AvisoGateResponse.model_fields
          and AvisoGateResponse.model_fields["resumen_solicitud"].is_required())
comprobar("FichaOportunidadResponse.resumen_solicitud en la raíz",
          "resumen_solicitud" in FichaOportunidadResponse.model_fields
          and FichaOportunidadResponse.model_fields["resumen_solicitud"].is_required())
# NUNCA en "reforma" (la comparte /leads/session), ni en ningún nivel de la
# sesión ni de la lista diaria.
comprobar("ReformaAviso NO tiene ningún campo con 'resumen' (C7)",
          not any("resumen" in n for n in ReformaAviso.model_fields), f"({sorted(ReformaAviso.model_fields)})")
# Todos los niveles de la sesión.
nombres = campos_recursivos(LeadSessionResponse)
comprobar("GET /leads/session: ningún campo con 'resumen' en ningún nivel",
          not any("resumen" in n for n in nombres), f"({sorted(n for n in nombres if 'resumen' in n)})")
# Todos los niveles de la lista diaria.
nombres = campos_recursivos(ListadoLlamadasResponse)
comprobar("GET /llamadas-del-dia: ningún campo con 'resumen' en ningún nivel (D26 1.3)",
          not any("resumen" in n for n in nombres), f"({sorted(n for n in nombres if 'resumen' in n)})")
# Las columnas que piden la sesión y la lista diaria (por nombre, con ast).
nombres_sesion = valor_ast("app/services/leads_service.py", "NOMBRES_REFORMA_SESION")
nombres_lista = valor_ast("app/services/listado_llamadas_service.py", "NOMBRES_SOLICITUD")
comprobar("NOMBRES_REFORMA_SESION y NOMBRES_SOLICITUD no piden el resumen",
          nombres_sesion is not None and nombres_lista is not None
          and "resumen_solicitud" not in nombres_sesion and "resumen_solicitud" not in nombres_lista,
          f"({nombres_sesion} | {nombres_lista})")
# COLUMNAS_CASO lo lleva (así lo leen gate-avisos y la ficha).
columnas = valor_ast("app/services/datos_oportunidad.py", "COLUMNAS_CASO")
comprobar("COLUMNAS_CASO lleva ('resumen_solicitud', 'l.resumen_solicitud')",
          columnas is not None and ("resumen_solicitud", "l.resumen_solicitud") in columnas)
# El máximo y que evaluar_resumen no tenga un 300 escrito a mano.
arbol = ast.parse((RAIZ_REPO / "app/services/resumen_solicitud.py").read_text(encoding="utf-8"))
numeros = [n.value for n in ast.walk(arbol) if isinstance(n, ast.Constant) and n.value == MAX_RESUMEN_SOLICITUD]
usa_constante = any(isinstance(n, ast.Name) and n.id == "MAX_RESUMEN_SOLICITUD" for n in ast.walk(arbol))
comprobar(f"MAX_RESUMEN_SOLICITUD = 300 y resumen_solicitud.py usa la constante, sin un {MAX_RESUMEN_SOLICITUD} escrito",
          MAX_RESUMEN_SOLICITUD == 300 and not numeros and usa_constante,
          f"(máximo {MAX_RESUMEN_SOLICITUD}, escritos {len(numeros)}, usa la constante {usa_constante})")
# El [AVISO] de create_lead: los nombres que usa la llamada a print que lo
# escribe; no puede usar ni "data" (el cuerpo) ni "resumen" (el texto).
arbol = ast.parse((RAIZ_REPO / "app/services/leads_service.py").read_text(encoding="utf-8"))
avisos = [n for n in ast.walk(arbol)
          if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "print"
          and "resumen_solicitud no guardado" in ast.unparse(n)]
usados = {x.id for a in avisos for x in ast.walk(a) if isinstance(x, ast.Name)}
comprobar("el [AVISO] de POST /leads existe y no usa el texto del resumen (ni 'data' ni 'resumen')",
          len(avisos) == 1 and "data" not in usados and "resumen" not in usados,
          f"({len(avisos)} avisos; nombres usados: {sorted(usados)})")

# Resumen y código de salida.
print("\n" + "=" * 78)
# El recuento.
print(f"RESULTADO: {ok}/{ok + len(fallos)} correctas")
# Separador.
print("=" * 78)
# Con fallos, se listan y código 1.
if fallos:
    # Cabecera.
    print("FALLOS:")
    # Uno por línea.
    for f in fallos:
        # Con su detalle.
        print(f"  - {f}")
    # Código 1.
    sys.exit(1)
