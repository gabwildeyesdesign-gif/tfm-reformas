# Reformas Integrales Amedida — Decisiones N0
## D17 — Cierre del nodo MCP Client (paso determinista post-cálculo) y discrepancia crítica encontrada en `connection.py`

**Sesión:** 2026-09-23 / 2026-09-24. Continúa directamente desde el cierre de D16 (captura conversacional
del lead, `POST /leads` verificado extremo a extremo desde n8n). Esta sesión aborda el siguiente bloque
pendiente de D16: el cierre determinista tras `calculate_estimate` (D16.5, Opción C — "sin nodo AI Agent
nuevo").

---

### D17.1 — Depuración del nodo `MCP Client`: de "Invalid expression" a ejecución correcta

Cronología real de los errores encontrados y resueltos, en orden (cada uno confirmado con ejecución real,
no solo lectura):

1. **Conectar `MCP Client` directamente a la salida normal del AI Agent, sin condición**, dispara el nodo
   en CADA turno de la conversación, no solo cuando se acaba de guardar el lead — rompe el flujo normal
   del chat. (Detectado al inicio de esta sesión, antes de rediseñar la condición de disparo — ver D17.2.)
2. **`Invalid expression`**, primera causa: se intentó leer `oportunidad_id` con
   `$('guardar_datos_reforma').item.json...`, una referencia cruzada a un nodo-tool colgado del puerto de
   herramientas del AI Agent. Esa referencia no es fiable desde fuera del propio Agente — el nodo-tool no
   se ejecuta como un nodo normal de la cadena principal, vive dentro de la orquestación interna del
   Agente.
3. **Solución adoptada para leer el dato: activar "Return Intermediate Steps" en el nodo AI Agent.** Con
   esa opción, el propio `$json` de salida del Agente incluye `intermediateSteps`, un array con
   `{action: {tool, toolInput, ...}, observation}` por cada tool llamada en ese turno. Forma real
   verificada (no asumida): `observation` es un **string JSON**, no un objeto —
   `"[{\"lead_id\":282,\"cliente_id\":252,\"oportunidad_id\":243,\"status\":\"nueva\"}]"` — hace falta
   `JSON.parse()` antes de poder leer `oportunidad_id`.
4. **`Invalid input for 'oportunidad_id'`**, con el valor numérico ya correcto (243) confirmado en la
   vista previa de la expresión: el modo **"Manual"** de "Values to Send" en el nodo `MCP Client` enviaba
   el valor como **string**, incompatible con el tipo `int` que exige la tool `calculate_estimate`
   (`Field(gt=0)` en `app/mcp_server/server.py`). **Solución verificada: cambiar "Input Mode" de "Manual" a
   "JSON"**, escribiendo el cuerpo completo a mano con la expresión sin comillas alrededor del número, lo
   que preserva el tipo JSON numérico en vez de forzarlo a texto.

**Resultado final verificado con ejecución real:** `calculate_estimate` devuelve correctamente
`oportunidad_id: 243`, `presupuesto_id: 140`, `importe_min_con_iva: "4840.00"`,
`importe_max_con_iva: "5566.00"`, `motivo_gate: null`, `requiere_aprobacion: false`,
`status: "presupuesto_enviado"`, `creado: true`.

---

### D17.2 — Regla de Tres: condición de disparo del `MCP Client` (cerrada y verificada)

| Opción | Descripción | Evaluación |
|---|---|---|
| A | Referenciar el nodo-tool por nombre con un método tipo `.isExecuted` | Descartada: depende de una API de expresiones de n8n no verificable de memoria, y de una referencia cruzada a un nodo que vive dentro del subgrafo del Agente |
| **B** | **Activar "Return Intermediate Steps" en el AI Agent; comprobar la presencia de la tool llamada dentro de `$json.intermediateSteps` del propio Agente** | **Elegida y verificada** |
| C | Consultar la base de datos en cada turno para comprobar si hay una oportunidad reciente sin presupuesto | Descartada: coste de una petición extra en turnos que no tienen nada que ver con guardar el lead; duplica un dato que el propio workflow ya tiene disponible |

**Implementación final del nodo `If`:** tipo de condición **Number** (no Boolean — ver más abajo por qué),
expresión:

```
{{ $json.intermediateSteps ? $json.intermediateSteps.filter(s => s.action.tool === 'guardar_datos_reforma').length : 0 }}
```
operador **"Larger than"**, valor de comparación fijo `0`.

**Por qué Number y no Boolean:** con tipo Boolean y operador "Is True" apareció el error
`wrong type: '=true' is a string but was expecting a boolean`, repetido incluso tras reconfigurar el
operador — indicio de un residuo de estado en la UI de n8n (2.16.1) al cambiar de operador, no de un error
de diseño. No se persiguió la causa exacta: se cambió a una condición numérica, que evita por completo la
ambigüedad string/boolean de los toggles de esa UI y además es directamente visible en el panel de
ejecución (se ve el número, no solo un interruptor).

**Verificado con ejecución real:** rama `True` (turno donde el Agente llama a `guardar_datos_reforma`)
dispara correctamente el `MCP Client` con el resultado completo de arriba.

**Rama `False` (turno sin llamada a la tool): sin verificar todavía.** Ver D17.3.

---

### D17.3 — Rama `False` del `If`: decisión y pendiente de verificación

El usuario planteó reconectar la rama `False` a "la entrada del LLM" (el AI Agent o el Chat Model). **Se
desaconsejó explícitamente:** el item de esa rama ya trae `$json.output` con la respuesta final que el
Agente generó en ese turno. Volver a pasarla por el modelo:

- gasta una llamada de API para un turno que ya estaba resuelto;
- arriesga una respuesta divergente de la que ya se decidió (¿cuál ve el cliente, la primera o la
  segunda?);
- abre la puerta a un bucle si esa segunda pasada por el Agente vuelve a entrar por el mismo `If`.

**Decisión:** la rama `False` no debe volver a tocar el Agente. Debe llegar directamente al mismo punto de
salida/convergencia final que la rama `True` (tras el nodo de cierre determinista), sin reprocesar nada.

**Pendiente de verificación real:** confirmar en ejecución que un turno sin llamada a la tool sigue
respondiendo con normalidad en la ventana de chat con este cableado.

---

### D17.4 — Cambio de diseño: Code node en vez de Switch + Set para el cierre determinista

En esta sesión se propuso inicialmente Switch (una expresión de "caso") + un Set por caso, justificado por
Regla de Tres con la visibilidad de la lógica de negocio en el lienzo como criterio decisivo (relevante
para un TFM que se presenta como portfolio). El usuario decidió sustituirlo por un único Code node,
justificándolo así (cita literal): *"creo que es mucho mas eficiente un nodo con codigo... Hay que mostrar
conocimiento, si hace falta se explica que hace dicho codigo."*

**Objeción registrada para la memoria del TFM, porque es relevante para la defensa:** "mostrar
conocimiento" no es un criterio de ingeniería — es el mismo razonamiento que llevaría a complicar
cualquier cosa para parecer más capaz, y es lo contrario del criterio que este mismo proyecto ya defendió
en D12 (cerrar el motor de diálogo propio por sobrecoste sin valor funcional) y en D16.5 (retirar el
Agente 2 en vez de mantenerlo solo para justificar el uso de IA). Un TFM de ingeniería se evalúa por saber
elegir la herramienta correcta y justificarla, no por la cantidad de código propio que contiene.

Dicho esto, la conclusión sí tiene un argumento de ingeniería legítimo, distinto del que se usó para
justificarla: la lógica a implementar es una función pura y pequeña (`{status, requiere_aprobacion,
importe_min_con_iva, importe_max_con_iva}` → un texto), y consolidarla en un único bloque comentado línea a
línea —coherente con el propio estilo de explicación de este proyecto (ver `CLAUDE.md`, "Cómo trabajamos
en este proyecto")— puede ser más mantenible que repartir la misma lógica entre una expresión de Switch y
cuatro nodos Set separados en el lienzo.

**Decisión final para la memoria del TFM:** usar el Code node, pero justificarlo en la defensa por la
cohesión de la función pura y la consistencia con el estilo de comentario del proyecto — nunca por
"aparentar más dominio técnico". **Pendiente: el Code node no se ha escrito todavía en esta sesión.**

Los cuatro casos que debe cubrir (deducidos del contrato real de `EstimateResponse`, `app/schemas/estimates.py`,
y del enum `MotivoGate`, `app/schemas/common.py` — no probados los tres primeros con datos reales todavía):

1. `status === 'requiere_revision'` → no se pudo calcular; sin importe.
2. `requiere_aprobacion === true` y `importe_min_con_iva === null` → Gate por cambios estructurales
   (`motivo_gate` es `cambios_estructurales` o `ambos`); sin cifra que mostrar.
3. `requiere_aprobacion === true` y el importe SÍ viene relleno → Gate solo por `importe_superior_umbral`;
   se puede mostrar el rango, pero avisando de que queda pendiente de aprobación.
4. `requiere_aprobacion === false` → caso normal, el único probado hasta ahora con datos reales.

---

### D17.5 — HALLAZGO CRÍTICO: discrepancia entre D16 y el estado real de `app/db/connection.py`

D16 (sección "Hallazgo de robustez del backend") afirma como hecho verificado:

> *"Corregido en sesión (`app/db/connection.py`, línea ~197): se añadió el mismo `if not conn.closed:`
> antes del `conn.rollback()` en `get_transactional_connection()`."*

**Verificado leyendo el archivo real en esta sesión: ese cambio NO está en el código actual.**
`get_transactional_connection()` sigue llamando a `conn.rollback()` en su bloque `except Exception:` sin
ninguna comprobación `if not conn.closed:` previa — exactamente el mismo patrón que D16 dice haber
corregido. El propio comentario que acompaña esa línea describe el riesgo aceptado de no tener la guarda
("si la conexión se perdiera del todo, este rollback podría fallar; aun así el `putconn()` del `finally`
se ejecuta igualmente..."), lo que indica que el comentario documentando el riesgo se conservó, pero el fix
descrito en D16 nunca llegó a aplicarse al archivo — o se aplicó y se perdió después (edición no guardada,
checkout, revert; no determinado).

**Contraste:** `get_db_connection()` (la función gemela de solo lectura) **sí tiene** la guarda
`if not conn.closed:` en su bloque `finally` — confirma que el patrón se implementó correctamente ahí, y
hace más extraño que no se replicara (o se perdiera) en la función de escritura, que es la que D16 dice
haber corregido explícitamente.

**Por qué esto es grave y no un simple olvido de una línea:** es una discrepancia entre documentación y
código verificable, del mismo tipo que el proyecto ya se ha comprometido a evitar ("Si algo se contradice
con lo que dice este archivo [CLAUDE.md], dilo explícitamente antes de asumir nada"). Un TFM que se
presenta como ejercicio de ingeniería rigurosa no puede tener una decisión cerrada en su propia
documentación que el código contradice.

**Pendiente inmediato, antes de dar por cerrado nada más relacionado con D16 o D17:**
1. Decidir si se vuelve a aplicar el fix ahora (añadir `if not conn.closed:` antes de la línea 197 de
   `get_transactional_connection()`) y se corrige la fecha/estado en D16, o
2. si merece la pena investigar primero por qué el cambio "desapareció" (para descartar que sea síntoma de
   un problema mayor de control de versiones o de guardado de archivos en el entorno de desarrollo).

---

## Estado real de la implementación al cierre de esta sesión

| Pieza | Estado |
|---|---|
| Nodo `If` (condición de disparo, Regla de Tres D17.2) | ✅ Construido y verificado con ejecución real en la rama `True` |
| Nodo `MCP Client` → `calculate_estimate` (rama `True`) | ✅ Verificado con ejecución real (`oportunidad_id` 243, `presupuesto_id` 140, caso sin Gate) |
| Rama `False` del `If` | ⏸️ Decisión de diseño tomada (D17.3), sin verificar con ejecución real; NO reconectar al Agente |
| Code node de cierre determinista (D17.4) | ⏸️ Decidido, no escrito todavía |
| Casos de Gate (con y sin cambios estructurales) | ⏸️ Sin probar con datos reales — solo se ha verificado el caso sin Gate |
| `app/db/connection.py::get_transactional_connection()` | ⚠️ **Discrepancia crítica sin resolver (D17.5)** — el fix que D16 da por hecho no está en el archivo |

---

## Pendiente para la próxima sesión

1. **Prioridad alta — resolver la discrepancia D17.5** en `connection.py` (decidir entre las dos opciones
   planteadas) y corregir D16 para que refleje el estado real.
2. Escribir el Code node de cierre determinista (D17.4), con los 4 casos ya identificados, comentado línea
   a línea según el estilo del proyecto.
3. Verificar con ejecución real la rama `False` del `If` (D17.3): que el chat responde con normalidad en un
   turno sin llamada a la tool, sin volver a pasar por el Agente.
4. Verificar con ejecución real al menos un caso de Gate (`requiere_aprobacion = true`), con y sin cambios
   estructurales — hasta ahora solo se ha probado el caso sin Gate.
5. Confirmar empíricamente qué campo lee de verdad el Chat Trigger de n8n para mostrar la respuesta final
   en la ventana de chat cuando la ejecución no termina directamente en el AI Agent — se ha asumido el
   campo `output` por convención, sin verificarlo.
6. Seguir con los pendientes ya abiertos de D16: guion de pruebas de regresión formal, confirmar el modelo
   usado en las pruebas D16.2/D16.4/D16.9/D16.11, y corregir la Adenda para incluir `lead_token` en el
   contrato de `POST /leads`.

## Referencias
- D16: `claude/Decisiones_D16_Agente_Conversacional_Captura_n8n.md`
- D12: `claude/Decisiones_D12_Cierre_Opcion3_Motor_Dialogo_Propio.md`
- `app/mcp_server/server.py`, `app/schemas/estimates.py`, `app/schemas/common.py`, `app/db/connection.py`
