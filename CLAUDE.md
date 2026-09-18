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

Estado real del código a día de hoy: POST /leads está implementado y
verificado por HTTP real (app/api/leads.py + app/services/leads_service.py,
registrado en main.py con el único include_router del proyecto). Los
otros cuatro endpoints siguen siendo un docstring de una línea, y el
servidor MCP no tiene ninguna tool registrada (list_tools() devuelve []).
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
- DB_POOL_MIN y DB_POOL_MAX NO están en .env: el .env solo contiene
  DATABASE_URL. Los valores 2 y 10 son los por defecto escritos en
  app/config.py. Para cambiarlos sin tocar código, hay que añadir
  primero esas variables al .env.

## Estado actual / pendiente

> **PENDIENTE DE REVISIÓN COMPLETA.** La rama `feat/n0-schemas-leads`
> tiene 9 commits por delante de `main` y **no se ha integrado**: está
> esperando la revisión completa de Gabi antes del merge. Todo el
> trabajo está subido a GitHub y verificado con ejecución real, pero
> `main` sigue en `90bca98` a propósito. **No hacer merge a `main` sin
> su confirmación explícita.**
>
> Revisión: https://github.com/gabwildeyesdesign-gif/tfm-reformas/compare/main...feat/n0-schemas-leads
>
> Cuando lo apruebe:
> `git checkout main && git merge --ff-only feat/n0-schemas-leads && git push`

Hecho y verificado con ejecución real: scaffolding, servidor MCP
montado, pool de Postgres, /health y /health/db, apagado ordenado,
esquemas Pydantic de leads, get_transactional_connection() (commit /
rollback automáticos), CHECK en oportunidades.tipo_reforma, y POST
/leads completo de punta a punta (HTTP real: 201 con datos válidos, 422
sin tocar la base de datos con datos inválidos).

Pendiente: los otros cuatro endpoints, las tools MCP, la concurrencia
del Session pooler bajo carga, la tabla de excepciones de la Adenda
(hoy un error no controlado de services/ sale como 500 genérico), y la
idempotencia frente a reintentos de n8n (D7, limitación asumida en N0).

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
