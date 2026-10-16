from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from .config import DATABASE_URL

POOL = ConnectionPool(DATABASE_URL, kwargs={"row_factory": dict_row},
                      min_size=2, max_size=16, open=False)


def init_pool():
    POOL.open(wait=True)


def close_pool():
    POOL.close()


@contextmanager
def connection():
    with POOL.connection() as conn:
        yield conn


def init_db():
    with connection() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS assets (
          id text PRIMARY KEY, name text NOT NULL, metric text NOT NULL,
          unit text NOT NULL, cadence_seconds integer NOT NULL CHECK (cadence_seconds > 0),
          source_uri text, created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS telemetry (
          asset_id text NOT NULL REFERENCES assets(id), ts timestamptz NOT NULL,
          value double precision NOT NULL, quality text NOT NULL DEFAULT 'measured',
          PRIMARY KEY(asset_id, ts)
        );
        CREATE INDEX IF NOT EXISTS telemetry_recent ON telemetry(asset_id, ts DESC);
        CREATE TABLE IF NOT EXISTS alerts (
          id uuid PRIMARY KEY, asset_id text NOT NULL REFERENCES assets(id),
          ts timestamptz NOT NULL, value double precision NOT NULL,
          expected double precision NOT NULL, score double precision NOT NULL,
          state text NOT NULL DEFAULT 'open' CHECK (state IN ('open','acknowledged')),
          reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          acknowledged_at timestamptz,
          UNIQUE(asset_id, ts)
        );
        CREATE TABLE IF NOT EXISTS reports (
          id uuid PRIMARY KEY, asset_id text NOT NULL REFERENCES assets(id),
          payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS detector_state (
          asset_id text PRIMARY KEY REFERENCES assets(id),
          last_alert_ts timestamptz, armed boolean NOT NULL DEFAULT true,
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        """)

