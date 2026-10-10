# Flujos de n8n (exportados y saneados)

Copia de los flujos **publicados** en la instalación local de n8n 2.16.1 (Docker), exportados el 2026-10-10
con "…" → Download. El repositorio es **público**, así que cada archivo pasa por un saneado antes de subirse
(decisión D27.5, Regla de Tres en `claude/Decisiones_D27_WF4_Avisos_Tecnicos_y_Pagina_Ficha.md` del Proyecto).

## Qué se cambia al sanear

| Dato real | Se sustituye por | Motivo |
|---|---|---|
| Dominio fijo de ngrok | `TU-DOMINIO.ngrok-free.dev` | Es la puerta pública de la instalación local |
| `chatId` del chat de administración | `CHAT_ID_ADMINISTRACION` | Identificador de una persona en Telegram |
| `chatId` del supergrupo técnico | `CHAT_ID_TECNICO` | Identificador de un grupo privado |
| Email de administración y técnico | `admin@example.com` | Dato personal |
| `webhookId` de cada nodo | (se borra la clave) | n8n crea otro al importar |
| `meta.instanceId`, `versionId` | (se borran) | Identifican la instalación |
| `active` | `false` | Se importa desactivado |

Las **credenciales no van en el JSON** (n8n solo guarda su nombre e id interno): al importar hay que
asignarlas otra vez. Ningún secreto (`X-Gate-Secret`, token del bot, contraseña del formulario, OAuth de
Gmail) está en estos archivos.

## Flujos

| Archivo | Para qué | Estado a 2026-10-10 |
|---|---|---|
| `WF2_aviso_gate.json` | Aviso del Gate a administración (email + Telegram); fallos de envío a WF4 | Publicado. Cambiará: enlace a la ficha |
| `WF4_aviso_tecnico.json` | Subflujo único de avisos técnicos (Telegram + email, sin datos del cliente) | Publicado |
| `WF4_errores_no_atrapados.json` | *Error Trigger* → WF4_aviso_tecnico | Flujo de errores (no necesita publicarse) |
| `WF_ficha_oportunidad.json` | Página HTML de la ficha completa (`GET /webhook/ficha?oportunidad_id=`) | Publicado |
| `WF_formulario_gate.json` | Formulario del resultado de la llamada (Gate), Fase 1 | Publicado. Cambiará en 4b |

`chat_tipo_reforma` (chat + Agente 1) no se exporta todavía: no está cerrado ni publicado.

## Cómo importar

n8n → **Create Workflow** → "…" → **Import from File** → elegir el JSON → asignar las credenciales en los nodos
marcados en rojo → sustituir los marcadores de la tabla por los valores reales → en `WF2_aviso_gate` y
`WF_ficha_oportunidad`, elegir `WF4_errores_no_atrapados` en Settings → Error workflow → Save → Publish.
