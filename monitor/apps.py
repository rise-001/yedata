from django.apps import AppConfig


class MonitorConfig(AppConfig):
    name = 'monitor'
    verbose_name = '余额哨兵'

    def ready(self):
        import sys
        # Run migrations automatically so the DB is always up to date.
        self._ensure_tables()

        # Start the monitor for every web process. Checking only whether the
        # ``gunicorn`` module happens to be imported is brittle across WSGI
        # launchers, while management commands must not create a background
        # thread. ``scheduler.start`` is idempotent within a process.
        management_commands = {
            'check', 'collectstatic', 'makemigrations', 'migrate', 'shell',
            'test', 'createsuperuser',
        }
        command = next((arg for arg in sys.argv[1:] if not arg.startswith('-')), '')
        is_management = command in management_commands
        if not is_management:
            from monitor import scheduler
            scheduler.start()
        else:
            try:
                from monitor import state
                from monitor.storage import load_sites, load_upstream_sessions, load_daily_usage
                saved, webhook = load_sites()
                state.saved_sites = saved
                state.global_webhook = webhook
                state.upstream_sessions.update(load_upstream_sessions())
                state.daily_usage.update(load_daily_usage())
            except Exception:
                pass

    @staticmethod
    def _ensure_tables():
        """Run any pending migrations at startup (safe to call multiple times)."""
        try:
            from django.db import connection
            from django.db.migrations.executor import MigrationExecutor
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if plan:
                from django.core.management import call_command
                call_command('migrate', '--run-syncdb', verbosity=0)
        except Exception:
            # If migration infra itself is broken, let the error surface at first DB use.
            pass
