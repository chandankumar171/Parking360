@echo off
REM Windows version. Run from the project root:  scripts\make_migrations_docker.bat
docker run --rm -v "%cd%":/app -w /app python:3.12-slim bash -c "apt-get update -qq && apt-get install -y -qq build-essential pkg-config default-libmysqlclient-dev >/dev/null && pip install -q -r requirements.txt && DJANGO_SETTINGS_MODULE=config.settings.dev SECRET_KEY=build-only DB_NAME=x DB_USER=x DB_PASSWORD=x python manage.py makemigrations accounts core parkings bookings payments reviews"
echo Done. Now commit the new apps\*\migrations\ folders.
