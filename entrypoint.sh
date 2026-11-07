#!/usr/bin/env bash
set -euo pipefail

# Stop early with a clear message if the migration files were never generated and committed.
if [ "${RUN_MIGRATIONS:-1}" = "1" ] && [ ! -f apps/accounts/migrations/0001_initial.py ]; then
  echo "ERROR: apps/*/migrations/ folders are missing, so the database tables cannot be created."
  echo "Run on your computer:  python manage.py makemigrations accounts core parkings bookings payments reviews"
  echo "(or scripts/make_migrations_docker.sh), then commit the migrations/ folders and redeploy."
  exit 1
fi

echo "Waiting for database..."
python - <<'PY'
import os, time, MySQLdb
extra = {}
if os.environ.get("DB_SSL_CA"):
    extra["ssl"] = {"ca": os.environ["DB_SSL_CA"]}
elif os.environ.get("DB_SSL_MODE"):
    extra["ssl_mode"] = os.environ["DB_SSL_MODE"]
for attempt in range(60):
    try:
        MySQLdb.connect(host=os.environ.get("DB_HOST", "127.0.0.1"), port=int(os.environ.get("DB_PORT", "3306")),
                        user=os.environ["DB_USER"], passwd=os.environ["DB_PASSWORD"], db=os.environ["DB_NAME"], **extra).close()
        break
    except Exception as exc:
        print(f"  not ready ({exc}); retrying")
        time.sleep(2)
else:
    raise SystemExit("Database never became ready")
PY

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput          # also creates/updates the superadmin from the environment
  python manage.py createcachetable           # shared cache for rate limiting (unused when REDIS_URL is set)
fi
python manage.py collectstatic --noinput
python manage.py check --deploy --fail-level ERROR   # refuse to start with a misconfigured production setup

# Optional: run the scheduler inside this container (single-container hosting). Leave off if you use the /jobs/run/ cron instead.
if [ "${RUN_SCHEDULER_IN_WEB:-0}" = "1" ]; then
  echo "Starting the scheduler inside this container"
  python manage.py run_scheduler &
fi
exec gunicorn config.wsgi:application -c gunicorn.conf.py
