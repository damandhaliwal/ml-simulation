-- 001_app_logging.sql: first application schema for local prediction logging.
-- Design: docs/db-eta-app-design.md. Applied once by hand as eta_admin.
-- Creates app schema plus runs/predictions/outcomes, owned by eta_admin,
-- then grants eta_app INSERT + SELECT only. No role creation here.

SET statement_timeout = '30s';
SET lock_timeout = '10s';

CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION eta_admin;

CREATE TABLE IF NOT EXISTS app.runs (
  run_id TEXT PRIMARY KEY,
  schema_version INT NOT NULL DEFAULT 1,
  simulated BOOL NOT NULL DEFAULT TRUE CHECK (simulated),
  source_sha256 TEXT NOT NULL,
  source_order_count INT NOT NULL CHECK (source_order_count > 0),
  scenario JSONB NOT NULL,
  code_commit TEXT NOT NULL,
  image_id TEXT NOT NULL,
  model_sha256 TEXT NOT NULL,
  model_metadata_sha256 TEXT NOT NULL,
  created_at_wall TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.predictions (
  run_id TEXT NOT NULL REFERENCES app.runs (run_id),
  order_id TEXT NOT NULL,
  request_payload JSONB NOT NULL,
  features JSONB NOT NULL,
  predicted_delivery_duration_minutes DOUBLE PRECISION NOT NULL CHECK (predicted_delivery_duration_minutes >= 5.0),
  model_sha256 TEXT NOT NULL,
  predicted_at_simulated TIMESTAMPTZ NOT NULL,
  recorded_at_wall TIMESTAMPTZ NOT NULL DEFAULT now(),
  model_latency_ms DOUBLE PRECISION NOT NULL CHECK (model_latency_ms >= 0),
  simulated BOOL NOT NULL DEFAULT TRUE CHECK (simulated),
  PRIMARY KEY (run_id, order_id)
);

CREATE TABLE IF NOT EXISTS app.outcomes (
  run_id TEXT NOT NULL REFERENCES app.runs (run_id),
  order_id TEXT NOT NULL,
  confirmed_at TIMESTAMPTZ NOT NULL,
  promised_delivery_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('delivered', 'cancelled')),
  delivered_at TIMESTAMPTZ,
  delivery_duration_minutes DOUBLE PRECISION,
  late_delivery BOOL,
  outcome_available_at_simulated TIMESTAMPTZ NOT NULL,
  observed_at_simulated TIMESTAMPTZ NOT NULL,
  recorded_at_wall TIMESTAMPTZ NOT NULL DEFAULT now(),
  simulated BOOL NOT NULL DEFAULT TRUE CHECK (simulated),
  PRIMARY KEY (run_id, order_id),
  CHECK (observed_at_simulated > outcome_available_at_simulated),
  CHECK (
    (status = 'delivered' AND delivered_at IS NOT NULL
      AND delivery_duration_minutes IS NOT NULL AND delivery_duration_minutes > 0
      AND late_delivery IS NOT NULL)
    OR (status = 'cancelled' AND delivered_at IS NULL
      AND delivery_duration_minutes IS NULL AND late_delivery IS NULL)
  )
);

ALTER SCHEMA app OWNER TO eta_admin;
ALTER TABLE app.runs OWNER TO eta_admin;
ALTER TABLE app.predictions OWNER TO eta_admin;
ALTER TABLE app.outcomes OWNER TO eta_admin;

GRANT CONNECT ON DATABASE eta TO eta_app;
GRANT USAGE ON SCHEMA app TO eta_app;
GRANT INSERT, SELECT ON app.runs TO eta_app;
GRANT INSERT, SELECT ON app.predictions TO eta_app;
GRANT INSERT, SELECT ON app.outcomes TO eta_app;
