-- 003_attempts.sql: operational log for failed / conflicting prediction attempts.
-- Design: plan Phase B. Applied once by hand as eta_admin.
-- One row per non-200 outcome on /predict. Never stores request bodies:
-- only our own error message plus the run/order correlation when parseable.
-- Table ownership follows the existing convention; eta_app gains INSERT+SELECT.

SET statement_timeout = '30s';
SET lock_timeout = '10s';

CREATE TABLE IF NOT EXISTS app.attempts (
  attempt_id TEXT PRIMARY KEY,
  recorded_at_wall TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_id TEXT,
  order_id TEXT,
  http_status INT NOT NULL CHECK (http_status >= 400),
  category TEXT NOT NULL CHECK (category IN ('invalid_request', 'conflict',
    'store_unavailable', 'internal_error')),
  detail TEXT NOT NULL,
  attempt_latency_ms DOUBLE PRECISION NOT NULL CHECK (attempt_latency_ms >= 0),
  simulated BOOL NOT NULL DEFAULT TRUE CHECK (simulated)
);

ALTER TABLE app.attempts OWNER TO eta_admin;
GRANT INSERT, SELECT ON app.attempts TO eta_app;
