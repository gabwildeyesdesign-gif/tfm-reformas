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
- El terminal real de trabajo es Git Bash sobre Windows, no PowerShell.
  Las rutas se escriben con barra normal (app/db/connection.py) y el
  intérprete siempre es ./venv/Scripts/python.exe. Cuando un comando
  lance un proceso hijo de Python y capture su salida, se le pone
  delante PYTHONIOENCODING=utf-8, o la primera tilde o eñe lo revienta
  (el porqué está en el gotcha de cp1252, más abajo). Esto no anula la
  nota sobre curl.exe y el alias curl: esa sigue valiendo para las
  pruebas que Gabi hace a mano en una consola de PowerShell.
- Un script de verificación que espera una excepción tiene que marcar
  FALLO explícito cuando NO sale ninguna. Se hace con la rama else: del
  try, con un mensaje del tipo "la excepción se ha tragado". El motivo
  es un caso real (D16): una guarda mal indentada dejó el raise dentro
  del if y la excepción se suprimía en silencio; la comprobación "no es
  InterfaceError" daba [OK] sobre ese fallo, porque si no sale nada,
  tampoco sale un InterfaceError. El fallo silencioso es peor que el
  error que se venía a arreglar, y solo esa rama else lo detecta.
- La prueba en negativo se ejecuta contra TODAS las versiones
  defectuosas conocidas, no solo contra la original. No basta con
  revertir el arreglo (git stash) y ver que falla: si durante el
  trabajo apareció otra versión rota —por ejemplo un intento previo mal
  indentado que ya no está en disco—, hay que recrearla en una copia,
  ejecutar el script contra ella y enseñar la salida literal, antes de
  restaurar la buena y confirmarlo con git diff. Cada versión rota
  demuestra que el script detecta un modo de fallo distinto.
- Procesos: solo se terminan los procesos arrancados en la sesión, por el
  PID guardado al arrancarlos (por ejemplo, el que uvicorn escribe en su
  log con "Started server process [N]", comprobado contra el que escucha
  en el puerto). Nunca se mata "lo que escuche en un puerto". Si el
  puerto está ocupado por otro proceso, se para y se avisa. El 8000 no se
  usa para pruebas: es el uvicorn de Gabi para n8n. Motivo: el
  2026-09-25 un bucle de regresión mató así el servidor de Gabi.
- Borrados en scripts: un script solo borra las filas que ha creado él,
  identificadas por una marca propia (emails del dominio reservado
  @example.com, ids sacados de ellos, o un valor aleatorio de la
  ejecución), nunca por accion, ruta o estado a secas. Para leer lo que
  ha escrito, la marca se combina con id > id_base (el id máximo anotado
  al empezar), para que lo que escriba otro proceso no cambie los
  recuentos. Una prueba en negativo que provoque errores 500 deja logs del
  manejador global sin marca: se borran por id exacto al terminar, tras
  comprobar que son los esperados. Motivo: el 2026-09-26 se descubrió que
  check_exception_handler borraba con accion = 'error_no_controlado' los
  errores 500 REALES del uvicorn de n8n (75 filas en 39 ejecuciones, según
  pg_stat_statements); corregido en 6bde841 y verificado con un log
  centinela.
- Cuando un arreglo depende de la ESTRUCTURA del código y no solo de su
  sintaxis, se verifica con ast.parse y se enseña la salida. py_compile
  no basta: devuelve exit code 0 sobre código que compila pero que anida
  las sentencias de forma equivocada. Es exactamente lo que ocurrió en
  D16, con el raise atrapado dentro del if porque Python ignora los
  comentarios al calcular los niveles de indentación.
- Conexiones de prueba sin commit (regla desde el 2026-10-08). Una prueba
  que modifica tablas reales dentro de una transacción (que se deshace al
  final) y pasa ESA conexión o su cursor al código bajo prueba abre la
  conexión con connection_factory=ConexionSinCommit: una subclase de
  psycopg2.extensions.connection cuyo commit() lanza un error
  (CommitProhibido) en vez de confirmar. Lleva su autoprueba: llamar a
  commit() y exigir la excepción, con la rama else que marca FALLO "la
  excepción se ha tragado". Así, si el código (o una versión rota en una
  prueba en negativo) llama a commit(), sale un FALLO contado y el
  ROLLBACK lo deshace todo. Motivo: el 2026-10-08, la versión rota N12 de
  POST /create-followup-task llamó a commit() con el cursor de la prueba
  S6 de check_followup_service y dejó horas_seguimiento_presupuesto en 8761
  en la tabla REAL (restaurada a 48 con un UPDATE protegido; detalle en la
  sección 6.3 de docs/Plan_Endpoint_Create_Followup_Task.txt). Ejemplo:
  scripts/check_followup_service.py (S0, S1, S3 y S6).
- Suite completa sin suspensiones (regla desde el 2026-10-03). Motivo: los
  tres cortes de conexión de la rama de /gate-decisions coincidieron con
  un Modern Standby de Windows con la red desconectada (ver "Scripts de
  verificación frágiles"); en este portátil, apagar la pantalla ya es
  entrar en esa suspensión. El lanzador que lo automatizaba
  (docs/Plan_Ejecutor_Suite.txt) quedó APLAZADO y lo sustituye esta regla.
  - Antes de empezar: portátil ENCHUFADO, con la pantalla y la suspensión
    en "Nunca" cuando está enchufado (ajuste ya hecho por Gabi en
    Windows), y la tapa abierta. Se anota la hora de inicio.
    (Nota 2026-10-03, decisión de Gabi: ya NO se deja en "Nunca", para no
    tener el portátil sin suspenderse de forma permanente. Ahora: portátil
    ENCHUFADO, tapa abierta, y pantalla y suspensión a 30 minutos cuando
    está enchufado. La suite dura unos 7 minutos (34 scripts, el
    2026-10-03); si un día dura más de 30, se mueve el ratón a mitad. La
    comprobación de Kernel-Power 506/507 al terminar NO cambia y es la que
    decide si la pasada vale.)
  - Al terminar: se comprueba en el registro de Windows si hubo algún
    Kernel-Power 506 (entra en Modern Standby) o 507 (sale) entre la hora
    de inicio y la de fin. Si los hubo, la pasada NO VALE: no cuenta ni
    como 32/32 ni como fallo, y se repite entera. Comando de PowerShell
    (probado el 2026-10-03: encuentra la suspensión de 12:29:14-12:29:57
    en 12:25-12:35 y ninguna en 13:30-13:45):
        # Pon aquí la hora de inicio y la de fin de la pasada.
        $inicio = '2026-10-03 12:25'; $fin = '2026-10-03 12:35'
        # Busca en el registro System los eventos 506/507 de Kernel-Power
        # en ese intervalo. -ErrorAction SilentlyContinue: si no hay
        # ninguno, Get-WinEvent da un error en vez de una lista vacía.
        $s = Get-WinEvent -FilterHashtable @{LogName='System'; `
               ProviderName='Microsoft-Windows-Kernel-Power'; Id=506,507; `
               StartTime=[datetime]$inicio; EndTime=[datetime]$fin} `
               -ErrorAction SilentlyContinue
        # Si encontró alguno, los enseña y avisa; si no, la pasada vale.
        if ($s) { $s | Select-Object TimeCreated, Id; 'LA PASADA NO VALE: hubo suspension' } else { 'ninguna suspension: la pasada vale' }
  - Limitación conocida (P4 del plan): check_foreign_keys,
    check_mcp_connection, check_pool_connections,
    check_pool_transaction_state y check_rls_estado no llaman nunca a
    sys.exit, así que terminan con código 0 aunque una comprobación salga
    mal (solo dan otro código si revientan con una excepción). Su salida
    hay que revisarla a mano: el código de salida no basta.
- Memoria en dos fases (regla desde el 2026-10-12, rama feat/n0-agente2).
  Con 8 GB, Docker/n8n y los navegadores ocupan casi toda la RAM. Cada bloque
  con pruebas se hace en dos fases:
  - Fase 1, con Docker/n8n abiertos (Gabi trabaja en n8n): SOLO se escribe
    el código y los scripts, con sus comentarios medidos, y se hace commit
    "SIN ejecutar". No se arranca uvicorn ni se ejecuta ninguna prueba (sí
    se permiten lecturas en solo lectura de la base de datos para medir).
  - Fase 2, cuando Gabi lo dice: antes de nada se MIDE la memoria libre
    (Win32_OperatingSystem.FreePhysicalMemory) y se comprueba que no hay
    procesos chrome, msedge ni docker/vmmem. Con menos de 1,5 GB libres, o
    con Docker/n8n, Chrome o Edge abiertos, NO se ejecuta nada: se para y se
    avisa. No se cierra ningún proceso ajeno: lo cierra Gabi (Chrome y Edge
    se quedan en segundo plano al cerrar la ventana; hay que "Salir" desde
    el icono junto al reloj). Una excepción solo si Gabi la pide y la prueba
    no escribe ninguna fila (B3, anotada); para B5 y la suite, el tutor la
    hizo obligatoria.
- Regresión de visitas (lección del 2026-10-12, H-B5-2): cada bloque que
  toque visitas (su esquema, su filtro, sus reglas o sus endpoints) ejecuta
  la regresión de TODOS los scripts que crean visitas, no solo los del
  bloque. En B4 se cambió el filtro de texto_cliente y no se ejecutó
  check_ficha_oportunidad_http, que fallaba en un 15 % de las pasadas.
- Los scripts solo comprueban SUS datos (lección del 2026-10-12, H-B5-1 y
  H-C-1): nada de contar en toda la respuesta o en toda una tabla, porque un
  dato real (una visita de mañana con el texto fijo del Gate, un seguimiento
  registrado desde el formulario) los hace fallar sin que nada esté roto.
  Se cuenta en los elementos de las oportunidades propias, o en las filas
  que existían al migrar, y lo demás sale como [INFO].
- Regla H-D39-4 (2026-10-21, commits de D39.9 6ed9ec0 y aba39c4):
  scripts/check_webhook_auth_http.py cuenta TODAS las filas de logs antes y
  después (contar_todo) y compara: un log AJENO escrito mientras se ejecuta
  lo hace fallar sin que nada esté roto. No se toca en ninguna rama que no
  sea la suya (pendiente, ver "Pendiente"), y mientras tanto nada que
  escriba en logs (otro script, la suite, una migración de datos, una
  prueba a mano contra el 8000) se ejecuta a la vez que él.

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
pydantic==2.13.5, starlette==1.6.0, psycopg2-binary==2.9.13,
tzdata==2026.4 (desde el 2026-09-26: en Windows, ZoneInfo("Europe/Madrid")
falla sin él con ZoneInfoNotFoundError).

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
autenticado por Bearer MCP_SECRET. Desde el 2026-09-25 existe también
GET /leads/session/{lead_token} (solo REST, en el router de leads, para
el router de n8n; no forma parte de los 5 endpoints originales).
Desde el 2026-09-26, POST /visits (solo REST, tercer include_router),
integrado en main (7934fa6). Desde el 2026-10-01, en la rama
feat/n0-gate-decisions (pendiente de merge), POST /gate-decisions (solo
REST, cuarto include_router), con su propia cabecera X-Gate-Secret.
(Nota 2026-10-03: POST /gate-decisions ya está integrado en main,
24cf60f. Desde el 2026-10-03, en la rama feat/n0-aviso-gate (pendiente de
merge), GET /gate-avisos/{oportunidad_id}: solo REST, sin tool MCP, de
SOLO LECTURA, quinto include_router, con la misma cabecera X-Gate-Secret
que /gate-decisions (devuelve importes y datos personales de casos con
Gate, que el Agente 2 no debe ver). No forma parte de los 5 endpoints
originales.)
(Nota 2026-10-06: GET /gate-avisos ya NO está pendiente de merge: está
integrado en main, 002cf48, comprobado en GitHub.)
(Nota 2026-10-07: desde el 2026-10-07, en la rama feat/n0-listado-wf3
(pendiente de merge), GET /llamadas-del-dia: solo REST, sin tool MCP, de
SOLO LECTURA, sexto include_router, con la cabecera X-Gate-Secret. Es la
lista diaria de llamadas de WF3 (D25). No forma parte de los 5 endpoints
originales.)
(Nota 2026-10-10: GET /llamadas-del-dia ya NO está pendiente de merge: integrado en main en f8a1f0f, comprobado en GitHub.)
create-followup-task sigue siendo un docstring de una línea.
(Nota 2026-10-08: la frase anterior ya no es cierta. POST
/create-followup-task está implementado y verificado en la rama
feat/n0-create-followup-task, pendiente de merge: séptimo include_router,
cabecera X-Gate-Secret, solo REST, sin tool MCP. Detalle en "Estado
actual".)
(Nota 2026-10-10: "pendiente de merge" ya no es cierto: integrado en
main en 1af758a, comprobado en GitHub.)
(Nota 2026-10-10: desde el 2026-10-10, en la rama feat/n0-ficha-oportunidad
(pendiente de merge), GET /oportunidades/{oportunidad_id}/ficha: la ficha
completa de cualquier oportunidad, con o sin Gate (D26.2). Solo REST, sin
tool MCP, de SOLO LECTURA, octavo include_router, cabecera X-Gate-Secret
puesta en el ROUTER (no en la ruta). No forma parte de los 5 endpoints
originales.)
(Nota 2026-10-11: corrige la nota anterior (C1 del plan
docs/Plan_Resumen_Lista_Diaria.txt): GET /oportunidades/{oportunidad_id}/ficha
ya NO está pendiente de merge: está integrada en main desde ea96f39, y la
rama feat/n0-ficha-oportunidad ya no existe en GitHub. En n8n existe ya la
página que la consume: WF_ficha_oportunidad, Webhook GET
/webhook/ficha?oportunidad_id= (decisión D26.8, documento en el Proyecto de
claude.ai; se copia a docs/ con la rama de exportación de n8n). Desde el
2026-10-10, en la rama feat/n0-resumen-lista-diaria (pendiente de merge),
GET /llamadas-del-dia lleva en cada elemento el resumen de la solicitud
(objeto "solicitud") y, en las visitas, texto_cliente (D26.1, 4a-2): mismo
router y misma ruta, contrato ampliado; sigue sin email ni importes.)
(Nota 2026-10-11, tutor: 4a-2 ya NO está pendiente de merge: integrado en
main en c1ba356 (fast-forward de feat/n0-resumen-lista-diaria, confirmado
por Gabi y comprobado en GitHub; la rama ya no existe). D26.8 (sección 5 de
docs/Decisiones_D26_...md) y D27 (docs/Decisiones_D27_...md) ya están en
docs/, y los JSON saneados de los flujos de n8n en n8n/ (D27.5). No hay
ningún bloque de backend abierto; el siguiente es 4b (D26.6).)
Lo que sigue es la especificación completa, no el estado actual.
(Nota 2026-10-12: desde el 2026-10-11, en la rama feat/n0-resultado-llamadas
(pendiente de merge), POST /resultados-llamada: el resultado de TODAS las
llamadas de administración (Gate, seguimiento y confirmación de visita), solo
REST, sin tool MCP, noveno include_router, cabecera X-Gate-Secret. POST
/gate-decisions pasa a ser su ALIAS con el contrato antiguo, solo Gate, hasta
que el formulario de n8n use la ruta nueva (D26.6). Detalle en "Estado
actual".)
(Nota 2026-10-12: corrige la nota anterior: 4b ya NO está pendiente de merge,
main = 579fd00 contiene sus commits (C1 del plan del Agente 2). Desde el
2026-10-12, en la rama feat/n0-agente2 (pendiente de merge): GET
/leads/session añade "reforma" y "plazo_orientativo"; get_business_rules
EXISTE como tool MCP sin parámetros y de solo lectura (list_tools devuelve
calculate_estimate y get_business_rules; NUNCA precio_m2, margen, IVA ni
umbrales); POST /visits filtra texto_cliente (D26.7: 1..300, 422 con email o
teléfono); y POST /visits, POST /resultados-llamada y el alias comprueban que
el hueco esté libre (D31: 409 hueco_ocupado con huecos_libres).
request_missing_information() sale de N0 (P14). Detalle en "Estado actual"
y en la Adenda 5.11.)
(Nota 2026-10-14: desde el 2026-10-13, en la rama feat/n0-horario-visitas
(pendiente de merge), D32: GET /huecos-libres (décimo include_router, solo
REST, de SOLO LECTURA, X-Gate-Secret en el router: las horas libres de
visita para el formulario de n8n, modo días y modo un día) y la tool MCP
consultar_huecos_libres (la misma consulta para el Agente 2; list_tools
devuelve calculate_estimate, get_business_rules y consultar_huecos_libres).
POST /visits, POST /resultados-llamada y el alias solo aceptan horas de la
rejilla (422 fuera_de_rejilla con horas_validas) y el choque cuenta el
desplazamiento. paso_huecos_min ya no se lee. Detalle en "Estado actual" y
en la Adenda 5.11 (e).)
(Nota 2026-10-14, D33: en la rama feat/n0-fecha-fin-visita (pendiente de
merge), POST /visits, POST /resultados-llamada y el alias /gate-decisions
responden además fecha_fin_visita (inicio + duracion_visita_min, mismo
formato y desfase; null sin visita), para el subflujo WF5 de n8n que copia
las visitas a Google Calendar. Sin endpoints ni tools nuevos.)
(Nota 2026-10-20, D28.1: en la rama feat/n0-resumen-solicitud (pendiente de
merge), POST /leads admite resumen_solicitud (opcional) y responde
resumen_solicitud_estado (guardado | sin_resumen | descartado_longitud |
descartado_email | descartado_telefono; nunca 422 por el resumen, D36.8);
GET /gate-avisos y la ficha lo devuelven en la raíz; NUNCA la lista diaria
ni /leads/session. Columna leads.resumen_solicitud (paso15, APLICADA el
2026-10-20). Detalle: Adenda 5.12 y docs/Plan_Resumen_Solicitud_D28.txt.)
(Nota 2026-10-22: corrige la nota anterior: feat/n0-resumen-solicitud ya NO
está pendiente de merge: está en main (su último commit, 315a20c, es
antecesor de main = aba39c4; comprobado con git merge-base --is-ancestor).
Desde el 2026-10-22, en la rama fix/n0-quitar-alias-gate-decisions
(pendiente de merge), el ALIAS POST /gate-decisions está QUITADO (bloque 5,
D26.6: el formulario publicado WF_formulario_llamadas llama a POST
/resultados-llamada desde el 2026-10-21 y WF_formulario_gate está
despublicado). La ruta responde 404; el cuarto include_router ya no existe
(los demás conservan su número). Se fueron también registrar_decision, el
parámetro solo_gate del servicio, GateDecisionCreate, GateDecisionResponse
y MotivoDescarte (DecisionGate se queda: lo usan GET /gate-avisos y la
ficha). X-Gate-Secret sigue siendo la llave de todas las rutas de
administración. Detalle: Adenda 5.5, reescrita.)
(Nota 2026-10-22, D42: en la rama feat/n0-direccion-obra (pendiente de
merge), POST /resultados-llamada admite direccion_obra {calle_numero,
codigo_postal, municipio}, OPCIONAL y solo con los resultados que dejan visita
(422 con descartar y cliente_cancela), y la guarda en la tabla nueva
direcciones_obra (paso16) en la misma transacción. CONFIDENCIAL (D42.5): solo
la devuelve GET /oportunidades/{id}/ficha; ninguna otra ruta ni tool, ni las
respuestas de POST /resultados-llamada, ni logs (solo
"direccion_obra_registrada": true/false). Sin endpoints ni tools nuevos.
Detalle: Adenda 5.13.)
(Nota 2026-10-23, D45: feat/n0-direccion-obra ya está en main (c0db982). En
la rama feat/n0-antelacion-visitas (pendiente de merge), antelación mínima
de las visitas, sin endpoints ni tools nuevos: el chat (tool
consultar_huecos_libres y POST /visits) desde el 2.º día laborable después
de hoy, y administración (GET /huecos-libres y POST /resultados-llamada con
visita_acordada u otra_hora) desde el 1.º; confirmar_hora, solo futura. 422
antelacion_insuficiente y sin_horas_motivo nuevo. El canal lo fija el
adaptador (parámetro obligatorio), nunca la petición. Reglas
dias_antelacion_visita_chat y dias_antelacion_visita_admin (paso17).
Detalle: Adenda 5.14 y docs/Plan_Antelacion_Visitas_D45.txt.)
(Nota 2026-10-26, D46: D45 ya está en main (abe22a6). En la rama
feat/n0-cambio-visita (pendiente de merge), POST /cambios-visita: undécimo
include_router, solo REST, sin tool MCP, X-Gate-Secret. Administración
cambia (sin límite) o cancela (pasa a 'perdida', con motivo) una visita YA
confirmada y futura, con informe; tabla nueva cambios_visita (paso18, la
12.ª). La ficha gana visita_modificable y cambios_visita (con tipo_cambio,
nunca accion). La repetición de POST /resultados-llamada da 409
visita_modificada_despues si su visita se cambió o canceló (P5). Detalle:
Adenda 5.15, docs/Contrato_Formulario_Llamadas.md (4c) y
docs/Plan_Cambio_Visita_D46.txt.)

calculate-estimate tiene las dos (MCP en producción, REST para
pruebas). get_business_rules() y request_missing_information() son
SOLO MCP. leads, gate-decisions, visits, create-followup-task son
SOLO REST.

## Gotchas ya resueltos (no los reinvestigues)

- En Git Bash, cualquier argumento que empiece por "/" (por ejemplo
  /leads) se convierte en una ruta de Windows (C:/Program Files/Git/leads)
  antes de llegar al programa, salvo con MSYS_NO_PATHCONV=1 delante del
  comando. Pasó el 2026-09-26 en la verificación con centinelas: el log
  que debía imitar uno de /leads se guardó con la ruta convertida. Es el
  mismo motivo por el que Docker se maneja desde PowerShell.
- Supavisor (Session pooler de Supabase) sustituye application_name
  por el suyo propio — filtrar por él en pg_stat_activity siempre da 0
  filas a través del pooler. No es un bug nuestro.
- Supavisor ignora TODOS los parámetros de arranque, no solo
  application_name: options="-c statement_timeout=..." tampoco llega
  (comprobado: seguía en '2min'). Por eso statement_timeout se fija con
  un SET + commit en ConexionConTimeout (connection_factory del pool,
  app/db/connection.py). Además, en modo Session reutiliza backends de
  Postgres entre clientes: el pid NO identifica a un cliente. Para
  aislar las conexiones de un proceso en pg_stat_activity, una lista
  blanca por pid previo no basta; hay que combinarla con la actividad
  posterior (state_change). Detalle en
  docs/TFM_Resumen_Sesion_Timeouts_DB_N0.txt, sección 6.
- Cada préstamo del pool (get_db_connection y
  get_transactional_connection) valida la conexión antes de entregarla:
  SELECT 1 + rollback, y si está muerta la descarta ([AVISO] en stderr)
  hasta encontrar una viva, con tope DB_POOL_MAX. Cuesta ~120-190 ms por
  préstamo según la red. Los tiempos límite (connect_timeout, keepalives,
  statement_timeout 30 s) son constantes DB_* de app/config.py,
  sobrescribibles por .env. OJO: DB_KEEPALIVES_COUNT no tiene efecto en
  Windows, y una conexión medio abierta puede seguir colgando el SELECT 1
  minutos (tcp_user_timeout no existe en Windows).
- El lifespan de FastAPI y el de fastmcp deben combinarse
  (combined_lifespan anidado), nunca uno reemplazar al otro — FastAPI
  solo acepta un lifespan.
- DB_POOL_MIN y DB_POOL_MAX NO están en .env: el .env contiene
  DATABASE_URL y WEBHOOK_SECRET (y, desde que se añadieron, MCP_SECRET y
  GATE_SECRET). Los valores 2 y 10 son los por defecto
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
- GATE_SECRET (tercer secreto, desde el 2026-09-30) también es
  obligatorio para ARRANCAR. app/api/security.py detiene uvicorn, antes de
  abrir el puerto, si GATE_SECRET falta o solo tiene espacios, si es igual
  a WEBHOOK_SECRET o si es igual a MCP_SECRET; y también si MCP_SECRET es
  igual a WEBHOOK_SECRET (P10: el mensaje de server.py ya lo prometía,
  pero nadie lo comprobaba). Los mensajes nombran las variables, nunca su
  valor. POST /gate-decisions exige la cabecera X-Gate-Secret (401 sin
  ella, o con el valor de WEBHOOK_SECRET en cualquiera de las dos
  cabeceras). Motivo: WEBHOOK_SECRET lo tiene la herramienta del Agente 2,
  y con el mismo valor el agente podría decidir su propio Gate. En n8n va
  en una credencial "Header Auth" PROPIA, solo para el formulario del
  Gate. Al integrar la rama, el uvicorn del 8000 no arranca sin
  GATE_SECRET en el .env (R4 del plan de /gate-decisions).
  (Nota 2026-10-22: POST /gate-decisions ya no existe, bloque 5; la
  cabecera X-Gate-Secret la exigen hoy POST /resultados-llamada y las demás
  rutas de administración, con las mismas reglas. Las comprobaciones de
  arranque no cambian y las sigue probando check_gate_decisions_http,
  CASO 0.)
- El precio que se devuelve al cliente lleva IVA INCLUIDO (D14):
  EstimateResponse trae importe_min_con_iva / importe_max_con_iva, y esas
  son también las columnas de presupuestos (renombradas en paso7), junto
  con iva_pct_aplicado. Se guarda el importe YA con IVA para que un
  presupuesto no cambie de precio si alguien edita
  reglas_negocio.iva_estandar_pct (21 %). OJO: el Gate se evalua contra el
  importe SIN IVA, porque el umbral mide riesgo comercial y el IVA no se
  queda en la empresa; hay un TODO en el servicio con la pregunta abierta.
- oportunidades.estado admite OCHO valores desde paso7: se añadió
  'seguimiento_pendiente' (D13). La columna
  oportunidades.fecha_ultimo_contacto existe pero está NULL en todas las
  filas: la escribirá el barrido de seguimiento, que aún no existe.
- El umbral del Gate NO es un valor global: desde D9 (2026-09-20) vive en
  la tabla umbrales_gate, una fila por tipo_reforma (bano y cocina 13.000 €,
  integral_vivienda y parcial_acabados 10.000 €, los cuatro ya cerrados). La fila
  reglas_negocio.umbral_aprobacion_manual sigue existiendo pero está marcada
  como OBSOLETA y nadie la lee: editarla no tiene ningún efecto.
  El proyecto tiene por tanto 9 tablas, no 8. (Desde paso10, 2026-09-28,
  son 10: se añadió decisiones_gate. Desde paso16, 2026-10-22, son 11, con
  direcciones_obra; desde paso18, 2026-10-25, son 12, con cambios_visita.)
- EstadoOportunidad (app/schemas/common.py, desde el 2026-10-03) replica
  el CHECK oportunidades_estado_check: los mismos 8 valores, en el mismo
  orden (nueva, cualificada, pendiente_aprobacion, visita_agendada,
  presupuesto_enviado, seguimiento_pendiente, ganada, perdida). Defensa
  doble: si se cambia uno, hay que cambiar el otro; si la base de datos
  admitiera un estado que el Enum no tiene, GET /gate-avisos daría un 500
  al leer una oportunidad en ese estado. Hoy solo lo usa
  AvisoGateResponse.estado_oportunidad; los demás servicios siguen
  escribiendo los estados como textos sueltos (pendiente, otra rama).
- m2 tiene tope: más de 0 y hasta 500 (MAX_M2_LEAD en app/schemas/common.py,
  y CHECK chk_leads_m2_rango en leads, defensa doble como en D5). Es Decimal,
  no float. OJO: el CHECK va sobre la expresión (datos_estructurados ->>
  'm2')::numeric porque NO existe ninguna columna m2 (D3: el dato vive en el
  JSONB). Garantiza el RANGO, no la PRESENCIA: si la clave falta, ->> da NULL
  y un CHECK solo rechaza lo FALSO, así que pasa. La presencia la garantiza
  Pydantic en el único camino real de escritura, POST /leads.
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
- (2026-10-11) Lo mismo pasa al LEER la entrada estándar: un script de
  Python que recibe un texto por stdin (por ejemplo, por un heredoc de Git
  Bash) lo lee en cp1252 y rompe las tildes ("añade" -> "aÃ±ade"). Solución:
  sys.stdin.reconfigure(encoding="utf-8") al principio del script. Pasó con
  la herramienta de comentarios del scratchpad y rompió tildes en tres
  scripts.
- (2026-10-11) En Git Bash, una ruta /c/... escrita DENTRO del texto de un
  python -c no se convierte (Python no la entiende): pasarla como argumento
  (sys.argv). Y un heredoc muy largo con muchas comillas puede no llegar
  entero a la terminal: escribir el script en un archivo y ejecutarlo.

## Estado actual / pendiente

> **Integrado en `main` el 2026-09-19.** La rama `feat/n0-schemas-leads`
> (esquemas y POST /leads, autenticación del webhook, RLS, notas
> técnicas y .gitattributes) se revisó y se fusionó por fast-forward
> (`git merge --ff-only`, sin merge commit) tras pasar la verificación
> final: check_webhook_auth_http 15/15, check_leads_endpoint_http 12/12,
> check_schemas 34/34 y check_rls_estado sin errores.
>
> **Integrado en `main` el 2026-09-20.** La rama
> `feat/n0-calculate-estimate` (POST /calculate-estimate por REST y como
> tool MCP, autenticación de /mcp, migraciones paso3 a paso6, umbral del
> Gate por categoría y tope de m2) se fusionó por fast-forward
> (`git merge --ff-only`, sin merge commit) tras pasar la verificación
> final sobre `main`: check_schemas 37/37, check_webhook_auth_http 15/15,
> check_leads_endpoint_http 12/12, check_estimate_service 107/107,
> check_calculate_estimate_http 21/21, check_mcp_calculate_estimate
> 19/19, check_migracion_m2_leads 23/23, check_exception_handler 32/32 y
> check_rls_estado sin errores.
>
> **Integrado en `main` el 2026-09-20 (segundo merge del día).** La rama
> `feat/n0-iva-y-seguimiento` (D14: el precio mostrado lleva IVA
> incluido; D13: base de datos preparada para el seguimiento a 48 h) se
> fusionó por fast-forward tras pasar la verificación final sobre `main`:
> check_estimate_service 129/129, check_calculate_estimate_http 24/24,
> check_mcp_calculate_estimate 19/19, check_migracion_iva_y_seguimiento
> 17/17, y el resto de la suite sin fallos.
>
> **Integrado en `main` el 2026-09-26.** POST /visits (rama
> feat/n0-visits), último commit 7934fa6.
>
> **Bloque actual: POST /gate-decisions, implementado y verificado en la
> rama `feat/n0-gate-decisions`, pendiente de merge** (Bloques A a E
> cerrados; el corte de mitad de octubre de D18 se cumple). Antes del
> merge, Gabi tiene que añadir GATE_SECRET a su .env, o el uvicorn del
> 8000 no arrancará (R4). Se aplica la misma regla de siempre: **no hacer
> merge a `main` sin la confirmación explícita de Gabi**, y siempre con
> `--ff-only`.
>
> **(Nota 2026-10-03: el párrafo anterior está superado.)** POST
> /gate-decisions está **integrado en `main` en 24cf60f** (fast-forward,
> confirmado por Gabi; GATE_SECRET ya estaba en su .env).
> **Bloque actual: GET /gate-avisos/{oportunidad_id}**, el endpoint de solo
> lectura para el aviso del Gate, implementado y verificado en la rama
> `feat/n0-aviso-gate`, **pendiente de merge** (Bloques A a D cerrados;
> plan docs/Plan_Endpoint_Aviso_Gate.txt). Misma regla: no hacer merge sin
> la confirmación explícita de Gabi, y siempre con `--ff-only`.
>
> **(Nota 2026-10-06: el párrafo anterior está superado.)** GET
> /gate-avisos/{oportunidad_id} está **integrado en `main` en 002cf48**
> (fast-forward de la rama `feat/n0-aviso-gate`, 5 commits, confirmado por
> Gabi; comprobado en GitHub el 2026-10-06: `main` = `002cf48`). La rama
> `feat/n0-aviso-gate` sigue existiendo en GitHub, apuntando al mismo
> commit; queda pendiente borrarla.
> **No hay ningún bloque de backend abierto.** El trabajo actual está en n8n,
> fuera de este repositorio: WF2 (aviso del Gate por email y Telegram)
> funciona y está publicado; el formulario del Gate (registra la decisión
> con POST /gate-decisions) está en curso. El siguiente bloque de backend
> previsto es el listado de pendientes para el recordatorio de WF3 (en su
> propia rama). Misma regla de siempre para cualquier merge.
>
> El router de n8n (D18.9: Chat Trigger → GET /leads/session/{lead_token}
> → Switch → Agente 1 o Agente 2) se construye en n8n, fuera de este
> repositorio; su endpoint está en main desde el 2026-09-25. Le falta el
> mensaje fijo para el estado 'perdida' (P6 del plan de /gate-decisions).
>
> **Numeración de los workflows de n8n (redefinida el 2026-09-28, P8 del
> plan de /gate-decisions; sustituye a la de D18):**
> - WF2 = aviso del Gate a administración, UNA sola vez, al aparecer el
>   Gate. Sin botones ni nodo Wait (D19).
> - WF3 = barridos periódicos, con dos consultas: el seguimiento de 48 h
>   (D13) y el recordatorio del Gate a administración cuando una
>   oportunidad lleva más de horas_recordatorio_gate (24) horas en
>   'pendiente_aprobacion'. Al registrarse la decisión, el estado cambia y
>   el recordatorio se detiene solo.
> - WF4 = errores (sin cambios respecto a D18).
>
> Trabajo posterior, fuera de esta rama (sección 11 del plan): el endpoint
> de solo lectura para el aviso del Gate (antes de WF2), WF2, WF3, el
> formulario de n8n con su credencial X-Gate-Secret, el mensaje del router
> para 'perdida' y la deuda técnica D3.
> (Nota 2026-10-06: de esa lista ya están hechos el endpoint de solo
> lectura para el aviso del Gate (en main, 002cf48) y WF2 (publicado en
> n8n). Siguen pendientes WF3, el formulario de n8n, el mensaje del router
> para 'perdida' y la deuda técnica D3.)
>
> **(Nota 2026-10-06, noche: corrige dos afirmaciones de este bloque que ya
> no son ciertas.)**
> 1. `main` está en `612a822`, no en `002cf48`. Las ramas
>    `feat/n0-aviso-gate` y `docs/claude-md-estado-2026-10-06` ya NO existen
>    en GitHub (comprobado con la lista de ramas del repositorio el
>    2026-10-06). En GitHub siguen tres ramas antiguas, ya integradas en
>    `main` (su último commit está en el historial de `main`):
>    `feat/n0-schemas-leads` (11417fe), `feat/n0-calculate-estimate`
>    (baedbfe) y `feat/n0-iva-y-seguimiento` (e74f4a6). Se pueden borrar.
> 2. El formulario del Gate en n8n (`WF_formulario_gate`, Fase 1) ya NO
>    está "en curso": está **cerrado y publicado** (2026-10-06). Llama a GET
>    /gate-avisos y a POST /gate-decisions con la credencial X-Gate-Secret.
>    Con él se registraron los descartes de los 5 casos de prueba 248-252
>    (registros 489, 490, 492 y 493; el de la 250 no se anotó). Por lo
>    tanto, de la lista de "Trabajo posterior" siguen pendientes WF3, el
>    mensaje del router para 'perdida' y la deuda técnica D3. La rama de
>    VISITA acordada (201) del formulario no se ha probado aún de punta a
>    punta.
> 3. WF2 lleva un ENLACE al formulario (un botón en el email y un enlace en
>    Telegram) con el número de oportunidad ya escrito. No es un botón de
>    aprobación: "sin botones ni nodo Wait" de D19 se sigue cumpliendo en el
>    sentido de que WF2 no espera ninguna respuesta.
> 4. **El repositorio es PÚBLICO** (comprobado en GitHub el 2026-10-06). Los
>    JSON exportados de n8n llevan el chatId de Telegram y el email de
>    administración: no se suben al repositorio sin sanearlos antes.
>
> **(Nota 2026-10-07.) Bloque actual: GET /llamadas-del-dia**, la lista
> diaria de llamadas para WF3, implementado y verificado en la rama
> `feat/n0-listado-wf3`, **pendiente de merge** (Bloques A a E cerrados;
> plan docs/Plan_Endpoint_Listado_WF3.txt). Misma regla de siempre: no hacer
> merge sin la confirmación explícita de Gabi, y siempre con `--ff-only`.
> Las decisiones de Gabi de este bloque son D25 (D25.2, D25.4, D25.7,
> D25.8, D25.14, D25.15 y D25.16) y D24.4: su documento lo añadirá el tutor
> a docs/ después del merge; hasta entonces se citan solo por su número. El
> siguiente bloque de backend previsto es POST /create-followup-task (paso
> de 'presupuesto_enviado' a 'seguimiento_pendiente', llamado por WF3 para
> cada seguimiento por abrir), en su propia rama.
>
> **(Nota 2026-10-07, tras el merge: el párrafo anterior está superado en dos
> puntos.)** La rama `feat/n0-listado-wf3` está **integrada en `main` en
> `f8a1f0f`** (fast-forward, confirmado por Gabi; la rama ya no existe en
> GitHub). El documento de D25 ya está en el repositorio:
> docs/Decisiones_D25_WF3_Seguimiento_y_Resultado_Llamadas.md. **No hay
> ningún bloque de backend abierto**; el siguiente es POST
> /create-followup-task (bloque 2 de D25.12).
>
> **(Nota 2026-10-08.) Bloque actual: POST /create-followup-task**, el paso
> de 'presupuesto_enviado' a 'seguimiento_pendiente' que WF3 pedirá por cada
> seguimiento por abrir, implementado y verificado en la rama
> `feat/n0-create-followup-task`, **pendiente de merge** (bloques A, B1, B2,
> C y D cerrados; plan docs/Plan_Endpoint_Create_Followup_Task.txt). Misma
> regla de siempre: no hacer merge sin la confirmación explícita de Gabi,
> después de la revisión del tutor, y siempre con `--ff-only`.
>
> **(Nota 2026-10-10: el párrafo anterior está superado.)** POST
> /create-followup-task está **integrado en `main` en `1af758a`**
> (fast-forward de `feat/n0-create-followup-task`, confirmado por Gabi;
> comprobado en GitHub el 2026-10-08). Esa rama sigue en GitHub apuntando al
> mismo commit: queda pendiente borrarla. **No hay ningún bloque de backend
> abierto.**
> **D26 (2026-10-10, docs/Decisiones_D26_Ficha_del_Cliente_en_la_Lista_Diaria.md)
> cambia el orden de D25.12 desde el bloque 3.** Hallazgo: GET
> /llamadas-del-dia no dice qué pidió cada cliente y la única ficha que
> existe (GET /gate-avisos) responde 409 sin_gate a los casos sin Gate, así
> que administración llamaría a ciegas a seguimientos y visitas del chat.
> Nuevo orden: (3) renombrado a "resultado de la llamada" sin cambios de
> comportamiento; (4a) ficha para CUALQUIER oportunidad (D26.2) y, en otra
> rama, resumen en cada elemento de la lista diaria: m2, nivel_acabados,
> incluye_cambios_estructurales y, en visitas, texto_cliente; SIGUE sin
> email ni importes (D26.1); (4b) registro del resultado de todas las
> llamadas + formulario ampliado (D25.9-D25.11); (5) WF3 en n8n, que NO se
> construye antes de 4a. El cambio de 4a toca dos cosas ya integradas: el
> contrato de GET /llamadas-del-dia (sus scripts comprueban claves EXACTAS)
> y GET /gate-avisos, que usan WF2 y el formulario del Gate, PUBLICADOS en
> n8n. Búsqueda de clientes por identificador: mejora posterior (D26.5).
>
> **(Nota 2026-10-10, tarde.) Bloque actual: 4a-1, GET
> /oportunidades/{oportunidad_id}/ficha**, implementado y verificado en la
> rama `feat/n0-ficha-oportunidad`, **pendiente de merge** (bloques A, B1,
> B2, C y D cerrados; plan docs/Plan_Endpoint_Ficha_Oportunidad.txt). Es un
> endpoint NUEVO (D26.6): GET /gate-avisos no cambia de contrato; B1 solo
> movió sus piezas comunes a app/services/datos_oportunidad.py, sin cambio
> de comportamiento (WF2 y el formulario del Gate no se tocan). Misma regla
> de siempre: no hacer merge sin la confirmación explícita de Gabi, después
> de la revisión del tutor, y siempre con `--ff-only`. El siguiente bloque
> de backend es 4a-2 (resumen de la solicitud en cada elemento de GET
> /llamadas-del-dia, D26.1), en su propia rama.
>
> **(Nota 2026-10-11: el párrafo anterior está superado.)** GET
> /oportunidades/{oportunidad_id}/ficha está **integrada en `main` en
> `ea96f39`** (la rama ya no existe en GitHub). **Bloque actual: 4a-2,
> resumen de la solicitud en GET /llamadas-del-dia** (D26.1), implementado
> y verificado en la rama `feat/n0-resumen-lista-diaria`, **pendiente de
> merge** (bloques A, B0, B2, C y D cerrados; no hay B1 porque P1 = b; plan
> docs/Plan_Resumen_Lista_Diaria.txt). Misma regla de siempre: no hacer
> merge sin la confirmación explícita de Gabi, después de la revisión del
> tutor, y siempre con `--ff-only`. El siguiente bloque de backend es 4b
> (registro del resultado de todas las llamadas, D26.6), y después WF3 en
> n8n (bloque 5).
>
> **(Nota 2026-10-12.) Bloque actual: 4b, registro del resultado de todas las
> llamadas** (D25.9-D25.11, D26.6), implementado y verificado en la rama
> `feat/n0-resultado-llamadas`, **pendiente de merge** (bloques A, B1, B2-M,
> B2, C y D cerrados; plan docs/Plan_Resultado_Llamadas.txt; contrato para el
> formulario docs/Contrato_Formulario_Llamadas.md). OJO: la migración paso12
> YA ESTÁ APLICADA en la base de datos real (solo expand; main sigue
> funcionando, demostrado con sus scripts). Misma regla de siempre: no hacer
> merge sin la confirmación explícita de Gabi, después de la revisión del
> tutor, y siempre con `--ff-only`. Decisiones de Gabi de este bloque:
> P1-P16 del plan y D29.1-D29.3 (su documento está en el Proyecto de
> claude.ai, todavía no en docs/). El siguiente bloque de backend es 4c, el
> aviso de "última llamada" (D29.2, regla horas_ultima_llamada); en n8n, el
> formulario ampliado (conectado a WF4, D27.4) y después WF3 (bloque 5).
>
> **(Nota 2026-10-12, rama feat/n0-agente2.)** El párrafo anterior está
> superado en dos puntos: 4b está **integrado en `main` (579fd00)**, y el
> aviso de "última llamada" ya no es un bloque de backend: **D30** revisa
> D29.2 y lo lleva a n8n (el documento lo tiene el tutor). **Bloque actual:
> el backend del Agente 2 (rama 1 de 2)**, implementado y verificado en la
> rama `feat/n0-agente2`, **pendiente de merge** (bloques A, B1, B2, B3, B4,
> B5-0, B5, C y D cerrados; plan docs/Plan_Agente2_Calendar.txt, hashes en
> su sección 12; contrato en la Adenda 5.11). Contenido: GET /leads/session
> con reforma y plazo_orientativo (D18.7, reglas de paso13); tool MCP
> get_business_rules; filtro D26.7 sobre visits.texto_cliente (300 en
> Pydantic); D31 (revisa D18.6), el hueco libre con bloqueo por día, en
> /visits, /resultados-llamada y el alias. OJO: la migración paso13 (7
> filas de reglas_negocio) YA ESTÁ APLICADA; paso13b (CHECK de texto_cliente
> a 1..300) está PREPARADA SIN APLICAR y va DESPUÉS del merge (sección 11
> del plan, con los comandos exactos de integración). Suite completa 48/48
> (Bloque C). Misma regla de siempre: no hacer merge sin la confirmación
> explícita de Gabi, después de la revisión del tutor, y siempre con
> `--ff-only`. Después del merge: reiniciar el uvicorn del 8000 SIN
> --reload, paso13b, y en n8n el Agente 2 (router, prompt, tools y memoria).
> La rama 2 (resumen de D28.1) va después, en su propia rama.
>
> **(Nota 2026-10-13: el párrafo anterior está superado.)** feat/n0-agente2
> está **integrada en `main` (9f4ddfc)**, la rama ya no existe, y el uvicorn
> del 8000 se reinició con ese código. **paso13b APLICADA el 2026-10-13 a las
> 20:57:21** (CHECK de texto_cliente a 1..300), en la rama corta
> `fix/n0-paso13b`, **pendiente de merge** (solo scripts, documentación y
> docs/schema_actual.sql; detalle en la sección 11.2 del plan). Misma regla
> de siempre: no hacer merge sin la confirmación explícita de Gabi, y siempre
> con `--ff-only`.
>
> **(Nota 2026-10-14.)** `fix/n0-paso13b` está **integrada en `main`
> (258ccf3)**. **Bloque actual: D32, el horario de visitas** (rejilla de
> D23.5, desplazamiento y disponibilidad AL OFRECER), implementado y
> verificado en la rama `feat/n0-horario-visitas`, **pendiente de merge**
> (bloques A, B1-B4, fase 2, revisión y D cerrados; plan
> docs/Plan_Horario_Visitas_D32.txt, hashes en su sección 10, integración en
> la 13; contrato en la Adenda 5.11 (e) y en docs/Contrato_Formulario_Llamadas.md,
> sección 4b). OJO: la migración paso14 (desplazamiento_min = 15 y
> dias_oferta_visita = 10) YA ESTÁ APLICADA (2026-10-13 23:21:45); paso14b
> (borrar paso_huecos_min) está PREPARADA SIN APLICAR y va DESPUÉS del
> merge. Suite completa 53/53 (2026-10-14). Misma regla de siempre: no
> hacer merge sin la confirmación explícita de Gabi, después de la revisión
> del tutor, y siempre con `--ff-only`. Después del merge: reiniciar el
> uvicorn del 8000 SIN --reload (app/ cambia) y paso14b.
>
> **(Nota 2026-10-14, 11:30: el párrafo anterior está superado.)**
> `feat/n0-horario-visitas` está **integrada en `main` (9d1f484)** y borrada;
> el uvicorn del 8000 se reinició con ese código (Gabi). **paso14b APLICADA
> el 2026-10-14 a las 11:27:42** (la fila paso_huecos_min ya no existe;
> reglas_negocio tiene 21 filas): check_migracion_paso14b 2/3 antes y 3/3
> después; regresión sin fallos. Detalle en la nota de la sección 13 del plan
> D32. Queda un fallo CONOCIDO en la suite: check_migracion_agente2 (33/38)
> exige todavía esa fila; se cambia en su propia rama.
>
> **(Nota 2026-10-14, tarde.)** El fallo conocido de check_migracion_agente2
> ya no existe: f81f6ac (en `main`) hace que su punto 3 exija que
> paso_huecos_min NO exista (CORRECTO en la suite de D33). **Bloque actual:
> D33, fecha_fin_visita** (decisión del tutor del 2026-10-14: WF5 copia las
> visitas a Google Calendar, las 'solicitada' como pendientes y las
> 'confirmada' como confirmadas; la base de datos sigue siendo la fuente de
> verdad, D31), implementado y verificado en la rama
> `feat/n0-fecha-fin-visita`, **pendiente de merge** (fase 1 7fd2277, fase 2
> 0584705, documentación en el commit del Bloque D; plan
> docs/Plan_Fecha_Fin_Visita_D33.txt). Una sola suma,
> reglas_visita.fin_de_visita() (en UTC, sin números escritos), con la
> lectura de reglas que ya hacía cada petición. P1: la repetición CON visita
> de /resultados-llamada lee las reglas (503 con log si faltan, como
> /visits). P2: el alias gana una clave (9). P3: el fin usa la duración
> VIGENTE. Sin migración (la base de datos no cambia); después del merge, reiniciar el uvicorn del 8000 SIN --reload (app/
> cambia). Misma regla de siempre: no hacer merge sin la confirmación
> explícita de Gabi, después de la revisión del tutor, y siempre con
> `--ff-only`.
>
> **(Nota 2026-10-20.)** `feat/n0-fecha-fin-visita` (D33) ya está en `main`.
> **Bloque actual: D28.1, resumen de la solicitud**, implementado y verificado
> en la rama `feat/n0-resumen-solicitud`, **pendiente de merge** (bloques A,
> B1, B2, B3, C y D; plan docs/Plan_Resumen_Solicitud_D28.txt, con los hashes
> y resultados en sus secciones 13-19; contrato en la Adenda 5.12). OJO: la
> migración paso15 (leads.resumen_solicitud + CHECK 1..300) YA ESTÁ APLICADA
> (2026-10-20 12:42:46; solo expand, main sigue funcionando). D36.8 revisa
> D28.1: el resumen nunca da 422 (nota fechada en D28). En la rama también:
> H-B1-1 (21a83c8) y H-C-2 (088a22b; los dos SHA-256 de su mensaje no son
> reales, corregidos en 77bb371 y en la sección 16 del plan). Suite 57/57 en
> una pasada válida. Misma regla de siempre: no hacer merge sin la
> confirmación explícita de Gabi, después de la revisión del tutor, y siempre
> con `--ff-only`. Después del merge: reiniciar el uvicorn del 8000 SIN
> --reload (app/ cambia); en n8n, la tool del Agente 1 (requisito C5), su
> prompt (D37.1: rangos como "entre X y Y") y la plantilla de WF2.
>
> **(Nota 2026-10-22.)** El párrafo anterior está superado:
> `feat/n0-resumen-solicitud` (D28.1) está en `main` (315a20c, antecesor de
> `main`). Después se integró **D39.9** (6ed9ec0 y aba39c4, 2026-10-21):
> tarifas_base.incluye_tipico con 12 textos nuevos, APLICADO en la base
> real (12 UPDATE, 12 logs, ids 16568-16579), con
> scripts/datos_d39_9_incluye_tipico.py (vuelta atrás escrita, sin
> ejecutar: `--deshacer-d39-9`), el JSON docs/datos/D39_9_incluye_tipico.json
> y scripts/check_datos_d39_9_incluye_tipico.py (`--esperado anterior|nuevo`;
> 44/44 con `nuevo` tras aplicar). De ahí sale la regla H-D39-4 ("Cómo
> trabajamos"). **Bloque actual: bloque 5, quitar el alias POST
> /gate-decisions** (D26.6), en la rama `fix/n0-quitar-alias-gate-decisions`,
> **pendiente de merge** (PASO 1, fase 1, SIN ejecutar; la fase 2 la decide
> Gabi). Decisiones del tutor y de Gabi: P1 = C (check_gate_decisions_service
> se retira; check_gate_decisions_http se queda con el arranque, H-0-6, el
> 404 del alias y el 401 de /resultados-llamada; las comprobaciones que solo
> tenía el alias pasan a check_resultados_llamada_http, CASO 13c, y
> check_resultados_llamada_service, S8), P2 = B (solo_gate quitado ahora),
> P3 (esquemas del alias borrados) y P4 = B (documentos vivos reescritos con
> su línea "Cambio 2026-10-22"; los registros históricos no se tocan).
> Misma regla de siempre: no hacer merge sin la confirmación explícita de
> Gabi, después de la revisión del tutor, y siempre con `--ff-only`.
> Después del merge: reiniciar el uvicorn del 8000 SIN --reload (app/ cambia).
> (Nota 2026-10-22, tarde: la fase 2 del bloque 5 está HECHA: verde 18/18,
> 57/57 y 210/210; los 15 mutantes en rojo (U1, U2, U4-U14, 401 y 404, con
> U11 repetido por un Modern Standby de 2 s); regresión de los 17 scripts que
> crean visitas 17/17; suite 57/57 en una pasada válida. Pendiente solo el
> veredicto del tutor y el merge, que confirma Gabi.)
> (Nota 2026-10-22, rama docs/n8n-export-bloque4, pendiente de merge: n8n/
> está al día con los emails al cliente del bloque 4 (chat, formulario de
> llamadas, WF3 y WF7_email_cliente, nuevo), exportados tras publicar y
> saneados con la convención de n8n/README.md. Solo documentación.)
>
> **(Nota 2026-10-22, tarde.) Bloque actual: D42, la dirección de la obra**
> (hallazgo H-D39-1; decisión D42 y veredicto del tutor al PASO 0), en la rama
> `feat/n0-direccion-obra`, **pendiente de merge** (PASO 1, fase 1, SIN
> ejecutar; la fase 2 la decide Gabi). Plan docs/Plan_Direccion_Obra_D42.txt;
> contrato en la Adenda 5.13 y en docs/Contrato_Formulario_Llamadas.md.
> Decisiones: P1 = A (direccion_obra opcional en la API, obligatoria solo en
> el formulario; B queda para N1), H1 (la repetición no mira ni escribe la
> dirección; el upsert queda como protección), H2 (el error de Postgres al
> guardarla se relanza saneado, "from None", sin el texto de la fila), H4
> (ningún script imprime una fila de direcciones_obra). H3, limitación
> aceptada sin código: un 422 de FastAPI repite lo enviado ("input"), también
> la dirección, a quien lo envió; no se guarda en ninguna tabla; según la
> exportación de n8n (f1301af), el formulario solo enseña "El sistema ha
> rechazado el formato de los datos" y WF4 recibe el código y su título,
> nunca el cuerpo; el historial de ejecuciones de n8n guarda lo escrito en el
> formulario (limitación de n8n, no del backend). OJO: la migración paso16
> (tabla direcciones_obra, la 11.ª, con RLS) está PREPARADA SIN APLICAR: se
> aplica en la fase 2 tras "confirmo la migración paso16", y después se
> regenera docs/schema_actual.sql. (Nota 2026-10-22, noche: paso16 APLICADA
> el 2026-10-22 a las 19:19:49, tras la frase del tutor; check 2/42 antes y
> 42/42 después; docs/schema_actual.sql regenerado. Fase 2 HECHA: verdes,
> N1-N16 en rojo, regresión 19/19 y suite 60/60; pendiente solo el veredicto
> del tutor y el merge, que confirma Gabi.) La foto de datos reales pasa a 11 tablas
> (solo SHA-256 y recuentos). Misma regla de siempre: no hacer merge sin la
> confirmación explícita de Gabi, después de la revisión del tutor, y siempre
> con `--ff-only`. Después del merge: reiniciar el uvicorn del 8000 SIN
> --reload (app/ cambia); en n8n (tutor y Gabi), el campo obligatorio del
> formulario y la dirección en la página de la ficha.
> (Nota 2026-10-22, H-D42-1, hallazgo del tutor en el PASO 1, PARA LA
> REVISIÓN: psycopg2 rechaza un texto con el carácter nulo U+0000 antes de
> enviarlo, con ValueError, no psycopg2.Error. En direccion_obra se corrige
> en esta rama (422, C1). El campo "informe" de POST /resultados-llamada
> tiene probablemente el mismo hueco (un 500 en vez de un 422): es deuda
> anterior y NO se toca aquí; lo mismo puede pasar en otros textos libres.)
>
> **(Nota 2026-10-23.)** `feat/n0-direccion-obra` (D42) ya está en `main`
> (c0db982). **Bloque actual: D45, antelación mínima de las visitas**
> (hallazgo H-129-1; decisión D45 y veredicto del tutor al PASO 0, APROBADO
> con 6 precisiones), en la rama `feat/n0-antelacion-visitas`, **pendiente de
> merge** (PASO 1, fase 1, SIN ejecutar; la fase 2 empieza cuando Gabi
> escriba "fase 2"). Plan docs/Plan_Antelacion_Visitas_D45.txt (recuento
> previsto en su sección 9); contrato en la Adenda 5.14 y en
> docs/Contrato_Formulario_Llamadas.md. OJO: la migración paso17 (dos filas
> de reglas_negocio) está PREPARADA SIN APLICAR: tiene modo --ensayo (deshace
> todo) y la real solo tras "confirmo la migración paso17"; se aplica ANTES
> del merge (expand). El formulario WF_formulario_llamadas (plan B, D43.3) NO
> se cambia; prompts/agente2_asesor.md va en un commit aparte con el texto del
> tutor. Misma regla de siempre: no hacer merge sin la confirmación explícita
> de Gabi, después de la revisión del tutor, y siempre con `--ff-only`.
> Después del merge: reiniciar el uvicorn del 8000 SIN --reload (app/ cambia).
> (Nota 2026-10-23, noche: la fase 2 está HECHA y paso17 APLICADA; detalle en
> la nota de la suite. Pendiente P-D45-1: repetir solo
> check_resultados_llamada_http cuando D46 permita cancelar las visitas de
> prueba del 26/10 desde la pantalla; hasta entonces V13 la cubre solo la
> prueba pura. Pendiente solo el veredicto del tutor y el merge, que confirma
> Gabi.)
> (Nota 2026-10-26: D45 está en main (abe22a6). P-D45-1 CERRADO en la rama de
> D46: check_resultados_llamada_http 215/215 el 2026-10-26 a las
> 15:43:57-15:47:51 (el 27/10 tenía 3 horas libres), así que V13 de D45
> queda cubierto también por HTTP; detalle en la 11.6 del plan D46.)
>
> **(Nota 2026-10-26.) Bloque actual: D46, cambiar o cancelar una visita YA
> confirmada** (hallazgo H-129-2; decisión D46 y veredictos del tutor al PASO
> 0, al PASO 1 y a la fase 2, APROBADA), en la rama `feat/n0-cambio-visita`,
> **pendiente de merge** (PASO 1 ab1036b, fase 2 a802dba, y el commit de
> P-D45-1 y documentación). Plan docs/Plan_Cambio_Visita_D46.txt (predicción
> y resultados en su sección 11); contrato en la Adenda 5.15 y en
> docs/Contrato_Formulario_Llamadas.md (4c). OJO: la migración paso18 (tabla
> cambios_visita, la 12.ª, con RLS) YA ESTÁ APLICADA (2026-10-25 20:54:49,
> solo expand; main no la lee). Misma regla de siempre: no hacer merge sin la
> confirmación explícita de Gabi y siempre con `--ff-only`. Después del
> merge: reiniciar el uvicorn del 8000 SIN --reload (app/ cambia). Lo que
> queda en n8n (sección 9 del plan; el tutor): la pantalla de llamadas
> (botones con visita_modificable, fecha_actual/hora_actual, GET
> /huecos-libres para la hora nueva, cambios_visita, los 409 y 422 nuevos,
> WF5 borrar/guardar y WF7, sin avisar a WF4 los errores del operador); el
> texto de la descripción del evento de WF5; WF7 (opcional, un tipo
> visita_cambiada); WF_ficha_oportunidad (cambios_visita y la etiqueta del
> evento visita_confirmada_cancelada).
>
> **(Nota 2026-10-27.)** D46 está en `main` (8777032). La exportación saneada
> de n8n tras D43-D46 está HECHA en la rama `docs/n8n-export-d46`, **pendiente
> de merge** (solo documentación y JSON de n8n/; ningún .py): WF_pantalla_llamadas
> NUEVO (sustituye al formulario), y WF3, WF2, WF5, la ficha y el chat
> actualizados. WF_formulario_llamadas queda **Retirado** el 27/10
> (despublicado, no borrado). docs/Contrato_Formulario_Llamadas.md, 4c: con el
> 200 (repetición) WF7 NO manda email (O-D46-1). Misma regla de siempre: no
> hacer merge sin la confirmación explícita de Gabi, y siempre con `--ff-only`.

Hecho y verificado con ejecución real: scaffolding, servidor MCP
montado, pool de Postgres, /health y /health/db, apagado ordenado,
esquemas Pydantic de leads, get_transactional_connection() (commit /
rollback automáticos), CHECK en oportunidades.tipo_reforma, y POST
/leads completo de punta a punta (HTTP real: 201 con datos válidos, 422
sin tocar la base de datos con datos inválidos).

POST /calculate-estimate (REST + tool MCP) implementado, verificado e
INTEGRADO EN main: migración paso3 (estado
'pendiente_aprobacion' + UNIQUE presupuestos.oportunidad_id, D8),
check_migracion 14/14, check_estimate_service 83/83,
check_calculate_estimate_http 20/20, check_mcp_calculate_estimate 19/19.
Detalle completo: docs/EndPoint_Calculate_estimate_final.txt.

Umbral del Gate por categoria (D9) implementado, verificado e integrado
el 2026-09-20: migracion paso4 (tabla umbrales_gate),
check_migracion_umbrales_gate 17/17, check_estimate_service 107/107,
check_calculate_estimate_http 21/21, check_mcp_calculate_estimate 19/19.
Detalle: docs/Umbral_Gate_por_Categoria_D9_final.txt. El umbral de
parcial_acabados (10.000 EUR) quedo CERRADO el 2026-09-20 (paso5): ya no
hay ningun umbral provisional. Defecto operativo abierto, anotado en
D10: nada impide tecnicamente un parcial_acabados de superficie grande
(70 m2 en nivel medio dan 22.540 EUR), que activaria el Gate.

Tope de superficie (2026-09-20): m2 acotado a 0 < m2 <= 500 con defensa
doble (Decimal con le=MAX_M2_LEAD en LeadCreate, y CHECK
chk_leads_m2_rango sobre la expresion JSONB, migracion paso6).
check_migracion_m2_leads 23/23. Antes, un lead de 60.000 m2 se aceptaba
y reventaba al calcular, porque importe_max no cabe en NUMERIC(10,2).

RESUELTO (2026-09-20): scripts/check_exception_handler.py daba 22/28
porque no enviaba X-Webhook-Secret (el script es anterior a la
autenticacion del webhook). Corregido: ahora 32/32. La causa no era el
manejador de excepciones, que funcionaba bien.

Guarda de conn.closed en get_transactional_connection() (D16),
integrada en main el 2026-09-24 por fast-forward (commit 9c4197f): el
rollback del bloque except ya no lanza InterfaceError encima del error
original cuando la conexión ya está cerrada, que es la misma guarda que
get_db_connection() tenía desde antes. Verificado con
scripts/check_rollback_conn_cerrada.py 17/17 (tres casos: conexión
cerrada + error, conexión viva + error, y sin error), y en negativo
contra las DOS versiones rotas conocidas, 14/17 cada una: la original
sin guarda (sale InterfaceError con el error real encadenado debajo) y
una con la guarda mal indentada (no sale nada, la excepción se traga).
Regresión sin cambios: check_create_lead_service 31/31 y
check_leads_endpoint_http 12/12. Queda abierto el caso OperationalError,
cuando psycopg2 todavía no ha detectado que la conexión murió y closed
sigue valiendo 0.

Tiempos límite y validación de conexiones del pool (rama
fix/n0-timeouts-db, commit c1c075b, 2026-09-25): arregla el
OperationalError en el primer execute tras un corte de red o del pooler
(4 veces en dos días, la última bloqueando POST /leads desde n8n).
Verificado con scripts/check_conexion_muerta.py 18/18 y con una prueba
real: uvicorn inactivo 8 min, sus 2 conexiones matadas con
pg_terminate_backend, POST /leads 201 con dos [AVISO] ... descartada, y
GET /health/db 200. En negativo: main FALLO y la versión con un solo
reemplazo 16/17. Suite completa 23/24 (el fallo es
check_tipo_reforma_constraint, ver Pendiente). Gabi verificó además una
conversación completa desde el chat de n8n con esta rama. Límites
abiertos: la conexión medio abierta en Windows y la muerte de la
conexión DURANTE una operación (el rollback del except puede tapar el
error original: el caso OperationalError de D16 sigue abierto).
Detalle: docs/TFM_Resumen_Sesion_Timeouts_DB_N0.txt.

GET /leads/session/{lead_token} (rama feat/n0-endpoint-sesion, commit
5f0b92d, 2026-09-25): el endpoint del router de n8n. 200 siempre que la
petición sea válida (un token inexistente da existe=false), 401 sin
X-Webhook-Secret (antes que el 422), 422 con más de 100 caracteres, y
NUNCA importes (D18.5: la consulta no los lee y el esquema no los
tiene). Verificado con scripts/check_lead_session_http.py 24/24; prueba
en negativo en dos niveles: con los importes solo en services/, 24/24
(los quita el response_model); con los importes también en el esquema,
14/24. Suite completa 24/25 (el fallo es check_tipo_reforma_constraint).
Detalle: docs/Plan_Endpoint_Sesion_Lead.txt y la sección 5.3 de la
Adenda.

POST /visits (rama feat/n0-visits, 2026-09-26; commits 41c3420
migración, 6bde841 borrados de logs, 6bf9c1d código, 9ddf148 y d3df19a
verificación): registra la SOLICITUD de visita técnica que el Agente 2
pide desde el chat (no es una reserva). Entrada lead_token, fecha, hora
(Europe/Madrid) y texto_cliente, nunca oportunidad_id; franjas y duración
en reglas_negocio. Migración paso9 (estado 'solicitada', texto_cliente,
updated_at, fecha_propuesta NOT NULL, índice único parcial de una visita
activa por oportunidad, 5 reglas): check_migracion_visitas 28/28 (en
negativo, sin índice ni CHECK, 24/28). check_visits_service 16/16 (cambio
de hora 23/10 -> +02:00 y 26/10 -> +01:00; con un desfase fijo +02:00,
4/6; configuración incompleta -> 503 con el log conservado).
check_visits_http 55/55 (401, 404, 409 x4, 422 de formato y de negocio,
201 nueva, 200 repetición, 201 sustitución, concurrencia, 409
visita_confirmada, 0 importes, 201 <-> creado en todas, y "no toca nada
ajeno"). En negativo: sin la comprobación del Gate 51/54, sin la del final
de la franja 44/54, sin FOR UPDATE 50/54 (el índice parcial frena el
duplicado, pero con un 500). Suite completa 27/28 (el fallo es
check_tipo_reforma_constraint). Detalle: docs/Plan_Endpoint_Visits.txt y
la sección 5.4 de la Adenda (códigos y limitaciones: Gate permanente,
festivos, hora exacta, visita confirmada).

POST /gate-decisions (rama feat/n0-gate-decisions, 2026-09-28 a
2026-10-01, pendiente de merge; hashes por bloque en la sección 10 del
plan): administración registra el resultado de su llamada a un cliente con
Gate (D19): visita_acordada (fecha y hora; crea una visita 'confirmada' y
la oportunidad pasa a 'visita_agendada') o descartar (motivo; pasa a
'perdida'), con el informe siempre. (Nota 2026-10-06: "pendiente de merge"
ya no es cierto: integrado en main en 24cf60f, que está en el historial de
main.) Cabecera X-Gate-Secret propia. Tabla
nueva decisiones_gate (paso10 + paso10b, con RLS):
check_migracion_decisiones_gate 38/38 (en negativo, quitando cada
restricción, de 34/38 a 37/38). Reglas de fecha compartidas con /visits en
app/services/reglas_visita.py (Bloque C1, sin cambio de comportamiento:
check_visits_http 55/55, check_visits_service 16/16).
check_gate_decisions_http 121/121 (arranque con GATE_SECRET vacío o
repetido, 401 incluido el valor de WEBHOOK_SECRET, 12 x 422 de forma, 404,
409 x5 motivos, 422 de fecha, 201 con todos sus efectos, 200 sin escribir
nada, concurrencia, regresión con /visits y /leads/session, 0 importes);
check_gate_decisions_service 14/14 (503 con el log conservado y su
"origen" en los dos endpoints). En negativo, sobre copias: dependencia de
WEBHOOK_SECRET 54/121, sin FOR UPDATE 117/121 (dos 500 por
UniqueViolation), sin sin_gate 120/121, estado antes que repetición
112/121, informe en el log 118/121, repetición que escribe 117/121, y en
el servicio 13/14 y 12/14. Suite completa 31/32 (el fallo es
check_tipo_reforma_constraint). Detalle: docs/Plan_Endpoint_Gate_Decisions.txt
y la sección 5.5 de la Adenda (limitaciones: Gate permanente, decisión
definitiva, operador no identificado y P11, aceptada como limitación
documentada el 2026-09-28).

GET /gate-avisos/{oportunidad_id} (rama feat/n0-aviso-gate, 2026-10-03,
pendiente de merge; plan fd96923 y cf51b92, código f3beb9a, pruebas
b6e9adf): los datos de un caso con Gate para el aviso de WF2 (email) y el
formulario de /gate-decisions. Solo lectura, solo casos con Gate (404 /
409 sin_presupuesto / 409 sin_gate), X-Gate-Secret, contacto de la
solicitud (null y origen "no_disponible" en los leads sin 'contacto'),
importes con IVA (columnas) y sin IVA (DEDUCIDOS, exactos por
construcción), umbral_gate_vigente y la decisión sin el informe. Contrato
y limitaciones: sección 5.6 de la Adenda. estado_oportunidad usa el Enum
EstadoOportunidad (corrección del tutor sobre el Bloque B; prueba mínima
19/19 tras el cambio). check_aviso_gate_http 75/75 (puerto 8023;
2147483648 y 99999999999999999999 -> 404, nunca 500; visitas calculadas
con zoneinfo, 2026-10-26T08:30+01:00 y 2027-03-29T08:30+02:00; 30
peticiones, 0 respuestas 500). check_aviso_gate_service 18/18 (barrido de
1.200.000 importes con IVA 21, 10, 4 y 0, 0 distintos, 33.000 en la
frontera de redondeo). En negativo, sobre copias: N1 dependencia del
webhook 27/75 (48 fallos en cascada: la llave del webhook abre y la del
Gate deja de abrir); N2 sin sin_gate 72/75 y 17/18; N3 contacto de la
ficha 71/75 y 15/18; N4 desfase fijo 73/75 y 17/18; N5 escribe en logs
74/75; N6 float 75/75 y 18/18, MUTANTE EQUIVALENTE no detectable por
diseño (margen mínimo de 0,00087 € hasta la frontera con IVA 21 %, error
de float del orden de 1e-8 € con el máximo de NUMERIC(10,2); float sigue
prohibido por D4); N7 informe en la respuesta 72/75; N8 sin redondear el
sin IVA 71/75 y 15/18. Regresión: check_gate_decisions_http 121/121,
check_visits_http 55/55, check_lead_session_http 24/24. Suite completa
34/34 en UNA pasada válida (ver "Suite de verificación").
(Nota 2026-10-06: "pendiente de merge" ya no es cierto: integrado en main,
002cf48.)

GET /llamadas-del-dia (rama feat/n0-listado-wf3, 2026-10-07, pendiente de
merge; plan a04d4c4 y ed99ae0, migración 39c117b, código 011b668, arreglo
5a35d24, pruebas 14c17a4): la lista diaria de llamadas de WF3, sin entrada,
con X-Gate-Secret. Cinco apartados (Gate sin decisión registrada,
seguimientos por abrir y abiertos, visitas sin confirmar y del próximo día
laborable), cada elemento con oportunidad_id, motivo, tipo_reforma,
contacto {nombre, telefono, origen} y sus fechas en hora de Madrid. NUNCA
email ni importes. Plazos ESTRICTOS desde presupuestos.created_at con
horas_recordatorio_gate (24) y horas_seguimiento_presupuesto (48, regla
NUEVA, migración paso11), enteros entre 1 y 8760 (antes no había máximo y
99.999.999 daba "timestamp out of range" y un 500: demostrado y
corregido). Solo
lectura con SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY como
primera orden; 503 configuracion_incompleta SIN log, con [AVISO] en stderr.
Contrato y limitaciones: fila 8 y sección 5.7 de la Adenda.
check_migracion_regla_seguimiento 9/9 (2/9 antes de migrar).
check_listado_llamadas_service 39/39 (límite exacto al microsegundo en una
transacción con ROLLBACK). check_listado_llamadas_http 88/88 (puerto 8024).
En negativo, sobre copias (servicio | HTTP): N1 webhook 39/39 | 46/88; N2
escribe en logs 38/39 | 87/88; N2b INSERT con READ ONLY 39/39 | 54/88; N3
base en la oportunidad 37/39 | 78/88; N4 <= 37/39 | 88/88; N5 mañana
natural 37/39 | 88/88; N6 +02:00 fijo 35/39 | 86/88; N7 ficha 39/39 |
84/88; N8 email 39/39 | 81/88; N9 sin fecha_ultimo_contacto 39/39 | 87/88;
N10 sin exclusión de d) 38/39 | 86/88; N11 plazos en el código 35/37 |
80/88; N12 sin máximo 34/38 | 85/88. Suite completa 37/37 en UNA pasada
válida (ver "Suite de verificación"). Gotcha encontrado al probar: Python
resta dos datetime con el MISMO objeto de zona "de reloj de pared" (da
24 h el día de 25); para medir la duración real, restar en UTC.
(Nota 2026-10-10: "pendiente de merge" ya no es cierto: integrado en main en f8a1f0f, comprobado en GitHub.)

check_tipo_reforma_constraint arreglado (rama feat/n0-gate-decisions,
commit 145dcea, 2026-10-03): su comprobación final contaba TODAS las
oportunidades de la tabla y exigía 0, así que daba "HAY FALLOS" siempre
que hubiera oportunidades reales. Ahora cuenta solo las suyas: las que
cuelgan del cliente con su EMAIL_PRUEBA (@example.invalid, único por el
índice clientes_email_lower_key). Con la tabla real llena (20
oportunidades): CORRECTO por primera vez. En negativo, una copia que deja
guardada una oportunidad PROPIA antes de la comprobación da HAY FALLOS
("oportunidades descartables restantes: 1"); sus filas se borraron por id
exacto.

check_migracion_iva_y_seguimiento arreglado (2026-10-03, misma rama): su
comprobación "ninguna fila tiene valor todavía" exigía que
oportunidades.fecha_ultimo_contacto fuera NULL en TODA la tabla. Era el
estado de un momento concreto que esta rama dejó de hacer cierto (POST
/gate-decisions la escribe, P5), y la primera decisión real del Gate la
habría hecho fallar para siempre. Ahora el recuento solo se imprime como
[INFO], con una nota fechada en el código: 16/16. La prueba en negativo
es el fallo real de ese mismo día con la versión anterior (16/17, por las
oportunidades de prueba que dejó un corte de check_gate_decisions_http).

Scripts de verificación frágiles (anotados, sin arreglar): hay tokens
fijos compartidos entre scripts: "tok-estr" lo usan check_calculate_estimate_http y
check_mcp_calculate_estimate, así que si uno se corta antes de limpiar,
el otro recibe el lead viejo (convendría un prefijo por script o
uuid4). check_graceful_shutdown y check_mcp_connection tienen el puerto
8000 escrito en el código: deberían leerlo de una variable de entorno
(fue la causa del incidente del 2026-09-25; mientras tanto, se ejecutan
desde una copia con otro puerto). De las dos fragilidades anotadas en
docs/TFM_Resumen_Sesion_Contacto_e_Idempotencia_N0.txt, sección 8, la de
check_migracion_m2_leads (max(tipo) sobre todos los logs) quedó corregida
el 2026-09-26 (6bde841); en check_exception_handler sigue abierta la de
su prueba D, que solo mira el código 201.
Conexiones directas de los scripts sin protección frente a cortes: tres
cortes en esta rama, todos con OperationalError "server closed the
connection unexpectedly" en una conexión abierta con psycopg2.connect
directo, que no tiene las protecciones del pool (keepalives y validación
con SELECT 1). El primero, en check_visits_http el 2026-09-30 (Bloque C1,
anotado en el mensaje de 520aae1); el segundo, en la conexión de
observación de check_transactional_connection, caso 6, el 2026-10-03
(145dcea); el tercero, en la de check_gate_decisions_http, caso 8, el
mismo día (d029322), que dejó 10 oportunidades propias sin limpiar hasta
la siguiente ejecución. Repetidos aisladamente, los dos de hoy dieron OK.
Hipótesis SIN VERIFICAR: una conexión que
pasa un rato sin usarse durante una suite larga muere por un corte de red.
Solución propuesta para después de N0: una función compartida para las
conexiones de los scripts. No se arregla en esta rama (es la primera
tarea pendiente después del merge, ver "Pendiente").
(Nota 2026-10-03, rama fix/ejecutor-suite: la hipótesis de arriba queda
REFUTADA por lo medido. Experimento con dos conexiones directas, una a
secas y otra con los parámetros de init_pool, y 15 min sin usarse: SIN
suspensión de Windows (13:30-13:45, suspensión bloqueada y 0 eventos 506/
507 en el intervalo), las DOS sobrevivieron; CON suspensiones (12:54-13:31,
tres Modern Standby con la red desconectada), las DOS murieron con el
mismo OperationalError, también la protegida con keepalives. El corte de
check_gate_decisions_http coincide al segundo con una suspensión
(12:29:14-12:29:57; su log se cerró a las 12:29:57). Los cortes de
check_transactional_connection (03/10) y check_visits_http (30/09): causa
SIN DETERMINAR, no hay registros de la hora de esas pasadas. El arreglo
es impedir la suspensión durante la suite (docs/Plan_Ejecutor_Suite.txt);
conectar_script() queda solo como mejora menor, por paridad de
statement_timeout (2 min en las conexiones de los scripts frente a 30 s
en las del pool), no como arreglo de los cortes.)
(Nota 2026-10-03, posterior a la anterior: la causa queda DETERMINADA en
los tres cortes. Los TRES coinciden con un Modern Standby con la red
desconectada, según el registro de Windows de este portátil (eventos
Kernel-Power 506/507 y 172, y NetworkProfile 10001/10000): 30/09
14:26:07-14:28:47 (check_visits_http; el corte ocurrió entre 14:25:22 y
14:29:09, según el log de esa suite), 03/10 12:05:58-12:07:24
(check_transactional_connection; entre 12:05:51 y 12:07:25, según la
fecha de creación de los logs de la primera suite) y 03/10
12:29:14-12:29:57 (check_gate_decisions_http). Los eventos del Wi-Fi
(WLAN-AutoConfig) no muestran nada en ningún intervalo. Gabi había dicho
que en dos de ellos estaba usando el ordenador; tras ver el registro,
retira ese testimonio: no notó las suspensiones. Manda el registro.
Conclusión: la causa de los cortes es la suspensión del portátil (3 de 3,
más el experimento: 15 min sin usarse NO matan la conexión y la
suspensión sí). Ya no hay causa "sin determinar".)
(Nota 2026-10-03, rama feat/n0-aviso-gate: check_foreign_keys informa
"Total de FOREIGN KEY en el esquema: 9", pero hay 6 restricciones de clave
foránea: cuenta por COLUMNAS, y la clave doble (visita_id, oportunidad_id)
de decisiones_gate hacia visitas sale como 4 filas. Anotado, sin arreglar.
Es uno de los cinco scripts de P4: su salida se revisa a mano.)

Suite de verificación (2026-10-03): 32 scripts check_*.py. Ninguno falla
ya por su propia lógica: check_tipo_reforma_constraint, el fallo fijo
anterior, da CORRECTO desde 145dcea. Las dos pasadas de hoy NO dieron
32/32 de una vez: 31/32 (145dcea) y 30/32 (d029322), en los dos casos por
cortes del pooler en conexiones directas de los scripts (ver frágiles);
los scripts afectados, repetidos aisladamente, pasaron (11/11 sin fallos,
121/121 y 17/17). Antes (2026-10-01): 31/32, con
check_tipo_reforma_constraint como único fallo. Desde
la rama de /gate-decisions se suman check_migracion_decisiones_gate,
check_gate_decisions_http (puerto 8019, nunca el 8000),
check_gate_decisions_service y check_schema_actual_ejecutable (ejecuta
docs/schema_actual.sql en un esquema de prueba dentro de una transacción
que termina en ROLLBACK y lo compara con public: 10/10). Se ejecuta con el
procedimiento seguro: check_graceful_shutdown y check_mcp_connection desde
una copia con otro puerto, y solo se detiene el uvicorn propio, por su PID.
(Nota 2026-10-03, rama feat/n0-aviso-gate: la suite tiene ahora 34
scripts, con check_aviso_gate_http y check_aviso_gate_service. 34/34 en
UNA pasada válida el 2026-10-03, de 17:45:51 a 17:52:45, con la regla de
uso (Gabi confirmó enchufado y tapa abierta) y sin ningún Kernel-Power
506/507 en el intervalo ("ninguna suspension: la pasada vale"). Salida de
los cinco scripts de P4 revisada a mano: sin fallos.)
(Nota 2026-10-07, rama feat/n0-listado-wf3: la suite tiene ahora 37
scripts, con check_migracion_regla_seguimiento,
check_listado_llamadas_service y check_listado_llamadas_http (puerto 8024,
nunca el 8000). 37/37 en UNA pasada válida el 2026-10-07, de 16:58:30 a
17:06:37, con la regla de uso (Gabi confirmó enchufado y tapa abierta) y
sin ningún Kernel-Power 506/507 en el intervalo ("ninguna suspension: la
pasada vale"). check_graceful_shutdown y check_mcp_connection desde copias
en los puertos 8030 y 8031; uvicorn del 8031 detenido por su PID (el del
log, igual al de netstat). Salida de los cinco scripts de P4 revisada a
mano: sin fallos.)
(Nota 2026-10-08, rama feat/n0-create-followup-task: la suite tiene ahora
39 scripts, con check_followup_service y check_followup_tasks_http (puerto
8025, nunca el 8000). 39/39 en UNA pasada válida el 2026-10-08, de
17:39:05 a 17:47:13, con la regla de uso (Gabi confirmó enchufado, tapa
abierta y memoria liberada: Docker/n8n y navegador cerrados) y sin ningún
Kernel-Power 506/507 entre 17:38 y 17:48 ("ninguna suspension: la pasada
vale"). check_graceful_shutdown y check_mcp_connection desde copias en el
8030 y el 8031; uvicorn del 8031 detenido por su PID (el del log, igual al
de netstat). Salida de los cinco scripts de P4 revisada a mano: sin
fallos. Aviso de memoria: con 8 GB, una tanda larga de pruebas en negativo
en segundo plano se cortó por falta de memoria con Docker/n8n abierto; se
ejecutaron de una en una tras cerrarlo.)
(Nota 2026-10-10, rama feat/n0-ficha-oportunidad: la suite tiene ahora 41
scripts, con check_ficha_oportunidad_service y check_ficha_oportunidad_http
(puerto 8026, nunca el 8000). 41/41 en UNA pasada válida el 2026-10-10, de
16:41:10 a 16:49:58, con la regla de uso (Gabi confirmó enchufado, tapa
abierta, y Docker/n8n y navegador cerrados) y sin ningún Kernel-Power
506/507 en el intervalo. OJO: con el minuto de margen a cada lado, el
comando encontró un 507 a las 16:40:50: era la SALIDA de un Modern Standby
(506 a las 16:30:04) que terminó 20 s antes de empezar la suite; el
portátil llevaba 30 min sin tocarse. Esa suspensión SÍ se solapó con cinco
pruebas en negativo, que se repitieron después sin suspensión y con los
mismos resultados. Lección: en tandas largas sin intervención de Gabi, la
pantalla a 30 min también suspende las pruebas en negativo; comprobar
506/507 también para ellas. check_graceful_shutdown y check_mcp_connection
desde copias en el 8030 y el 8031; uvicorn del 8031 detenido por su PID
(19892, el del log, igual al de netstat). Salida de los cinco scripts de
P4 revisada a mano: sin fallos.)
(Nota 2026-10-11, rama feat/n0-resumen-lista-diaria: siguen siendo 41
scripts. 41/41 en UNA pasada válida, de 00:22:18 a 00:31:37, con la regla
de uso (Docker/n8n y navegador cerrados, confirmado por Gabi) y sin ningún
Kernel-Power 506/507 entre 00:17 y 00:33. check_graceful_shutdown y
check_mcp_connection desde copias en el 8030 y el 8031. Salida de los
cinco scripts de P4 revisada a mano: sin fallos. Gotcha encontrado: en
Windows, el python.exe del venv es un LANZADOR que arranca el intérprete
real como proceso HIJO. Un uvicorn lanzado con Popen sobre él tiene OTRO
PID que el del servidor (lanzado 29852; log y netstat 28768, hijo de
29852): el PID que vale es el del log comprobado con netstat, nunca el de
Popen. El ejecutor no detuvo nada al ver que no coincidían; 28768 se
detuvo después por su PID, y el lanzador terminó con él.)
(Nota 2026-10-12, rama feat/n0-resultado-llamadas: la suite tiene ahora 44
scripts (el tutor dijo 43): check_migracion_resultado_llamadas,
check_resultados_llamada_service y check_resultados_llamada_http (puerto
8028, nunca el 8000). 44/44 en UNA pasada válida el 2026-10-11, de 22:59:52
a 23:10:48, con la regla de uso (Docker/n8n y navegador cerrados, wsl
--shutdown, enchufado y tapa abierta, confirmado por Gabi) y sin ningún
Kernel-Power 506/507 entre 22:58 y 23:12. Uvicorn del 8031 detenido por su
PID 28620 (log = netstat; el lanzador 29800 terminó con él). Salida de los
cinco scripts de P4 revisada a mano: sin fallos. Las pruebas en negativo se
hicieron en tres tandas con commit entre ellas, cada una con su
comprobación de 506/507.)
(Nota 2026-10-12, rama feat/n0-agente2: la suite tiene ahora 48 scripts, con
check_migracion_agente2, check_texto_agente_service, check_mcp_business_rules
(puerto 8029) y check_disponibilidad_service (check_lead_session_http ya
existía; usa el 8016). 48/48 en UNA pasada (18:46:04-18:59:41) tras corregir
check_migracion_resultado_llamadas (H-C-1: 49/50 por datos reales, ahora
50/50), con 1,99 GB libres, Docker/n8n, Chrome y Edge cerrados, enchufado, y
sin Kernel-Power 506/507 entre 18:45 y 19:01; datos reales iguales antes y
después. Uvicorn del 8031 detenido por su PID 25988 (log = netstat). Los
scripts que crean visitas piden sus fechas a scripts/fechas_prueba.py (un
bloque de 4 semanas por script desde la semana +30, y exigir_libres: si una
visita AJENA ocupa sus huecos, FALLO y se para sin borrar nada). Además,
check_migracion_paso13b (nuevo) FALLA a propósito hasta que se aplique
paso13b después del merge: es su prueba en negativo.)
(Nota 2026-10-13: paso13b ya está APLICADA; check_migracion_paso13b da 6/6 y
deja de fallar. La suite sigue teniendo 48 scripts, sin ningún fallo
esperado.)
(Nota 2026-10-14, rama feat/n0-horario-visitas: la suite tiene ahora 54
scripts: check_migracion_d32, check_huecos_libres_service,
check_huecos_libres_http (puerto 8032), check_mcp_huecos_libres (puerto
8033) y check_migracion_paso14b. 53/53 en UNA pasada (2026-10-14
10:31:38-10:40:55, sin check_migracion_paso14b, que aún no existía), con
1,60 GB libres, Docker/n8n, Chrome, Edge y WSL cerrados y sin Kernel-Power
506/507; uvicorn del 8031 detenido por su PID 31796 (log = netstat); datos
reales iguales. check_migracion_paso14b FALLA a propósito (2/3) hasta que
se aplique paso14b después del merge: es su prueba en negativo. Lección:
Edge se vuelve a abrir en segundo plano; medir justo antes de ejecutar.)
(Nota 2026-10-14, rama feat/n0-fecha-fin-visita, D33: siguen siendo 54
scripts, sin ninguno nuevo. 54/54 con código 0 en UNA pasada
(16:23:25-16:32:59), con 1,56 GB libres, sin chrome, msedge, docker ni vmmem
y sin Kernel-Power 506/507 entre 16:08 y 16:35; uvicorn del 8031 detenido
por su PID 13944 (log = netstat); datos reales iguales (SHA-256 de las 10
tablas). check_migracion_paso14b ya da 3/3 (paso14b aplicada).)
(Nota 2026-10-20, rama feat/n0-resumen-solicitud: la suite tiene ahora 57
scripts (check_migracion_resumen_solicitud, check_resumen_solicitud_service
y check_resumen_solicitud_http, puerto 8034). 57/57 en UNA pasada válida
(15:32:52-15:51:42), sin Kernel-Power 506/507; la de 15:08-15:28 NO valió
(Modern Standby 15:21-15:27). Regla nueva: ningún SHA-256, recuento o salida
se escribe en un commit en la misma orden que lo calcula.)
(Nota 2026-10-22, rama fix/n0-quitar-alias-gate-decisions: se retira
check_gate_decisions_service, así que la suite pasa de 57 a 56 scripts. OJO,
contradicción dicha al tutor: en main hay 58 check_*.py, no 57, porque D39.9
añadió check_datos_d39_9_incluye_tipico, que exige --esperado anterior|nuevo
y sin argumentos sale con código 2. Tras el retiro quedan 57 archivos. La
cifra 56 vale si ese check se ejecuta APARTE (con --esperado nuevo), no
dentro de la suite; si entra en ella, son 57. Pendiente de decidir antes de
la fase 2.)
(Nota 2026-10-22, veredicto del tutor sobre fe0378c: check_datos_d39_9_incluye_tipico
ENTRA en la suite con --esperado nuevo (vigila que D39.9 sigue aplicado): la
suite tiene 57 scripts. Quien la lance tiene que pasarle ese argumento; sin él
sale con código 2. 57/57 con código 0 en UNA pasada válida el 2026-10-22, de
13:22:35 a 13:39:49, sin Kernel-Power 41, 506 ni 507 entre 13:21 y 13:41, con
1,93 GB libres y ningún chrome, msedge, docker/vmmem ni WINWORD. OJO: el
portátil iba con BATERÍA (decisión de Gabi; con batería la pantalla y la
suspensión saltan a los 15 min, no a los 30). check_graceful_shutdown y
check_mcp_connection desde copias en el 8030 y el 8031; uvicorn del 8031
detenido por su PID 3924 (log = netstat). Salida de los cinco de P4 revisada
a mano: sin fallos. Datos reales de las 10 tablas iguales antes (00:37) y
después (13:40), 0 filas nuevas.)
(Nota 2026-10-22, rama feat/n0-direccion-obra, D42: la suite pasa a 60
scripts (veredicto del tutor), con check_migracion_direcciones_obra,
check_direccion_obra_service y check_direccion_obra_http (puerto 8035, nunca
el 8000), y sigue incluyendo check_datos_d39_9_incluye_tipico con
--esperado nuevo. check_migracion_direcciones_obra FALLA a propósito hasta
que se aplique paso16 (es su primera prueba en negativo). Los dos que crean
visitas tienen su bloque al final de scripts/fechas_prueba.py. Todavía sin
ninguna pasada: fase 1, SIN ejecutar.)
(Nota 2026-10-22, noche, fase 2 de D42: 60/60 con código 0 en UNA pasada
válida, de 20:11:58 a 20:30:29, sin Kernel-Power 41, 506 ni 507, enchufado,
2,51 GB libres y ningún chrome, msedge, docker/vmmem ni WINWORD.
check_graceful_shutdown y check_mcp_connection desde copias en el 8030 y el
8031; uvicorn del 8031 detenido por su PID 11900 (log = netstat). Salida de
los cinco de P4 revisada a mano: sin fallos. Datos reales de las 10 tablas
anteriores iguales antes (18:28) y después (20:31); direcciones_obra, 0
filas. Detalle: sección 6 de docs/Plan_Direccion_Obra_D42.txt.)
(Nota 2026-10-23, rama feat/n0-antelacion-visitas, D45: la suite pasa a 61
scripts, con check_migracion_d45, que FALLA a propósito (0/12) hasta que se
aplique paso17. check_mcp_huecos_libres lee ahora la base de datos en una
sesión de solo lectura. Todavía sin ninguna pasada: fase 1, SIN ejecutar.)
(Nota 2026-10-23, noche, fase 2 de D45: paso17 APLICADA a las 22:03:09-11
tras "confirmo la migración paso17" (check_migracion_d45 0/12 antes, 12/12
después). Suite de 61 en UNA pasada válida, de 22:52:06 a 23:14:21, sin
Kernel-Power 41, 506 ni 507, enchufado y sin procesos ajenos: 60/61 con
código 0 + 1 BLOQUEADO POR DATOS (check_resultados_llamada_http, guarda
H-C-2: el próximo laborable, 26/10, tiene 4 visitas de prueba confirmadas
de las pruebas 127-129 y no le quedan 3 horas libres; decisión del tutor:
no se tocan, pendiente P-D45-1). Los 13 mutantes en rojo. H-D45-2:
check_visits_service ajustado a las nueve reglas. Datos reales iguales
salvo las dos filas de paso17. Detalle: sección 9 del plan D45.)
(Nota 2026-10-25, rama feat/n0-cambio-visita, D46: la suite pasa a 64
scripts, con check_migracion_cambios_visita, check_cambios_visita_service y
check_cambios_visita_http (puerto 8036, nunca el 8000). 63/64 con código 0 +
1 BLOQUEADO POR DATOS (check_resultados_llamada_http, P-D45-1) en UNA
pasada válida, de 21:41:49 a 22:01:10, sin Kernel-Power 41, 506 ni 507,
enchufado y sin procesos ajenos; uvicorn del 8031 detenido por su PID 3348
(log = netstat). Los cinco de P4 revisados a mano (check_foreign_keys 14 ->
23). Datos reales de las 11 tablas anteriores iguales antes y después;
cambios_visita, 0 filas. El 2026-10-26, check_resultados_llamada_http
215/215 (P-D45-1 cerrado). Detalle: sección 11.6 del plan D46.)

POST /create-followup-task (rama feat/n0-create-followup-task, 2026-10-07 a
2026-10-08, pendiente de merge; plan 9853491 y e975a24, B1 0354a01, B2
ce6f421, pruebas ad072f5, documentación y comentarios en el commit del
Bloque D): abre el seguimiento de 48 h de una oportunidad sin Gate que no
ha pedido visita. Entrada oportunidad_id y motivo (solo
'sin_respuesta_visita' en N0, D25.13); X-Gate-Secret; 201 (abre: estado
'seguimiento_pendiente', updated_at y un log 'seguimiento_abierto'; NO
toca fecha_ultimo_contacto) o 200 (ya abierto, sin escribir; log_id null
si el estado se puso a mano); 404; 409 con seis motivos en orden
(sin_presupuesto, con_gate, estado_no_permitido, contacto_registrado,
visita_existente, plazo_no_cumplido); 422; 503 configuracion_incompleta
SIN log, con [AVISO]. La condición "seguimiento por abrir" vive en UN solo
sitio, app/services/condicion_seguimiento.py, que usan también el apartado
b) de GET /llamadas-del-dia (B1, que le añadió el criterio del Gate, P1) y
el endpoint (una columna por criterio). FOR UPDATE, READ COMMITTED y
segunda barrera (UPDATE con AND estado = 'presupuesto_enviado' y rowcount
= 1). check_followup_service 34/34, check_followup_tasks_http 125/125
(matriz lista <-> endpoint, 5 simultáneas, carrera con POST /visits). En
negativo, N1-N12 con N2b y N3b, de una en una (resultados exactos en la
sección 6.3 del plan). Regresión: check_gate_decisions_http 121/121,
check_visits_http 55/55, check_lead_session_http 24/24 y los dos del
listado 39/39 y 88/88. Contrato y limitaciones: fila 5 y secciones 2 y 5.8
de la Adenda. Incidente de la N12 (2026-10-08): una versión rota dejó
horas_seguimiento_presupuesto en 8761 en la tabla real; restaurada a 48 y
regla nueva en "Cómo trabajamos" (conexiones de prueba sin commit).
(Nota 2026-10-10: "pendiente de merge" ya no es cierto: integrado en main en 1af758a, comprobado en GitHub.)

GET /oportunidades/{oportunidad_id}/ficha (rama feat/n0-ficha-oportunidad,
2026-10-10, pendiente de merge; plan 680982f y 20eb472, B1 1c0b2d4, B2
26a60b4, pruebas 377bac9, comentarios 28c4c26, documentación en el commit
del Bloque D): la ficha completa de CUALQUIER oportunidad, con o sin Gate
(D26.2, bloque 4a-1 de D26.6), para la página protegida de n8n que abre
administración antes de llamar. Solo lectura, X-Gate-Secret EN EL ROUTER
(P8), 200 / 401 / 422 / 404 (sin 409 ni 503). Contacto, reforma, fotos y
presupuesto (con y sin IVA, umbral VIGENTE, requiere_aprobacion) salen de
app/services/datos_oportunidad.py, compartido con GET /gate-avisos (B1:
a_madrid y deducir_sin_iva movidas sin cambiar su cuerpo; 75/75 y 18/18
con los scripts sin tocar, ast.dump igual, el mismo SQL y las respuestas
de los 7 casos reales con Gate idénticas por SHA-256). Además: todas las
visitas, las llamadas CON su informe, el historial de estados reconstruido
de logs (solo detalle->>'estado' y ->>'decision'; nunca un evento
inventado: historial_cuadra = false si no cuadra, por ejemplo 'ganada'
puesta a mano) y otras_oportunidades_mismo_email. SET TRANSACTION
ISOLATION LEVEL REPEATABLE READ, READ ONLY como primera orden (importada
del listado). EstadoVisita nuevo en app/schemas/common.py.
check_ficha_oportunidad_service 40/40 (la S4 demuestra REPEATABLE READ con
un cambio confirmado en mitad de la ficha, con ConexionSinCommit) y
check_ficha_oportunidad_http 69/69 (puerto 8026). En negativo, sobre
copias, de una en una (servicio | HTTP): N1 40/40 | 23/38; N2 32/39 |
31/38; N3 40/40 | 67/69; N4 40/40 | 65/69 (y gate-avisos 17/18 y 73/75);
N5 39/40 | 63/69; N6 37/40 | 69/69 (solo la S4 lo ve); N7 40/40 | 67/69;
N8 40/40 | 66/69; N9b 39/40 | 68/69; N10 37/40 | 67/69; N11 40/40 | 66/69
(y 15/18 y 71/75); N12 40/40 | 67/69; N13 40/40 | 68/69 (y 17/18 y 74/75);
N14 40/40 | 67/69. Pasada sobre los datos reales: 27/27 fichas sin error,
27 cuadran. Contrato y limitaciones (auditoría de lecturas, umbral vigente
y no el aplicado, 'ganada' sin registro, copia de texto_cliente en logs):
fila 9 y sección 5.9 de la Adenda. Suite completa 41/41 en UNA pasada
válida (ver "Suite de verificación").
(Nota 2026-10-11: "pendiente de merge" ya no es cierto: integrada en main en
ea96f39.)

Resumen de la solicitud en GET /llamadas-del-dia (rama
feat/n0-resumen-lista-diaria, 2026-10-10 a 2026-10-11, pendiente de merge;
plan cc650c3 y 601145f, B0 584c075, B2 3aa5496, pruebas 62c9a21 y 454f41b,
documentación en el commit del Bloque D): D26.1, bloque 4a-2. Cada elemento
de los cinco apartados lleva el objeto "solicitud" {m2 (texto exacto o
null), nivel_acabados, incluye_cambios_estructurales}, siempre presente
(SolicitudLlamada, extra="forbid"), y los de visita (d y e), texto_cliente
de ESA visita. Sigue sin email ni importes (la razón es la ACUMULACIÓN, D26
sección 1.3). Las tres expresiones se toman por nombre de COLUMNAS_CASO
(datos_oportunidad.py, sin tocarlo) en NOMBRES_SOLICITUD /
COLUMNAS_SOLICITUD_SQL, a nivel de módulo: un nombre inexistente da
KeyError al arrancar uvicorn. Las mismas cinco consultas (solo columnas
más). B0: ConexionSinCommit en la S5 de check_listado_llamadas_service.
check_listado_llamadas_service 69/69 (S4b, S5 con tres leads distintos, S8
cinco órdenes, S9 con ast) y check_listado_llamadas_http 109/109 (puerto
8024; CASOS 18-21). En negativo, sobre copias, de una en una (servicio |
HTTP): N1 61/69 | 101/109; N2 68/69 | 104/109; N3 69/69 | 104/109; N4 69/69
| 105/109; N5 65/69 | 109/109 (solo S9); N6 67/69 | 109/109 (solo S8); N7
63/69 | 107/109; N8 62/69 | 102/109 (ficha y gate-avisos no lo ven, C11);
N9 69/69 | 106/109; N10 45/48 | 59/109 (la lista entera en 500); N11
KeyError al importar (ni el script ni uvicorn arrancan). Contrato y
limitaciones (R1: un lead mal formado tumba la lista entera; R2:
texto_cliente sin filtro hasta D26.7): fila 8 y nota del 2026-10-11 de la
sección 5.7 de la Adenda. Suite completa 41/41 en UNA pasada válida.
(Nota 2026-10-11: "pendiente de merge" ya no es cierto: integrado en main en
c1ba356, comprobado en GitHub.)

Resultado de todas las llamadas (rama feat/n0-resultado-llamadas,
2026-10-11 a 2026-10-12, pendiente de merge; plan 5f7d4df y 2cb01a6, B1
c9d7332, B2-M cfb81ae y 500d203, B2 5f62f85, C c665cb4, 3c14bd7, bc30112,
ab166ba y f0f7686, D en el commit del Bloque D): D25.9-D25.11, bloque 4b de
D26.6. POST /resultados-llamada registra el resultado de las tres llamadas
de la lista diaria: gate y seguimiento (visita_acordada | descartar) y
confirmación de una visita pedida desde el chat (confirmar_hora | otra_hora |
cliente_cancela, que pasa la oportunidad a 'perdida'). El tipo lo DEDUCE el
backend (app/services/tipo_llamada.py, compartido con la ficha). Una sola
fila definitiva por oportunidad (el UNIQUE se mantiene, P6). Motivo nuevo
'sin_decision' (D29.3), solo por la ruta nueva y nunca en cliente_cancela.
B1 renombró solo el contrato público (P3 = b): gate_decisions_service.py ->
resultados_llamada_service.py (git mv); la tabla sigue siendo
decisiones_gate, con COMMENT ON TABLE. POST /gate-decisions es el ALIAS con
su contrato de siempre (registrar_decision -> registrar_resultado con
solo_gate=True): check_gate_decisions_http 121/121 SIN tocar en B1, B2 y C.
Migración paso12 (solo expand, APLICADA el 2026-10-11 tras "confirmo la
migración paso12"): tipo_llamada (DEFAULT 'gate'), visita_cancelada_id
(clave foránea doble), CHECK ampliados y nuevos; vuelta atrás escrita sin
ejecutar. La ficha gana llamada_registrable (P9), fecha_visita_cancelada y el
evento llamada_registrada (L-b); GET /gate-avisos admite 'sin_decision' en su
motivo (C17, un valor más). check_migracion_resultado_llamadas 50/50 (7/50
antes), check_resultados_llamada_service 37/37 y check_resultados_llamada_http
160/160 (puerto 8028); quince pruebas en negativo N1-N15 (resultados exactos
en la sección 8.5 del plan) y suite completa 44/44 en UNA pasada válida.
Contrato y limitaciones: fila 10 y sección 5.10 de la Adenda, con notas en
la fila 3 y en 5.5-5.9.

Pendiente, PRIMERA tarea después del merge de feat/n0-gate-decisions: una
función compartida para las conexiones directas de los scripts check_*.py
(hoy psycopg2.connect sin keepalives ni validación con SELECT 1; tres
cortes del pooler en esa rama, ver "Scripts de verificación frágiles").
(Nota 2026-10-03: sustituida por el lanzador de la suite que impide la
suspensión de Windows, docs/Plan_Ejecutor_Suite.txt, rama
fix/ejecutor-suite; el experimento refutó que la causa fuera el tiempo
sin usarse.)

Pendiente: create-followup-task,
get_business_rules y request_missing_information, la concurrencia
del Session pooler bajo carga, la tabla de excepciones de la Adenda
(hoy un error no controlado de services/ sale como 500 genérico), y la
idempotencia frente a reintentos de n8n (D7, limitación asumida en N0).
(Nota 2026-10-03, añadido al cerrar la rama feat/n0-aviso-gate, fuera de
ella: reutilizar EstadoOportunidad en los servicios que escriben los
estados como textos sueltos (gate_decisions_service, visits_service,
estimate_service); la columna presupuestos.umbral_gate_aplicado (deuda
técnica, escrita por calculate-estimate como iva_pct_aplicado); cerrar los
5 casos de prueba 248-252 en 'pendiente_aprobacion' (POST /gate-decisions,
descartar) antes de activar WF3, lo hará Gabi; y una regla de negocio por
decidir: las visitas no tienen antelación máxima (validar_fecha solo exige
que la fecha sea futura, de lunes a viernes y dentro de una franja).)
(Nota 2026-10-06: los 5 casos de prueba 248-252 YA están cerrados, con
descartes registrados desde el formulario del Gate. WF3 ya se puede activar.
Lo demás de la nota anterior sigue pendiente. Pendiente también:
create-followup-task, uno de los 5 endpoints originales, sigue siendo un
docstring de una línea: hay que implementarlo o justificar formalmente que
sale del alcance de N0.)
(Nota 2026-10-07: GET /llamadas-del-dia, rama feat/n0-listado-wf3, deja
lista la consulta que necesita create-followup-task (apartado
seguimientos_por_abrir); create-followup-task, WF3 en n8n, la ampliación
de seguimientos y visitas (D25.9-D25.11) y el email al cliente (D25.1)
siguen pendientes. Los 8 seguimientos por abrir de prueba (4 sin la clave
'contacto') NO se tocan: se cerrarán desde el formulario cuando exista esa
ampliación y servirán como datos de prueba (D25.16).)
(Nota 2026-10-08: create-followup-task ya está implementado, rama
feat/n0-create-followup-task, pendiente de merge. Pendiente nuevo: aplicar
la regla de las conexiones de prueba sin commit (ConexionSinCommit, ver
"Cómo trabajamos") a la S5 de scripts/check_listado_llamadas_service.py,
que ya está en main: pasa su cursor con INSERT sin confirmar a
leer_listado. Mismo patrón, menor alcance (solo filas propias nuevas,
ninguna real modificada); en su propia rama. Pendiente también, para el
bloque del Agente 2 (P12 del plan de create-followup-task): su router
tendrá que tratar 'seguimiento_pendiente' como 'presupuesto_enviado'; el
router de chat actual no se rompe, porque decide con existe y
presupuesto.existe, no con el estado.)
(Nota 2026-10-10: create-followup-task ya NO está pendiente de merge: integrado en main en 1af758a. Lo demás de la nota anterior sigue pendiente.)
(Nota 2026-10-11, rama feat/n0-resumen-lista-diaria.) RESUELTO el
pendiente de la S5 de scripts/check_listado_llamadas_service.py: abre su
conexión con ConexionSinCommit desde el Bloque B0 (584c075), con su
autoprueba S0 y un except que cuenta como FALLO un commit() del código.
Pendientes nuevos, cada uno en su propia rama:
  - R1 / P5 del plan del resumen: dos CHECK en leads, como
    chk_leads_m2_rango, para nivel_acabados (solo 'basico', 'medio',
    'alto') e incluye_cambios_estructurales (booleano). Hoy un solo lead mal
    formado tumbaría GET /llamadas-del-dia ENTERA (500 a WF3). La migración
    solo AÑADE (expand-contract no aplica); comprobar antes que ninguna
    fila real la incumple (el 2026-10-10: 0 de 27).
  - C10 (deuda técnica): app/services/estimate_service.py (POST
    /calculate-estimate) tiene su propia copia de las tres expresiones del
    resumen (m2, nivel_acabados, cambios) desde 124ba1b. Que las lea de
    COLUMNAS_CASO, con su regresión completa (REST y tool MCP). La S9 de
    check_listado_llamadas_service la TOLERA, esté o no.
  - C11: check_ficha_oportunidad_* y check_aviso_gate_* comprueban las
    claves de "reforma" y el texto de m2, pero nunca el VALOR de
    incluye_cambios_estructurales ni de nivel_acabados frente a lo enviado
    (la N8 del plan del resumen, una expresión compartida cambiada, les
    pasó 40/40, 69/69, 18/18 y 75/75). Añadir esas comprobaciones, con
    valores distintos por caso.
(Nota 2026-10-12, rama feat/n0-resultado-llamadas.) Pendientes nuevos, cada
uno en su propia rama:
  - scripts/check_migracion_decisiones_gate.py tiene 41/166 sentencias en
    tramos de más de 4 sin comentario desde main (no es de esta rama; el
    tutor pidió NO arreglarlo aquí). Comentarlo.
  - paso12b (P13), DESPUÉS del merge: ALTER COLUMN tipo_llamada DROP DEFAULT
    (hoy el DEFAULT 'gate' podría esconder un olvido del código).
  - Quitar el alias POST /gate-decisions cuando el formulario de n8n use POST
    /resultados-llamada (D26.6); con él cambian los cinco scripts que lo
    usan para preparar datos (check_aviso_gate_http,
    check_ficha_oportunidad_http, check_followup_tasks_http,
    check_listado_llamadas_http, check_migracion_iva_y_seguimiento) y
    check_gate_decisions_* pasa a probar solo la ruta nueva o se retira.
    (Nota 2026-10-22: HECHO en la rama fix/n0-quitar-alias-gate-decisions,
    pendiente de merge. Dos correcciones a esta lista, del PASO 0: H-0-2,
    check_migracion_iva_y_seguimiento NO llamaba al alias: solo lo nombraba
    en comentarios y en un texto que imprime (solo cambia ese texto); H-0-3,
    cambian también check_resultados_llamada_http (CASO 10 fuera, CASO 13c
    nuevo) y check_resultados_llamada_service (S3 y S7 sin el caso del
    alias, S6 nuevo, S8 nuevo), que la lista no nombraba. Pendiente nuevo,
    en su propia rama: H-D39-4, que check_webhook_auth_http cuente solo sus
    logs, no toda la tabla.)
  - En n8n (fuera del repositorio): el formulario ampliado
    (docs/Contrato_Formulario_Llamadas.md), conectado a WF4 (D27.4); la
    etiqueta del evento llamada_registrada en WF_ficha_oportunidad.
  - Bloque 4c (D29.2): el aviso de "última llamada" en GET /llamadas-del-dia
    con la regla nueva horas_ultima_llamada (propuesta 168).
  - Copiar a docs/ el documento de D29 (está en el Proyecto de claude.ai).
  - dump_schema.py no vuelca los COMMENT ON TABLE: el de decisiones_gate no
    aparece en docs/schema_actual.sql.
(Nota 2026-10-12, rama feat/n0-agente2.) De la lista anterior: el bloque 4c
ya NO es de backend (D30 revisa D29.2: el aviso de "última llamada" va a
n8n), y el documento de D29 YA está en docs/ (cbd2ec7, junto con el de D28).
Pendientes nuevos:
  - Después del merge de feat/n0-agente2: reiniciar el uvicorn del 8000 sin
    --reload; paso13b (sección 11.2 del plan), con el cambio de
    check_migracion_visitas y del CASO 20 de check_listado_llamadas_http y
    regenerar docs/schema_actual.sql.
  - n8n: el Agente 2 (router con 'perdida' y 'ganada', prompt, tools
    solicitar_visita y get_business_rules, Simple Memory), sin X-Gate-Secret
    (N10 del plan); prompts/agente2_asesor.md cuando esté probado; el
    formulario publicado tiene que enseñar el 409 hueco_ocupado.
  - Rama 2: el resumen de D28.1 (leads.resumen_solicitud, POST /leads, ficha,
    GET /gate-avisos, plantilla de WF2 y el Agente 1 que lo escribe).
  - N1: la restricción de exclusión de visitas (segunda barrera de D31, con
    una columna de fin guardado) y el calendario externo.
(Nota 2026-10-13, rama fix/n0-paso13b.) feat/n0-agente2 está integrada en
main (9f4ddfc) y el uvicorn del 8000 ya se reinició con ese código (Gabi).
paso13b está APLICADA el 2026-10-13 a las 20:57:21 (tras "confirmo la
migración paso13b"): el CHECK chk_visitas_texto_cliente_longitud es ahora de
1..300, como MAX_TEXTO_VISITA. check_migracion_paso13b 4/6 antes y 6/6
después; check_migracion_visitas (300/301) 28/28; CASO 20 de
check_listado_llamadas_http (300) 109/109; regresión de los scripts que
crean visitas sin fallos; docs/schema_actual.sql regenerado. Detalle en la
nota de la sección 11.2 del plan del Agente 2. Pendiente menor: el
comentario de app/schemas/listado_llamadas.py sobre texto_cliente sigue
diciendo "1..1000 caracteres".
(Nota 2026-10-14, rama feat/n0-horario-visitas, D32.) Pendientes nuevos:
  - Después del merge: reiniciar el uvicorn del 8000 sin --reload; paso14b
    (sección 13.2 del plan D32), con el cambio de check_migracion_agente2.
  - n8n: el formulario de POST /resultados-llamada con las páginas "Día" y
    "Hora" y el bucle "Ninguna le va bien" (contrato, 4b), y el Agente 2 con
    la tool consultar_huecos_libres (ofrecer SOLO sus horas).
  - Una 'solicitada' antigua fuera de la rejilla no se puede confirmar con
    confirmar_hora (K3 del plan D32; el 2026-10-13 había 0).
(Nota 2026-10-14, 11:30.) paso14b ya está APLICADA. Pendiente nuevo, en su
propia rama: check_migracion_agente2 (punto 3) exige la fila paso_huecos_min,
que ya no existe (33/38 desde paso14b): que exija que NO exista, o que deje de
mirarla, con su prueba antes y después.
(Nota 2026-10-23, D45, P2 del veredicto del tutor.) Pendiente nuevo, en su
propia rama: unificar el día laborable. Hoy hay cuatro comprobaciones
sueltas "weekday() < 5" o ">= 5" (reglas_visita.py y huecos_libres_service.py),
siguiente_laborable() en listado_llamadas_service.py y, desde D45,
primer_dia_permitido() en reglas_visita.py. Que todas usen una sola función
(tocaría la lista diaria: con su regresión).

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
- Decisiones_D16_Agente_Conversacional_Captura_n8n.md — D16: agente
  conversacional de captura en n8n (revisa D11).
- Decisiones_D17_Cierre_MCP_Client_Gate_Determinista.md — D17: cierre
  del nodo MCP Client y paso determinista posterior al cálculo.
- Decisiones_D18_Agente2_en_N0_Arquitectura_Post_Calculo.md — D18: el
  Agente 2 entra en N0 (revisa D16.5); router de chat (D18.9), sin
  importes para el agente (D18.5) y plazos.
- Plan_Endpoint_Sesion_Lead.txt — plan aprobado de GET
  /leads/session/{lead_token}.
- Plan_Endpoint_Visits.txt — plan de POST /visits (P1-P5).
- Plan_Endpoint_Gate_Decisions.txt — plan de POST /gate-decisions (D19,
  P1-P11, bloques con sus hashes y trabajo posterior). D19 no tiene todavía
  documento de decisiones propio: su contexto está en la sección 1.1.
  (Nota 2026-10-06: los documentos de D19 a D24 existen en el Proyecto de
  claude.ai de Gabi, pero NO están en este repositorio todavía; copiarlos
  a docs/ está pendiente. Hasta entonces, aquí no se puede consultar su
  texto.)
- Plan_Endpoint_Aviso_Gate.txt — plan de GET /gate-avisos/{oportunidad_id}
  (P1-P7 decididas, bloques A a D con sus hashes).
- Decisiones_D25_WF3_Seguimiento_y_Resultado_Llamadas.md — D25: WF3,
  seguimiento, resultado de las llamadas, modo de proceder de
  administración y orden de los bloques (D25.1-D25.16; copiado del
  Proyecto de claude.ai el 2026-10-07). D19-D24 siguen sin estar aquí.
- Plan_Endpoint_Listado_WF3.txt — plan de GET /llamadas-del-dia y de la
  regla horas_seguimiento_presupuesto (P1-P9 decididas, pruebas en
  negativo N1-N12 con sus resultados exactos, bloques A a E con sus
  hashes).
- Plan_Endpoint_Create_Followup_Task.txt — plan de POST
  /create-followup-task y de la condición compartida (P1-P12 decididas,
  pruebas en negativo N1-N12 con sus resultados exactos, incidente de la
  N12, bloques A, B1, B2, C y D con sus hashes).
- Decisiones_D26_Ficha_del_Cliente_en_la_Lista_Diaria.md — D26: resumen
  de la solicitud en la lista diaria, ficha para cualquier oportunidad,
  visitas en un solo bloque del email de WF3, nuevo orden de bloques
  (revisa D25.12) y búsqueda de clientes como mejora posterior.
  (Nota 2026-10-11: desde esta fecha incluye también la sección 5, D26.8,
  página de la ficha en n8n.)
- Decisiones_D27_WF4_Avisos_Tecnicos_y_Pagina_Ficha.md — D27: WF4 (avisos
  técnicos por Telegram y email), su conexión con WF2, errores de la página
  de la ficha y exportación saneada de los flujos a n8n/ (D27.1-D27.7;
  copiado del Proyecto de claude.ai el 2026-10-11).
- Plan_Endpoint_Ficha_Oportunidad.txt — plan de GET
  /oportunidades/{oportunidad_id}/ficha (D26.2, 4a-1): P1-P12 decididas,
  el corte de aviso_gate_service (B1), pruebas en negativo N1-N14 con sus
  resultados exactos y bloques A, B1, B2, C y D con sus hashes.
- Plan_Resumen_Lista_Diaria.txt — plan del resumen de la solicitud en GET
  /llamadas-del-dia (D26.1, 4a-2): P1-P10 decididas (P1 = b, sin B1),
  hallazgos C1-C11, pruebas en negativo N1-N11 y N-B0 con sus resultados
  exactos y bloques A, B0, B2, C y D con sus hashes.
- Plan_Resultado_Llamadas.txt — plan del registro del resultado de todas
  las llamadas (D25.9-D25.11, 4b): pre-decisiones con Regla de Tres, P1-P16
  decididas, D29.1-D29.3, hallazgos C1-C18, migración paso12, pruebas en
  negativo N1-N15 con sus resultados exactos y bloques A, B1, B2-M, B2, C y
  D con sus hashes.
- Contrato_Formulario_Llamadas.md — lo que necesita el formulario de n8n
  para registrar el resultado de una llamada: la ficha, las páginas por
  tipo, los campos de cada resultado, los motivos y qué enseñar ante cada
  respuesta (P15; la Adenda 5.10 remite a él).
  (Nota 2026-10-12: incluye el 409 hueco_ocupado de D31, también por el alias.)
- Plan_Agente2_Calendar.txt — plan de la rama feat/n0-agente2 (el backend
  del Agente 2): P1-P22 decididas, D31 (sección 10.3), hallazgos y pruebas
  en negativo N1-N9 con sus resultados exactos, integración y paso13b
  (sección 11) y hashes (sección 12).
- Plan_Horario_Visitas_D32.txt — plan de D32 (rama feat/n0-horario-visitas):
  rejilla de D23.5, desplazamiento, disponibilidad única, GET /huecos-libres
  y la tool consultar_huecos_libres; P1-P12 (sección 11), mediciones, lista
  de scripts afectados (6), pruebas en negativo N1-N14 con sus resultados
  (sección 12), hashes (10) e integración y paso14b (13). D23.5 y D32 los
  tiene el tutor en el Proyecto de claude.ai.
- Plan_Fecha_Fin_Visita_D33.txt — plan de D33 (rama
  feat/n0-fecha-fin-visita): fecha_fin_visita en /visits, /resultados-llamada
  y el alias; P1-P3 con alternativas, pruebas en negativo N1-N4 con sus
  resultados, y resultados de la fase 2 con los SHA-256 (sección 7). D33 lo
  tiene el tutor en el Proyecto de claude.ai.
- Plan_Resumen_Solicitud_D28.txt — plan de D28.1 (rama
  feat/n0-resumen-solicitud): dónde se guarda, opcional, límite, qué pasa si
  no pasa el filtro (P1 = b, D36.8), repetición, dónde se ve; migración
  paso15; pruebas en negativo N1-N10 con previsto y real; H-B1-1, H-C-2,
  D37.1; suite 57/57 (secciones 12-19). D36 y D37 los tiene el tutor en el
  Proyecto de claude.ai.
- Plan_Direccion_Obra_D42.txt — plan de D42 (rama feat/n0-direccion-obra):
  tabla propia direcciones_obra (paso16), el veredicto del tutor al PASO 0
  (P1 = A, H1-H4, resultados sin visita, forma del campo, regla de D42.5),
  pruebas en negativo N1-N15 y resultados de la fase 2. D42 lo tiene el tutor
  en el Proyecto de claude.ai.
- Plan_Antelacion_Visitas_D45.txt — plan de D45 (antelación mínima de las
  visitas, paso17), con su predicción y resultados.
- Plan_Cambio_Visita_D46.txt — plan de D46 (rama feat/n0-cambio-visita):
  POST /cambios-visita y la tabla cambios_visita (paso18), P1-P11 con los
  veredictos del tutor, hallazgos H-D46-1 a H-D46-9, predicción y resultados
  de la fase 2 (N1-N18) y el cierre de P-D45-1 (sección 11). D46 lo tiene el
  tutor en el Proyecto de claude.ai.
- Decisiones_D28_Resumen_Conversacion_y_Enlace_Ficha.md y
  Decisiones_D29_Me_Lo_Pienso_y_Ultima_Llamada.md — D28 (resumen de la
  conversación, rama 2) y D29 (copiados del Proyecto de claude.ai el
  2026-10-12). D30 y D31 los tiene el tutor en el Proyecto: resumidos en
  una línea en la Adenda 5.11.
- Plan_Ejecutor_Suite.txt — lanzador de la suite que impide la suspensión
  de Windows. APLAZADO el 2026-10-03; lo sustituye la regla de uso de este
  archivo.
- Plan_calculate_estimate.txt — plan de POST /calculate-estimate.
- Plan_Fase1_Autenticacion_Webhook.txt — SUPERADO (lo dice su primera
  línea); lo implementado está en
  TFM_Resumen_Sesion_Autenticacion_Webhook_y_Notas_N0.txt.

Fuera de docs/: prompts/agente1_captura.md es el prompt del Agente 1
(captura de datos en el chat). No está comprobado que coincida con el que
tiene hoy el nodo del agente en n8n.
(Nota 2026-10-07: comprobado. El system message del nodo "AI Agent" del
JSON exportado de chat_tipo_reforma es idéntico a este archivo, 5.791
caracteres, comparado con un script. Si se cambia en un sitio, hay que
cambiarlo en el otro.)
(Nota 2026-10-22: la cifra de 5.791 ya no vale. prompts/agente1_captura.md
en main (último cambio 74e0e7c, 2026-10-20, D28.1 y D37.1) mide 10.558
caracteres CON el salto de línea final y 10.557 sin él (10.629 bytes en
UTF-8, sin BOM, LF), medido con Python el 2026-10-22. 10.557 es la cifra
que esperaba el tutor: la diferencia es solo ese salto final, que el nodo
de n8n no lleva (n8n/README.md: "idéntico, más el salto de línea final").)

## Esquema de la base de datos: cuál de los dos .sql manda

docs/schema_actual.sql es la ÚNICA fuente fiable del esquema actual. Lo
genera scripts/dump_schema.py leyendo information_schema de la base de
datos real, y está verificado por ejecución real: el SQL generado se
ejecutó contra un esquema de prueba y recreó las 8 tablas con sus 15
restricciones PK/UNIQUE/FK y sus 7 CHECK. Se regenera ejecutando:

    .\venv\Scripts\python.exe scripts\dump_schema.py

(Nota 2026-10-01: las cifras de arriba son las de entonces. Hoy son 10
tablas, y desde b505390 la comprobación la hace un script:
scripts/check_schema_actual_ejecutable.py, 10/10 con 10 claves primarias,
6 unique, 6 claves foráneas, 15 CHECK y 2 índices sueltos. dump_schema
escribe ahora bien las claves foráneas compuestas y ordena las tablas para
que el archivo se pueda ejecutar de principio a fin.)

docs/schema_n0_v2.sql es HISTÓRICO. Se escribió a mano y divergió de la
realidad sin que nadie lo notara: le faltan la columna
presupuestos.motivo_gate, las tablas reglas_negocio y tarifas_base
enteras, y el CHECK chk_oportunidades_tipo_reforma. NO debe consultarse
para nada del desarrollo actual. Se conserva solo como registro de cómo
se diseñó el esquema al principio.
