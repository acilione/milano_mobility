\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'ingestion_user', :'ingestion_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'ingestion_user')\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'transformer_user', :'transformer_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'transformer_user')\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'bi_user', :'bi_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'bi_user')\gexec

CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS staging_dbt;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS marts;

CREATE TABLE IF NOT EXISTS audit.ingestion_manifest (
    pipeline_run_id text PRIMARY KEY,
    source text NOT NULL,
    retrieved_at timestamptz NOT NULL,
    effective_snapshot_date date NOT NULL,
    object_uri text NOT NULL,
    sha256 char(64) NOT NULL,
    http_etag text,
    http_last_modified text,
    bytes bigint NOT NULL CHECK (bytes > 0),
    validator_status text,
    status text NOT NULL CHECK (
        status IN ('RECEIVED', 'VALIDATED', 'QUARANTINED', 'PUBLISHED', 'SKIPPED', 'FAILED')
    ),
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_manifest_source_sha
    ON audit.ingestion_manifest (source, sha256);
CREATE INDEX IF NOT EXISTS ix_manifest_snapshot
    ON audit.ingestion_manifest (effective_snapshot_date DESC);

CREATE TABLE IF NOT EXISTS staging.agency (
    agency_id text NOT NULL,
    agency_name text NOT NULL,
    agency_url text NOT NULL,
    agency_timezone text NOT NULL,
    agency_lang text,
    entity_hash char(64) NOT NULL,
    source_snapshot_date date NOT NULL,
    pipeline_run_id text NOT NULL REFERENCES audit.ingestion_manifest(pipeline_run_id),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_snapshot_date, pipeline_run_id, agency_id)
);

CREATE TABLE IF NOT EXISTS staging.stops (
    stop_id text NOT NULL,
    stop_code text,
    stop_name text NOT NULL,
    stop_desc text,
    stop_lat double precision NOT NULL,
    stop_lon double precision NOT NULL,
    zone_id text,
    location_type smallint,
    parent_station text,
    entity_hash char(64) NOT NULL,
    source_snapshot_date date NOT NULL,
    pipeline_run_id text NOT NULL REFERENCES audit.ingestion_manifest(pipeline_run_id),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_snapshot_date, pipeline_run_id, stop_id)
);

CREATE TABLE IF NOT EXISTS staging.routes (
    route_id text NOT NULL,
    agency_id text,
    route_short_name text,
    route_long_name text,
    route_desc text,
    route_type smallint NOT NULL,
    route_color text,
    route_text_color text,
    entity_hash char(64) NOT NULL,
    source_snapshot_date date NOT NULL,
    pipeline_run_id text NOT NULL REFERENCES audit.ingestion_manifest(pipeline_run_id),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_snapshot_date, pipeline_run_id, route_id)
);

CREATE TABLE IF NOT EXISTS staging.trips (
    route_id text NOT NULL,
    service_id text NOT NULL,
    trip_id text NOT NULL,
    trip_headsign text,
    direction_id smallint,
    block_id text,
    shape_id text,
    entity_hash char(64) NOT NULL,
    source_snapshot_date date NOT NULL,
    pipeline_run_id text NOT NULL REFERENCES audit.ingestion_manifest(pipeline_run_id),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_snapshot_date, pipeline_run_id, trip_id)
);

CREATE TABLE IF NOT EXISTS staging.stop_times (
    trip_id text NOT NULL,
    arrival_seconds integer NOT NULL CHECK (arrival_seconds >= 0),
    departure_seconds integer NOT NULL CHECK (departure_seconds >= 0),
    stop_id text NOT NULL,
    stop_sequence integer NOT NULL CHECK (stop_sequence >= 0),
    stop_headsign text,
    pickup_type smallint,
    drop_off_type smallint,
    entity_hash char(64) NOT NULL,
    source_snapshot_date date NOT NULL,
    pipeline_run_id text NOT NULL REFERENCES audit.ingestion_manifest(pipeline_run_id),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_snapshot_date, pipeline_run_id, trip_id, stop_sequence)
);

CREATE TABLE IF NOT EXISTS staging.calendar (
    service_id text NOT NULL,
    monday smallint NOT NULL,
    tuesday smallint NOT NULL,
    wednesday smallint NOT NULL,
    thursday smallint NOT NULL,
    friday smallint NOT NULL,
    saturday smallint NOT NULL,
    sunday smallint NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    entity_hash char(64) NOT NULL,
    source_snapshot_date date NOT NULL,
    pipeline_run_id text NOT NULL REFERENCES audit.ingestion_manifest(pipeline_run_id),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_snapshot_date, pipeline_run_id, service_id)
);

CREATE TABLE IF NOT EXISTS staging.calendar_dates (
    service_id text NOT NULL,
    service_date date NOT NULL,
    exception_type smallint NOT NULL CHECK (exception_type IN (1, 2)),
    entity_hash char(64) NOT NULL,
    source_snapshot_date date NOT NULL,
    pipeline_run_id text NOT NULL REFERENCES audit.ingestion_manifest(pipeline_run_id),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_snapshot_date, pipeline_run_id, service_id, service_date)
);

CREATE TABLE IF NOT EXISTS staging.weather_day (
    weather_date date NOT NULL,
    area_id text NOT NULL,
    temperature_min_c numeric(5, 2),
    temperature_max_c numeric(5, 2),
    precipitation_mm numeric(7, 2),
    weather_code smallint,
    source_uri text NOT NULL,
    pipeline_run_id text NOT NULL,
    retrieved_at timestamptz NOT NULL,
    PRIMARY KEY (weather_date, area_id)
);

GRANT USAGE ON SCHEMA audit, staging TO :"ingestion_user";
GRANT SELECT, INSERT, UPDATE ON audit.ingestion_manifest TO :"ingestion_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA staging TO :"ingestion_user";
ALTER TABLE staging.agency OWNER TO :"ingestion_user";
ALTER TABLE staging.stops OWNER TO :"ingestion_user";
ALTER TABLE staging.routes OWNER TO :"ingestion_user";
ALTER TABLE staging.trips OWNER TO :"ingestion_user";
ALTER TABLE staging.stop_times OWNER TO :"ingestion_user";
ALTER TABLE staging.calendar OWNER TO :"ingestion_user";
ALTER TABLE staging.calendar_dates OWNER TO :"ingestion_user";
ALTER TABLE staging.weather_day OWNER TO :"ingestion_user";

GRANT CONNECT ON DATABASE mobility TO :"transformer_user", :"bi_user";
GRANT USAGE ON SCHEMA audit, staging, staging_dbt, core, marts TO :"transformer_user";
GRANT SELECT ON ALL TABLES IN SCHEMA audit, staging TO :"transformer_user";
GRANT CREATE, USAGE ON SCHEMA staging_dbt, core, marts TO :"transformer_user";

GRANT USAGE ON SCHEMA marts TO :"bi_user";
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO :"bi_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"transformer_user" IN SCHEMA marts
    GRANT SELECT ON TABLES TO :"bi_user";
