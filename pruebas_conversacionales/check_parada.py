# =============================================================================
# check_parada.py — Prueba SIN RED de la regla de parada de ejecutar_caso() (H-180-1).
# La regla para la conversación cuando un "no"/"si"/"sí" llega sin resumen antes,
# pero solo si ese mensaje es el ÚLTIMO del caso. Se cambian post_mensaje y
# recibir_respuesta por funciones falsas y se comprueban 4 casos.
# Uso (Git Bash, en la raíz del proyecto):
#   PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe pruebas_conversacionales/check_parada.py
#   PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe pruebas_conversacionales/check_parada.py <otra copia del ejecutor>
# Sale con código 0 si todo está bien y con 1 si algún caso falla.
# =============================================================================

import importlib.util      # cargar el ejecutor desde una ruta concreta (la actual o una copia antigua)
import sys                 # leer el argumento y salir con el código adecuado
from pathlib import Path   # rutas independientes del sistema

RUTA_DEFECTO = Path(__file__).with_name("ejecutar_conversaciones.py")  # el ejecutor que está junto a este script
RUTA = Path(sys.argv[1]) if len(sys.argv) > 1 else RUTA_DEFECTO        # o el que se pase como argumento
PARADA = "PARADA: no hubo resumen antes de este mensaje"               # texto exacto que anota la regla de parada

especificacion = importlib.util.spec_from_file_location("ejecutor_probado", RUTA)  # prepara la carga del archivo
ejecutor = importlib.util.module_from_spec(especificacion)             # crea el módulo vacío
especificacion.loader.exec_module(ejecutor)                            # ejecuta el archivo (solo define funciones; main() no se llama)


def red_prohibida(*args, **kwargs):                                     # sustituto de cualquier acceso a la red
    raise RuntimeError("RED PROHIBIDA: la prueba ha intentado conectarse")  # si se usa, el turno sale como (ERROR...) y la prueba falla


ejecutor.urllib.request.urlopen = red_prohibida                        # bloquea el POST real, por si quedara algún camino
ejecutor.websockets.connect = red_prohibida                            # bloquea el WebSocket real

enviados: list[str] = []                                               # mensajes que el ejecutor habría enviado, en orden
respuestas: list[str] = []                                             # respuestas falsas del bot, una por turno enviado


def post_falso(url_webhook: str, sesion: str, texto: str) -> str:      # misma firma que post_mensaje
    enviados.append(texto)                                             # apunta lo que se habría enviado
    return f"ej-{len(enviados)}"                                       # un executionId inventado


async def recibir_falso(url_webhook: str, sesion: str, ejecucion: str, espera_max: float) -> list[str]:  # misma firma que recibir_respuesta
    turno = int(ejecucion.split("-")[1])                               # el número de envío sale del executionId inventado
    return [respuestas[turno - 1]]                                     # la respuesta preparada para ese envío


ejecutor.post_mensaje = post_falso                                     # el ejecutor buscará esta función al enviar
ejecutor.recibir_respuesta = recibir_falso                             # y esta al esperar la respuesta

ok, fallos = 0, 0                                                      # contadores de comprobaciones


def comprobar(nombre: str, condicion: bool, detalle: str) -> None:     # anota y enseña una comprobación
    global ok, fallos                                                  # modifica los contadores de arriba
    if condicion:                                                      # se cumple
        ok += 1
        print(f"  [OK]    {nombre}")
    else:                                                              # no se cumple: se enseña qué pasó de verdad
        fallos += 1
        print(f"  [FALLO] {nombre} -> {detalle}")


def lanzar(mensajes: list[str], bot: list[str]) -> list[dict]:         # ejecuta un caso con respuestas preparadas
    enviados.clear()                                                   # empieza sin envíos anteriores
    respuestas[:] = bot                                                # carga las respuestas de este caso
    caso = {"id": "check-parada", "mensajes": mensajes}                # caso mínimo, sin comprobaciones propias
    return ejecutor.ejecutar_caso("http://falso.invalid/webhook/x", caso, 1.0, None)  # sin email: variantes=None


print(f"Ejecutor probado: {RUTA}")                                     # deja constancia de qué archivo se ha probado

# CASO 1: "no" en mitad, como respuesta a una pregunta (los tabiques). NO debe parar.
filas = lanzar(["quiero reformar el baño", "no", "6 m2, acabados medios"],
               ["¿Vas a tirar algún tabique?", "Entendido. ¿Cuántos metros tiene?", "Perfecto, anotado."])
paradas = [f for f in filas if f["fallos"] == PARADA]                  # filas marcadas con la parada
comprobar("caso 1: \"no\" en mitad no para y se envía el mensaje siguiente",
          not paradas and enviados == ["quiero reformar el baño", "no", "6 m2, acabados medios"],
          f"paradas={len(paradas)}, enviados={enviados}")

# CASO 2: "si" final sin resumen antes. SÍ debe parar, sin enviarlo.
filas = lanzar(["quiero reformar la cocina", "si"], ["¿Cuántos metros tiene la cocina?"])
comprobar("caso 2: \"si\" final sin resumen para con el texto de PARADA",
          filas[-1]["fallos"] == PARADA and enviados == ["quiero reformar la cocina"],
          f"última fila={filas[-1]['fallos']!r}, enviados={enviados}")

# CASO 3: "si" final con el resumen ("Antes de guardar nada") justo antes. NO debe parar.
filas = lanzar(["cocina de 10 m2, acabados medios, sin tabiques", "si"],
               ["Antes de guardar nada, revisa estos datos: ...", "Según los datos que nos has facilitado..."])
comprobar("caso 3: \"si\" final con resumen antes no para y se envía",
          all(f["fallos"] != PARADA for f in filas) and enviados[-1:] == ["si"],
          f"fallos={[f['fallos'] for f in filas]}, enviados={enviados}")

# CASO 4: "no" final sin resumen antes. SÍ debe parar.
filas = lanzar(["quiero pintar el salón", "no"], ["¿Cuántos metros tiene el salón?"])
comprobar("caso 4: \"no\" final sin resumen para con el texto de PARADA",
          filas[-1]["fallos"] == PARADA and enviados == ["quiero pintar el salón"],
          f"última fila={filas[-1]['fallos']!r}, enviados={enviados}")

print(f"\nRESULTADO: {ok}/{ok + fallos} correctas")                     # recuento final
sys.exit(0 if fallos == 0 else 1)                                      # código de salida para quien lo lance
