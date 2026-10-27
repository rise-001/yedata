"""
APScheduler-based background scheduler.
Replaces the setInterval(schedulerTick, 5000) loop from server.js.
Started once via AppConfig.ready().
"""
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)

_scheduler = None


def _run_daily_reports(now: float) -> None:
    from monitor import state
    from monitor.balance import run_daily_report
    from monitor.time_utils import local_parts

    tz_set = {(s.get('timezone') or 'Asia/Shanghai')
              for s in state.saved_sites if s.get('enabled', True) is not False}
    if state.global_webhook:
        for tz in tz_set:
            parts = local_parts(tz)
            # Catch up after a restart or an outage, until the local day ends.
            if int(parts['hour']) < settings.DAILY_REPORT_HOUR:
                continue
            day = f'{parts["year"]}-{parts["month"]}-{parts["day"]}'
            if state.daily_report_sent.get(tz) == day:
                continue
            last_attempt = state.daily_report_last_attempt.get(tz)
            if last_attempt is not None and now - last_attempt < 60:
                continue
            state.daily_report_last_attempt[tz] = now
            try:
                if run_daily_report(timezone=tz):
                    state.daily_report_sent[tz] = day
            except Exception as exc:
                logger.error('Daily balance report failed: %s', exc)

    for cache in (state.daily_report_sent, state.daily_report_last_attempt):
        for tz in list(cache):
            if tz not in tz_set:
                del cache[tz]


def start():
    global _scheduler
    if _scheduler is not None:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning('APScheduler not installed — background checks disabled.')
        return

    from monitor import state
    from monitor.storage import load_sites, load_upstream_sessions, load_daily_usage
    from monitor.adapters import normalize_site, with_saved_secrets

    # Load persistent state on startup.
    saved, webhook = load_sites()
    state.saved_sites = saved
    state.global_webhook = webhook
    state.upstream_sessions.update(load_upstream_sessions())
    state.daily_usage.update(load_daily_usage())

    _scheduler = BackgroundScheduler(timezone='UTC')

    @_scheduler.scheduled_job('interval', seconds=5, id='balance_tick', max_instances=1)
    def tick():
        now = time.time()
        concurrency = settings.CHECK_CONCURRENCY

        # Per-site scheduled checks.
        running_count = len(state.scheduled_running)
        for site in list(state.saved_sites):
            if site.get('enabled', True) is False:
                continue
            sid = str(site.get('id', ''))
            if sid in state.scheduled_running:
                continue
            if running_count >= concurrency:
                break
            interval = normalize_site(site).get('intervalSeconds', 300)
            last = state.scheduled_last_run.get(sid, 0)
            if now - last >= interval:
                _run_site(site, sid)
                running_count += 1

        _run_daily_reports(now)

        # Prune expired admin sessions.
        for token in list(state.sessions):
            if state.sessions[token]['expires_at'] <= now:
                del state.sessions[token]

        # Prune expired alerts (6 h window).
        for key in list(state.alerts):
            if state.alerts[key] + 6 * 3600 <= now:
                del state.alerts[key]

        # Prune expired upstream login blocks.
        for key in list(state.upstream_login_blocked):
            if state.upstream_login_blocked[key] <= now:
                del state.upstream_login_blocked[key]

    def _run_site(site: dict, sid: str):
        """Fire-and-forget a single site check (runs in scheduler thread pool)."""
        state.scheduled_running.add(sid)
        state.scheduled_last_run[sid] = time.time()
        try:
            from monitor.balance import check_and_notify, record_daily_usage
            full_site = with_saved_secrets(site, state.saved_sites)
            result = check_and_notify(full_site)
            record_daily_usage(full_site, result)
            state.scheduled_results[sid] = {**result, 'ok': True,
                                             'checkedAt': _now_iso()}
        except Exception as exc:
            state.scheduled_results[sid] = {'ok': False, 'message': str(exc),
                                             'checkedAt': _now_iso()}
        finally:
            state.scheduled_running.discard(sid)

    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    _scheduler.start()
    logger.info('Balance-watch scheduler started (%d site(s) loaded)', len(state.saved_sites))


def stop():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
