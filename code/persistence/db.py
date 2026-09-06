"""Database configuration from the environment; no secrets live here.

The API connects as the least-privileged eta_app login, never eta_admin.
Required: POSTGRES_DB, POSTGRES_APP_USER, POSTGRES_APP_PASSWORD.
Optional: PGHOST (default 127.0.0.1; use postgres inside Docker),
PGPORT (default 5432).
"""

import os

REQUIRED = ("POSTGRES_DB", "POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD")


def db_config_from_env(env: dict | None = None) -> dict | None:
    """Return psycopg keyword args, or None when logging is not configured.

    None means no app credentials are present at all. A partial set is a
    ValueError: half-configured logging must fail loudly, never guess.
    The password travels in memory only; callers must never log this dict.
    """
    env = os.environ if env is None else env
    present = {key: env.get(key) for key in REQUIRED}
    if all(value is None for value in present.values()):
        return None
    missing = [key for key, value in present.items() if not value]
    if missing:
        raise ValueError(f"Incomplete DB configuration; missing: {', '.join(missing)}")
    try:
        port = int(env.get("PGPORT", "5432"))
    except (TypeError, ValueError):
        raise ValueError("PGPORT must be an integer") from None
    return {"host": env.get("PGHOST", "127.0.0.1"), "port": port,
            "dbname": present["POSTGRES_DB"], "user": present["POSTGRES_APP_USER"],
            "password": present["POSTGRES_APP_PASSWORD"], "connect_timeout": 5}
