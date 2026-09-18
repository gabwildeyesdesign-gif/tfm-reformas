-- ============================================================================
-- AVISO: ESTE ARCHIVO ESTA DESACTUALIZADO respecto a la base de datos real.
-- Marcado el 2026-09-17 tras verificar el esquema vivo contra Supabase.
--
-- Diferencias CONFIRMADAS con la base de datos en produccion:
--
--   1. Falta la columna presupuestos.motivo_gate
--      VARCHAR con CHECK (motivo_gate IN ('cambios_estructurales',
--      'importe_superior_umbral', 'ambos'))
--
--   2. Faltan DOS tablas enteras, que si existen y estan pobladas:
--      reglas_negocio (5 filas) y tarifas_base (12 filas).
--      Por eso los documentos hablan de 8 tablas y aqui solo hay 6.
--
--   3. Falta el CHECK anadido a oportunidades.tipo_reforma:
--      CONSTRAINT chk_oportunidades_tipo_reforma
--      CHECK (tipo_reforma IN ('bano','cocina','integral_vivienda',
--                              'parcial_acabados') OR tipo_reforma IS NULL)
--
-- NO usar este archivo para recrear la base de datos tal cual: faltarian
-- esas tablas y restricciones. Regenerarlo desde el esquema real es una
-- tarea pendiente aparte.
-- ============================================================================

CREATE TABLE clientes (
    id              SERIAL PRIMARY KEY,
    nombre          VARCHAR(150) NOT NULL,
    email           VARCHAR(150) UNIQUE NOT NULL,
    telefono        VARCHAR(30),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE leads (
    id                  SERIAL PRIMARY KEY,
    cliente_id          INTEGER NOT NULL REFERENCES clientes(id),
    canal               VARCHAR(50),
    mensaje_original    TEXT,
    fotos_urls          JSONB,
    datos_estructurados JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE oportunidades (
    id                  SERIAL PRIMARY KEY,
    lead_id             INTEGER NOT NULL REFERENCES leads(id),
    tipo_reforma        VARCHAR(80),
    prioridad           VARCHAR(20) CHECK (prioridad IN ('baja','media','alta')),
    datos_completos     BOOLEAN NOT NULL DEFAULT false,
    confianza_ia        NUMERIC(3,2),
    estado              VARCHAR(30) NOT NULL DEFAULT 'nueva'
                        CHECK (estado IN ('nueva','cualificada','visita_agendada',
                                           'presupuesto_enviado','ganada','perdida')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE presupuestos (
    id                      SERIAL PRIMARY KEY,
    oportunidad_id          INTEGER NOT NULL REFERENCES oportunidades(id),
    importe_min             NUMERIC(10,2),
    importe_max             NUMERIC(10,2),
    duracion_estimada_dias  INTEGER,
    requiere_aprobacion     BOOLEAN NOT NULL DEFAULT false,
    aprobado_por            VARCHAR(100),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE visitas (
    id                  SERIAL PRIMARY KEY,
    oportunidad_id      INTEGER NOT NULL REFERENCES oportunidades(id),
    fecha_propuesta     TIMESTAMPTZ,
    estado              VARCHAR(30) NOT NULL DEFAULT 'reservada'
                        CHECK (estado IN ('reservada','confirmada','completada','cancelada')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE logs (
    id              SERIAL PRIMARY KEY,
    entity_type     VARCHAR(30) NOT NULL,
    entity_id       INTEGER NOT NULL,
    accion          VARCHAR(60) NOT NULL,
    detalle         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Row Level Security: activada sin políticas.
-- El rol 'postgres' (usado por FastAPI vía el pooler) es propietario de las tablas
-- y no se ve afectado por RLS. Esto solo cierra el acceso público vía claves
-- anon/authenticated de la API automática de Supabase, que este proyecto no usa.
ALTER TABLE clientes      ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads         ENABLE ROW LEVEL SECURITY;
ALTER TABLE oportunidades ENABLE ROW LEVEL SECURITY;
ALTER TABLE presupuestos  ENABLE ROW LEVEL SECURITY;
ALTER TABLE visitas       ENABLE ROW LEVEL SECURITY;
ALTER TABLE logs          ENABLE ROW LEVEL SECURITY;
