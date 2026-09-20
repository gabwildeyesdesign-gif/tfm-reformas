# Reformas Integrales Amedida — Contexto del proyecto (TFM)

Sistema de hiperautomatización para una empresa ficticia de reformas.
TFM del Máster en Agentes de IA e Hiperautomatización (EBIS). Alcance
actual: Nivel N0 (núcleo mínimo aprobable).

## Cómo trabajamos en este proyecto

- Bloques pequeños y revisables. No implementar de golpe varios
  endpoints ni varias tools a la vez.
- Explica tu razonamiento ANTES de escribir código, y cada bloque de
  código DESPUÉS, línea por línea — quien lee esto está aprendiendo
  Python desde cero.
- "Verificado" significa ejecución real (arrancar el servidor, hacer la
  petición real, ver la salida real) — nunca solo lectura de código.
- Si algo se contradice con lo que dice este archivo, dilo
  explícitamente antes de asumir nada; no lo reinterpretes en silencio.

## REGLA CRÍTICA: nunca arrancar uvicorn con --reload

En Windows, --reload cuelga el apagado del servidor cuando corre en un
proceso lanzado sin consola interactiva (confirmado leyendo el código
fuente de uvicorn/supervisors/basereload.py: el reloader manda
CTRL_C_EVENT, que solo llega a procesos con consola real). Arrancar
SIEMPRE así: uvicorn app.main:app (sin --reload).

## Stack y por qué

- n8n — orquestación (webhooks, sub-workflows, reintentos, Telegram
  para el Gate HITL)
- FastAPI + Python — lógica de negocio determinista
- fastmcp (paquete standalone, NO el SDK oficial "mcp" — desde agosto
  2026 el SDK oficial renombró FastMCP a MCPServer) — servidor MCP
  montado DENTRO del mismo proceso FastAPI (un solo proceso, no dos —
  decisión por RAM de 8GB y por no duplicar pools de conexión contra el
  Session pooler de Supabase)
- PostgreSQL / Supabase — Session pooler; acceso SÍNCRONO con
  psycopg2.pool.ThreadedConnectionPool (DB_POOL_MIN=2, DB_POOL_MAX=10)
  — decisión consciente de escala, no desconocimiento; plan de
  migración a async documentado si hiciera falta
- UiPath — OCR de facturas, solo en N2 (condicionado)

Versiones instaladas (verificadas en el venv): Python 3.12.10,
fastmcp==4.0.3, mcp==2.2.0 (dependencia de fastmcp), fastapi==0.141.1,
pydantic==2.13.5, starlette==1.6.0, psycopg2-binary==2.9.13.

## Estructura de carpetas (regla de disciplina)

app/main.py, app/api/, app/mcp_server/, app/services/, app/db/,
app/schemas/, app/config.py, scripts/ (verificación, no producción).

services/ NUNCA importa fastapi ni fastmcp — es la única capa con
lógica de negocio real. api/ y mcp_server/server.py son adaptadores
delgados que llaman siempre a las mismas funciones de services/.

## Dos puertas: REST vs MCP — IMPLEMENTADO SOLO PARCIALMENTE

Estado real del código a día de hoy: POST /leads y POST
/calculate-estimate están implementados y verificados por HTTP real
(dos include_router en main.py). calculate-estimate tiene además su
tool MCP calculate_estimate (list_tools() devuelve solo esa), con /mcp
autenticado por Bearer MCP_SECRET. gate-decisions, visits y
create-followup-task siguen siendo un docstring de una línea.
Lo que sigue es la especificación completa, no el estado actual.

calculate-estimate tiene las dos (MCP en producción, REST para
pruebas). get_business_rules() y request_missing_information() son
SOLO MCP. leads, gate-decisions, visits, create-followup-task son
SOLO REST.

## Gotchas ya resueltos (no los reinvestigues)

- Supavisor (Session pooler de Supabase) sustituye application_name
  por el suyo propio — filtrar por él en pg_stat_activity siempre da 0
  filas a través del pooler. No es un bug nuestro.
- El lifespan de FastAPI y el de fastmcp deben combinarse
  (combined_lifespan anidado), nunca uno reemplazar al otro — FastAPI
  solo acepta un lifespan.
- DB_POOL_MIN y DB_POOL_MAX NO están en .env: el .env contiene
  DATABASE_URL y WEBHOOK_SECRET. Los valores 2 y 10 son los por defecto
  escritos en app/config.py. Para cambiarlos sin tocar código, hay que
  añadir primero esas variables al .env.
- POST /leads EXIGE la cabecera X-Webhook-Secret con el valor de
  WEBHOOK_SECRET del .env. Sin ella, o con otro valor, responde 401
  (antes incluso de validar el cuerpo, así que un 401 no dice nada del
  JSON). Probarlo a mano en PowerShell (curl.exe, no curl: en Windows
  PowerShell 5.1 "curl" es un alias de Invoke-WebRequest):
      curl.exe -X POST http://127.0.0.1:8000/leads -H "Content-Type: application/json" -H "X-Webhook-Secret: <valor del .env>" -d "@cuerpo.json"
  En /docs, el botón "Authorize" permite pegar el secreto una vez. n8n
  debe enviarlo con una credencial "Header Auth" en el nodo HTTP.
- MCP_SECRET (distinto de WEBHOOK_SECRET) también es obligatorio para
  ARRANCAR: si falta o está vacío, uvicorn se detiene al importar
  app/mcp_server/server.py. /mcp exige "Authorization: Bearer
  <MCP_SECRET>" (401 sin él). Se usa una clase propia
  (VerificadorSecretoMCP, compare_digest), NO StaticTokenVerifier de
  fastmcp (comparación no constante, "no usar en producción").
  scripts/check_mcp_connection.py ya envía el token.
- El umbral del Gate NO es un valor global: desde D9 (2026-09-20) vive en
  la tabla umbrales_gate, una fila por tipo_reforma (bano y cocina 13.000 €,
  integral_vivienda y parcial_acabados 10.000 €; el de parcial_acabados es
  PROVISIONAL hasta cerrar D10). La fila
  reglas_negocio.umbral_aprobacion_manual sigue existiendo pero está marcada
  como OBSOLETA y nadie la lee: editarla no tiene ningún efecto.
  El proyecto tiene por tanto 9 tablas, no 8.
- m2 y cualquier número dentro de un JSONB llegan a Python como float si
  se lee el JSONB entero. Para Decimal exacto, extraerlo en SQL:
  (datos_estructurados ->> 'm2')::numeric.
- En scripts que prueban varias violaciones de restricción dentro de UNA
  transacción, cada una va en su propio SAVEPOINT (ROLLBACK TO +
  RELEASE); si no, la primera aborta la transacción y las demás dan
  InFailedSqlTransaction. Patrón: scripts/check_migracion_calculate_estimate.py.
- WEBHOOK_SECRET es obligatorio para ARRANCAR el servidor: si falta o
  solo tiene espacios, uvicorn se detiene al importar
  app/api/security.py, antes de abrir el puerto. Los scripts que solo
  importan app.config no lo necesitan. Plantilla en .env.example.
- En Windows, leer como UTF-8 el stderr de un subproceso de Python
  revienta con UnicodeDecodeError en cuanto el hijo escribe una tilde
  o una ñ. Aparece en scripts que lanzan un subproceso (por ejemplo,
  uvicorn con subprocess.run) y capturan su salida con
  capture_output=True y encoding="utf-8": al ir a una tubería y no a
  una consola, el hijo escribe en la codificación del sistema, cp1252,
  y no en UTF-8 (reproducido de forma aislada: el hijo informa de
  sys.stderr.encoding = cp1252 y manda "á" como el byte 0xe1, justo el
  byte del error original en el caso 6 de
  scripts/check_webhook_auth_http.py). Solución: pasar
  PYTHONIOENCODING=utf-8 en el entorno del hijo, por ejemplo
  env=dict(os.environ, PYTHONIOENCODING="utf-8").

## Estado actual / pendiente

> **Integrado en `main` el 2026-09-19.** La rama `feat/n0-schemas-leads`
> (esquemas y POST /leads, autenticación del webhook, RLS, notas
> técnicas y .gitattributes) se revisó y se fusionó por fast-forward
> (`git merge --ff-only`, sin merge commit) tras pasar la verificación
> final: check_webhook_auth_http 15/15, check_leads_endpoint_http 12/12,
> check_schemas 34/34 y check_rls_estado sin errores.
>
> **Siguiente bloque: POST /calculate-estimate**, en la rama
> `feat/n0-calculate-estimate`, creada desde `main` y todavía sin
> trabajo. Se aplica la misma regla que antes: **no hacer merge a
> `main` sin la confirmación explícita de Gabi**, y siempre con
> `--ff-only`.

Hecho y verificado con ejecución real: scaffolding, servidor MCP
montado, pool de Postgres, /health y /health/db, apagado ordenado,
esquemas Pydantic de leads, get_transactional_connection() (commit /
rollback automáticos), CHECK en oportunidades.tipo_reforma, y POST
/leads completo de punta a punta (HTTP real: 201 con datos válidos, 422
sin tocar la base de datos con datos inválidos).

POST /calculate-estimate (REST + tool MCP) implementado y verificado el
2026-09-19 en la rama feat/n0-calculate-estimate, SIN commitear a la
espera de la revisión de Gabi: migración paso3 (estado
'pendiente_aprobacion' + UNIQUE presupuestos.oportunidad_id, D8),
check_migracion 14/14, check_estimate_service 83/83,
check_calculate_estimate_http 20/20, check_mcp_calculate_estimate 19/19.
Detalle completo: docs/EndPoint_Calculate_estimate_final.txt.

Umbral del Gate por categoria (D9) implementado y verificado el
2026-09-20, tambien SIN commitear: migracion paso4 (tabla umbrales_gate),
check_migracion_umbrales_gate 17/17, check_estimate_service 107/107,
check_calculate_estimate_http 21/21, check_mcp_calculate_estimate 19/19.
Detalle: docs/Umbral_Gate_por_Categoria_D9_final.txt. PENDIENTE: el
umbral de parcial_acabados (10.000 EUR) es PROVISIONAL hasta cerrar D10.

AVISO: scripts/check_exception_handler.py falla 22/28 también en main
(no envía X-Webhook-Secret desde que /leads exige autenticación). Fallo
anterior a este bloque, pendiente de arreglar.

Pendiente: gate-decisions, visits, create-followup-task,
get_business_rules y request_missing_information, la concurrencia
del Session pooler bajo carga, la tabla de excepciones de la Adenda
(hoy un error no controlado de services/ sale como 500 genérico), y la
idempotencia frente a reintentos de n8n (D7, limitación asumida en N0).

Pendiente hasta DESPUÉS de tener POST /calculate-estimate funcionando
(decisión de Gabi, 2026-09-18): suite de pytest (unitarias mockeadas
con TestClient + integración contra un Postgres efímero vía
testcontainers en Docker, que ya está disponible porque n8n corre en
Docker localmente) y un workflow mínimo de GitHub Actions. Hasta
entonces no se escribe ningún test ni se añade ninguna dependencia de
testing; la verificación sigue siendo con los scripts check_*.py.

## Para el razonamiento completo de cada decisión

Ver docs/ en este repo — este archivo es un resumen de referencia
rápida, no sustituye esa documentación. Contiene:

- Informe_Decisiones_N0_TFM_Reformas.txt — esquema de datos y reglas
  de negocio.
- Adenda_Decisiones_N0_Endpoints_y_Excepciones.txt — contrato de los 5
  endpoints y tabla de excepciones.
- TFM_Decisiones_Arquitectura_MCP_y_DB.txt — servidor MCP y capa de
  datos. El "plan de migración a async" citado en la sección de stack
  está aquí, en la sección 7.8.
- TFM_Resumen_Sesion_Backend_N0.txt — narrativa de la sesión de
  scaffolding.
- TFM_Decisiones_Modelo_Datos_Leads.txt — modelo de datos de leads y
  decisiones D1 a D7.
- Decision_RLS_N0.txt — qué protege RLS hoy (cierra la Data API
  pública a anon/authenticated; el backend se lo salta), por qué no hay
  políticas y cuándo pasan a ser obligatorias.
- Notas_Tecnicas_N0.txt — psycopg2-binary como decisión consciente de
  desarrollo local, y el orden de estados de oportunidades sin
  protección en la base de datos (a decidir con gate-decisions/visits).
- TFM_Resumen_Sesion_Autenticacion_Webhook_y_Notas_N0.txt — narrativa
  de la sesión de autenticación del webhook, RLS y notas N0, con las
  salidas reales de verificación.

## Esquema de la base de datos: cuál de los dos .sql manda

docs/schema_actual.sql es la ÚNICA fuente fiable del esquema actual. Lo
genera scripts/dump_schema.py leyendo information_schema de la base de
datos real, y está verificado por ejecución real: el SQL generado se
ejecutó contra un esquema de prueba y recreó las 8 tablas con sus 15
restricciones PK/UNIQUE/FK y sus 7 CHECK. Se regenera ejecutando:

    .\venv\Scripts\python.exe scripts\dump_schema.py

docs/schema_n0_v2.sql es HISTÓRICO. Se escribió a mano y divergió de la
realidad sin que nadie lo notara: le faltan la columna
presupuestos.motivo_gate, las tablas reglas_negocio y tarifas_base
enteras, y el CHECK chk_oportunidades_tipo_reforma. NO debe consultarse
para nada del desarrollo actual. Se conserva solo como registro de cómo
se diseñó el esquema al principio.
