"""
In-memory runtime state shared across the request/scheduler boundary.
All mutable objects here are module-level singletons — safe because Django
runs in a single process (or per-worker with APScheduler running in each).
"""
import threading

# ── Admin sessions  {token: {expires_at: float}} ──────────────────────────
sessions: dict[str, dict] = {}

# ── Brute-force guard  {ip: {count, blocked_until, updated_at}} ───────────
login_attempts: dict[str, dict] = {}

# ── Alert dedup  {key: sent_timestamp_float} ──────────────────────────────
alerts: dict[str, float] = {}

# ── In-flight alert sends (dedup concurrent checks) {key: Future} ─────────
alert_sends: dict[str, object] = {}

# ── Scheduled check state ─────────────────────────────────────────────────
scheduled_results: dict[str, dict] = {}   # site_id -> last result
scheduled_last_run: dict[str, float] = {} # site_id -> epoch float
scheduled_running: set[str] = set()

# ── Upstream session cache  {cache_key: session_dict} ────────────────────
upstream_sessions: dict[str, dict] = {}
upstream_login_promises: dict[str, object] = {}  # concurrent dedup
upstream_login_blocked: dict[str, float] = {}    # blocked until epoch

# ── Daily report dedup  {timezone: day_key} ──────────────────────────────
daily_report_sent: dict[str, str] = {}

# ── Daily usage snapshots  {site_id: {day_key: float}} ───────────────────
daily_usage: dict[str, dict[str, float]] = {}

# ── Saved sites + webhook (primary source of truth is disk; this is cache) -
saved_sites: list[dict] = []
global_webhook: str = ''

_state_lock = threading.Lock()
