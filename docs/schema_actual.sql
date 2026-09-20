-- ==========================================================================
-- ESQUEMA REAL DE LA BASE DE DATOS - ARCHIVO GENERADO AUTOMATICAMENTE
--
-- Generado por scripts/dump_schema.py el 2026-09-20 11:19:55 UTC
-- NO EDITAR A MANO: se sobrescribe al volver a ejecutar el script.
--
-- Reconstruido leyendo information_schema (columns,
-- table_constraints, key_column_usage, constraint_column_usage y
-- check_constraints), no copiado de ningun archivo previo.
--
-- Tablas: 9
-- ==========================================================================


-- ------------------------------------------------------------------------
-- Tabla: clientes   (0 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE clientes (
    id                       SERIAL NOT NULL,
    nombre                   VARCHAR(150) NOT NULL,
    email                    VARCHAR(150) NOT NULL,
    telefono                 VARCHAR(30),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT clientes_pkey PRIMARY KEY (id),
    CONSTRAINT clientes_email_key UNIQUE (email)
);

-- ------------------------------------------------------------------------
-- Tabla: leads   (0 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE leads (
    id                       SERIAL NOT NULL,
    cliente_id               INTEGER NOT NULL,
    canal                    VARCHAR(50),
    mensaje_original         TEXT,
    fotos_urls               JSONB,
    datos_estructurados      JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT leads_cliente_id_fkey FOREIGN KEY (cliente_id) REFERENCES clientes(id),
    CONSTRAINT leads_pkey PRIMARY KEY (id),
    CONSTRAINT chk_leads_m2_rango CHECK ((((datos_estructurados ->> 'm2'::text))::numeric > (0)::numeric) AND (((datos_estructurados ->> 'm2'::text))::numeric <= (500)::numeric))
);

-- ------------------------------------------------------------------------
-- Tabla: logs   (0 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE logs (
    id                       SERIAL NOT NULL,
    entity_type              VARCHAR(30) NOT NULL,
    entity_id                INTEGER NOT NULL,
    accion                   VARCHAR(60) NOT NULL,
    detalle                  JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT logs_pkey PRIMARY KEY (id)
);

-- ------------------------------------------------------------------------
-- Tabla: oportunidades   (0 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE oportunidades (
    id                       SERIAL NOT NULL,
    lead_id                  INTEGER NOT NULL,
    tipo_reforma             VARCHAR(80),
    prioridad                VARCHAR(20),
    datos_completos          BOOLEAN NOT NULL DEFAULT false,
    confianza_ia             NUMERIC(3,2),
    estado                   VARCHAR(30) NOT NULL DEFAULT 'nueva'::character varying,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT oportunidades_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES leads(id),
    CONSTRAINT oportunidades_pkey PRIMARY KEY (id),
    CONSTRAINT chk_oportunidades_tipo_reforma CHECK (((tipo_reforma)::text = ANY ((ARRAY['bano'::character varying, 'cocina'::character varying, 'integral_vivienda'::character varying, 'parcial_acabados'::character varying])::text[])) OR (tipo_reforma IS NULL)),
    CONSTRAINT oportunidades_estado_check CHECK ((estado)::text = ANY ((ARRAY['nueva'::character varying, 'cualificada'::character varying, 'pendiente_aprobacion'::character varying, 'visita_agendada'::character varying, 'presupuesto_enviado'::character varying, 'ganada'::character varying, 'perdida'::character varying])::text[])),
    CONSTRAINT oportunidades_prioridad_check CHECK ((prioridad)::text = ANY ((ARRAY['baja'::character varying, 'media'::character varying, 'alta'::character varying])::text[]))
);

-- ------------------------------------------------------------------------
-- Tabla: presupuestos   (0 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE presupuestos (
    id                       SERIAL NOT NULL,
    oportunidad_id           INTEGER NOT NULL,
    importe_min              NUMERIC(10,2),
    importe_max              NUMERIC(10,2),
    duracion_estimada_dias   INTEGER,
    requiere_aprobacion      BOOLEAN NOT NULL DEFAULT false,
    aprobado_por             VARCHAR(100),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    motivo_gate              VARCHAR(30),
    CONSTRAINT presupuestos_oportunidad_id_fkey FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
    CONSTRAINT presupuestos_pkey PRIMARY KEY (id),
    CONSTRAINT presupuestos_oportunidad_id_key UNIQUE (oportunidad_id),
    CONSTRAINT presupuestos_motivo_gate_check CHECK ((motivo_gate)::text = ANY ((ARRAY['cambios_estructurales'::character varying, 'importe_superior_umbral'::character varying, 'ambos'::character varying])::text[]))
);

-- ------------------------------------------------------------------------
-- Tabla: reglas_negocio   (5 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE reglas_negocio (
    id                       SERIAL NOT NULL,
    clave                    VARCHAR(50) NOT NULL,
    valor                    NUMERIC(10,2) NOT NULL,
    descripcion              TEXT,
    fecha_actualizacion      DATE NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT reglas_negocio_pkey PRIMARY KEY (id),
    CONSTRAINT reglas_negocio_clave_key UNIQUE (clave)
);

-- ------------------------------------------------------------------------
-- Tabla: tarifas_base   (12 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE tarifas_base (
    id                       SERIAL NOT NULL,
    tipo_reforma             VARCHAR(30) NOT NULL,
    nivel_acabados           VARCHAR(20) NOT NULL,
    precio_m2                NUMERIC(10,2) NOT NULL,
    incluye_tipico           TEXT,
    fuente                   TEXT,
    fecha_actualizacion      DATE NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT tarifas_base_pkey PRIMARY KEY (id),
    CONSTRAINT tarifas_base_tipo_reforma_nivel_acabados_key UNIQUE (tipo_reforma, nivel_acabados),
    CONSTRAINT tarifas_base_nivel_acabados_check CHECK ((nivel_acabados)::text = ANY ((ARRAY['basico'::character varying, 'medio'::character varying, 'alto'::character varying])::text[])),
    CONSTRAINT tarifas_base_tipo_reforma_check CHECK ((tipo_reforma)::text = ANY ((ARRAY['bano'::character varying, 'cocina'::character varying, 'integral_vivienda'::character varying, 'parcial_acabados'::character varying])::text[]))
);

-- ------------------------------------------------------------------------
-- Tabla: umbrales_gate   (4 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE umbrales_gate (
    tipo_reforma             VARCHAR(30) NOT NULL,
    umbral                   NUMERIC(10,2) NOT NULL,
    provisional              BOOLEAN NOT NULL DEFAULT false,
    descripcion              TEXT,
    fecha_actualizacion      DATE NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT umbrales_gate_pkey PRIMARY KEY (tipo_reforma),
    CONSTRAINT chk_umbrales_gate_tipo_reforma CHECK ((tipo_reforma)::text = ANY ((ARRAY['bano'::character varying, 'cocina'::character varying, 'integral_vivienda'::character varying, 'parcial_acabados'::character varying])::text[])),
    CONSTRAINT chk_umbrales_gate_umbral_positivo CHECK (umbral > (0)::numeric)
);

-- ------------------------------------------------------------------------
-- Tabla: visitas   (0 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE visitas (
    id                       SERIAL NOT NULL,
    oportunidad_id           INTEGER NOT NULL,
    fecha_propuesta          TIMESTAMPTZ,
    estado                   VARCHAR(30) NOT NULL DEFAULT 'reservada'::character varying,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT visitas_oportunidad_id_fkey FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
    CONSTRAINT visitas_pkey PRIMARY KEY (id),
    CONSTRAINT visitas_estado_check CHECK ((estado)::text = ANY ((ARRAY['reservada'::character varying, 'confirmada'::character varying, 'completada'::character varying, 'cancelada'::character varying])::text[]))
);

-- ==========================================================================
-- Row Level Security
-- ==========================================================================
ALTER TABLE clientes         ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads            ENABLE ROW LEVEL SECURITY;
ALTER TABLE logs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE oportunidades    ENABLE ROW LEVEL SECURITY;
ALTER TABLE presupuestos     ENABLE ROW LEVEL SECURITY;
ALTER TABLE reglas_negocio   ENABLE ROW LEVEL SECURITY;
ALTER TABLE tarifas_base     ENABLE ROW LEVEL SECURITY;
ALTER TABLE umbrales_gate    ENABLE ROW LEVEL SECURITY;
ALTER TABLE visitas          ENABLE ROW LEVEL SECURITY;
