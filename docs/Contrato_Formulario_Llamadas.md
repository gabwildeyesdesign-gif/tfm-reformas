# Reformas Integrales Amedida — Contrato para el formulario de llamadas (n8n)

**Para quién:** Gabi, para ampliar el formulario de n8n (D25.10) sin leer el código.
**Backend:** `POST /resultados-llamada` (Adenda 5.10) y la ficha `GET /oportunidades/{id}/ficha` (Adenda 5.9).
**Origen:** P15 de `docs/Plan_Resultado_Llamadas.txt`; rama `feat/n0-resultado-llamadas` (2026-10-11/12).
**Mientras tanto:** el formulario publicado sigue llamando a `POST /gate-decisions`, que es ahora un ALIAS con su
contrato de siempre (solo casos con Gate). Cuando este formulario ampliado esté publicado, el alias se quita en otra
rama (D26.6).

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

Ningún otro campo: uno de más da 422.

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

`sin_decision` solo existe en la ruta nueva: el formulario publicado (alias) no lo admite.

### Ejemplos

```json
{"oportunidad_id": 1522, "resultado": "visita_acordada", "fecha": "2026-10-19", "hora": "10:00", "informe": "Acepta visita el lunes."}
{"oportunidad_id": 1523, "resultado": "descartar", "motivo": "sin_decision", "informe": "Tercera llamada; sigue sin decidirse."}
{"oportunidad_id": 1530, "resultado": "confirmar_hora", "fecha": "2026-10-13", "hora": "11:00", "informe": "Confirma la hora que pidió."}
{"oportunidad_id": 1531, "resultado": "otra_hora", "fecha": "2026-10-13", "hora": "17:00", "informe": "Prefiere por la tarde."}
{"oportunidad_id": 1532, "resultado": "cliente_cancela", "motivo": "plazo", "informe": "Ya no le corre prisa; cancela."}
```

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
| `visita_cancelada_id` | La pedida que se canceló (`otra_hora`, `cliente_cancela`), o `null`. |
| `creado` | `true` en el 201, `false` en el 200. |

Nunca lleva importes.

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
| 422 | `misma_hora_pedida` | "Es la hora que pidió: elige 'confirmar la hora pedida'." | No |
| 422 | (lista de Pydantic, sin `motivo`) | Error del formulario (un campo mal enviado). | **Sí** |
| 401 | — | "Error de configuración" (la credencial). | **Sí** |
| 404 | `oportunidad_no_encontrada` | "Oportunidad no encontrada." | No |
| 503 | `configuracion_incompleta` | "Sistema no disponible: avisa al responsable técnico." (`faltan` dice qué regla) | **Sí** |
| 500 / sin conexión | — | "Sistema no disponible." | **Sí** (como D27.6) |

Reintentos de n8n: solo ante 500 o sin conexión (un reintento que llega dos veces da 200, no duplica nada). Nunca
ante 4xx ni 503.

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
- El ALIAS POST /gate-decisions (el formulario de hoy) responde exactamente igual, con el mismo cuerpo, en
  `visita_acordada` (P17): el formulario publicado tiene que enseñarlo también.

## 5. Lo que este formulario NO hace (N0)

- "Me lo pienso" no se registra: el caso sigue en la lista diaria al día siguiente (D25.14). La salida prevista es
  el aviso de "última llamada" (D29.2, bloque 4c) y, en ese momento, `descartar` con `sin_decision`. `aplazar` y el
  registro de intentos de llamada quedan para N1 (D29.1, D24.4).
  *(Nota 2026-10-12: D30 revisa D29.2: el aviso de "última llamada" no se hace en el backend (no hay bloque 4c),
  sino en n8n. El documento de D30 lo tiene el tutor en el Proyecto de claude.ai.)*
- No corrige un resultado ya registrado ni cambia una visita ya confirmada (a mano en N0).
- No identifica al operador (la llave es compartida).
