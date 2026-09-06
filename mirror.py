"""
One-way mirror: copies the live database to a standby on a schedule.

WHY IT IS ONE-WAY
-----------------
A commission ledger must have exactly one source of truth. If both databases
accepted writes and drifted apart, you would end up with two contradictory
answers to "what is this agent owed", and no safe way to reconcile them. So the
primary (Neon) takes every read and write, and the standby (Supabase) receives a
full replica on a timer.

That makes this a warm backup and a disaster-recovery position, NOT automatic
failover. If the primary dies, the portal is down until you repoint
DATABASE_URL at the standby — a deliberate, human decision, taken knowing you
may lose whatever was written since the last mirror run.

ISOLATION
---------
The mirror runs on its own connection in a background thread. Every failure is
caught, recorded as an incident, and never reaches a user request. A broken
standby cannot take the live portal down.
"""
import os
import threading
import time
from datetime import datetime

import db

try:
    import psycopg2
    import psycopg2.extras
except ImportError:                       # only needed when a standby is configured
    psycopg2 = None

MIRROR_URL = os.environ.get("MIRROR_DATABASE_URL", "").strip()


def _identity(url):
    """host + database name, ignoring credentials and query options."""
    try:
        tail = url.split("@", 1)[1]
        host = tail.split("/")[0].split("?")[0].lower()
        name = tail.split("/", 1)[1].split("?")[0].lower() if "/" in tail else ""
        return host, name
    except (IndexError, AttributeError):
        return None, None


def _same_as_primary():
    """After a failover it is easy to leave both variables pointing at the same
    database. The mirror would then copy it onto itself — wiping the very backup
    you just relied on. Refuse instead."""
    primary = os.environ.get("DATABASE_URL", "").strip()
    if not primary or not MIRROR_URL:
        return False
    return _identity(primary) == _identity(MIRROR_URL) and _identity(MIRROR_URL) != (None, None)


SELF_MIRROR = _same_as_primary()
MIRROR_ENABLED = bool(MIRROR_URL) and not SELF_MIRROR
MIRROR_INTERVAL_MIN = int(float(os.environ.get("MIRROR_INTERVAL_MINUTES", "60")))

# Copied in dependency order so a partial run never leaves dangling references.
TABLES = ["companies", "users", "clients", "client_events", "payments", "payouts",
          "reviews", "collaborations", "fx", "settings", "audit", "incidents"]

_state = {
    "enabled": MIRROR_ENABLED,
    "self_mirror": SELF_MIRROR,
    "target": "Supabase" if "supabase" in MIRROR_URL.lower() else ("standby" if MIRROR_URL else ""),
    "last_run": "", "last_success": "", "last_error": "",
    "rows": 0, "duration_s": None, "running": False, "runs": 0, "failures": 0,
}
_lock = threading.Lock()
_thread = None


def state():
    with _lock:
        s = dict(_state)
    s["interval_minutes"] = MIRROR_INTERVAL_MIN
    s["host"] = _safe_host()
    return s


def _safe_host():
    """Never expose the password when showing where the mirror points."""
    if not MIRROR_URL:
        return ""
    try:
        tail = MIRROR_URL.split("@", 1)[1]
        return tail.split("/")[0]
    except (IndexError, AttributeError):
        return "configured"


def _connect_mirror():
    url = MIRROR_URL
    if "sslmode=" not in url and "localhost" not in url and "127.0.0.1" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return psycopg2.connect(url, connect_timeout=15, application_name="statspack-mirror")


def _mirror_schema_sql():
    """The standby is always Postgres, so it uses db.fill_pg — the same
    substitution the primary uses. Keeping a private copy here is what let the
    schemas drift when a new column type was introduced."""
    return db.fill_pg(db.SCHEMA), db.fill_pg(db.INDEXES)


def _counts(cur, tables=("users", "clients", "payments")):
    out = {}
    for t in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            out[t] = int(cur.fetchone()[0] or 0)
        except Exception:
            out[t] = 0
    return out


def _wrong_direction(target_cur):
    """Returns a reason string when copying would clearly lose real records."""
    target = _counts(target_cur)
    try:
        with db.connection() as src:
            source = {t: int((src.one(f"SELECT COUNT(*) AS n FROM {t}") or {}).get("n") or 0)
                      for t in ("users", "clients", "payments")}
    except Exception:
        return None                       # primary unreadable; the copy fails anyway

    # A live portal always has at least one admin. Zero users means this is not
    # the real primary — almost certainly the two URLs are the wrong way round.
    if source["users"] == 0 and target["users"] > 0:
        return (f"The primary has no user accounts but the standby has "
                f"{target['users']}. That usually means DATABASE_URL and "
                f"MIRROR_DATABASE_URL are the wrong way round. Nothing was copied.")

    if target["payments"] > source["payments"]:
        return (f"The standby holds more payment records than the primary "
                f"({target['payments']} vs {source['payments']}). Copying would "
                f"erase {target['payments'] - source['payments']} of them. Nothing was "
                f"copied — check which database is live, then use Force copy if the "
                f"primary really is correct.")
    return None


def run_once(reason="scheduled", force=False):
    """Copy every table. Returns a report; never raises."""
    if SELF_MIRROR:
        msg = ("MIRROR_DATABASE_URL points at the same database as DATABASE_URL. "
               "Copying would erase your only copy, so no run was attempted. "
               "Point the mirror at the other database.")
        try:
            with db.connection() as c:
                record_incident(c, "mirror.self", "error",
                                "Standby is pointed at the live database", msg)
        except Exception:
            pass
        return {"ok": False, "error": msg}
    if not MIRROR_ENABLED:
        return {"ok": False, "error": "No MIRROR_DATABASE_URL set."}

    with _lock:
        if _state["running"]:
            return {"ok": False, "error": "A mirror run is already in progress."}
        _state["running"] = True

    started = time.time()
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    total = 0
    conn = None
    try:
        conn = _connect_mirror()
        conn.autocommit = False
        cur = conn.cursor()

        schema, indexes = _mirror_schema_sql()
        cur.execute(schema)

        # Guard against the two variables being set the wrong way round. The
        # copy wipes the target first, so pointing an empty database at a
        # populated one and running it backwards would destroy real records.
        if not force:
            problem = _wrong_direction(cur)
            if problem:
                conn.rollback()
                try:
                    with db.connection() as c:
                        record_incident(c, "mirror.direction", "error",
                                        "Mirror refused: it would destroy data", problem)
                except Exception:
                    pass
                raise RuntimeError(problem)

        # Snapshot inside one transaction: the standby is either fully the old
        # copy or fully the new one, never a half-written mixture.
        for table in TABLES:
            try:
                with db.connection() as src:
                    rows = src.query(f"SELECT * FROM {table}")
            except Exception as e:
                raise RuntimeError(f"could not read {table} from the primary: {e}")

            cur.execute(f"DELETE FROM {table}")
            if not rows:
                continue
            cols = list(rows[0].keys())
            collist = ",".join(f'"{c}"' for c in cols)
            data = [tuple(r.get(c) for c in cols) for r in rows]
            # execute_values batches many rows into one statement. psycopg2's
            # executemany sends a separate round trip per row, which is fine on
            # a local socket but adds ~10ms each over the internet — minutes
            # instead of seconds once there are thousands of payments.
            psycopg2.extras.execute_values(
                cur, f'INSERT INTO {table} ({collist}) VALUES %s', data, page_size=500)
            total += len(rows)

        # Keep sequences ahead of the copied ids, so the standby can accept
        # writes immediately if you ever have to promote it.
        #
        # Only tables with a serial `id` have a sequence — fx and settings key
        # on a code instead. Each attempt sits inside a SAVEPOINT: a plain
        # rollback here would discard the schema and every row copied above.
        for table in TABLES:
            cur.execute("SAVEPOINT seq_fix")
            try:
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)")
                cur.execute("RELEASE SAVEPOINT seq_fix")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT seq_fix")
                cur.execute("RELEASE SAVEPOINT seq_fix")

        cur.execute(indexes)
        conn.commit()
        cur.close()

        duration = round(time.time() - started, 2)
        with _lock:
            _state.update({"last_run": stamp, "last_success": stamp, "last_error": "",
                           "rows": total, "duration_s": duration, "runs": _state["runs"] + 1})
        try:
            with db.connection() as c:
                resolve_incident(c, "mirror")
        except Exception:
            pass
        return {"ok": True, "rows": total, "duration_s": duration, "at": stamp,
                "reason": reason}

    except Exception as e:
        msg = str(e)[:400]
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        with _lock:
            _state.update({"last_run": stamp, "last_error": msg,
                           "runs": _state["runs"] + 1, "failures": _state["failures"] + 1})
        try:
            with db.connection() as c:
                record_incident(c, "mirror", "error", "Standby database mirror failed", msg)
        except Exception:
            pass
        return {"ok": False, "error": msg, "at": stamp}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        with _lock:
            _state["running"] = False


def check_mirror_health():
    """Can the standby be reached at all, and how big is it?"""
    if SELF_MIRROR:
        return {"configured": True, "self_mirror": True, "reachable": False,
                "host": _safe_host(),
                "error": "This is the same database as the primary."}
    if not MIRROR_ENABLED:
        return {"configured": False}
    out = {"configured": True, "host": _safe_host(), "reachable": False,
           "used_mb": None, "quota_mb": float(os.environ.get("MIRROR_QUOTA_MB", "500")),
           "free_mb": None, "pct": None, "rows": {}, "error": None}
    conn = None
    try:
        conn = _connect_mirror()
        cur = conn.cursor()
        cur.execute("SELECT pg_database_size(current_database())")
        used = float(cur.fetchone()[0] or 0) / 1048576
        out.update({"reachable": True, "used_mb": round(used, 3),
                    "free_mb": round(max(0.0, out["quota_mb"] - used), 3),
                    "pct": round(min(100.0, used / out["quota_mb"] * 100), 2)})
        for table in ("users", "clients", "payments"):
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                out["rows"][table] = int(cur.fetchone()[0] or 0)
            except Exception:
                out["rows"][table] = None
        cur.close()
    except Exception as e:
        out["error"] = str(e)[:300]
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return out


# --------------------------------------------------------------------------
# incidents — what the super user sees when something has gone wrong
# --------------------------------------------------------------------------
def record_incident(conn, kind, severity, title, detail=""):
    """Repeated failures collapse into one row with a counter, so a standby
    that has been down for a week is one alert, not a thousand."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    existing = conn.one(
        "SELECT * FROM incidents WHERE kind = ? AND resolved = 0 ORDER BY id DESC", (kind,))
    if existing:
        conn.execute(
            "UPDATE incidents SET last_seen = ?, occurrences = ?, detail = ?, "
            "severity = ?, title = ? WHERE id = ?",
            (now, int(existing["occurrences"] or 1) + 1, detail[:1000], severity,
             title, existing["id"]))
        return existing["id"]
    return conn.insert(
        "INSERT INTO incidents (kind, severity, title, detail, resolved, resolved_at, "
        "first_seen, last_seen, occurrences) VALUES (?,?,?,?,?,?,?,?,?)",
        (kind, severity, title, detail[:1000], 0, "", now, now, 1))


def resolve_incident(conn, kind):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE incidents SET resolved = 1, resolved_at = ? "
                 "WHERE kind = ? AND resolved = 0", (now, kind))


def open_incidents(conn):
    return conn.query("SELECT * FROM incidents WHERE resolved = 0 ORDER BY id DESC LIMIT 50")


def all_incidents(conn, limit=100):
    return conn.query(f"SELECT * FROM incidents ORDER BY id DESC LIMIT {int(limit)}")


# --------------------------------------------------------------------------
def start_worker():
    global _thread
    if _thread or not MIRROR_ENABLED:
        return
    def loop():
        time.sleep(45)                       # let the server settle first
        while True:
            report = run_once("scheduled")
            print("  mirror:", "copied %s rows in %ss" % (report.get("rows"), report.get("duration_s"))
                  if report.get("ok") else "FAILED — %s" % report.get("error"))
            time.sleep(max(300, MIRROR_INTERVAL_MIN * 60))
    _thread = threading.Thread(target=loop, daemon=True, name="db-mirror")
    _thread.start()
