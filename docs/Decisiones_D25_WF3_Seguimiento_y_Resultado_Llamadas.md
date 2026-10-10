# Reformas Integrales Amedida — Decisiones N0
## D25 — WF3 (barridos periódicos), seguimiento y resultado de las llamadas

**Sesión:** 2026-10-07. **Cerrado el 2026-10-07** (queda abierta solo A4, para el 15 de octubre).
**Base:** D13 (seguimiento 48 h por barrido periódico), D21.7 (recordatorio del Gate en WF3), D24.3 y D24.4
(no contesta e intentos de llamada), Adenda fila 5 y sección 2 (`create-followup-task`).

---

## 1. Hallazgos de la revisión (con su fuente)

1. **El seguimiento de 48 h de WF3 es `POST /create-followup-task`** (Adenda, fila 5 y sección 2). Hoy son dos archivos
   con solo un docstring (`app/api/followup_tasks.py`, `app/services/followup_service.py`), sin `include_router`.
2. **n8n no lee la base de datos** (D21.6): WF3 necesita que el backend le dé las listas.
3. **No existe la regla del intervalo de 48 h en `reglas_negocio`** (D13 exige leerlo de ahí). Hace falta migración.
4. **La consulta de D13 dice `creado_en`; la columna es `created_at`.** "48 h desde la pre-estimación" apunta a
   `presupuestos.created_at`, no a `oportunidades.created_at`.
5. **n8n 2.16.1 no recupera un barrido perdido** (D13: solo el *durable scheduler*, n8n 2.36+). Verificación en la
   instalación real: pendiente desde D13.
6. **Solo los casos con Gate pueden registrar el resultado de una llamada:** `POST /gate-decisions` exige
   `pendiente_aprobacion`, y `decisiones_gate` admite una fila por oportunidad (`UNIQUE (oportunidad_id)`).
7. **Una visita pedida desde el chat queda `solicitada` y ningún endpoint la pasa a `confirmada`.**
8. **No es un defecto:** `POST /visits` pasa la oportunidad a `visita_agendada`; un cliente que pidió visita no
   entra en el barrido de 48 h.

## 2. Decisiones tomadas (Gabi, 2026-10-07)

- **D25.1 — El seguimiento lo hace administración llamando al cliente.** El email automático al cliente es opcional
  y depende del email del presupuesto (D19.5): se añade reutilizando esa plantilla cuando exista; si no hay tiempo, N1.
- **D25.2 — Un barrido al día, por la mañana (9:00, días laborables).** Una sola lista diaria a administración, sin
  repeticiones dentro del día. En la demo y en las pruebas, WF3 se lanza a mano (*Execute workflow*).
- **D25.3 — En producción n8n estaría en la nube** (coherente con D20); el problema del portátil apagado es propio
  del entorno de pruebas del TFM y se documenta como tal.
- **D25.4 — "Visitas de mañana" y "confirmación de visitas pedidas desde el chat" entran en N0** (decisión de Gabi,
  que corrige la propuesta del tutor de dejarlas en el bloque del Agente 2 sin fecha). Consecuencia: hace falta una
  forma de pasar una visita de `solicitada` a `confirmada` (hallazgo 7).
- **D25.5 — "Llámame en 2 horas":** sin cambios respecto a D24.4 (registro de intentos de llamada, condicionado al
  6 de noviembre; si no, N1; nunca en el calendario de visitas).
- **D25.6 — "No contesta" no se decide a las 48 h:** sigue D24.3 (3 intentos en 2 días laborables distintos),
  aceptado por Gabi el 06/10 y **reconfirmado el 07/10**.
- **D25.7 — Plazos (antes A1):** dos reglas separadas en `reglas_negocio`: recordatorio del Gate a las **24 h**
  (`horas_recordatorio_gate`, ya existe) y seguimiento a las **48 h** (regla nueva, migración). Miden cosas
  distintas: el Gate mide si administración ha llamado; el seguimiento, si el cliente ha reaccionado. Decisión de
  Gabi, 07/10.
- **D25.8 — Estados (antes A2), opción B:** se mantienen `pendiente_aprobacion` (Gate) y `seguimiento_pendiente`
  en la base de datos, y se unifican en lo que ve administración: una sola lista diaria y un solo formulario, con la
  etiqueta del motivo. Motivo: el estado cambia la llamada (el cliente con Gate nunca ha visto una cifra; el de
  seguimiento sí). La eficiencia es la misma (tabla pequeña; `estado IN (a, b)` cuesta igual). Rechazadas: un
  estado único nuevo (migración y cambios en código verificado, y se pierde información) y dos listas separadas.
  Decisión de Gabi, 07/10.

- **D25.9 — Registro del resultado de las llamadas (antes A3), opción B:** un único concepto, "resultado de la
  llamada", para los tres tipos de llamada (Gate, seguimiento y confirmación de una visita pedida desde el chat). Un
  endpoint de ficha, un endpoint de registro, una tabla y un catálogo de motivos. Se hace en **dos bloques**:
  (1) renombrar sin cambiar ningún comportamiento (endpoints, tabla `decisiones_gate`, servicio, esquemas, scripts,
  direcciones en n8n), con la regresión completa igual que antes; (2) ampliar a seguimientos y confirmación de
  visitas. `GATE_SECRET` conserva su nombre ("llave de administración"). Rechazadas: ampliar sin renombrar (nombres
  engañosos; queda como salida si el 15 de octubre no hay tiempo) y endpoints/tabla/formulario nuevos (duplicación).
- **D25.10 — Formulario (antes A5), opción A:** se amplía el formulario actual; no se crea otro. El Gate y el
  seguimiento usan las mismas páginas (visita acordada / descartar); la única rama nueva es "confirmar visita"
  (confirmar la hora pedida / confirmar otra hora / el cliente cancela). Rechazadas: un formulario por tipo
  (duplicación, tres cosas que mantener) y un formulario mínimo sin ficha (sin teléfono a la vista ni aviso de
  "ya registrado").
- **D25.11 — Si el cliente cancela la visita pedida desde el chat en la llamada de confirmación, la oportunidad pasa a
  `perdida`, con motivo** (decisión de Gabi). Consecuencia para el plan del bloque 2: revisar la regla de una fila por
  oportunidad y decisión definitiva (D21.5) para este tipo de llamada.
- **D25.12 — Orden de los bloques** (un endpoint por rama, CLAUDE.md): (1) regla de 48 h + listado de solo lectura
  para WF3; (2) `POST /create-followup-task`; (3) renombrado sin cambios de comportamiento; (4) ampliación a
  seguimientos y confirmación de visitas + rama nueva del formulario; (5) WF3 en n8n. El listado va primero porque
  no depende de los demás y WF3 puede usarlo desde el principio.

- **D25.13 — Segundo motivo de `create-followup-task` (`sin_decision_post_visita`, 5 días laborables tras la visita
  sin `ganada`/`perdida`): fuera de N0, propuesto como ampliación futura (N1).** Decisión de Gabi, 07/10: primero
  terminar y pulir N0. Coherente con D21.2 (lo posterior a la visita es manual en N0). El endpoint de N0 implementa
  solo `sin_respuesta_visita`; la regla `dias_espera_decision_post_visita` se queda en la base de datos sin uso.
  Mejora barata si sobra tiempo: una sección "visitas hechas hace más de 5 días sin decisión" en la lista diaria,
  solo como aviso.

- **D25.14 — Modo de proceder de administración (Gabi, 07/10).** WF2 avisa al momento (Telegram y email) y
  **administración llama a ese cliente el mismo día**: es la razón de ser de la automatización. El recordatorio del
  Gate (24 h) cubre al cliente que, tras la llamada, dice que se lo tiene que pensar; el seguimiento (48 h) cubre al
  cliente sin Gate que no ha pedido visita. En los dos casos es normal dejar margen. Llamar el mismo día es una
  **obligación de administración** (procedimiento de la empresa): la automatización existe para quitarle carga y
  agilizar el proceso, no para sustituir esa llamada (Gabi, 07/10). El sistema no la hace cumplir: sin registro de intentos de llamada (D24.4) no se distingue "se lo piensa" de
  "nadie llamó", ni se puede medir el tiempo hasta la primera llamada. Por eso el apartado de la lista es "Gate sin
  decisión registrada", nunca "cliente pensándoselo".
- **D25.15 — Plazos en horas exactas (opción A del plan de `GET /llamadas-del-dia`).** Un caso entra en la lista en
  el primer barrido de las 9:00 posterior a cumplir el plazo (24 h Gate, 48 h seguimiento), con límite estricto. Con
  un solo barrido al día, el retraso real es de hasta un día más (Gate: 24–48 h; seguimiento: 48–72 h); con D25.14 es
  aceptable. Rechazadas: contar por días del barrido (más difícil de explicar, sin necesidad de negocio) y varios
  barridos al día (rompe D25.2). El tutor había explicado mal que "24 h = la mañana siguiente"; corregido aquí.
- **D25.16 — Los 8 casos de prueba en `presupuesto_enviado` de más de 48 h** (4 sin contacto): se dejan y se cierran
  desde el formulario cuando exista la ampliación de seguimientos, como prueba de esa ampliación. Recomendación del
  tutor; no afecta al código. **Confirmado por Gabi el 07/10.**

## 3. Decisiones abiertas

- **A4 — Reparto N0:** con D25.4, N0 crece. Revisión obligatoria en el punto de control del 15 de octubre.

## 4. Estado de implementación

- **Bloque 1 de D25.12 (regla de 48 h + `GET /llamadas-del-dia`): IMPLEMENTADO, verificado e integrado en `main`**
  el 2026-10-07 (`f8a1f0f`; plan `docs/Plan_Endpoint_Listado_WF3.txt`). Regla `horas_seguimiento_presupuesto = 48`
  (migración paso11); las dos reglas solo admiten enteros entre 1 y 8760 h (sin máximo, 99.999.999 h daba un 500,
  demostrado y corregido). Suite completa 37/37 en una pasada válida.
- Siguiente: bloque 2, `POST /create-followup-task`.
- **[Nota 2026-10-08] Bloque 2 de D25.12 (`POST /create-followup-task`): IMPLEMENTADO y verificado** en la rama
  `feat/n0-create-followup-task`, **pendiente de merge** (plan `docs/Plan_Endpoint_Create_Followup_Task.txt`). Solo
  el motivo `sin_respuesta_visita` (D25.13), plazo de `horas_seguimiento_presupuesto` desde `presupuestos.created_at`
  con límite estricto (D25.7, D25.15), y la condición compartida con el apartado b) de `GET /llamadas-del-dia`.
  `check_followup_service` 34/34, `check_followup_tasks_http` 125/125 y suite completa 39/39 en una pasada válida.
  Siguiente: bloque 3 (renombrado sin cambios de comportamiento, D25.9).
- **[Nota 2026-10-10]** El bloque 2 ya NO está pendiente de merge: integrado en `main` en `1af758a`. **D26** cambia el
  orden de D25.12 desde el bloque 3: (3) renombrado; (4a) ficha para cualquier oportunidad y resumen de la solicitud
  en la lista diaria (dos ramas); (4b) registro del resultado de todas las llamadas + formulario; (5) WF3. Ver
  `docs/Decisiones_D26_Ficha_del_Cliente_en_la_Lista_Diaria.md`.
