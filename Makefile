# Convenience targets (Linux / macOS / WSL). Every target is a plain command you can also run by hand.
.PHONY: install migrate run test check verify seed reseed reconcile up down logs

install:        ## install Python dependencies
	pip install -r requirements.txt

migrate:        ## apply database migrations (also syncs the superadmin from .env)
	python manage.py migrate

run:            ## start the development server
	python manage.py runserver

test:           ## run the whole test suite
	python manage.py test apps

check:          ## Django system checks
	python manage.py check

verify:         ## everything CI runs: migrations committed, checks, tests
	python manage.py makemigrations --check --dry-run
	python manage.py check
	python manage.py test apps

seed:           ## create demo owners, drivers, parkings and bookings (development only)
	python manage.py seed_demo_data

reseed:         ## wipe and rebuild the demo data
	python manage.py seed_demo_data --reset

reconcile:      ## verify payments, refunds and the owner ledger agree
	python manage.py reconcile_payments

up:             ## build and start web + MySQL + scheduler in Docker
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f web scheduler
