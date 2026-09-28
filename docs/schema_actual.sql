-- ==========================================================================
-- ESQUEMA REAL DE LA BASE DE DATOS - ARCHIVO GENERADO AUTOMATICAMENTE
--
-- Generado por scripts/dump_schema.py el 2026-09-28 12:01:14 UTC
-- NO EDITAR A MANO: se sobrescribe al volver a ejecutar el script.
--
-- Reconstruido leyendo information_schema (columns,
-- table_constraints, key_column_usage y check_constraints), pg_constraint
-- (claves foráneas) y pg_indexes, no copiado de ningun archivo previo.
--
-- Tablas: 10, en orden topológico: cada una después de las
-- tablas a las que apunta, para que el archivo se pueda ejecutar entero.
-- ==========================================================================


-- ------------------------------------------------------------------------
-- Tabla: clientes   (15 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE clientes (
    id                       SERIAL NOT NULL,
    nombre                   VARCHAR(150) NOT NULL,
    email                    VARCHAR(150) NOT NULL,
    telefono                 VARCHAR(30),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT clientes_pkey PRIMARY KEY (id)
);

-- ------------------------------------------------------------------------
-- Tabla: leads   (20 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE leads (
    id                       SERIAL NOT NULL,
    cliente_id               INTEGER NOT NULL,
    canal                    VARCHAR(50),
    mensaje_original         TEXT,
    fotos_urls               JSONB,
    datos_estructurados      JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    lead_token               VARCHAR(100),
    CONSTRAINT leads_cliente_id_fkey FOREIGN KEY (cliente_id) REFERENCES clientes(id),
    CONSTRAINT leads_pkey PRIMARY KEY (id),
    CONSTRAINT leads_lead_token_key UNIQUE (lead_token),
    CONSTRAINT chk_leads_m2_rango CHECK ((((datos_estructurados ->> 'm2'::text))::numeric > (0)::numeric) AND (((datos_estructurados ->> 'm2'::text))::numeric <= (500)::numeric))
);

-- ------------------------------------------------------------------------
-- Tabla: logs   (12 filas en el momento del volcado)
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
-- Tabla: oportunidades   (20 filas en el momento del volcado)
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
    fecha_ultimo_contacto    TIMESTAMPTZ,
    CONSTRAINT oportunidades_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES leads(id),
    CONSTRAINT oportunidades_pkey PRIMARY KEY (id),
    CONSTRAINT chk_oportunidades_tipo_reforma CHECK (((tipo_reforma)::text = ANY ((ARRAY['bano'::character varying, 'cocina'::character varying, 'integral_vivienda'::character varying, 'parcial_acabados'::character varying])::text[])) OR (tipo_reforma IS NULL)),
    CONSTRAINT oportunidades_estado_check CHECK ((estado)::text = ANY ((ARRAY['nueva'::character varying, 'cualificada'::character varying, 'pendiente_aprobacion'::character varying, 'visita_agendada'::character varying, 'presupuesto_enviado'::character varying, 'seguimiento_pendiente'::character varying, 'ganada'::character varying, 'perdida'::character varying])::text[])),
    CONSTRAINT oportunidades_prioridad_check CHECK ((prioridad)::text = ANY ((ARRAY['baja'::character varying, 'media'::character varying, 'alta'::character varying])::text[]))
);

-- ------------------------------------------------------------------------
-- Tabla: presupuestos   (12 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE presupuestos (
    id                       SERIAL NOT NULL,
    oportunidad_id           INTEGER NOT NULL,
    importe_min_con_iva      NUMERIC(10,2),
    importe_max_con_iva      NUMERIC(10,2),
    duracion_estimada_dias   INTEGER,
    requiere_aprobacion      BOOLEAN NOT NULL DEFAULT false,
    aprobado_por             VARCHAR(100),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    motivo_gate              VARCHAR(30),
    iva_pct_aplicado         NUMERIC(4,2) NOT NULL DEFAULT 21,
    CONSTRAINT presupuestos_oportunidad_id_fkey FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
    CONSTRAINT presupuestos_pkey PRIMARY KEY (id),
    CONSTRAINT presupuestos_oportunidad_id_key UNIQUE (oportunidad_id),
    CONSTRAINT presupuestos_motivo_gate_check CHECK ((motivo_gate)::text = ANY ((ARRAY['cambios_estructurales'::character varying, 'importe_superior_umbral'::character varying, 'ambos'::character varying])::text[]))
);

-- ------------------------------------------------------------------------
-- Tabla: reglas_negocio   (12 filas en el momento del volcado)
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
    fecha_propuesta          TIMESTAMPTZ NOT NULL,
    estado                   VARCHAR(30) NOT NULL DEFAULT 'solicitada'::character varying,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    texto_cliente            TEXT NOT NULL,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT visitas_oportunidad_id_fkey FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
    CONSTRAINT visitas_pkey PRIMARY KEY (id),
    CONSTRAINT visitas_id_oportunidad_id_key UNIQUE (id, oportunidad_id),
    CONSTRAINT chk_visitas_texto_cliente_longitud CHECK ((char_length(texto_cliente) >= 1) AND (char_length(texto_cliente) <= 1000)),
    CONSTRAINT visitas_estado_check CHECK ((estado)::text = ANY ((ARRAY['solicitada'::character varying, 'confirmada'::character varying, 'completada'::character varying, 'cancelada'::character varying])::text[]))
);

-- ------------------------------------------------------------------------
-- Tabla: decisiones_gate   (0 filas en el momento del volcado)
-- ------------------------------------------------------------------------
CREATE TABLE decisiones_gate (
    id                       SERIAL NOT NULL,
    oportunidad_id           INTEGER NOT NULL,
    visita_id                INTEGER,
    decision                 VARCHAR(20) NOT NULL,
    motivo                   VARCHAR(30),
    informe                  TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT decisiones_gate_oportunidad_id_fkey FOREIGN KEY (oportunidad_id) REFERENCES oportunidades(id),
    CONSTRAINT decisiones_gate_visita_oportunidad_fkey FOREIGN KEY (visita_id, oportunidad_id) REFERENCES visitas(id, oportunidad_id),
    CONSTRAINT decisiones_gate_pkey PRIMARY KEY (id),
    CONSTRAINT decisiones_gate_oportunidad_id_key UNIQUE (oportunidad_id),
    CONSTRAINT chk_decisiones_gate_coherencia CHECK ((((decision)::text <> 'visita_acordada'::text) OR ((visita_id IS NOT NULL) AND (motivo IS NULL))) AND (((decision)::text <> 'descartar'::text) OR ((motivo IS NOT NULL) AND (visita_id IS NULL)))),
    CONSTRAINT chk_decisiones_gate_decision CHECK ((decision)::text = ANY ((ARRAY['visita_acordada'::character varying, 'descartar'::character varying])::text[])),
    CONSTRAINT chk_decisiones_gate_informe CHECK ((informe = btrim(informe)) AND ((char_length(informe) >= 1) AND (char_length(informe) <= 2000))),
    CONSTRAINT chk_decisiones_gate_motivo CHECK ((motivo IS NULL) OR ((motivo)::text = ANY ((ARRAY['precio'::character varying, 'plazo'::character varying, 'no_contesta'::character varying, 'proyecto_no_viable'::character varying, 'otro'::character varying])::text[])))
);

-- ==========================================================================
-- Indices que no respaldan ninguna restriccion (pg_indexes)
-- ==========================================================================
CREATE UNIQUE INDEX clientes_email_lower_key ON public.clientes USING btree (lower((email)::text));
CREATE UNIQUE INDEX visitas_una_activa_por_oportunidad ON public.visitas USING btree (oportunidad_id) WHERE ((estado)::text = ANY ((ARRAY['solicitada'::character varying, 'confirmada'::character varying])::text[]));

-- ==========================================================================
-- Row Level Security
-- ==========================================================================
ALTER TABLE clientes         ENABLE ROW LEVEL SECURITY;
ALTER TABLE decisiones_gate  ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads            ENABLE ROW LEVEL SECURITY;
ALTER TABLE logs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE oportunidades    ENABLE ROW LEVEL SECURITY;
ALTER TABLE presupuestos     ENABLE ROW LEVEL SECURITY;
ALTER TABLE reglas_negocio   ENABLE ROW LEVEL SECURITY;
ALTER TABLE tarifas_base     ENABLE ROW LEVEL SECURITY;
ALTER TABLE umbrales_gate    ENABLE ROW LEVEL SECURITY;
ALTER TABLE visitas          ENABLE ROW LEVEL SECURITY;
