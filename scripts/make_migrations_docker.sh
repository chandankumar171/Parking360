#!/usr/bin/env bash
# Generates the missing migrations/ folders using Docker, so you do not need MySQL or mysqlclient on your computer.
# Run from the project root:  bash scripts/make_migrations_docker.sh
set -euo pipefail
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c '
  apt-get update -qq && apt-get install -y -qq build-essential pkg-config default-libmysqlclient-dev >/dev/null &&
  pip install -q -r requirements.txt &&
  DJANGO_SETTINGS_MODULE=config.settings.dev SECRET_KEY=build-only DB_NAME=x DB_USER=x DB_PASSWORD=x \
  python manage.py makemigrations accounts core parkings bookings payments reviews &&
  chmod -R a+rw apps'
echo "Done. Now commit the new apps/*/migrations/ folders."
