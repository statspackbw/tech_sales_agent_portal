"""
Core domain logic: authentication, settings, FX and the commission engine.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import string
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, date

import db

PBKDF2_ROUNDS = 240_000
SESSION_DAYS = 7

# ---- commission defaults (all editable by an admin in Settings) -----------
DEFAULT_SETTINGS = {
    "company_name": "StatsPack",
    "portal_name": "StatsPack Tech Sales Agent Portal",
    "base_currency": "USD",
    # First client payment: agent takes 60%, StatsPack retains 40%. Once only.
    "commission_first_rate": "0.60",
    # Monthly payments inside year one: agent takes 10%, StatsPack retains 90%.
    "commission_recurring_rate": "0.10",
    # Window measured from the date of the client's first payment.
    "commission_window_months": "12",
    "support_email": "support@statspack.co.ls",
    # Exchange rates: automatic sync from ExchangeRate-API when a key is set.
    "fx_auto": "1",
    "fx_interval_hours": "12",
    "fx_last_sync": "",
    "fx_last_status": "never run",
}

# Every code ExchangeRate-API publishes, so any territory can be traded in.
# Seed rates are rough placeholders; a sync replaces them with live figures.
WORLD_FX = [
    ("AED",3.67,"UAE Dirham"),("AFN",68.0,"Afghan Afghani"),("ALL",89.0,"Albanian Lek"),
    ("AMD",385.0,"Armenian Dram"),("ANG",1.79,"Netherlands Antillian Guilder"),
    ("ARS",1010.0,"Argentine Peso"),("AUD",1.52,"Australian Dollar"),("AWG",1.79,"Aruban Florin"),
    ("AZN",1.70,"Azerbaijani Manat"),("BAM",1.80,"Bosnia and Herzegovina Mark"),
    ("BBD",2.0,"Barbados Dollar"),("BDT",120.0,"Bangladeshi Taka"),("BGN",1.80,"Bulgarian Lev"),
    ("BHD",0.376,"Bahraini Dinar"),("BIF",2900.0,"Burundian Franc"),("BMD",1.0,"Bermudian Dollar"),
    ("BND",1.34,"Brunei Dollar"),("BOB",6.91,"Bolivian Boliviano"),("BRL",5.75,"Brazilian Real"),
    ("BSD",1.0,"Bahamian Dollar"),("BTN",84.0,"Bhutanese Ngultrum"),("BYN",3.27,"Belarusian Ruble"),
    ("BZD",2.01,"Belize Dollar"),("CAD",1.39,"Canadian Dollar"),("CHF",0.88,"Swiss Franc"),
    ("CLP",960.0,"Chilean Peso"),("CNY",7.15,"Chinese Renminbi"),("COP",4300.0,"Colombian Peso"),
    ("CRC",515.0,"Costa Rican Colon"),("CUP",24.0,"Cuban Peso"),("CVE",101.0,"Cape Verdean Escudo"),
    ("CZK",23.2,"Czech Koruna"),("DJF",178.0,"Djiboutian Franc"),("DKK",6.87,"Danish Krone"),
    ("DOP",60.5,"Dominican Peso"),("DZD",133.0,"Algerian Dinar"),("EGP",49.0,"Egyptian Pound"),
    ("ERN",15.0,"Eritrean Nakfa"),("ETB",122.0,"Ethiopian Birr"),("EUR",0.92,"Euro"),
    ("FJD",2.26,"Fiji Dollar"),("FKP",0.78,"Falkland Islands Pound"),("FOK",6.87,"Faroese Krona"),
    ("GBP",0.78,"Pound Sterling"),("GEL",2.72,"Georgian Lari"),("GGP",0.78,"Guernsey Pound"),
    ("GHS",15.5,"Ghanaian Cedi"),("GIP",0.78,"Gibraltar Pound"),("GMD",70.0,"Gambian Dalasi"),
    ("GNF",8600.0,"Guinean Franc"),("GTQ",7.73,"Guatemalan Quetzal"),("GYD",209.0,"Guyanese Dollar"),
    ("HKD",7.78,"Hong Kong Dollar"),("HNL",25.3,"Honduran Lempira"),("HRK",6.95,"Croatian Kuna"),
    ("HTG",131.0,"Haitian Gourde"),("HUF",380.0,"Hungarian Forint"),("IDR",15800.0,"Indonesian Rupiah"),
    ("ILS",3.65,"Israeli New Shekel"),("IMP",0.78,"Manx Pound"),("INR",84.0,"Indian Rupee"),
    ("IQD",1310.0,"Iraqi Dinar"),("ISK",138.0,"Icelandic Krona"),("JEP",0.78,"Jersey Pound"),
    ("JMD",157.0,"Jamaican Dollar"),("JOD",0.709,"Jordanian Dinar"),("JPY",152.0,"Japanese Yen"),
    ("KES",129.0,"Kenyan Shilling"),("KGS",86.0,"Kyrgyzstani Som"),("KHR",4050.0,"Cambodian Riel"),
    ("KRW",1380.0,"South Korean Won"),("KWD",0.307,"Kuwaiti Dinar"),("KYD",0.83,"Cayman Islands Dollar"),
    ("KZT",495.0,"Kazakhstani Tenge"),("LAK",21800.0,"Lao Kip"),("LBP",89000.0,"Lebanese Pound"),
    ("LKR",293.0,"Sri Lanka Rupee"),("LRD",190.0,"Liberian Dollar"),("LYD",4.85,"Libyan Dinar"),
    ("MAD",9.95,"Moroccan Dirham"),("MDL",17.9,"Moldovan Leu"),("MKD",56.8,"Macedonian Denar"),
    ("MMK",2100.0,"Burmese Kyat"),("MNT",3400.0,"Mongolian Togrog"),("MOP",8.0,"Macanese Pataca"),
    ("MRU",39.7,"Mauritanian Ouguiya"),("MVR",15.4,"Maldivian Rufiyaa"),("MXN",20.2,"Mexican Peso"),
    ("MYR",4.45,"Malaysian Ringgit"),("NGN",1650.0,"Nigerian Naira"),("NIO",36.8,"Nicaraguan Cordoba"),
    ("NOK",11.0,"Norwegian Krone"),("NPR",134.0,"Nepalese Rupee"),("NZD",1.68,"New Zealand Dollar"),
    ("OMR",0.384,"Omani Rial"),("PAB",1.0,"Panamanian Balboa"),("PEN",3.78,"Peruvian Sol"),
    ("PGK",3.95,"Papua New Guinean Kina"),("PHP",58.5,"Philippine Peso"),("PKR",278.0,"Pakistani Rupee"),
    ("PLN",4.0,"Polish Zloty"),("PYG",7800.0,"Paraguayan Guarani"),("QAR",3.64,"Qatari Riyal"),
    ("RON",4.58,"Romanian Leu"),("RSD",108.0,"Serbian Dinar"),("RUB",97.0,"Russian Ruble"),
    ("RWF",1360.0,"Rwandan Franc"),("SAR",3.75,"Saudi Riyal"),("SBD",8.4,"Solomon Islands Dollar"),
    ("SDG",601.0,"Sudanese Pound"),("SEK",10.6,"Swedish Krona"),("SGD",1.34,"Singapore Dollar"),
    ("SHP",0.78,"Saint Helena Pound"),("SLE",22.5,"Sierra Leonean Leone"),("SOS",571.0,"Somali Shilling"),
    ("SRD",35.0,"Surinamese Dollar"),("SSP",4100.0,"South Sudanese Pound"),
    ("STN",22.8,"Sao Tome and Principe Dobra"),("SYP",13000.0,"Syrian Pound"),
    ("THB",34.5,"Thai Baht"),("TJS",10.7,"Tajikistani Somoni"),("TMT",3.5,"Turkmenistan Manat"),
    ("TND",3.15,"Tunisian Dinar"),("TOP",2.38,"Tongan Paanga"),("TRY",34.5,"Turkish Lira"),
    ("TTD",6.78,"Trinidad and Tobago Dollar"),("TVD",1.52,"Tuvaluan Dollar"),
    ("TWD",32.4,"New Taiwan Dollar"),("UAH",41.4,"Ukrainian Hryvnia"),("UGX",3680.0,"Ugandan Shilling"),
    ("UYU",42.5,"Uruguayan Peso"),("UZS",12800.0,"Uzbekistani Som"),
    ("VES",47.0,"Venezuelan Bolivar Soberano"),("VND",25400.0,"Vietnamese Dong"),
    ("VUV",119.0,"Vanuatu Vatu"),("WST",2.75,"Samoan Tala"),("XAF",605.0,"Central African CFA Franc"),
    ("XCD",2.70,"East Caribbean Dollar"),("XDR",0.75,"Special Drawing Rights"),
    ("XOF",605.0,"West African CFA Franc"),("XPF",110.0,"CFP Franc"),("YER",250.0,"Yemeni Rial"),
    ("ZWL",26.0,"Zimbabwean Dollar"),
]

# SADC first — these appear at the top of pickers because they are used daily.
DEFAULT_FX = [
    ("USD", 1.0, "US Dollar (base)"),
    ("LSL", 18.0, "Lesotho Loti"),
    ("ZAR", 18.0, "South African Rand"),
    ("BWP", 13.5, "Botswana Pula"),
    ("NAD", 18.0, "Namibian Dollar"),
    ("SZL", 18.0, "Eswatini Lilangeni"),
    ("ZMW", 26.0, "Zambian Kwacha"),
    ("MWK", 1735.0, "Malawian Kwacha"),
    ("TZS", 2600.0, "Tanzanian Shilling"),
    ("MZN", 64.0, "Mozambican Metical"),
    ("MUR", 46.0, "Mauritian Rupee"),
    ("ZWG", 26.0, "Zimbabwe Gold"),
    ("AOA", 900.0, "Angolan Kwanza"),
    ("CDF", 2800.0, "Congolese Franc"),
    ("MGA", 4500.0, "Malagasy Ariary"),
    ("SCR", 14.5, "Seychellois Rupee"),
    ("KMF", 455.0, "Comorian Franc"),
]

AGENT_TYPES = [
    "Tech Sales Agent", "Enterprise Sales", "Channel Partner", "Reseller",
    "Referral Partner", "Field Agent", "Inside Sales", "Distributor",
]

INDUSTRIES = [
    "Agriculture", "Automotive", "Banking & Finance", "Construction", "Education",
    "Energy & Utilities", "Government & Public Sector", "Healthcare", "Hospitality & Tourism",
    "Insurance", "Legal", "Logistics & Transport", "Manufacturing", "Media & Marketing",
    "Mining & Resources", "Non-profit & NGO", "Professional Services", "Real Estate",
    "Retail & Wholesale", "Security", "Technology & Software", "Telecommunications", "Other",
]

EVENT_KINDS = ["note", "stage", "call", "meeting", "demo", "proposal", "email", "site visit"]

STAGES = ["Prospect", "Qualified", "Demo", "Proposal", "Won", "Lost", "Churned"]


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------
def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return date.today().strftime("%Y-%m-%d")


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def add_months(d, months):
    """Date `months` calendar months after d, clamping short months."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    # clamp: 31 Jan + 1 month -> 28/29 Feb
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


# --------------------------------------------------------------------------
# passwords & sessions
# --------------------------------------------------------------------------
def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt}${dk.hex()}"


def verify_password(password, stored):
    try:
        scheme, rounds, salt, digest = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def password_problem(pw):
    """Returns an error string, or None if acceptable."""
    if len(pw) < 8:
        return "Password must be at least 8 characters."
    if pw.lower() in ("password", "12345678", "statspack", "admin123", "agent123"):
        return "That password is too common. Choose another."
    if pw.isdigit():
        return "Password cannot be only numbers."
    return None


def temp_password(n=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def create_session(conn, user_id, ip=""):
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at, ip) VALUES (?,?,?,?,?)",
        (token, user_id, now_iso(), expires, ip),
    )
    return token


def user_for_token(conn, token):
    if not token:
        return None
    row = conn.one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ?",
        (token, now_iso()),
    )
    if row and row.get("status") != "Active":
        return None
    return row


def destroy_session(conn, token):
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def purge_sessions(conn, user_id=None):
    if user_id:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    else:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(e):
    return bool(EMAIL_RE.match((e or "").strip()))


# --------------------------------------------------------------------------
# settings & FX
# --------------------------------------------------------------------------
def get_settings(conn):
    rows = conn.query("SELECT skey, svalue FROM settings")
    out = dict(DEFAULT_SETTINGS)
    for r in rows:
        out[r["skey"]] = r["svalue"]
    return out


SETTING_TO_COMPANY = {
    "commission_first_rate": "first_rate",
    "commission_recurring_rate": "recurring_rate",
    "commission_window_months": "window_months",
}


def set_setting(conn, key, value):
    existing = conn.one("SELECT skey FROM settings WHERE skey = ?", (key,))
    if existing:
        conn.execute("UPDATE settings SET svalue = ? WHERE skey = ?", (str(value), key))
    else:
        conn.execute("INSERT INTO settings (skey, svalue) VALUES (?,?)", (key, str(value)))

    # Commission rules live on the company row; Settings is the host company's
    # view of them. Writing one without the other would let them drift, and an
    # admin would change the rate and see nothing happen.
    column = SETTING_TO_COMPANY.get(key)
    if column:
        try:
            v = int(float(value)) if column == "window_months" else float(value)
            conn.execute(f"UPDATE companies SET {column} = ? WHERE is_host = 1", (v,))
        except (TypeError, ValueError):
            pass


def get_fx(conn):
    rows = conn.query("SELECT * FROM fx ORDER BY code")
    return {r["code"]: float(r["rate"]) for r in rows}, rows


def fx_rate(conn, code):
    row = conn.one("SELECT rate FROM fx WHERE code = ?", (code or "USD",))
    return float(row["rate"]) if row and float(row["rate"]) > 0 else 1.0


def to_usd(amount, rate):
    try:
        rate = float(rate)
        if rate <= 0:
            rate = 1.0
        return round(float(amount) / rate, 2)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
# EXCHANGE RATE SYNC  (ExchangeRate-API — https://www.exchangerate-api.com)
# --------------------------------------------------------------------------
# The free plan needs a key and refreshes once a day, which is ample: we ask
# for USD as the base and store "units per 1 USD", matching the table exactly.
#
# Rates are LOCKED INTO each payment when it is recorded, so a sync only ever
# affects future payments. Historic commission cannot move underneath you.
# --------------------------------------------------------------------------
FX_ENDPOINT = "https://v6.exchangerate-api.com/v6/{key}/latest/USD"
FX_TIMEOUT = 20


def fx_api_key():
    return os.environ.get("EXCHANGERATE_API_KEY", "").strip()


def fetch_live_rates():
    """Return (rates_dict, error_string). Never raises."""
    key = fx_api_key()
    if not key:
        return None, "No EXCHANGERATE_API_KEY set — add it in Render, then redeploy."
    try:
        req = urllib.request.Request(
            FX_ENDPOINT.format(key=key),
            headers={"User-Agent": "StatsPack-Agent-Portal/1.1"})
        with urllib.request.urlopen(req, timeout=FX_TIMEOUT) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = {401: "the API key was rejected",
                  403: "the plan does not allow this request",
                  429: "the monthly request quota is used up"}.get(
                      e.code, f"HTTP {e.code}")
        return None, f"Provider refused the request ({detail})."
    except urllib.error.URLError as e:
        return None, f"Could not reach the provider ({e.reason})."
    except (ValueError, TimeoutError) as e:
        return None, f"Unreadable response from the provider ({e})."

    if payload.get("result") != "success":
        return None, f"Provider returned an error: {payload.get('error-type', 'unknown')}."
    rates = payload.get("conversion_rates") or {}
    if not rates:
        return None, "Provider returned no rates."
    return rates, None


def sync_fx(conn, actor=None, ip=""):
    """Update every currency the provider recognises. Leaves the rest alone.

    Returns a report so an admin can see exactly what moved and what did not,
    rather than trusting a silent green tick.
    """
    rates, err = fetch_live_rates()
    if err:
        set_setting(conn, "fx_last_status", err)
        return {"ok": False, "error": err, "updated": [], "unmatched": []}

    rows = conn.query("SELECT * FROM fx ORDER BY code")
    updated, unmatched = [], []
    for row in rows:
        code = row["code"]
        if code == "USD":
            continue
        live = rates.get(code)
        if live is None or float(live) <= 0:
            unmatched.append(code)
            continue
        old = float(row["rate"])
        new = round(float(live), 6)
        conn.execute(
            "UPDATE fx SET rate = ?, source = ?, synced_at = ?, updated_at = ? WHERE code = ?",
            (new, "ExchangeRate-API", now_iso(), now_iso(), code))
        if abs(new - old) > 1e-9:
            updated.append({"code": code, "old": old, "new": new,
                            "change_pct": round((new - old) / old * 100, 2) if old else None})

    stamp = now_iso()
    set_setting(conn, "fx_last_sync", stamp)
    status = f"{len(updated)} rate(s) changed"
    if unmatched:
        status += f"; {len(unmatched)} not offered by the provider ({', '.join(unmatched)})"
    set_setting(conn, "fx_last_status", status)
    if actor:
        audit(conn, actor, "fx.synced", status, ip)
    return {"ok": True, "updated": updated, "unmatched": unmatched,
            "synced_at": stamp, "status": status}


def fx_is_stale(conn):
    s = get_settings(conn)
    if s.get("fx_auto") != "1" or not fx_api_key():
        return False
    last = s.get("fx_last_sync", "")
    if not last:
        return True
    try:
        when = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    hours = float(s.get("fx_interval_hours", 12) or 12)
    return datetime.utcnow() - when > timedelta(hours=hours)


_fx_thread = None


def start_fx_worker():
    """Background refresh. Harmless if the host sleeps — it just runs later."""
    global _fx_thread
    if _fx_thread or not fx_api_key():
        return

    def loop():
        time_module = __import__("time")
        time_module.sleep(20)          # let the server finish booting
        while True:
            try:
                with db.connection() as conn:
                    if fx_is_stale(conn):
                        report = sync_fx(conn)
                        print("  fx sync:", report.get("status") or report.get("error"))
            except Exception as e:
                print("  fx sync failed:", e)
            time_module.sleep(1800)     # re-check every 30 minutes

    _fx_thread = threading.Thread(target=loop, daemon=True, name="fx-sync")
    _fx_thread.start()


# --------------------------------------------------------------------------
# COMMISSION ENGINE
# --------------------------------------------------------------------------
# Rules, exactly as agreed with agents:
#
#   Stage                  Agent    StatsPack   Duration        Trigger
#   ---------------------------------------------------------------------
#   First client payment    60%        40%      Once only       On receipt
#   Monthly, months 1-12    10%        90%      12 months from  On receipt
#                                               first payment   of each
#   After month 12           0%       100%      Indefinite      n/a
#
# The window is measured from the date of the client's FIRST payment, so a
# late-starting client still gets its full twelve months.
# --------------------------------------------------------------------------
FIRST_PAYMENT = "First payment"
IN_WINDOW = "Monthly (year 1)"
OUT_OF_WINDOW = "After month 12"
VOIDED = "Voided"


def commission_rules(settings):
    return (
        float(settings.get("commission_first_rate", 0.60)),
        float(settings.get("commission_recurring_rate", 0.10)),
        int(float(settings.get("commission_window_months", 12))),
    )


def effective_rules(conn, agent_id, settings, _cache={}):
    """Commission rates that apply to ONE agent.

    Resolution order, most specific first:
      1. rates set on the agent themselves
      2. rates set on their company
      3. the portal-wide defaults in Settings

    Returns (first_rate, recurring_rate, window_months, source_label).
    """
    base = commission_rules(settings)
    if not agent_id:
        return base + ("portal default",)

    agent = conn.one("SELECT first_rate, recurring_rate, window_months, company_id "
                     "FROM users WHERE id = ?", (agent_id,))
    if not agent:
        return base + ("portal default",)

    company = None
    if agent.get("company_id"):
        company = conn.one("SELECT first_rate, recurring_rate, window_months "
                           "FROM companies WHERE id = ?", (agent["company_id"],))

    def pick(field, idx, cast=float):
        if agent.get(field) is not None:
            return cast(agent[field]), "agent"
        if company and company.get(field) is not None:
            return cast(company[field]), "company"
        return base[idx], "portal"

    first, s1 = pick("first_rate", 0)
    recur, s2 = pick("recurring_rate", 1)
    window, s3 = pick("window_months", 2, lambda v: int(float(v)))

    if "agent" in (s1, s2, s3):
        label = "agent override"
    elif "company" in (s1, s2, s3):
        # The host company's rates ARE the portal defaults, kept in step by
        # set_setting below. Calling them "company rate" would be confusing.
        host = conn.one("SELECT is_host FROM companies WHERE id = ?",
                        (agent.get("company_id"),))
        label = "portal default" if (host and host.get("is_host")) else "company rate"
    else:
        label = "portal default"
    return first, recur, window, label


def classify_payments(payments, settings, rules=None):
    """
    Takes every payment for ONE client (list of dicts, any order) and returns
    the same payments enriched with commission fields.

    Voided payments keep their place in the record but earn nothing, and are
    excluded when deciding which payment counts as 'first' — so voiding a
    mistaken first payment correctly promotes the next real one.
    """
    if rules:
        first_rate, recurring_rate, window = rules[0], rules[1], rules[2]
    else:
        first_rate, recurring_rate, window = commission_rules(settings)

    live = [p for p in payments if not p.get("voided")]
    live.sort(key=lambda p: (str(p.get("paid_date") or ""), p.get("id") or 0))

    first_date = parse_date(live[0]["paid_date"]) if live else None
    window_end = add_months(first_date, window) if first_date else None
    first_id = live[0]["id"] if live else None

    enriched = []
    for p in payments:
        p = dict(p)
        amount_usd = float(p.get("amount_usd") or 0)
        if p.get("voided"):
            p["commission_kind"] = VOIDED
            p["commission_rate"] = 0.0
            p["agent_commission_usd"] = 0.0
            p["statspack_share_usd"] = 0.0
        elif p["id"] == first_id:
            p["commission_kind"] = FIRST_PAYMENT
            p["commission_rate"] = first_rate
            p["agent_commission_usd"] = round(amount_usd * first_rate, 2)
            p["statspack_share_usd"] = round(amount_usd - amount_usd * first_rate, 2)
        else:
            pd = parse_date(p.get("paid_date"))
            inside = bool(pd and window_end and pd <= window_end)
            rate = recurring_rate if inside else 0.0
            p["commission_kind"] = IN_WINDOW if inside else OUT_OF_WINDOW
            p["commission_rate"] = rate
            p["agent_commission_usd"] = round(amount_usd * rate, 2)
            p["statspack_share_usd"] = round(amount_usd - amount_usd * rate, 2)
        p["window_end"] = window_end.strftime("%Y-%m-%d") if window_end else ""
        enriched.append(p)

    enriched.sort(key=lambda p: (str(p.get("paid_date") or ""), p.get("id") or 0))
    return enriched


def client_payment_rows(conn, client_ids, settings, newest_first=True):
    """Enriched payments for a set of clients, grouped correctly per client.

    newest_first=True suits a feed of activity across many clients; pass False
    for a single client's history, where the sequence is what explains the
    commission (the 60% payment first, then the 10% ones).
    """
    if not client_ids:
        return []
    marks = ",".join("?" for _ in client_ids)
    rows = conn.query(
        f"SELECT p.*, c.name AS client_name, c.agent_id "
        f"FROM payments p JOIN clients c ON c.id = p.client_id "
        f"WHERE p.client_id IN ({marks}) ORDER BY p.paid_date, p.id",
        tuple(client_ids),
    )
    by_client = {}
    for r in rows:
        by_client.setdefault(r["client_id"], []).append(r)
    out = []
    rules_cache = {}
    for cid, plist in by_client.items():
        agent_id = plist[0].get("agent_id")
        if agent_id not in rules_cache:
            rules_cache[agent_id] = effective_rules(conn, agent_id, settings)
        out.extend(classify_payments(plist, settings, rules_cache[agent_id]))
    out.sort(key=lambda p: (str(p.get("paid_date") or ""), p.get("id") or 0),
             reverse=newest_first)
    return out


def agent_commission_summary(conn, agent_id, settings):
    """Everything owed to, and already paid to, one agent."""
    clients = conn.query("SELECT id FROM clients WHERE agent_id = ?", (agent_id,))
    ids = [c["id"] for c in clients]
    payments = client_payment_rows(conn, ids, settings)

    earned = round(sum(p["agent_commission_usd"] for p in payments), 2)
    first_earned = round(sum(p["agent_commission_usd"] for p in payments
                             if p["commission_kind"] == FIRST_PAYMENT), 2)
    recur_earned = round(sum(p["agent_commission_usd"] for p in payments
                             if p["commission_kind"] == IN_WINDOW), 2)
    collected = round(sum(float(p["amount_usd"]) for p in payments if not p.get("voided")), 2)

    paid_row = conn.one(
        "SELECT COALESCE(SUM(amount_usd),0) AS t FROM payouts WHERE agent_id = ? AND status = 'Paid'",
        (agent_id,),
    )
    pending_row = conn.one(
        "SELECT COALESCE(SUM(amount_usd),0) AS t FROM payouts WHERE agent_id = ? AND status IN ('Pending','Approved')",
        (agent_id,),
    )
    paid = round(float(paid_row["t"] or 0), 2)
    in_flight = round(float(pending_row["t"] or 0), 2)

    return {
        "earned_usd": earned,
        "first_payment_usd": first_earned,
        "recurring_usd": recur_earned,
        "collected_usd": collected,
        "paid_out_usd": paid,
        "in_flight_usd": in_flight,
        "outstanding_usd": round(earned - paid - in_flight, 2),
        "payments": payments,
    }


# --------------------------------------------------------------------------
# CLIENT TIMELINE  (the flowchart from Prospect to close)
# --------------------------------------------------------------------------
def log_event(conn, client, user, kind="note", body="", from_stage="", to_stage="",
              next_step="", due_date=""):
    return conn.insert(
        "INSERT INTO client_events (client_id, company_id, author_id, author_name, kind, "
        "from_stage, to_stage, body, next_step, due_date, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (client["id"], client.get("company_id", 1) or 1,
         (user or {}).get("id", 0), (user or {}).get("name", "system"),
         kind, from_stage, to_stage, body[:4000], next_step[:400], due_date[:20], now_iso()))


def client_timeline(conn, client_id):
    return conn.query(
        "SELECT * FROM client_events WHERE client_id = ? ORDER BY created_at, id",
        (client_id,))


def stage_progress(stage):
    """Where a client sits on the pipeline, and whether it ended badly."""
    pipeline = ["Prospect", "Qualified", "Demo", "Proposal", "Won"]
    if stage in ("Lost", "Churned"):
        return {"index": -1, "total": len(pipeline), "closed": True,
                "outcome": stage, "pipeline": pipeline}
    idx = pipeline.index(stage) if stage in pipeline else 0
    return {"index": idx, "total": len(pipeline), "closed": stage == "Won",
            "outcome": "Won" if stage == "Won" else "", "pipeline": pipeline}


# --------------------------------------------------------------------------
# PERMANENT DELETION  (super users only)
# --------------------------------------------------------------------------
def agent_footprint(conn, agent_id):
    """Exactly what would be destroyed. Shown before anyone confirms."""
    clients = conn.query("SELECT id, name FROM clients WHERE agent_id = ?", (agent_id,))
    ids = [c["id"] for c in clients]
    payments = collected = 0
    if ids:
        marks = ",".join("?" for _ in ids)
        row = conn.one(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(amount_usd),0) AS t "
            f"FROM payments WHERE client_id IN ({marks}) AND voided = 0", tuple(ids))
        payments = int(row["n"] or 0)
        collected = round(float(row["t"] or 0), 2)
    payouts = conn.one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount_usd),0) AS t "
        "FROM payouts WHERE agent_id = ? AND status = 'Paid'", (agent_id,))
    reviews = conn.one("SELECT COUNT(*) AS n FROM reviews WHERE agent_id = ?", (agent_id,))
    return {
        "clients": len(clients),
        "client_names": [c["name"] for c in clients][:25],
        "payments": payments,
        "collected_usd": collected,
        "payouts_paid": int(payouts["n"] or 0),
        "payouts_paid_usd": round(float(payouts["t"] or 0), 2),
        "reviews": int(reviews["n"] or 0),
    }


def export_agent(conn, agent_id):
    """Full snapshot, taken before deletion so the record survives the person."""
    settings = get_settings(conn)
    agent = conn.one("SELECT * FROM users WHERE id = ?", (agent_id,))
    if not agent:
        return None
    agent = dict(agent)
    agent.pop("password_hash", None)
    clients = conn.query("SELECT * FROM clients WHERE agent_id = ?", (agent_id,))
    ids = [c["id"] for c in clients]
    return {
        "exported_at": now_iso(),
        "reason": "snapshot taken immediately before permanent deletion",
        "agent": agent,
        "clients": clients,
        "payments": client_payment_rows(conn, ids, settings, newest_first=False),
        "timeline": [e for cid in ids for e in client_timeline(conn, cid)],
        "payouts": conn.query("SELECT * FROM payouts WHERE agent_id = ?", (agent_id,)),
        "reviews": conn.query("SELECT * FROM reviews WHERE agent_id = ?", (agent_id,)),
        "commission_summary": {k: v for k, v in
                               agent_commission_summary(conn, agent_id, settings).items()
                               if k != "payments"},
    }


def purge_agent(conn, agent_id):
    """Delete the agent and everything attached, in dependency order."""
    clients = conn.query("SELECT id FROM clients WHERE agent_id = ?", (agent_id,))
    ids = [c["id"] for c in clients]
    counts = {"payments": 0, "clients": len(ids), "payouts": 0, "reviews": 0,
              "sessions": 0, "timeline": 0}
    if ids:
        marks = ",".join("?" for _ in ids)
        row = conn.one(f"SELECT COUNT(*) AS n FROM payments WHERE client_id IN ({marks})",
                       tuple(ids))
        counts["payments"] = int(row["n"] or 0)
        conn.execute(f"DELETE FROM payments WHERE client_id IN ({marks})", tuple(ids))
        row = conn.one(f"SELECT COUNT(*) AS n FROM client_events WHERE client_id IN ({marks})",
                       tuple(ids))
        counts["timeline"] = int(row["n"] or 0)
        conn.execute(f"DELETE FROM client_events WHERE client_id IN ({marks})", tuple(ids))
    row = conn.one("SELECT COUNT(*) AS n FROM payouts WHERE agent_id = ?", (agent_id,))
    counts["payouts"] = int(row["n"] or 0)
    row = conn.one("SELECT COUNT(*) AS n FROM reviews WHERE agent_id = ?", (agent_id,))
    counts["reviews"] = int(row["n"] or 0)
    row = conn.one("SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (agent_id,))
    counts["sessions"] = int(row["n"] or 0)

    conn.execute("DELETE FROM clients WHERE agent_id = ?", (agent_id,))
    conn.execute("DELETE FROM payouts WHERE agent_id = ?", (agent_id,))
    conn.execute("DELETE FROM reviews WHERE agent_id = ?", (agent_id,))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (agent_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (agent_id,))
    return counts


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
def audit_standalone(user, action, detail="", ip="", target="", meta=None):
    """Write an audit row on its own connection so it survives the rollback
    that follows a rejected request. Failed sign-ins must be recorded."""
    try:
        with db.connection() as own:
            audit(own, user, action, detail, ip, target, meta)
    except Exception:
        pass


def audit(conn, user, action, detail="", ip="", target="", meta=None):
    """meta is a small dict rendered when a super admin opens a log entry."""
    payload = ""
    if meta:
        try:
            payload = json.dumps(meta, default=str)[:4000]
        except (TypeError, ValueError):
            payload = ""
    conn.execute(
        "INSERT INTO audit (user_id, actor, action, detail, ip, company_id, target, meta, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (user["id"] if user else 0, (user or {}).get("email", "system"), action, detail, ip,
         (user or {}).get("company_id", 0) or 0, str(target)[:120], payload, now_iso()),
    )


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------
def bootstrap(conn):
    """Seed settings, FX and the first admin. Idempotent."""
    for k, v in DEFAULT_SETTINGS.items():
        if not conn.one("SELECT skey FROM settings WHERE skey = ?", (k,)):
            conn.execute("INSERT INTO settings (skey, svalue) VALUES (?,?)", (k, v))

    for code, rate, label in list(DEFAULT_FX) + list(WORLD_FX):
        if not conn.one("SELECT code FROM fx WHERE code = ?", (code,)):
            conn.execute(
                "INSERT INTO fx (code, rate, label, source, synced_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (code, rate, label, "manual", "", now_iso()),
            )

    # The host company (StatsPack itself) is always company #1.
    host = conn.one("SELECT id FROM companies WHERE is_host = 1")
    if not host:
        conn.insert(
            "INSERT INTO companies (name, slug, country, contact_email, contact_phone, status, "
            "is_host, base_currency, first_rate, recurring_rate, window_months, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("StatsPack", "statspack", "Lesotho",
             os.environ.get("ADMIN_EMAIL", "admin@statspack.co.ls"), "", "Active", 1,
             "USD", 0.60, 0.10, 12, "The host company.", now_iso()))
        # Existing rows from before multi-tenancy belong to the host company.
        conn.execute("UPDATE users SET company_id = 1 WHERE company_id IS NULL OR company_id = 0")
        conn.execute("UPDATE clients SET company_id = 1 WHERE company_id IS NULL OR company_id = 0")

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@statspack.co.ls").strip().lower()
    existing = conn.one("SELECT id FROM users WHERE role = 'admin'")
    if existing:
        # Upgrading an existing install: make sure someone holds super rights.
        if not conn.one("SELECT id FROM users WHERE role = 'admin' AND is_super = 1"):
            first = conn.one("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
            if first:
                conn.execute("UPDATE users SET is_super = 1 WHERE id = ?", (first["id"],))
                print(f"  granted super-user rights to admin #{first['id']}")
        return None

    admin_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    generated = False
    if not admin_pw:
        admin_pw = temp_password(12)
        generated = True

    # A password the operator chose deliberately is theirs to keep; only a
    # password we generated for them has to be replaced at first sign-in.
    conn.insert(
        "INSERT INTO users (name, email, phone, country, role, status, password_hash, "
        "must_change_pw, is_super, company_id, quota_usd, start_date, notes, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("StatsPack Admin", admin_email, "", "Lesotho", "admin", "Active",
         hash_password(admin_pw), 1 if generated else 0, 1, 1, 0, today(), "", now_iso()),
    )
    return {"email": admin_email, "password": admin_pw, "generated": generated}
