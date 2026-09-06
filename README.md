# StatsPack Tech Sales Agent Portal — v1.5

A two-role portal for managing tech sales agents across the SADC region.
StatsPack admins manage agents, clients, payments and commission; agents log
their own pipeline and see exactly what they have earned and what is still owed.

Built in the StatsPack VMS visual language — teal `#4EC3C1`, slate `#3E5F71`,
amber `#E3A93E`, coral `#DB6B4E`, Trebuchet MS, light sidebar with the teal
profile header — so it sits beside EduTrack 360 and Analytics Lab without
looking like a different product.

---

## The commission rules it implements

| Payment stage | Agent | StatsPack | Duration | Trigger |
|---|---|---|---|---|
| First client payment | 60% | 40% | Once only | On receipt of the client's first payment |
| Monthly payments — year 1 | 10% | 90% | 12 months after the first payment | On receipt of each monthly payment |
| After month 12 | — | 100% | Indefinite | Commission ceases |

The twelve-month window is measured **from each client's own first payment**,
so a client signed in November still gets a full twelve months. All three
figures are editable in Settings if the agent agreement ever changes.

**Commission is triggered by money actually received, not by a signature.**
Only an admin can record a payment. Agents can create and update their own
clients, but they cannot mark a payment as received — that separation is the
main financial control in the system.

---

## If the primary dies: how you find out, and how to switch over

### How you find out

The in-app banner cannot help here — the portal is down. Two channels do work:

1. **Your cron-job.org ping.** Point it at `https://your-domain/api/health` and
   turn on failure alerts. That endpoint answers without touching the database,
   so during an outage it returns 503 with `"database": "UNREACHABLE"` and the
   recovery step. This is your primary alarm; set it up.
2. **Neon's own dashboard**, which shows project status directly.

Anyone opening the portal sees a plain message rather than a broken page: *"The
database is not responding. If it is only waking up this will clear in a few
seconds — otherwise contact your StatsPack administrator."*

### Will Supabase have everything?

It has everything up to **the last successful copy**, and System health tells you
exactly when that was and how long ago, in plain words: *"You would lose
everything recorded since 14:00 UTC — about 40 minutes of work."*

Whatever was recorded in that gap — payments, new clients, activity notes — is
not on the standby. To shrink it:

- Lower `MIRROR_INTERVAL_MINUTES` in Render. `15` is comfortable at this data
  size; each copy is a few hundred rows.
- Press **Copy to standby now** after recording a batch of payments. That is the
  data you would least like to re-key.

The portal warns you if the standby has never been copied to, or if the last
successful copy is more than three times the interval old — a mirror that has
quietly stopped is more dangerous than one that fails loudly, because your
recovery point silently gets worse.

### Making Supabase the main database

1. Render → your service → **Environment**.
2. Change `DATABASE_URL` to the Supabase connection string.
3. **Swap the other one too**: set `MIRROR_DATABASE_URL` to the old Neon
   string. The two simply change places — no new project needed. Once Neon is
   healthy again it becomes the standby and gets refreshed from Supabase.
4. Save; Render redeploys automatically.
5. Sign in. Everyone signs in again — sessions are deliberately not mirrored.
6. Open System health and press **Copy to standby now**.

If you leave both variables pointing at the same database, the mirror refuses to
run and says so rather than copying it onto itself. Swapping back later is the
same two-line change.

Verified end to end in `test_promotion.py`: the portal runs on the copy, admins
and agents sign in, collections and commission match the primary to the cent,
per-agent rate overrides and partnerships survive, and it accepts new agents,
clients and payments with correct commission and working reports.

---

## v1.4 — failover safety with existing data

Written for the situation you are actually in: **Neon already holds live
records, Supabase starts empty.**

**The first copy goes the right way.** Neon → Supabase. Verified against a
database with existing agents, clients and payments: the upgrade migrates it,
the first copy lands 185 rows on Supabase, and the money is untouched.

**Guard against the copy running backwards.** The dangerous setup mistake is
putting the empty database in `DATABASE_URL` and your real one in
`MIRROR_DATABASE_URL`. The copy wipes its target first, so that would destroy
live records. The mirror now refuses when the primary has no user accounts but
the standby does, or when the standby holds more payment records than the
primary, and explains which is likely the wrong way round. Nothing is deleted.
A **Force copy** option exists for when you are certain the primary is right.

**Guard against copying a database onto itself.** After a failover it is easy
to leave both variables on the same database. That is now detected at startup
and refused, rather than quietly erasing the backup.

**Failover is a swap, not a rebuild.** Supabase becomes primary and Neon becomes
the standby. Tested end to end: super admin, company admin and agent all sign
in on the copy, the money matches, new work is accepted, and Neon is refreshed
from Supabase once it returns.

**The portal starts even with no database.** Previously it exited, so Render
crash-looped and `/api/health` never answered — you would have seen a generic
platform error instead of a clear message. It now starts, logs the problem, and
serves the outage response.

**Outages fail fast.** Every request used to spend about 36 seconds retrying
before giving up, so pages appeared to hang. A short circuit breaker means the
error now arrives in **under half a second**.

**Every role, every action, the same clear message.** Verified for super admin,
company admin and agent across reading dashboards, recording payments, creating
agents, adding clients, logging activity and downloading reports — all return
503 with *"The database is not responding…"*, and the interface shows a full
outage screen rather than a small error toast. No write is half-saved.

---

## What happens when a database goes down

The two directions behave very differently. Both are tested in
`test_failure_modes.py`.

### The standby (Supabase) crashes — you are fine

| | |
|---|---|
| Portal | keeps serving normally |
| Agents | can sign in, add clients, log activity |
| Admins | can record payments and pay commission |
| Warning | incident raised, red banner on every page for super users |
| Recovery | the next copy succeeds on its own and clears the incident |

The standby is never in the path of a user request, so losing it costs you the
backup and nothing else.

### The primary (Neon) crashes — the portal stops

| | |
|---|---|
| Portal | returns 503 with a readable message, on every page |
| Writes | none are accepted — nothing is half-saved |
| `/api/health` | still answers, with `"database": "UNREACHABLE"` and what to do |
| Standby | holds a full copy but is **not** promoted automatically |

**The standby does not take over by itself, and that is deliberate.** Automatic
failover on a commission ledger means two databases can both accept writes,
disagree about who is owed what, and leave you with no safe way to reconcile.
Promotion is a decision you take:

1. In Render, change `DATABASE_URL` to the Supabase connection string.
2. Redeploy.
3. Everyone signs in again — sessions are not mirrored, which is the safer
   default.

You lose only what was written since the last copy, which System health shows.

**How you find out.** Since the portal is down, the in-app banner cannot reach
you — so point your cron-job.org ping at `/api/health` and turn on failure
alerts. It returns 503 the moment the database is unreachable, and it keeps
answering when everything else is broken, which is exactly when you need it.

---

## v1.3.2 — failure handling

Testing an actual primary-database outage showed the portal returning a generic
**500 "Something went wrong on the server"**, and `/api/health` failing with it —
so an uptime monitor could tell you something was wrong but not what.

Now: a vanished or unreachable database is recognised as such rather than
escaping as an unhandled error, every page returns a clear 503, and
`/api/health` answers without needing the database so it can report the outage
and name the recovery step.

---

## v1.3.1 — rate precision fix (important)

Verifying the Supabase copy row by row turned up a bug in the **primary**, not
the mirror.

Rate columns were stored as `NUMERIC(18,2)` — two decimal places. That is right
for money but wrong for rates:

| You entered | Was stored as | Effect |
|---|---|---|
| 12.5% commission | 13% | agent overpaid by 0.5% of every payment |
| 27.5% partner split | 28% | partner overpaid |
| BHD 0.376 | 0.38 | ~1% error on every Bahraini conversion |
| KWD 0.307 | 0.31 | ~1% error on every Kuwaiti conversion |

`fx.rate`, `payments.fx_rate`, both commission rates on users and companies, and
`collaborations.split_pct` are now `NUMERIC(18,6)`. Existing databases are
widened automatically on boot.

**After deploying, do two things.** Press **Sync now** on Settings so the
exchange rates are re-fetched at full precision — values already rounded to two
places stay rounded until re-entered. Then check any agent on a non-round
commission rate and re-enter it.

The root cause was duplication: `mirror.py` kept its own copy of the schema
type substitutions, so it did not know about the new precision type. Both now
call `db.fill_pg()`, so the schemas cannot drift apart again.

### Verification added

- **`test_mirror_fidelity.py`** — compares every column of every row across all
  twelve tables between primary and standby, using deliberately awkward data:
  unicode (`Мосэнерго 日本語`), apostrophes, embedded quotes, newlines inside
  notes, NULL commission overrides, exact decimals and base64 avatars.
- **`test_promotion.py`** — the disaster drill. Starts the portal against the
  Supabase copy alone and confirms admins and agents can sign in, that
  collections and commission match the primary to the cent, that per-agent rate
  overrides and partnerships survived, and that the copy accepts new agents,
  clients and payments with correct commission and working reports.

Sessions are deliberately **not** mirrored, so everyone signs in again after a
failover. That is the safer default.

---

## v1.5 — agent search, client deletion, keep-alive

### Search agents

A search box on the Agents page matches name, email, phone, country, agent type,
notes and company, with dropdown filters for type and status. Company admins
only ever search within their own company.

### Delete clients

Two levels, because a client that has never paid is not the same as one that has:

| Situation | Who can delete | What happens |
|---|---|---|
| No payments recorded | Any admin | Client, activity trail and partnerships removed |
| Payments recorded | **Super user only** | Everything above, plus payments and commission |

Deleting a paying client shows exactly what will be destroyed — payments,
collections, commission, activity entries, partnerships — offers a JSON download
first, and requires you to type the client's name. The log keeps a permanent
record of who deleted what and how much money it represented.

Where possible the portal points you at setting the stage to **Churned**
instead, which achieves the same day-to-day result and keeps the history.

### Keeping the database awake

**Read this before widening the window.** Neon's free plan suspends the compute
after 5 minutes idle, and that cannot be turned off on the free plan. But the
same plan caps compute at **100 CU-hours a month** — roughly 400 hours at the
default size, against 730 hours in a month. A round-the-clock ping would exhaust
the allowance and Neon would **suspend the database until the next billing
period**, which is far worse than a wake-up delay of a few hundred milliseconds.

So the ping runs only during working hours:

| Setting | Default | Notes |
|---|---|---|
| `KEEPALIVE` | `1` | `0` turns it off |
| `KEEPALIVE_START_HOUR` | `6` | local time |
| `KEEPALIVE_END_HOUR` | `19` | local time |
| `KEEPALIVE_DAYS` | `0,1,2,3,4` | Monday to Friday |
| `KEEPALIVE_TZ_OFFSET` | `2` | Lesotho, UTC+2 |
| `KEEPALIVE_MINUTES` | `4` | under Neon's 5-minute idle timeout |

That works out at about **71 of your 100 CU-hours a month**, leaving headroom
for actual usage. System health shows the projection as a bar, and the portal
raises a warning if your window would take you past the allowance. Widening it
to 24/7 would come to 182% of quota — the warning fires, and the startup log
says so too.

Note this only helps while Render itself is awake, so keep the cron ping on
`/api/health` running as well.

---

## v1.4.5 — partnerships no longer claw back earned commission (important)

**The bug.** Ending a partnership removed the partner's commission
retroactively — on every payment, including ones received while they were
actively helping. A partner who had earned $240 and been paid it went to $0
earned and −$240 outstanding, and the lead agent was silently credited with the
$240 the partner had already been paid.

That is the negative balance appearing on a collaboration, and it was wrong in
both directions: the partner appeared to owe money back, and the lead's figures
overstated what they were due.

**The fix.** Commission is now attributed by **when a payment was recorded**,
not by whether the partnership happens to be running today:

| When the payment was recorded | Who earns it |
|---|---|
| Before the partner joined | Lead only |
| While the partnership was live | Split, as agreed |
| After it ended | Lead only |

So ending a partnership is forward-looking. The partner keeps everything they
earned while helping, their balance stays square, and the lead takes the full
share on everything afterwards.

**Why "when recorded" rather than the invoice date.** Payments are often entered
late or backdated. What matters is whether the two agents were working the client
together when the money was processed — dating it by the invoice would exclude a
partner who demonstrably helped, simply because the invoice predated the
agreement.

**If you have already ended a partnership**, check the two agents' figures. The
correction applies automatically once you deploy: the partner's earnings
reappear and the lead's drop back to what they are actually owed.

One of my earlier tests asserted the wrong behaviour here — it expected the lead
to reclaim the partner's share, so it passed while the bug was present. The
expectation has been corrected and `test_collab_history.py` now covers the whole
lifecycle.

---

## When an agent's outstanding balance goes negative

This is not a fault. It means the agent has already been paid more than they have
earned, because commission was recalculated *after* a payout was made. It happens
when:

- **A commission rate was corrected downward.** The most likely cause right after
  upgrading: rates used to be stored to two decimal places, so 12.5% was held as
  13%. Re-entering the true rate lowers what the agent has earned, while the
  payout already made stands.
- **A payment was voided** after the agent was paid on it.
- **A partnership ended** after the partner had been paid their share.
- **A client was reassigned** to a different agent.

**You usually need do nothing.** The excess carries forward and is absorbed by
their next commission automatically. In testing, a $10.22 overpayment cleared
itself on the next month's payment with no intervention.

In the portal, an overpaid agent shows the amount in red with an **overpaid**
label, and the Pay button becomes **Why?**. Opening it shows earned versus paid,
the current rate and where it came from, and the likely reason. If you decide to
pay them anyway and write off the difference, **Pay anyway** records it and notes
it in the activity log.

**Check whether it affected a real payout.** If an agent was paid at 13% when
their contract says 12.5%, they were overpaid in cash, not just on screen. That
is a conversation to have with them directly rather than something to leave for
the system to net off quietly.

### v1.4.4 — the message itself

The old wording was written for a developer, not for you:

> That is more than this agent is owed. Outstanding is $-10.22. Send force:true
> to override.

It now names the agent, states the overpayment plainly, explains why it happens,
says the balance corrects itself, and offers the override as a button rather
than as an instruction to send a JSON field.

---

## How long a copy takes

Measured, not estimated. The copy is a full snapshot of every table, so the time
depends on total rows rather than on how much changed.

| Your data | Rows copied | Time |
|---|---|---|
| First weeks — a few agents, a handful of payments | ~200 | **1–2 seconds** |
| A year in — 10 agents, ~1,200 payments | ~1,800 | **2–3 seconds** |
| Busy — 40 agents, 800 clients, 9,600 payments | ~11,000 | **3–5 seconds** |

At 11,000 rows the database is about 10 MB, so a busy year uses roughly 2% of
the 500 MB free allowance. Storage is not the constraint here.

The copy runs on a background thread and never blocks a user request. Agents can
keep working while it happens.

### v1.4.3 — batched inserts

The first version sent one round trip per row. On a local connection that is
invisible, but Render → Supabase is a real network hop, and every row would have
cost about 10ms:

| Rows | One round trip per row | Batched |
|---|---|---|
| 9,600 payments at 5ms latency | ~48 seconds | ~0.1s |
| 9,600 payments at 10ms latency | ~96 seconds | ~0.2s |
| 9,600 payments at 25ms latency | ~4 minutes | ~0.5s |

Rows are now batched 500 at a time, cutting 9,600 round trips to about 20.
Verified byte-for-byte afterwards: the standby still matches the primary column
by column, including the payment totals.

---

## v1.4.2 — final pre-deploy audit

A last pass over the newest code found four bugs, all fixed.

**A deleted agent kept taking commission.** Purging an agent removed the person
but left their partnerships behind, so a deleted partner carried on taking their
30% share of the lead agent's commission indefinitely. Purging now removes
partnerships, and the lead's commission returns to 100%.

**Backups were missing partnerships.** `collaborations` and `incidents` were not
exported, so restoring from a backup would have lost every partnership silently.
Both are now included.

The second bug hid the first: my orphan check passed only because the table it
was checking was absent from the export. The test now fails if the table is
missing at all, rather than quietly finding nothing.

**Deleting a client left its partnership behind**, pointing at a record that no
longer existed.

**Confusing validation order.** An out-of-range commission split reported as a
conflict rather than a bad number, because the duplicate-partnership check ran
first. Inputs are validated before state.

Also verified in this pass: the circuit breaker does not misfire under load (30
concurrent requests, all fine, normal responses in 0.01s), every report still
builds after deletions, partnerships cannot be offered to suspended or
non-existent agents, and System health behaves correctly when no standby is
configured.

---

## Which database is in charge

**Neon is the primary. Supabase is the standby.** That is the normal, permanent
configuration and nothing about it changes:

| Variable | Points at | Role |
|---|---|---|
| `DATABASE_URL` | Neon | every read and write |
| `MIRROR_DATABASE_URL` | Supabase | receives a copy on a timer |

Supabase never serves the portal while Neon is healthy. It exists so that if
Neon is ever lost you have a runnable copy rather than a dead end.

### Failover is temporary and reversible

If Neon fails you swap the two values, and **swap them back** when Neon returns.
There is no rebuild and no new project. The full round trip is tested in
`test_swap.py`:

1. **Normal** — Neon primary, Supabase standby.
2. **Neon fails** — swap the two variables, redeploy. The portal runs on
   Supabase; agents keep working. Neon becomes the standby.
3. **Neon returns** — press **Copy to standby now** *while still on Supabase*.
   This pushes everything recorded during the outage back to Neon.
4. **Swap back** — Neon is primary again, Supabase is the standby again. All the
   work done during the outage is there.

**Step 3 is the one to remember.** If you swap back without it, Neon is still
holding pre-outage data and everything recorded during the outage sits only on
Supabase. The portal will not let you destroy it: the mirror refuses to
overwrite a standby that holds more payment records than the primary, tells you
how many are at risk, and raises an incident. You then swap forward again, press
Copy, and swap back properly.

---

## Setting up the Supabase standby

The standby is a **warm backup, not automatic failover**. Neon stays the single
source of truth for every read and write; Supabase receives a full copy on a
timer. Two databases both accepting commission writes would eventually disagree
about who is owed what, with no safe way to reconcile — so this is deliberately
one-way.

### Steps

1. Go to <https://supabase.com> and sign up (GitHub sign-in is quickest).
2. **New project.** Give it a name, set a strong database password (save it),
   and choose the region closest to your Neon region — the copy runs faster when
   they are near each other.
3. Wait about two minutes while it provisions.
4. **Project Settings → Database → Connection string → URI.** Copy it. It looks
   like `postgresql://postgres:PASSWORD@db.xxxx.supabase.co:5432/postgres`.
   Replace `[YOUR-PASSWORD]` with the password from step 2.
5. In Render, add environment variables:

   | Key | Value |
   |---|---|
   | `MIRROR_DATABASE_URL` | the Supabase URI |
   | `MIRROR_INTERVAL_MINUTES` | `60` (optional, default 60) |
   | `MIRROR_QUOTA_MB` | `500` (optional, your Supabase plan limit) |
   | `DB_QUOTA_MB` | `500` (optional, your Neon plan limit) |

6. Redeploy. The standby's tables are created automatically on the first copy —
   you do not need to run any SQL in Supabase.
7. Open **System health** in the portal and press **Copy to standby now**.

You do not need to create tables, users or policies in Supabase. Leave Row Level
Security alone; the mirror connects as the database owner.

### If the primary ever fails

Point `DATABASE_URL` at the Supabase URI in Render and redeploy. You lose only
what was written since the last copy, which System health shows. This is a
deliberate human decision, not something the portal does behind your back.

---

## v1.3 — standby, teamwork, reports

**Two databases, one truth.** Supabase mirror as above, with a System health
page showing used and remaining space on both, row counts side by side, when the
last copy ran, and how long it took. A standby failure cannot affect the live
portal — tested by destroying the standby mid-session and confirming reads,
writes and commission all continued. Failures raise an incident, repeated
failures collapse into one alert with a counter, and a good run resolves it
automatically. Super users see a banner on every page while anything is open.

**Storage.** Both databases report used, free and percentage against your plan
limit, with a per-table breakdown. Crossing 80% raises a warning incident and
92% an error, so you hear about it before writes start failing.

**Teamwork.** An agent chasing a client in a country they don't know can browse
colleagues filtered by country or by industry they have actually closed in,
ranked by clients won and win rate. They offer a share of *their own* commission
— StatsPack's share never moves. Once accepted the partner can open that client
and log activity, but cannot change the deal or its stage. Either side can end
it; commission already earned is untouched, and future commission returns fully
to the lead.

The directory deliberately does **not** show what colleagues earn. Peers should
choose on track record, not on who is paid most.

**Reports.** Six CSV downloads — commission by agent, payments register, client
pipeline, payout history, agent performance, activity trail — with optional date
ranges and an on-screen preview. Agents get the same reports scoped to their own
clients. Cells beginning `=`, `+`, `-` or `@` are neutralised so a client name
cannot execute as a formula when the file opens in Excel.

**Editing existing agents.** Agent type and per-agent commission are now in the
main edit dialog. Agents created before agent types existed show a **not set**
badge in the list rather than being silently assigned a default — the system
does not guess what someone's contract says.

**Login image.** The white veil was at 90% opacity, which erased the photograph.
It is now 34%, with a soft halo behind the form so text stays readable over a
busy image. Verified against a deliberately harsh test background.

---

## v1.2.2 — company registration fix

**Registering a company failed with "Unknown endpoint".**

The Companies page assigned the dialog straight to the button
(`onclick = companyModal`), which hands the browser's click Event to the
function as its first argument. The dialog took that truthy value to mean it was
*editing* an existing company, so it hid the administrator fields and sent
`PATCH /api/admin/companies/undefined` — a path matching no route.

Fixed at both ends: the button now calls `companyModal(null)`, and the dialog
decides it is editing only when it receives an object with an `id`.

**Why the tests missed it.** Every suite up to this point called the API
directly and never touched the interface, so a purely front-end wiring bug was
invisible to all 268 of them. `test_ui.py` now drives a real browser: it loads
every page as both a super user and an agent, opens every "add" dialog and
checks its fields are present, registers a company end to end, edits one, logs
an activity, opens the log drill-down, and fails on any JavaScript error or
failed API call.

The same mistake existed nowhere else — every other handler was checked.

---

## v1.2.1 — pre-deployment audit

A dedicated adversarial pass found **16 further bugs**, all fixed. Worth knowing
what they were, because most were invisible to normal use.

**Cross-tenant holes (the serious ones).** Several endpoints checked that you
were *an* admin but not *which company's* admin. A company administrator could
record payments against another company's client, void their payments, approve
their payouts, reset their agents' passwords, export their full commission
records, and delete their progress notes. Every endpoint is now scoped and the
holes are covered by tests that specifically attempt each attack.

**Suspending a company didn't lock anyone out.** It ended live sessions, but
users could sign straight back in — sign-in checked the account's status and not
the company's. Both are now checked.

**Client reassignment could strand a client.** Moving a client to an agent in
another company left `company_id` pointing at the old one, so it belonged to
nobody: invisible to its new owner, still counted in the old company's totals.
The client now follows its agent.

**Backups were incomplete.** `companies` and `client_events` were missing, so a
restore would have silently lost every tenant and every activity trail.

**Deleting left orphans.** Removing a client or purging an agent left timeline
entries behind, pointing at records that no longer existed.

**Connection pool exhaustion.** Under 12 simultaneous requests the pool errored
instead of waiting, so surplus users got server errors. Requests now queue for a
free connection. Tested at 40 simultaneous requests — four times the pool size —
with none failing.

**Avatars bloated every list.** Full-size images were inlined into the agent
list, making one response 284KB. A 64px thumbnail is now stored alongside the
full image and used in lists; the same response is 4KB.

Also verified clean: SQL injection through filter parameters, path traversal,
XSS storage and escaping, malformed JSON, privilege escalation via crafted
request bodies, division by zero on FX rates, same-day payment ordering,
double-payout prevention, and health-endpoint leakage.

---

## What changed in v1.2

### Three intermittent errors, one cause — fixed

Sign-in failing then working on retry, agent creation failing then working, and
errors when an agent saved a client were **all the same bug**. Neon suspends the
database when idle and drops every connection; the pool handed those dead
connections to the next request, which died before anything committed, and the
retry got a fresh one.

The pool now validates each connection before use, discards dead ones instead of
recycling them, and the server retries a lost connection up to three times with
a short backoff. Retrying is safe because the failure happens before commit, so
nothing can be written twice. Verified by killing every database connection
mid-flight under concurrent load: 14 of 14 requests succeeded.

If the database is genuinely down you now get *"The database is waking up. Please
try again in a few seconds"* rather than a generic server error.

### Companies — clients running their own agent networks

A super user registers a company and its first administrator. That administrator
signs in at the same address, creates their own agents, and sees **only** their
own data. Isolation is enforced server-side on every endpoint and covered by
tests: cross-company reads, edits, payouts, reviews and log entries all return
403.

Each company has its own commission rules, which its agents inherit.

### Per-agent commission

Rates resolve most-specific-first: **agent override → company rate → portal
default**. Every screen showing a rate also shows which level it came from, so
you can see at a glance who is on non-standard terms.

The Settings page and the host company's rates are now a single stored value.
Previously they were two copies that could drift, meaning an admin could change
the rate in Settings and see nothing happen.

### Pipeline flowchart and activity trail

Every client page shows its progress from Prospect to Won, with the date each
stage was reached, and an outcome banner if it was Lost or Churned. Beneath it
is a dated trail: stage moves are recorded automatically with an optional note,
and agents log calls, meetings, demos, proposals and site visits with a next
step and follow-up date.

This closes the gap flagged in v1.1 — you can now see *what the agent has been
doing*, not just where the client sits. Entries cannot be edited after saving,
and stage changes cannot be deleted at all, so it stays a record rather than a
summary written later.

### Other additions

- **Profile pictures.** Resized to 256px in the browser and stored as a data URL
  (there is no object storage on the free tier). Non-images and oversized files
  are rejected.
- **Client industry.** 23 categories, filterable in the client search.
- **Agent type.** Tech Sales Agent, Channel Partner, Reseller, Referral Partner
  and others, chosen when registering an agent.
- **Log drill-down.** Super users click any activity-log row for the full
  record: structured detail, who did it, which company, and their surrounding
  activity — each of which is itself clickable. Company admins see their own
  company's log but cannot drill in, because the detail can span tenants.
- **160 currencies**, up from 17.

---

## Live exchange rates (v1.1)

Rates refresh automatically from **ExchangeRate-API**, which covers every SADC
currency in the table.

1. Get a free key at <https://www.exchangerate-api.com/> (registration required).
2. In Render, add the environment variable `EXCHANGERATE_API_KEY` and redeploy.
3. Settings shows a **Sync now** button and a change report; the background
   worker then refreshes on the interval you set (12 hours by default).

Each currency row is tagged **live** or **manual**, so you can always see which
figures came from the provider and which you typed.

**One currency needs manual attention: ZWG.** The provider publishes Zimbabwe as
`ZWL`, not `ZWG`. Anything the provider does not recognise is reported after
each sync and left on your manual rate rather than guessed at. Set ZWG by hand,
or switch the row to ZWL if that matches your invoices. Note the provider also
flags Zimbabwe as a currency where published rates often diverge from rates
actually available.

**Syncing cannot move commission you have already recorded.** Every payment
stores the rate that applied the moment it was recorded, so a sync affects
future payments only. This is tested, not assumed.

---

## Permanently deleting an agent (v1.1)

Reserved for **super users**. The first admin account is super; admins created
afterwards are not, so a colleague can run the portal day to day without being
able to erase financial history.

The flow deliberately has friction:

1. Open the agent, scroll to the red **Permanently delete** panel.
2. A footprint is shown first — exactly how many clients, payments, payouts and
   notes will be destroyed, and how much collected revenue that represents.
3. **Download their records** takes a full JSON snapshot before anything is
   removed. Do this. Commission history is a financial record, and several SADC
   jurisdictions require you to retain it for years after an agent leaves.
4. You must type the agent's full name to arm the delete button.
5. Deletion cascades through payments, clients, payouts, reviews and sessions.

A line survives in the activity log recording who deleted whom, and what was
destroyed. Nothing else remains.

Blocked by design: deleting yourself, deleting another super user, and deleting
the last remaining administrator. **If you only want to stop someone signing in,
use Suspend instead** — it revokes their sessions immediately and keeps every
record intact.

---

## Filtering the activity log (v1.1)

Filter by who, action, date range and free text across the detail column.
Action supports a `prefix*` wildcard, so `payment*` catches recorded, voided and
restored in one go. The row count always reports the true number of matches even
when the display is capped, and **Export CSV** exports what you are looking at.

---

## Who can do what

Verified by test, not by intention.

| Action | Agent | Admin |
|---|:--:|:--:|
| Create a client | own only | any |
| Move a client Prospect → Qualified → Demo → Proposal | yes | yes |
| Mark a client Won or Lost | yes | yes |
| Edit contact details, monthly value, notes | own only | any |
| See another agent's clients or payments | **no** | yes |
| Record a payment received | **no** | yes |
| Void or restore a payment | **no** | yes |
| Reassign a client to another agent | **no** | yes |
| Delete a client | **no** | only if it has no payments |
| Create or approve a payout | **no** | yes |
| Change commission rules or FX rates | **no** | yes |
| Add a progress note about an agent | **no** | yes |
| Read progress notes written about them | yes | yes |

Agents run their own pipeline end to end without needing approval. An agent
marking a client **Won is a pipeline claim, not a financial event** — no
commission exists until an admin records money actually received. That
separation is the point: it keeps the forecast honest without giving the agent
a lever on money.

---

## Deploying it free, on your own subdomain

Render's free web service wipes its filesystem on every restart and redeploy,
so a SQLite file on Render would lose your commission history without warning.
The database therefore lives on Neon, whose free tier is permanent.

### 1. Create the database (Neon — free)

1. Sign up at <https://neon.tech> and create a project.
2. Copy the connection string. It looks like
   `postgresql://user:password@ep-xxx.eu-central-1.aws.neon.tech/neondb`.

### 2. Push this folder to GitHub

```bash
git init
git add .
git commit -m "StatsPack Tech Sales Agent Portal"
git remote add origin https://github.com/statspackbw/statspack-agent-portal.git
git push -u origin main
```

### 3. Create the web service (Render — free)

New → Web Service → connect the repo. Render reads `render.yaml`, but confirm:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python server.py`
- **Instance type:** Free

Then add three environment variables:

| Key | Value |
|---|---|
| `DATABASE_URL` | your Neon connection string |
| `ADMIN_EMAIL` | e.g. `admin@statspack.co.ls` |
| `ADMIN_PASSWORD` | a strong password you choose |

Set `ADMIN_PASSWORD` **before the first deploy**. If you leave it blank the
portal generates one and prints it to the Render logs once — recoverable, but
awkward. A password you set yourself is not forced to change at first sign-in;
a generated one is.

### 4. Point your subdomain at it

Render → your service → Settings → Custom Domains → add e.g.
`agents.statspack.co.ls`. Render gives you a CNAME target; add that record at
your DNS provider. HTTPS is issued automatically. Custom domains work on the
free plan.

### 5. Add your brand images

Drop `logo.png` and `login.png` into the repository **root** (not `static/`) and
push. The server serves them exactly as EduTrack 360 does. Until then the login
page falls back to a slate gradient and the mokorotlo triangle mark — no broken
images.

---

## Two things about the free tier you should know

**It sleeps.** A free Render service spins down after about 15 minutes of
inactivity, and the next request takes roughly 50 seconds to wake it. An agent
clicking your link at 8am will think it is broken. Free fix: create a job at
<https://cron-job.org> that requests `https://your-domain/api/health` every
10 minutes. That stays inside the free 750 hours/month for a single service.

**Keep your own backups.** Settings → Backup downloads every agent, client,
payment and payout as JSON (never passwords). Do this before each payout run
and keep the file off the server. Neon's free tier has no long retention, and a
commission ledger is not something to hold in one place only.

---

## Running it locally

```bash
python3 server.py
```

With no `DATABASE_URL` it uses a local SQLite file (`portal.db`) and prints the
admin credentials on first boot. Visit <http://localhost:8470>.

To run locally against Postgres:

```bash
DATABASE_URL='postgresql://user:pass@host/db' python3 server.py
```

### Tests

```bash
ADMIN_PASSWORD='TestAdmin!2026' python3 server.py &
python3 test_portal.py http://localhost:8470
```

```bash
python3 test_fx.py                       # no server needed
python3 test_v11.py http://localhost:8470
python3 test_ui.py http://localhost:8470  # needs: pip install playwright
```

515 checks across the fourteen files, covering authentication, role separation, the
commission engine and its edge cases, FX rate locking and syncing, payouts,
suspension, audit filtering, permanent deletion, tenant isolation, per-agent
commission resolution, the client timeline and avatar validation. They pass identically on
SQLite and Postgres. Run them against a **throwaway** database — they create and
destroy agents and payments.

Upgrading needs no manual step. Missing columns are added on boot, indexes are
created only after that migration runs, existing users and clients are moved
into the host company, and an existing admin keeps super rights. Tested by
upgrading a populated v1.1 Postgres database: passwords, payments and commission
totals all survived untouched.

---

## How it is put together

| File | What it does |
|---|---|
| `server.py` | Routing, API endpoints, static serving, HTTP plumbing |
| `core.py` | Auth, sessions, settings, FX, and the commission engine |
| `db.py` | One SQL surface over both SQLite and Postgres |
| `static/app.js` | The whole frontend — router and every screen |
| `static/styles.css` | The StatsPack theme |
| `test_portal.py` | End-to-end tests — auth, permissions, commission engine |
| `test_v11.py` | End-to-end tests — audit filters, deletion, super users |
| `test_fx.py` | Unit tests — rate sync merge logic, with the provider stubbed |
| `test_v12.py` | End-to-end tests — tenancy, per-agent rates, timeline, avatars |
| `test_audit_security.py` | Adversarial — cross-tenant attacks, escalation, orphans |
| `test_edge.py` | Adversarial — injection, traversal, concurrency, integrity |
| `test_v13.py` | End-to-end tests — teamwork, reports, storage, incidents |
| `test_ui.py` | Browser click-through — pages, dialogs, wiring (needs playwright) |
| `mirror.py` | Standby replication, storage checks and incident recording |
| `test_mirror_fidelity.py` | Column-by-column primary vs standby comparison |
| `test_promotion.py` | Failover drill — running the portal on the standby copy |
| `test_failure_modes.py` | Both crash directions: standby down, primary down |
| `test_swap.py` | Failover round trip: Neon → Supabase → back to Neon |
| `test_final_audit.py` | Orphaned records, breaker behaviour, teamwork edge cases |
| `test_v15.py` | Agent search, client deletion, keep-alive quota maths |
| `test_collab_history.py` | Partnership lifecycle and commission attribution |

No web framework. `psycopg2` is the only dependency, and only when you use
Postgres.

### Decisions worth knowing about

**Exchange rates are locked into each payment when it is recorded.** Editing a
rate later changes future payments only. Without this, a currency move would
silently rewrite commission you had already agreed and paid.

**Voiding is reversible, deleting is not offered.** Voiding a payment leaves it
on the record earning nothing, and correctly promotes the next live payment to
be the 60% "first payment". A client with payments against it cannot be
deleted at all — set the stage to Churned instead.

**Changing a commission rule recalculates history.** Rates are applied at read
time rather than frozen per payment, so correcting a mistyped rate fixes every
affected figure. The trade-off is that changing a rule mid-year restates past
statements, so change rules only when the agreement itself changes.

**Failed sign-ins are logged on a separate transaction**, so the record
survives the rollback that follows the rejected request. Activity log →
`login.failed`.

---

## Things this deliberately does not do

- **No email.** New agents get a one-time password shown on screen for you to
  pass on. Adding SMTP or a service like Resend is the natural next step.
- **No invoicing.** It records that a payment arrived; it does not raise the
  invoice.
- **No 2FA yet.** EduTrack 360 has TOTP; the same approach ports across if you
  want it here.
- **No payment-gateway integration.** Payouts are recorded, not executed.
- **Stage is not locked once a client is paying.** An agent can roll a client
  that has paid three months running back to Prospect. It cannot leak money —
  commission comes from recorded payments, not from stage — and the move is now
  recorded on the timeline, but it will still misreport your pipeline.
- **Super rights cannot be granted through the interface.** The first admin
  holds them. To move them, update `users.is_super` in the database directly.
- **Profile pictures live in the database.** Fine at this scale (roughly 30–60KB
  each), but if you ever have hundreds of users, move them to object storage.
- **Deleting a company is not offered.** Delete its agents individually, or
  suspend the company, which signs everyone in it out immediately.

## Before you give agents access

Employment law differs across SADC, and in several member states a
commission-only agent can be reclassified as an employee — the degree of
control you exercise is one of the factors. A portal that sets quotas, records
performance reviews and schedules payouts is evidence of control. Worth a local
legal opinion on how your agent contracts are worded. I am not a lawyer and
this is not legal advice.
