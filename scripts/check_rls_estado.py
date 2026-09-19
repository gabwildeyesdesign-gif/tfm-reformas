"""
FASE 2 - Estado REAL de Row Level Security (RLS) en la base de datos.

Las 8 tablas tienen ENABLE ROW LEVEL SECURITY y en el repositorio no
existe ninguna CREATE POLICY. Aun asi, POST /leads inserta y lee filas
sin problema. La hipotesis es que el rol con el que se conecta el
backend se salta RLS. Este script lo comprueba con consultas reales, sin
suponer nada:

  1. Atributos del rol actual (current_user): rolsuper, rolbypassrls.
  2. Propietario de cada tabla y si tiene RLS activado (relrowsecurity)
     y FORZADO (relforcerowsecurity). FORCE importa: con FORCE, RLS se
     aplica incluso al propietario de la tabla.
  3. Cuantas politicas (CREATE POLICY) existen de verdad.
  4. Que permisos (GRANT) tienen los roles de la API publica de
     Supabase (anon, authenticated) sobre esas mismas tablas.
  5. Prueba empirica: SET ROLE anon dentro de una transaccion que se
     deshace SIEMPRE con ROLLBACK, para ver que ocurre de verdad cuando
     ese rol lee y escribe.

Es un script de SOLO LECTURA en efecto: lo unico que podria escribir (el
INSERT de la prueba 5) se hace dentro de una transaccion que termina en
ROLLBACK pase lo que pase.
"""

import sys
from pathlib import Path

# Mismo motivo que en el resto de scripts/: el paquete "app" esta en la
# raiz del repositorio, no en scripts/.
RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
sys.stdout.reconfigure(encoding="utf-8")

import psycopg2
from psycopg2.extras import RealDictCursor

# Se importa solo DATABASE_URL: este script no tiene nada que ver con
# webhooks, y gracias a que la comprobacion de WEBHOOK_SECRET vive en
# app/api/security.py (y no en app/config.py), no la necesita.
from app.config import DATABASE_URL

cn = psycopg2.connect(DATABASE_URL)
cur = cn.cursor(cursor_factory=RealDictCursor)


def titulo(texto):
    print("\n" + "=" * 78)
    print(texto)
    print("=" * 78)


# ----------------------------------------------------------------------
titulo("1. ROL CON EL QUE SE CONECTA EL BACKEND (el de DATABASE_URL)")
# ----------------------------------------------------------------------
# current_user es el rol con el que se evaluan los permisos AHORA MISMO.
# A traves del Session pooler de Supabase, el usuario de la cadena de
# conexion es "postgres.<id_proyecto>", pero el rol real en Postgres es
# el que va antes del punto; por eso se consulta current_user en vez de
# deducirlo de la URL.
cur.execute(
    """
    SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolinherit
    FROM pg_roles
    WHERE rolname = current_user;
    """
)
rol = cur.fetchone()
for k, v in rol.items():
    print(f"  {k:<14} = {v}")
ROL_ACTUAL = rol["rolname"]

# ----------------------------------------------------------------------
titulo("2. TABLAS DE public: PROPIETARIO, RLS ACTIVADO Y RLS FORZADO")
# ----------------------------------------------------------------------
# pg_tables.tableowner da el propietario. relrowsecurity y
# relforcerowsecurity estan en pg_class (el catalogo interno de
# tablas); information_schema no los expone, por eso se une con pg_class.
# pg_has_role(..., 'MEMBER') dice si el rol actual ES o PERTENECE al rol
# propietario: pertenecer tambien da los privilegios de propietario.
cur.execute(
    """
    SELECT t.tablename,
           t.tableowner,
           (t.tableowner = current_user)                 AS es_propietario,
           pg_has_role(current_user, t.tableowner, 'MEMBER') AS miembro_del_propietario,
           c.relrowsecurity      AS rls_activado,
           c.relforcerowsecurity AS rls_forzado
    FROM pg_tables t
    JOIN pg_class c      ON c.relname = t.tablename
    JOIN pg_namespace n  ON n.oid = c.relnamespace AND n.nspname = t.schemaname
    WHERE t.schemaname = 'public'
    ORDER BY t.tablename;
    """
)
tablas = cur.fetchall()
print(f"  {'tabla':<16} {'propietario':<12} {'es_prop':<8} {'miembro':<8} "
      f"{'rls_on':<7} {'rls_forzado'}")
for t in tablas:
    print(f"  {t['tablename']:<16} {t['tableowner']:<12} {str(t['es_propietario']):<8} "
          f"{str(t['miembro_del_propietario']):<8} {str(t['rls_activado']):<7} "
          f"{t['rls_forzado']}")

# ----------------------------------------------------------------------
titulo("3. POLITICAS RLS EXISTENTES (CREATE POLICY) EN public")
# ----------------------------------------------------------------------
cur.execute("SELECT COUNT(*) AS n FROM pg_policies WHERE schemaname = 'public';")
n_politicas = cur.fetchone()["n"]
print(f"  Numero de politicas: {n_politicas}")

# ----------------------------------------------------------------------
titulo("4. ROLES DE LA API PUBLICA DE SUPABASE Y SUS PERMISOS")
# ----------------------------------------------------------------------
# anon          = rol que usa PostgREST cuando alguien llama con la
#                 "anon key" (la clave publica que suele ir en el
#                 frontend).
# authenticated = rol que usa PostgREST con un JWT de usuario logueado.
# authenticator = rol con el que PostgREST se conecta y desde el que
#                 cambia a uno de los dos anteriores.
# has_table_privilege responde si el rol PODRIA hacer esa operacion
# segun los GRANT, sin tener en cuenta RLS (RLS se evalua despues).
cur.execute(
    """
    SELECT r.rolname, r.rolbypassrls
    FROM pg_roles r
    WHERE r.rolname IN ('anon', 'authenticated', 'authenticator', 'service_role')
    ORDER BY r.rolname;
    """
)
roles_api = cur.fetchall()
print("  Roles de la API presentes en la base de datos:")
for r in roles_api:
    print(f"    {r['rolname']:<14} rolbypassrls = {r['rolbypassrls']}")

print("\n  GRANT de anon y authenticated sobre cada tabla (sin contar RLS):")
print(f"    {'tabla':<16} {'rol':<14} SELECT INSERT UPDATE DELETE")
for t in tablas:
    for rol_api in ("anon", "authenticated"):
        if not any(r["rolname"] == rol_api for r in roles_api):
            continue
        cur.execute(
            """
            SELECT has_table_privilege(%(r)s, %(t)s, 'SELECT') s,
                   has_table_privilege(%(r)s, %(t)s, 'INSERT') i,
                   has_table_privilege(%(r)s, %(t)s, 'UPDATE') u,
                   has_table_privilege(%(r)s, %(t)s, 'DELETE') d;
            """,
            {"r": rol_api, "t": f"public.{t['tablename']}"},
        )
        p = cur.fetchone()
        print(f"    {t['tablename']:<16} {rol_api:<14} {str(p['s']):<6} {str(p['i']):<6} "
              f"{str(p['u']):<6} {p['d']}")

# Privilegios POR DEFECTO: los que Postgres dara automaticamente a
# cualquier tabla que se cree en el futuro en public (objtype 'r' =
# tablas). Se consulta porque docs/Decision_RLS_N0.txt descarta revocar
# los GRANT como alternativa a RLS con el argumento de que las tablas
# nuevas los recibirian otra vez; aqui se comprueba en vez de suponerlo.
# En la ACL, "arwdDxtm" son todas las letras de permiso de tabla
# (a=INSERT, r=SELECT, w=UPDATE, d=DELETE, D=TRUNCATE, ...).
cur.execute(
    """
    SELECT pg_get_userbyid(d.defaclrole) AS creador, d.defaclacl::text AS acl
    FROM pg_default_acl d
    JOIN pg_namespace n ON n.oid = d.defaclnamespace
    WHERE n.nspname = 'public' AND d.defaclobjtype = 'r';
    """
)
print("\n  Privilegios por defecto para TABLAS NUEVAS en public:")
for f in cur.fetchall():
    print(f"    creadas por {f['creador']:<15} -> {f['acl']}")

# ¿Tiene PostgREST expuesto el esquema public? Supabase guarda esa
# configuracion como un ajuste del rol authenticator (pgrst.db_schemas).
# Si no aparece, no se puede concluir nada desde la base de datos: el
# ajuste puede vivir en la configuracion del propio servicio.
cur.execute(
    """
    SELECT unnest(s.setconfig) AS ajuste
    FROM pg_db_role_setting s
    JOIN pg_roles r ON r.oid = s.setrole
    WHERE r.rolname = 'authenticator';
    """
)
ajustes = [f["ajuste"] for f in cur.fetchall()]
print("\n  Ajustes del rol authenticator (configuracion de PostgREST):")
for a in ajustes or ["(ninguno visible desde la base de datos)"]:
    print(f"    {a}")
cn.commit()

# ----------------------------------------------------------------------
titulo("5. PRUEBA EMPIRICA: QUE VE Y QUE PUEDE ESCRIBIR EL ROL anon")
# ----------------------------------------------------------------------
# SET LOCAL ROLE cambia el rol SOLO hasta el final de la transaccion
# actual. Todo el bloque termina en ROLLBACK, asi que nada de lo que
# pase aqui queda escrito ni cambia la sesion.
#
# Se comparan dos lecturas de la misma tabla: con el rol del backend y
# con anon. Para que la comparacion diga algo, la tabla tiene que tener
# filas; clientes suele estar vacia tras las limpiezas de los scripts,
# asi que se usa reglas_negocio, que tiene datos de configuracion fijos.
TABLA_LECTURA = "reglas_negocio"
try:
    cur.execute(f"SELECT COUNT(*) AS n FROM {TABLA_LECTURA};")
    n_backend = cur.fetchone()["n"]
    print(f"  Como {ROL_ACTUAL:<10}: SELECT COUNT(*) FROM {TABLA_LECTURA} -> {n_backend}")

    cur.execute("SET LOCAL ROLE anon;")
    cur.execute("SELECT current_user AS u;")
    print(f"  Rol cambiado a: {cur.fetchone()['u']}")

    cur.execute(f"SELECT COUNT(*) AS n FROM {TABLA_LECTURA};")
    n_anon = cur.fetchone()["n"]
    print(f"  Como anon      : SELECT COUNT(*) FROM {TABLA_LECTURA} -> {n_anon}")

    # SAVEPOINT: un error dentro de una transaccion la deja "abortada" y
    # ya no admite mas consultas. El savepoint permite volver atras solo
    # hasta este punto y seguir usando la transaccion.
    cur.execute("SAVEPOINT antes_insert;")
    try:
        cur.execute(
            "INSERT INTO clientes (nombre, email) VALUES ('rls-prueba', 'rls@example.com');"
        )
        print("  Como anon      : INSERT INTO clientes -> ACEPTADO (dentro de ROLLBACK)")
    except psycopg2.Error as e:
        cur.execute("ROLLBACK TO SAVEPOINT antes_insert;")
        print(f"  Como anon      : INSERT INTO clientes -> RECHAZADO")
        print(f"                   SQLSTATE {e.pgcode}: {e.pgerror.strip().splitlines()[0]}")
except psycopg2.Error as e:
    print(f"  No se pudo completar la prueba: SQLSTATE {e.pgcode}: {e.pgerror}")
finally:
    # Pase lo que pase, se deshace todo: el cambio de rol y cualquier
    # escritura que hubiera llegado a aceptarse.
    cn.rollback()

cur.execute("SELECT current_user AS u;")
print(f"  Tras ROLLBACK, rol actual: {cur.fetchone()['u']}")
cn.commit()

# ----------------------------------------------------------------------
titulo("CONCLUSION (calculada a partir de los datos de arriba)")
# ----------------------------------------------------------------------
todas_rls = all(t["rls_activado"] for t in tablas)
ninguna_forzada = not any(t["rls_forzado"] for t in tablas)
todas_propias = all(t["es_propietario"] for t in tablas)
print(f"  Tablas en public                      : {len(tablas)}")
print(f"  Todas con RLS activado                : {todas_rls}")
print(f"  Ninguna con RLS forzado (FORCE)       : {ninguna_forzada}")
print(f"  Politicas definidas                   : {n_politicas}")
print(f"  {ROL_ACTUAL} es propietario de todas     : {todas_propias}")
print(f"  {ROL_ACTUAL} tiene rolbypassrls          : {rol['rolbypassrls']}")
print(f"  {ROL_ACTUAL} es superusuario             : {rol['rolsuper']}")

cur.close()
cn.close()
