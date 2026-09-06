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
import time
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

# Circuit breaker. When the database is genuinely gone, every request would
# otherwise spend ~36s working that out (three attempts x a 12s connect
# timeout), so pages hang instead of failing. After a confirmed outage we fail
# fast for a few seconds, then let one request try again.
CONNECT_TIMEOUT = int(os.environ.get("DB_CONNECT_TIMEOUT", "8"))
BREAKER_SECONDS = float(os.environ.get("DB_BREAKER_SECONDS", "5"))
_breaker_until = 0.0
_breaker_lock = threading.Lock()


def _breaker_open():
    with _breaker_lock:
        return time.monotonic() < _breaker_until


def _trip_breaker():
    global _breaker_until
    with _breaker_lock:
        _breaker_until = time.monotonic() + BREAKER_SECONDS


def _reset_breaker():
    global _breaker_until
    with _breaker_lock:
        _breaker_until = 0.0


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
  first_rate     {RATE} NOT NULL DEFAULT 0.60,
  recurring_rate {RATE} NOT NULL DEFAULT 0.10,
  window_months  INTEGER NOT NULL DEFAULT 12,
  notes          TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
  id          {PK},
  kind        TEXT NOT NULL DEFAULT '',
  severity    TEXT NOT NULL DEFAULT 'warning',
  title       TEXT NOT NULL DEFAULT '',
  detail      TEXT NOT NULL DEFAULT '',
  resolved    INTEGER NOT NULL DEFAULT 0,
  resolved_at TEXT NOT NULL DEFAULT '',
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS collaborations (
  id            {PK},
  client_id     INTEGER NOT NULL,
  company_id    INTEGER NOT NULL DEFAULT 1,
  owner_id      INTEGER NOT NULL,
  partner_id    INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'Requested',
  split_pct     {RATE} NOT NULL DEFAULT 0.30,
  reason        TEXT NOT NULL DEFAULT '',
  response_note TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  responded_at  TEXT NOT NULL DEFAULT '',
  ended_at      TEXT NOT NULL DEFAULT ''
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
  first_rate    {RATE},
  recurring_rate {RATE},
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
  fx_rate     {RATE} NOT NULL DEFAULT 1,
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
  rate       {RATE} NOT NULL DEFAULT 1,
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
CREATE INDEX IF NOT EXISTS idx_collab_client   ON collaborations(client_id);
CREATE INDEX IF NOT EXISTS idx_collab_partner  ON collaborations(partner_id);
CREATE INDEX IF NOT EXISTS idx_collab_owner    ON collaborations(owner_id);
"""


def fill_pg(sql):
    """Postgres flavour of the schema. The mirror targets Postgres whatever the
    primary is, so it shares this one function rather than keeping its own copy
    of the substitutions — a second copy is how they drift apart."""
    return (sql.replace("{PK}", "SERIAL PRIMARY KEY")
               .replace("{MONEY}", "NUMERIC(18,2)")
               .replace("{RATE}", "NUMERIC(18,6)"))


def fill_sqlite(sql):
    return (sql.replace("{PK}", "INTEGER PRIMARY KEY AUTOINCREMENT")
               .replace("{MONEY}", "REAL").replace("{RATE}", "REAL"))


def _fill(sql):
    """{MONEY} holds amounts (2 dp is right for cash). {RATE} holds rates and
    exchange rates, which need far more precision."""
    return fill_pg(sql) if IS_PG else fill_sqlite(sql)


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
                pass
        with _lock:
            if _pool is None:
                url = DATABASE_URL
                # Neon and most managed providers require TLS
                if "sslmode=" not in url and "localhost" not in url and "127.0.0.1" not in url:
                    url += ("&" if "?" in url else "?") + "sslmode=require"
                # Serverless Postgres suspends when idle and drops sockets.
                # Keepalives make the client notice quickly; the timeout stops
                # a cold start from hanging a web request indefinitely.
                try:
                    _pool = ThreadedConnectionPool(
                        1, POOL_MAX, url,
                        connect_timeout=CONNECT_TIMEOUT,
                        keepalives=1, keepalives_idle=30,
                        keepalives_interval=10, keepalives_count=5,
                        application_name="statspack-agent-portal")
                except Exception as e:
                    raise ConnectionLost(f"Cannot reach the database: {e}")


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
            if _breaker_open():
                raise ConnectionLost("Database is unreachable (checked moments ago).")
            try:
                init_pool()
            except ConnectionLost:
                _trip_breaker()
                raise
            except Exception as e:
                _trip_breaker()
                raise ConnectionLost(str(e))
            # Wait for a free connection rather than failing the request.
            if not _slots.acquire(timeout=POOL_WAIT_SECONDS):
                raise ConnectionLost("All database connections are busy.")
            self.slot = True
            self.raw = None
            stale = []
            try:
                for _ in range(4):
                    try:
                        candidate = _pool.getconn()
                    except Exception as e:
                        _trip_breaker()
                        raise ConnectionLost(f"Cannot reach the database: {e}")
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
                _trip_breaker()
                raise ConnectionLost("Could not obtain a live database connection.")
            _reset_breaker()
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
RATE_T = "NUMERIC(18,6)" if IS_PG else "REAL"

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
    ("users", "first_rate", RATE_T),
    ("users", "recurring_rate", RATE_T),
    ("users", "window_months", "INTEGER"),
    ("clients", "industry", "TEXT NOT NULL DEFAULT ''"),
    ("clients", "company_id", "INTEGER NOT NULL DEFAULT 1"),
    ("audit", "company_id", "INTEGER NOT NULL DEFAULT 0"),
    ("audit", "target", "TEXT NOT NULL DEFAULT ''"),
    ("audit", "meta", "TEXT NOT NULL DEFAULT ''"),
]


# Columns created before v1.3.1 were NUMERIC(18,2) and silently rounded rates.
# Widening is safe and loses nothing; values already rounded stay rounded until
# re-entered or re-synced.
TYPE_FIXES = [
    ("fx", "rate"), ("payments", "fx_rate"),
    ("users", "first_rate"), ("users", "recurring_rate"),
    ("companies", "first_rate"), ("companies", "recurring_rate"),
    ("collaborations", "split_pct"),
]


def widen_rate_columns(conn):
    if not IS_PG:
        return []                      # SQLite REAL is already full precision
    fixed = []
    for table, column in TYPE_FIXES:
        try:
            row = conn.one(
                "SELECT numeric_scale FROM information_schema.columns "
                "WHERE table_name = ? AND column_name = ?", (table, column))
            if row and row.get("numeric_scale") is not None and int(row["numeric_scale"]) < 6:
                conn.execute(f"ALTER TABLE {table} ALTER COLUMN {column} "
                             f"TYPE NUMERIC(18,6)")
                fixed.append(f"{table}.{column}")
        except Exception as e:
            print(f"  precision fix skipped for {table}.{column}: {e}")
    return fixed


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
        widened = widen_rate_columns(conn)
        if widened:
            print("  rate precision widened on:", ", ".join(widened))
    run(_index_sql())
    return "postgres" if IS_PG else "sqlite"


# --------------------------------------------------------------------------
# KEEP-ALIVE — stop the database going cold during working hours
# --------------------------------------------------------------------------
# Neon's free plan suspends the compute after 5 minutes idle and that setting
# cannot be changed on the free plan. But the same plan caps compute at 100
# CU-hours per month (~400 hours at the default 0.25 CU), and a month is 730
# hours. Pinging around the clock would exhaust the allowance and Neon would
# suspend the project FOR THE REST OF THE MONTH — far worse than a wake-up
# delay of a few hundred milliseconds.
#
# So the ping runs only in working hours on working days: about 12 hours x 22
# days = 264 hours a month, which sits comfortably inside the allowance.
# --------------------------------------------------------------------------
KEEPALIVE_ON = os.environ.get("KEEPALIVE", "1") not in ("0", "false", "False", "off", "no")
KEEPALIVE_START = int(float(os.environ.get("KEEPALIVE_START_HOUR", "6")))
KEEPALIVE_END = int(float(os.environ.get("KEEPALIVE_END_HOUR", "19")))
KEEPALIVE_DAYS = os.environ.get("KEEPALIVE_DAYS", "0,1,2,3,4")      # Mon-Fri
KEEPALIVE_TZ = float(os.environ.get("KEEPALIVE_TZ_OFFSET", "2"))    # Lesotho = UTC+2
KEEPALIVE_EVERY = int(float(os.environ.get("KEEPALIVE_MINUTES", "4")))
CU_SIZE = float(os.environ.get("NEON_CU_SIZE", "0.25"))
CU_QUOTA = float(os.environ.get("NEON_CU_QUOTA", "100"))

_keepalive_thread = None
_ka = {"pings": 0, "last_ping": "", "last_error": ""}


def _local_now():
    from datetime import datetime as _dt, timedelta as _td
    return _dt.utcnow() + _td(hours=KEEPALIVE_TZ)


def _ka_days():
    return {int(d) for d in KEEPALIVE_DAYS.split(",") if d.strip().isdigit()}


def in_keepalive_window():
    now = _local_now()
    return now.weekday() in _ka_days() and KEEPALIVE_START <= now.hour < KEEPALIVE_END


def keepalive_state():
    hours_per_day = max(0, KEEPALIVE_END - KEEPALIVE_START)
    monthly_hours = hours_per_day * len(_ka_days()) * 4.345
    cu = monthly_hours * CU_SIZE
    return {
        "enabled": bool(KEEPALIVE_ON and IS_PG),
        "window": f"{KEEPALIVE_START:02d}:00-{KEEPALIVE_END:02d}:00",
        "days": KEEPALIVE_DAYS, "tz_offset": KEEPALIVE_TZ,
        "every_minutes": KEEPALIVE_EVERY,
        "awake_now": in_keepalive_window(),
        "hours_per_month": round(monthly_hours, 1),
        "cu_hours_per_month": round(cu, 1),
        "quota_cu_hours": CU_QUOTA,
        "quota_pct": round(cu / CU_QUOTA * 100, 1) if CU_QUOTA else None,
        "within_quota": cu <= CU_QUOTA * 0.85,
        "pings": _ka["pings"], "last_ping": _ka["last_ping"],
        "last_error": _ka["last_error"],
    }


def start_keepalive():
    global _keepalive_thread
    if _keepalive_thread or not KEEPALIVE_ON or not IS_PG:
        return

    def loop():
        from datetime import datetime as _dt
        time.sleep(30)
        while True:
            try:
                if in_keepalive_window():
                    with connection() as conn:
                        conn.one("SELECT 1 AS ok")
                    _ka["pings"] += 1
                    _ka["last_ping"] = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    _ka["last_error"] = ""
            except Exception as e:
                _ka["last_error"] = str(e)[:200]
            time.sleep(max(60, KEEPALIVE_EVERY * 60))

    _keepalive_thread = threading.Thread(target=loop, daemon=True, name="db-keepalive")
    _keepalive_thread.start()


def reachable():
    """Cheap liveness probe used by /api/health. Never raises."""
    try:
        with connection() as conn:
            conn.one("SELECT 1 AS ok")
        return True, ""
    except Exception as e:
        return False, str(e)[:200]


def engine_name():
    return "postgres" if IS_PG else f"sqlite ({SQLITE_PATH})"


ALL_TABLES = ("companies", "users", "clients", "client_events", "payments", "payouts",
              "reviews", "collaborations", "fx", "settings", "audit", "incidents")


def storage_stats(conn=None):
    """How much of the free-tier allowance is used.

    Neon and Supabase both cap free projects at roughly 500 MB, so the quota is
    configurable rather than hard-coded.
    """
    quota_mb = float(os.environ.get("DB_QUOTA_MB", "500"))
    out = {"engine": "postgres" if IS_PG else "sqlite", "quota_mb": quota_mb,
           "used_mb": None, "free_mb": None, "pct": None, "tables": [], "error": None}
    try:
        if conn is None:
            with connection() as c:
                return _stats_with(c, out)
        return _stats_with(conn, out)
    except Exception as e:
        out["error"] = str(e)[:200]
        return out


def _stats_with(conn, out):
    if IS_PG:
        row = conn.one("SELECT pg_database_size(current_database()) AS b")
        used = float(row["b"] or 0) / (1024 * 1024)
        rows = conn.query(
            "SELECT relname AS name, pg_total_relation_size(c.oid) AS bytes "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 15")
        out["tables"] = [{"name": r["name"], "mb": round(float(r["bytes"] or 0) / 1048576, 3)}
                         for r in rows]
    else:
        used = 0.0
        for suffix in ("", "-wal", "-shm"):
            path = SQLITE_PATH + suffix
            if os.path.exists(path):
                used += os.path.getsize(path)
        used /= (1024 * 1024)
        out["tables"] = [{"name": t, "rows": int((conn.one(
            f"SELECT COUNT(*) AS n FROM {t}") or {}).get("n") or 0)} for t in ALL_TABLES]

    out["used_mb"] = round(used, 3)
    out["free_mb"] = round(max(0.0, out["quota_mb"] - used), 3)
    out["pct"] = round(min(100.0, used / out["quota_mb"] * 100), 2) if out["quota_mb"] else None
    for t in ALL_TABLES:
        try:
            n = conn.one(f"SELECT COUNT(*) AS n FROM {t}")
            for entry in out["tables"]:
                if entry["name"] == t:
                    entry["rows"] = int(n["n"] or 0)
        except Exception:
            pass
    return out
