-- 002_risk_logging.sql: late-delivery probability beside each prediction.
-- Design: plan Phase A step A2. Applied once by hand as eta_admin.
-- Nullable so pre-risk rows stay NULL and are excluded from risk metrics.
-- Table ownership and existing grants are unchanged.

SET statement_timeout = '30s';
SET lock_timeout = '10s';

ALTER TABLE app.predictions
  ADD COLUMN IF NOT EXISTS late_probability DOUBLE PRECISION;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'predictions_late_probability_range') THEN
    ALTER TABLE app.predictions
      ADD CONSTRAINT predictions_late_probability_range
      CHECK (late_probability IS NULL OR (late_probability >= 0.0 AND late_probability <= 1.0));
  END IF;
END
$$;

ALTER TABLE app.predictions OWNER TO eta_admin;
GRANT INSERT, SELECT ON app.predictions TO eta_app;
