"""
Database layer for the StatsPack Tech Sales Agent Portal.

Runs on Postgres when DATABASE_URL is set (production / Render + Neon),
and on a local SQLite file otherwise (development on your machine).

The rest of the application writes plain SQL with '?' placeholders and never
needs to know which engine is underneath.
"""
import os
import re
import sqlite3
import threading
from decimal import Decimal

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if IS_PG:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    from psycopg2.pool import ThreadedConnectionPool

SQLITE_PATH = os.environ.get("SQLITE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "portal.db"))

_pool = None
_local = threading.local()
_lock = threading.Lock()

# psycopg2's pool raises rather than waits when every connection is busy, so a
# burst of simultaneous users would get server errors. This semaphore makes the
# surplus requests queue for a moment instead of failing.
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))
POOL_WAIT_SECONDS = 20
_slots = threading.BoundedSemaphore(POOL_MAX)


class ConnectionLost(Exception):
    """The database dropped the connection. The caller may safely retry:
    nothing was committed, so no half-finished write can survive."""


def _is_connection_error(exc):
    """True when the failure is transport or capacity, not a bad query."""
    if IS_PG:
        if isinstance(exc, (psycopg2.InterfaceError, psycopg2.OperationalError)):
            return True
        if isinstance(exc, psycopg2.pool.PoolError):
            return True
    text = str(exc).lower()
    return any(marker in text for marker in (
        "server closed the connection", "connection already closed",
        "connection not open", "terminating connection", "ssl connection has been closed",
        "could not connect", "connection refused", "cursor already closed",
        "consuming input failed", "eof detected", "connection pool exhausted"))


# --------------------------------------------------------------------------
# schema  ({PK} and {MONEY} are substituted per engine)
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id             {PK},
  name           TEXT NOT NULL,
  slug           TEXT NOT NULL DEFAULT '',
  country        TEXT NOT NULL DEFAULT '',
  contact_email  TEXT NOT NULL DEFAULT '',
  contact_phone  TEXT NOT NULL DEFAULT '',
  status         TEXT NOT NULL DEFAULT 'Active',
  is_host        INTEGER NOT NULL DEFAULT 0,
  base_currency  TEXT NOT NULL DEFAULT 'USD',
  first_rate     {MONEY} NOT NULL DEFAULT 0.60,
  recurring_rate {MONEY} NOT NULL DEFAULT 0.10,
  window_months  INTEGER NOT NULL DEFAULT 12,
  notes          TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_events (
  id          {PK},
  client_id   INTEGER NOT NULL,
  company_id  INTEGER NOT NULL DEFAULT 0,
  author_id   INTEGER NOT NULL DEFAULT 0,
  author_name TEXT NOT NULL DEFAULT '',
  kind        TEXT NOT NULL DEFAULT 'note',
  from_stage  TEXT NOT NULL DEFAULT '',
  to_stage    TEXT NOT NULL DEFAULT '',
  body        TEXT NOT NULL DEFAULT '',
  next_step   TEXT NOT NULL DEFAULT '',
  due_date    TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id            {PK},
  name          TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE,
  phone         TEXT NOT NULL DEFAULT '',
  country       TEXT NOT NULL DEFAULT '',
  role          TEXT NOT NULL DEFAULT 'agent',
  status        TEXT NOT NULL DEFAULT 'Active',
  password_hash TEXT NOT NULL,
  must_change_pw INTEGER NOT NULL DEFAULT 0,
  is_super      INTEGER NOT NULL DEFAULT 0,
  company_id    INTEGER NOT NULL DEFAULT 1,
  agent_type    TEXT NOT NULL DEFAULT '',
  avatar        TEXT NOT NULL DEFAULT '',
  avatar_thumb  TEXT NOT NULL DEFAULT '',
  first_rate    {MONEY},
  recurring_rate {MONEY},
  window_months INTEGER,
  quota_usd     {MONEY} NOT NULL DEFAULT 0,
  start_date    TEXT NOT NULL DEFAULT '',
  notes         TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  ip         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS clients (
  id             {PK},
  agent_id       INTEGER NOT NULL,
  name           TEXT NOT NULL,
  contact_person TEXT NOT NULL DEFAULT '',
  contact_email  TEXT NOT NULL DEFAULT '',
  contact_phone  TEXT NOT NULL DEFAULT '',
  country        TEXT NOT NULL DEFAULT '',
  industry       TEXT NOT NULL DEFAULT '',
  company_id     INTEGER NOT NULL DEFAULT 1,
  product        TEXT NOT NULL DEFAULT '',
  currency       TEXT NOT NULL DEFAULT 'USD',
  monthly_value  {MONEY} NOT NULL DEFAULT 0,
  stage          TEXT NOT NULL DEFAULT 'Prospect',
  won_date       TEXT NOT NULL DEFAULT '',
  notes          TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
  id          {PK},
  client_id   INTEGER NOT NULL,
  amount      {MONEY} NOT NULL DEFAULT 0,
  currency    TEXT NOT NULL DEFAULT 'USD',
  fx_rate     {MONEY} NOT NULL DEFAULT 1,
  amount_usd  {MONEY} NOT NULL DEFAULT 0,
  paid_date   TEXT NOT NULL,
  reference   TEXT NOT NULL DEFAULT '',
  note        TEXT NOT NULL DEFAULT '',
  voided      INTEGER NOT NULL DEFAULT 0,
  recorded_by INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payouts (
  id           {PK},
  agent_id     INTEGER NOT NULL,
  amount_usd   {MONEY} NOT NULL DEFAULT 0,
  period_label TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'Pending',
  reference    TEXT NOT NULL DEFAULT '',
  note         TEXT NOT NULL DEFAULT '',
  paid_date    TEXT NOT NULL DEFAULT '',
  created_by   INTEGER NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  id         {PK},
  agent_id   INTEGER NOT NULL,
  author_id  INTEGER NOT NULL,
  period     TEXT NOT NULL DEFAULT '',
  rating     INTEGER NOT NULL DEFAULT 3,
  body       TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fx (
  code       TEXT PRIMARY KEY,
  rate       {MONEY} NOT NULL DEFAULT 1,
  label      TEXT NOT NULL DEFAULT '',
  source     TEXT NOT NULL DEFAULT 'manual',
  synced_at  TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  skey  TEXT PRIMARY KEY,
  svalue TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit (
  id         {PK},
  user_id    INTEGER NOT NULL DEFAULT 0,
  actor      TEXT NOT NULL DEFAULT '',
  action     TEXT NOT NULL DEFAULT '',
  detail     TEXT NOT NULL DEFAULT '',
  ip         TEXT NOT NULL DEFAULT '',
  company_id INTEGER NOT NULL DEFAULT 0,
  target     TEXT NOT NULL DEFAULT '',
  meta       TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_clients_agent   ON clients(agent_id);
CREATE INDEX IF NOT EXISTS idx_payments_client ON payments(client_id);
CREATE INDEX IF NOT EXISTS idx_payouts_agent   ON payouts(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user   ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_reviews_agent   ON reviews(agent_id);
CREATE INDEX IF NOT EXISTS idx_users_company   ON users(company_id);
CREATE INDEX IF NOT EXISTS idx_clients_company ON clients(company_id);
CREATE INDEX IF NOT EXISTS idx_events_client   ON client_events(client_id);
CREATE INDEX IF NOT EXISTS idx_audit_company   ON audit(company_id);
"""


def _fill(sql):
    if IS_PG:
        return sql.replace("{PK}", "SERIAL PRIMARY KEY").replace("{MONEY}", "NUMERIC(18,2)")
    return sql.replace("{PK}", "INTEGER PRIMARY KEY AUTOINCREMENT").replace("{MONEY}", "REAL")


def _schema_sql():
    return _fill(SCHEMA)


def _index_sql():
    return _fill(INDEXES)


# --------------------------------------------------------------------------
# connections
# --------------------------------------------------------------------------
def _connect_sqlite():
    conn = sqlite3.connect(SQLITE_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def init_pool():
    global _pool
    if IS_PG and _pool is None:
        with _lock:
            if _pool is None:
                url = DATABASE_URL
                # Neon and most managed providers require TLS
                if "sslmode=" not in url and "localhost" not in url and "127.0.0.1" not in url:
                    url += ("&" if "?" in url else "?") + "sslmode=require"
                # Serverless Postgres suspends when idle and drops sockets.
                # Keepalives make the client notice quickly; the timeout stops
                # a cold start from hanging a web request indefinitely.
                _pool = ThreadedConnectionPool(
                    1, POOL_MAX, url,
                    connect_timeout=12,
                    keepalives=1, keepalives_idle=30,
                    keepalives_interval=10, keepalives_count=5,
                    application_name="statspack-agent-portal")


class _Conn:
    """Thin wrapper giving both engines the same call surface."""

    def __init__(self, raw):
        self.raw = raw

    def _prep(self, sql):
        return sql.replace("?", "%s") if IS_PG else sql

    def _cursor(self):
        if IS_PG:
            return self.raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self.raw.cursor()

    def query(self, sql, params=()):
        cur = self._cursor()
        cur.execute(self._prep(sql), params)
        rows = cur.fetchall()
        cur.close()
        return [_clean(r) for r in rows]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        cur = self._cursor()
        cur.execute(self._prep(sql), params)
        cur.close()

    def insert(self, sql, params=()):
        """INSERT returning the new row id."""
        cur = self._cursor()
        if IS_PG:
            cur.execute(self._prep(sql) + " RETURNING id", params)
            row = cur.fetchone()
            new_id = row["id"] if row else None
        else:
            cur.execute(sql, params)
            new_id = cur.lastrowid
        cur.close()
        return new_id

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


def _clean(row):
    """Normalise a row into a plain dict with JSON-safe values."""
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, Decimal):
            d[k] = float(v)
    return d


def _healthy(raw):
    """A pooled connection can be dead on arrival after the server suspends.
    Ping it rather than discover the corpse mid-request."""
    if getattr(raw, "closed", 0):
        return False
    try:
        cur = raw.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        raw.rollback()
        return True
    except Exception:
        return False


class connection:
    """Context manager: `with db.connection() as conn:`

    On Postgres, every checkout is validated before use and any connection
    that died while idle is discarded rather than handed to a request.
    """

    def __enter__(self):
        self.slot = False
        if IS_PG:
            init_pool()
            # Wait for a free connection rather than failing the request.
            if not _slots.acquire(timeout=POOL_WAIT_SECONDS):
                raise ConnectionLost("All database connections are busy.")
            self.slot = True
            self.raw = None
            stale = []
            try:
                for _ in range(4):
                    candidate = _pool.getconn()
                    if _healthy(candidate):
                        self.raw = candidate
                        break
                    stale.append(candidate)
            finally:
                for dead in stale:                     # never reuse these
                    try:
                        _pool.putconn(dead, close=True)
                    except Exception:
                        pass
            if self.raw is None:
                _slots.release()
                self.slot = False
                raise ConnectionLost("Could not obtain a live database connection.")
            self.conn = _Conn(self.raw)
        else:
            if not hasattr(_local, "sqlite"):
                _local.sqlite = _connect_sqlite()
            self.raw = _local.sqlite
            self.conn = _Conn(self.raw)
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        broken = exc is not None and _is_connection_error(exc)
        try:
            if exc_type is None:
                self.raw.commit()
            else:
                if not broken:
                    self.raw.rollback()
        except Exception:
            broken = True
        finally:
            if IS_PG:
                try:
                    _pool.putconn(self.raw, close=broken)
                except Exception:
                    pass
            elif broken:
                try:
                    self.raw.close()
                except Exception:
                    pass
                if hasattr(_local, "sqlite"):
                    del _local.sqlite
            if self.slot:
                self.slot = False
                _slots.release()
        if broken:
            raise ConnectionLost(str(exc))
        return False


def _column_exists(conn, table, column):
    if IS_PG:
        row = conn.one(
            "SELECT 1 AS c FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?", (table, column))
        return bool(row)
    rows = conn.query(f"PRAGMA table_info({table})")
    return any(r.get("name") == column for r in rows)


MONEY_T = "NUMERIC(18,2)" if IS_PG else "REAL"

MIGRATIONS = [
    # v1.1
    ("users", "is_super", "INTEGER NOT NULL DEFAULT 0"),
    ("fx", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("fx", "synced_at", "TEXT NOT NULL DEFAULT ''"),
    # v1.2
    ("users", "company_id", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "agent_type", "TEXT NOT NULL DEFAULT ''"),
    ("users", "avatar", "TEXT NOT NULL DEFAULT ''"),
    ("users", "avatar_thumb", "TEXT NOT NULL DEFAULT ''"),
    ("users", "first_rate", MONEY_T),
    ("users", "recurring_rate", MONEY_T),
    ("users", "window_months", "INTEGER"),
    ("clients", "industry", "TEXT NOT NULL DEFAULT ''"),
    ("clients", "company_id", "INTEGER NOT NULL DEFAULT 1"),
    ("audit", "company_id", "INTEGER NOT NULL DEFAULT 0"),
    ("audit", "target", "TEXT NOT NULL DEFAULT ''"),
    ("audit", "meta", "TEXT NOT NULL DEFAULT ''"),
]


def migrate(conn):
    """Add columns introduced after the first release. Safe to re-run."""
    applied = []
    for table, column, ddl in MIGRATIONS:
        try:
            if not _column_exists(conn, table, column):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                applied.append(f"{table}.{column}")
        except Exception as e:
            print(f"  migration warning: {table}.{column} -> {e}")
    return applied


def init_db():
    """Create tables and apply migrations. Safe to run on every boot."""
    if IS_PG:
        init_pool()
    def run(sql):
        with connection() as conn:
            if IS_PG:
                cur = conn.raw.cursor()
                cur.execute(sql)
                cur.close()
            else:
                conn.raw.executescript(sql)

    # 1. tables  2. add any missing columns  3. indexes (which may use them)
    run(_schema_sql())
    with connection() as conn:
        applied = migrate(conn)
        if applied:
            print("  migrations applied:", ", ".join(applied))
    run(_index_sql())
    return "postgres" if IS_PG else "sqlite"


def engine_name():
    return "postgres" if IS_PG else f"sqlite ({SQLITE_PATH})"
