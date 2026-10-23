# Reformas Integrales Amedida — Contrato para el formulario de llamadas (n8n)

**Para quién:** Gabi, para ampliar el formulario de n8n (D25.10) sin leer el código.
**Backend:** `POST /resultados-llamada` (Adenda 5.10) y la ficha `GET /oportunidades/{id}/ficha` (Adenda 5.9).
**Origen:** P15 de `docs/Plan_Resultado_Llamadas.txt`; rama `feat/n0-resultado-llamadas` (2026-10-11/12).
**Estado:** el formulario publicado (`WF_formulario_llamadas`) llama a `POST /resultados-llamada`; es la única ruta.
**Cambio 2026-10-22: alias POST /gate-decisions quitado (bloque 5); versión anterior en aba39c4.** `POST /gate-decisions` responde ahora 404 (D26.6).

---

## 1. La llave

Las dos peticiones llevan la cabecera **`X-Gate-Secret`** con la credencial "Header Auth" de administración (la
misma del formulario del Gate). **Nunca** la del Agente 2 (`X-Webhook-Secret`): con ella, las dos dan 401.

## 2. Antes de enseñar nada: leer la ficha

`GET /oportunidades/{oportunidad_id}/ficha` (la misma que la página de la ficha, D26.8). De la respuesta, el
formulario solo necesita estos campos:

| Campo | Para qué |
|---|---|
| `llamada_registrable` | **Decide las páginas.** `null` = no hay nada que registrar ahora. Si no, `{tipo_llamada, resultados}`. |
| `llamadas` | Si no está vacía, ya hay un resultado (es definitivo): enseñar "ya registrado" con su resultado y su fecha. |
| `contacto` | Nombre y teléfono para llamar (`origen = "no_disponible"`: la solicitud no los trae). |
| `visitas` | En una confirmación, la visita con `estado_visita = "solicitada"`: su `fecha_visita` es la hora PEDIDA (la que hay que enviar en `confirmar_hora`) y su `texto_cliente`, lo que dijo el cliente. |

`llamada_registrable` la calcula el backend con la MISMA regla que usa al registrar: el formulario no tiene que
deducir nada.

**[Nota 2026-10-20, D28.1]** La ficha trae también `resumen_solicitud` (texto o `null`, en la raíz): el resumen que
escribió el Agente 1 sobre lo que quiere el cliente. El formulario NO lo necesita para decidir nada; si lo enseña al
operador, con `esc()` y como "Resumen del asistente" (orientativo; `null` en las solicitudes anteriores al
2026-10-20). Contrato: Adenda, 5.12.

**[Nota 2026-10-22, D42]** La ficha trae también `direccion_obra` (en la raíz): `{calle_numero, codigo_postal,
municipio}` o `null`. Es CONFIDENCIAL: la ficha es la única lectura que la devuelve. El formulario no la necesita para
decidir nada (la pregunta siempre que quede una visita, sección 3); la página de la ficha la enseñará al técnico, con
`esc()`. Contrato: Adenda, 5.13.

| `tipo_llamada` | Cuándo | `resultados` |
|---|---|---|
| `gate` | Caso con Gate pendiente | `visita_acordada`, `descartar` |
| `seguimiento` | Seguimiento abierto, o por abrir con el plazo de 48 h cumplido | `visita_acordada`, `descartar` |
| `confirmacion_visita` | El cliente pidió una visita desde el chat | `confirmar_hora`, `otra_hora`, `cliente_cancela` |

Gate y seguimiento usan **las mismas páginas** (D25.10). La única rama nueva es la de confirmación.

## 3. Qué se envía

`POST /resultados-llamada`, cuerpo JSON. **No** se envía el tipo de llamada (lo deduce el backend).

| Campo | Tipo | Notas |
|---|---|---|
| `oportunidad_id` | entero > 0 | El de la ficha. |
| `resultado` | texto | Uno de `resultados`. |
| `fecha` | `"YYYY-MM-DD"` | Ver la tabla de abajo. Hora de Madrid. |
| `hora` | `"HH:MM"` | Inicio de la visita, hora de Madrid. Sin segundos ni zona (`"10:00Z"` da 422). |
| `motivo` | texto | Ver la tabla de motivos. |
| `informe` | texto | **Siempre.** 1 a 2000 caracteres (sin contar espacios de los extremos). Para la auditoría anual. |
| `direccion_obra` | objeto o `null` | (D42, desde el 2026-10-22, rama feat/n0-direccion-obra) La dirección de la obra: ver abajo. |

Ningún otro campo: uno de más da 422.

### `direccion_obra` (D42): la dirección de la obra, CONFIDENCIAL

El técnico necesita la dirección; administración la pregunta en la llamada. **En el formulario es OBLIGATORIA** cuando
queda una visita confirmada (`visita_acordada`, `confirmar_hora`, `otra_hora`) (D42.2). En la API es **opcional**
(veredicto del tutor, P1 = A): si el formulario no la envía, la visita se registra igual, la ficha da `null` y el log
dice `"direccion_obra_registrada": false`. Con `descartar` o `cliente_cancela`, un objeto da **422** (no queda ninguna
visita); `"direccion_obra": null` vale lo mismo que no enviarla, con cualquier resultado.

| Clave | Tipo | Regla (422 si no se cumple) |
|---|---|---|
| `calle_numero` | texto | Calle y número (y piso, si se quiere). 1 a 150 caracteres, sin contar espacios de los extremos. |
| `codigo_postal` | **texto** | 5 cifras españolas con provincia 01-52 (`^(0[1-9]\|[1-4][0-9]\|5[0-2])[0-9]{3}$`). Siempre entre comillas: `28013` como número da 422 (un `01001` perdería el cero). |
| `municipio` | texto | 1 a 100 caracteres, sin contar espacios de los extremos. |

Ninguna otra clave dentro del objeto (una de más, por ejemplo `piso`, da 422). Se guarda en la misma transacción que la
visita (todo o nada). **La respuesta NO la devuelve nunca**, y no sale en ninguna otra lectura (lista diaria, aviso del
Gate, sesión, tools del agente, logs): solo en la ficha, `direccion_obra` (D42.5).

### Qué campos lleva cada resultado

| `resultado` | `fecha` y `hora` | `motivo` | Efecto |
|---|---|---|---|
| `visita_acordada` | **obligatorias** (la visita acordada) | prohibido | Visita nueva confirmada; oportunidad → `visita_agendada` |
| `descartar` | prohibidas | **obligatorio** | Oportunidad → `perdida` |
| `confirmar_hora` | **obligatorias**: la hora PEDIDA, tal cual sale en la ficha | prohibido | La visita pedida pasa a confirmada |
| `otra_hora` | **obligatorias**: la NUEVA hora (distinta de la pedida) | prohibido | La pedida se cancela y se crea la nueva, confirmada |
| `cliente_cancela` | prohibidas | **obligatorio** | La visita pedida se cancela; oportunidad → `perdida` (D25.11) |

### Motivos (`descartar` y `cliente_cancela`)

| Valor | Etiqueta propuesta | En `descartar` | En `cliente_cancela` |
|---|---|---|---|
| `precio` | Precio: le parece caro | sí | sí |
| `plazo` | Plazo: no le encajan los tiempos | sí | sí |
| `proyecto_no_viable` | Proyecto no viable técnicamente | sí | sí |
| `otro` | Otro (explícalo en el informe) | sí | sí |
| `no_contesta` | No contesta (tras varios intentos, D24.3) | sí | **no** (quien cancela, contestó) |
| `sin_decision` | Sigue sin decidirse en la última llamada (D29.3) | sí | **no** (quien cancela, ha decidido) |

`sin_decision` es un motivo más del catálogo (el alias, ya quitado, no lo admitía).

### Ejemplos

```json
{"oportunidad_id": 1522, "resultado": "visita_acordada", "fecha": "2026-10-19", "hora": "10:00", "informe": "Acepta visita el lunes."}
{"oportunidad_id": 1523, "resultado": "descartar", "motivo": "sin_decision", "informe": "Tercera llamada; sigue sin decidirse."}
{"oportunidad_id": 1530, "resultado": "confirmar_hora", "fecha": "2026-10-13", "hora": "11:00", "informe": "Confirma la hora que pidió."}
{"oportunidad_id": 1531, "resultado": "otra_hora", "fecha": "2026-10-13", "hora": "17:00", "informe": "Prefiere por la tarde."}
{"oportunidad_id": 1532, "resultado": "cliente_cancela", "motivo": "plazo", "informe": "Ya no le corre prisa; cancela."}
{"oportunidad_id": 1533, "resultado": "visita_acordada", "fecha": "2026-10-19", "hora": "11:00", "informe": "Acepta visita.", "direccion_obra": {"calle_numero": "Calle Ejemplo 12, 3º B", "codigo_postal": "28013", "municipio": "Madrid"}}
```

(El último ejemplo, D42: la dirección es inventada.)

Franjas de visita (de `reglas_negocio`, hoy): mañana de 08:30 a 13:30 y tarde de 17:00 a 20:00, visitas de 60 min
que tienen que empezar y terminar dentro de la franja, de lunes a viernes y en el futuro.

## 4. Qué responde y qué enseñar

**201** (registrado) o **200** (era una repetición exacta: no se ha escrito nada; normal si n8n reintenta). Cuerpo:

| Campo | Contenido |
|---|---|
| `llamada_id` | Id del resultado. |
| `oportunidad_id` | El enviado. |
| `tipo_llamada` | El deducido. |
| `resultado`, `motivo` | Los registrados (en un 200, los que ya estaban). |
| `estado_oportunidad` | `visita_agendada` o `perdida`. |
| `visita_id`, `fecha_visita` | La visita que queda CONFIRMADA (`null` en `descartar` y `cliente_cancela`). `fecha_visita` con su desfase: `"2026-10-19T10:00:00+02:00"`. |
| `fecha_fin_visita` | (D33, desde el 2026-10-14) Su FIN: `fecha_visita` + `duracion_visita_min` de `reglas_negocio`, con el mismo formato y desfase (`"2026-10-19T11:00:00+02:00"`); `null` cuando `fecha_visita` es `null`. Es la hora de fin del evento de Google Calendar de WF5: **n8n no suma la duración por su cuenta**. Va también en el 200. |
| `visita_cancelada_id` | La pedida que se canceló (`otra_hora`, `cliente_cancela`), o `null`. |
| `creado` | `true` en el 201, `false` en el 200. |

Nunca lleva importes. (D42) Tampoco la dirección de la obra: las claves de la respuesta no cambian.

(Nota 2026-10-22, D42, limitaciones de N0.)
- **Una dirección ya registrada no se puede corregir por la API; en N1 se editaría desde la ficha.** El resultado de
  una llamada es definitivo: la repetición exacta (200) no mira `direccion_obra` (como el informe) ni la escribe, y
  otro resultado da 409 `resultado_ya_registrado`.
- **El 422 repite lo enviado.** FastAPI devuelve en el 422 lo que se envió (`"input"`), también la dirección. No se
  guarda en ninguna tabla. El formulario publicado solo enseña "El sistema ha rechazado el formato de los datos" y
  WF4 recibe el código y su título, nunca el cuerpo (leído por el tutor en `n8n/WF_formulario_llamadas.json`,
  f1301af). El historial de ejecuciones de n8n guarda lo escrito en el formulario, como ya guarda el nombre y el
  teléfono: es una limitación de n8n, no del backend.

(Nota 2026-10-14, D33.) `POST /visits` lleva también `fecha_fin_visita` (la visita pedida desde el chat, que WF5 pone como
PENDIENTE). Limitación: el fin se calcula con la duración VIGENTE; si administración cambia
`duracion_visita_min`, un 200 posterior trae el fin nuevo (la tabla `visitas` no guarda el fin).

### Errores: `{"detail": {"motivo": "...", "mensaje": "..."}}`

El `mensaje` lo escribe el backend para administración y se puede enseñar tal cual. Qué hacer con cada `motivo`:

| Código | `motivo` | Qué enseñar | ¿Aviso técnico (WF4)? |
|---|---|---|---|
| 409 | `resultado_ya_registrado` | "Esta llamada ya tiene un resultado registrado (es definitivo)." | No |
| 409 | `resultado_no_admitido` | "La situación del cliente ha cambiado desde que abriste la ficha: vuelve a abrirla." | No |
| 409 | `visita_cambiada` | "El cliente ha cambiado la hora pedida: vuelve a abrir la ficha y confírmale la nueva." | No |
| 409 | `estado_no_permitido` | "Esta oportunidad no tiene ninguna llamada pendiente." | No |
| 409 | `plazo_no_cumplido`, `contacto_registrado`, `visita_existente`, `con_gate` | "Esta oportunidad no está en el seguimiento ahora mismo." (+ `mensaje`) | No |
| 409 | `sin_presupuesto` | "Todavía no tiene presupuesto." | No |
| 409 | `visita_activa_existente` | "Ya tiene una visita activa." | No |
| 409 | `hueco_ocupado` | "Esa hora ya está ocupada por otra visita." + las horas de `huecos_libres` (ver abajo), y volver a la página de la fecha. | No |
| 422 | `fecha_pasada`, `fin_de_semana`, `fuera_de_franja` | El `mensaje` (dice las franjas) y volver a la página de la fecha. | No |
| 422 | `antelacion_insuficiente` (D45, desde el 2026-10-23) | El `mensaje` ("La primera fecha posible para la visita es el <día> <dd/mm>.") y volver a la página "Día". Con `visita_acordada` y `otra_hora` la fecha tiene que ser, como pronto, el día laborable siguiente a hoy; no debería salir si el día viene de GET /huecos-libres, salvo una página abierta antes de medianoche y enviada después. `confirmar_hora` no la recibe nunca. OJO: el formulario del plan B (WF_formulario_llamadas, que no se cambia) la trataría como error técnico. | No |
| 422 | `fuera_de_rejilla` (D32, desde el 2026-10-13) | No debería salir nunca si las horas vienen de GET /huecos-libres (sección 4b). Si sale: el `mensaje` (dice las horas válidas; también van aparte en `horas_validas`) y volver a la página "Día". | **Sí** (indica un fallo del formulario) |
| 422 | `misma_hora_pedida` | "Es la hora que pidió: elige 'confirmar la hora pedida'." | No |
| 422 | (lista de Pydantic, sin `motivo`) | Error del formulario (un campo mal enviado). | **Sí** |
| 401 | — | "Error de configuración" (la credencial). | **Sí** |
| 404 | `oportunidad_no_encontrada` | "Oportunidad no encontrada." | No |
| 503 | `configuracion_incompleta` | "Sistema no disponible: avisa al responsable técnico." (`faltan` dice qué regla) | **Sí** |
| 500 / sin conexión | — | "Sistema no disponible." | **Sí** (como D27.6) |

Reintentos de n8n: solo ante 500 o sin conexión (un reintento que llega dos veces da 200, no duplica nada). Nunca
ante 4xx ni 503.
(Nota 2026-10-14, D33, P1: la repetición de un resultado CON visita lee las reglas de visita para calcular
`fecha_fin_visita`; si están rotas, da el mismo 503 `configuracion_incompleta` que el registro, no un 200. Sin
visita, la repetición sigue dando 200.)

### 409 `hueco_ocupado` (D31, desde el 2026-10-12, rama feat/n0-agente2)

Con `visita_acordada`, `confirmar_hora` y `otra_hora`, la visita no puede solaparse con OTRA visita activa
(`solicitada` o `confirmada`, de cualquier cliente): un solo técnico, y una visita ocupa de su hora de inicio a esa
hora más la duración (60 min). La visita pedida por el propio cliente no cuenta: `confirmar_hora` la confirma y
`otra_hora` la cancela. Cuerpo exacto (el único error con una clave más, `huecos_libres`):

```json
{"detail": {"motivo": "hueco_ocupado",
            "mensaje": "Esa hora no está disponible: coincide con otra visita. Horas de inicio libres ese día: 08:30, 09:00, 11:00, 11:30, 12:00, 12:30, 17:00, 17:30, 18:00, 18:30, 19:00.",
            "huecos_libres": ["08:30", "09:00", "11:00", "11:30", "12:00", "12:30", "17:00", "17:30", "18:00", "18:30", "19:00"]}}
```

- `huecos_libres`: horas de inicio de ESE día que siguen libres, cada 30 min (regla `paso_huecos_min`), con la
  visita entera dentro de su franja y ya en el futuro. Lista vacía si no queda ninguna; entonces el `mensaje` es
  "Esa hora no está disponible y ese día no queda ninguna hora libre: propón otro día."
- Qué enseñar a administración: "Esa hora ya está ocupada por otra visita. Horas libres ese día: 08:30, 09:00, …"
  (las de `huecos_libres`, o "ninguna: elige otro día" si está vacía), y volver a la página de la fecha con el
  mismo resultado. No se escribe nada: se puede volver a enviar con otra hora. No es un aviso técnico (WF4 no).
- El mensaje nunca dice de quién es la otra visita ni a qué hora termina.

*(Nota 2026-10-14, D32: el ejemplo de arriba es de D31. Desde D32, `huecos_libres` ya no va cada 30 min: son las
horas libres de la rejilla (con las reglas de hoy, 08:30, 09:45, 11:00, 12:15, 17:00 y 18:15) que no chocan con
otra visita contando 15 min de desplazamiento; la forma del cuerpo no cambia. Con GET /huecos-libres (sección 4b)
este 409 solo sale si alguien coge la hora entre la página "Hora" y el envío: el formulario vuelve entonces a la
página "Día", recalculada.)*

## 4b. Elegir día y hora: GET /huecos-libres (D32, desde el 2026-10-13)

Para `visita_acordada` y `otra_hora` (en `confirmar_hora` la hora ya la pidió el cliente). Las visitas empiezan a
horas fijas (la rejilla) y el formulario ofrece SOLO las libres, así administración no se entera de que una hora
estaba ocupada cuando el cliente ya ha colgado. Misma credencial (`X-Gate-Secret`). Solo lectura: no escribe nada.

**Por qué son dos páginas.** El formulario de n8n no ejecuta JavaScript (el campo HTML no admite `<script>`) ni
tiene botón "Atrás": una página no puede cambiar sus opciones según lo que se elige en otra. Por eso el día se
elige en una página y la hora en la siguiente, que se construye al abrirla. Y la página "Hora" vuelve a preguntar
al backend (no reutiliza lo de la página "Día"): durante la llamada otro cliente, desde el chat, puede haber
cogido una hora, y no se le puede ofrecer a este.

### Página "Día": `GET /huecos-libres` (sin parámetros)

```json
{"modo": "dias",
 "generado_en": "2026-10-14T09:12:03+02:00",
 "dias": [
   {"fecha": "2026-10-15", "dia_semana": "jueves",
    "manana": ["09:45", "11:00"], "tarde": ["18:15"], "num_horas_libres": 3,
    "etiqueta": "Jueves 15/10 — mañana: 09:45 · 11:00 — tarde: 18:15"},
   ...]}
```

- Los próximos `dias_oferta_visita` (10) días de lunes a viernes, DESDE EL DÍA LABORABLE SIGUIENTE A HOY (D45,
  desde el 2026-10-23: `dias_antelacion_visita_admin` = 1; antes, desde hoy), que tienen al menos una hora libre;
  los días completos no salen. Si no hay ninguno, `"dias": []`.
- `manana` y `tarde`: siempre presentes; lista vacía si esa franja no tiene horas ese día.
- `etiqueta`: el texto EXACTO de la opción. Una franja sin horas no se escribe ("Jueves 15/10 — tarde: 17:00 ·
  18:15"). Es única: lleva la fecha.
- Construcción del campo (Define Form: Using JSON): un `dropdown` con
  `fieldOptions.values = [{"option": d.etiqueta} para cada día] + [{"option": "Otro día más adelante"}]`.
  El valor que devuelve el desplegable es el TEXTO de la opción: n8n busca el día por `etiqueta` en la lista que ya
  tiene y toma su `fecha`. "Otro día más adelante" es un texto fijo (no choca con ninguna etiqueta, que empiezan por
  el nombre de un día) y lleva a una página con un campo `date`; esa fecha va a la página "Hora".

### Página "Hora": `GET /huecos-libres?fecha=AAAA-MM-DD` (al abrirla)

```json
{"modo": "dia",
 "generado_en": "2026-10-14T09:13:40+02:00",
 "fecha": "2026-10-15", "dia_semana": "jueves",
 "manana": ["09:45", "11:00"], "tarde": ["18:15"],
 "horas": ["09:45", "11:00", "18:15"],
 "sin_horas_motivo": null}
```

- `horas`: mañana y tarde juntas, en orden: las opciones de un campo `radio`, tal cual (`{"option": "09:45"}`...).
- Siempre se añade la opción fija **"Ninguna le va bien → elegir otro día"** (no choca con una hora "HH:MM"), y el
  textarea del informe.
- Si `horas` viene vacía, la página SOLO ofrece "Ninguna le va bien → elegir otro día". `sin_horas_motivo` dice
  por qué (se puede enseñar en un campo `html`): `dia_completo` (alguien cogió la última durante la llamada),
  `fin_de_semana` o `fecha_pasada` (fechas del calendario de "Otro día más adelante"), y desde D45
  `antelacion_insuficiente` (hoy, o una fecha anterior al día laborable siguiente; texto propuesto: "Las visitas
  se acuerdan como pronto para el siguiente día laborable."). El formulario del plan B no conoce este valor y
  enseñaría su texto genérico.
- 422 solo si la fecha está mal escrita o no existe (por ejemplo `2026-02-30`): error del formulario (WF4 sí).

### El bucle "Ninguna le va bien" (opción (a), acordada el 2026-10-13)

Dentro de la MISMA ejecución: la rama "Ninguna le va bien → elegir otro día" vuelve al nodo de la página "Día", que
llama OTRA VEZ a `GET /huecos-libres` (los datos recalculados, nunca la lista de la primera vuelta). n8n tiene que
conservar `oportunidad_id` y el resultado elegido (`visita_acordada` u `otra_hora`) entre vueltas, y puede pasar el
informe ya escrito como `defaultValue` del textarea de la siguiente página "Hora" para no perderlo. El 409
`hueco_ocupado` del envío vuelve también a la página "Día", recalculada. La documentación de n8n no describe volver
a una página anterior: el bucle se comprueba al construirlo en n8n; el backend no cambia.

### Al enviar

`POST /resultados-llamada` con la `fecha` (de la etiqueta o del calendario) y la `hora` (la opción de radio). Si
alguien la cogió entre la página "Hora" y el envío: 409 `hueco_ocupado` (sección 4) y vuelta a "Día". Un 422
`fuera_de_rejilla` no debería salir nunca: si sale, el formulario está enviando una hora que no vino de la consulta.

## 5. Lo que este formulario NO hace (N0)

- "Me lo pienso" no se registra: el caso sigue en la lista diaria al día siguiente (D25.14). La salida prevista es
  el aviso de "última llamada" (D29.2, bloque 4c) y, en ese momento, `descartar` con `sin_decision`. `aplazar` y el
  registro de intentos de llamada quedan para N1 (D29.1, D24.4).
  *(Nota 2026-10-12: D30 revisa D29.2: el aviso de "última llamada" no se hace en el backend (no hay bloque 4c),
  sino en n8n. El documento de D30 lo tiene el tutor en el Proyecto de claude.ai.)*
- No corrige un resultado ya registrado ni cambia una visita ya confirmada (a mano en N0).
- No identifica al operador (la llave es compartida).
