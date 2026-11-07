# ParkShare

A parking marketplace. **Drivers** search, book a bay for a time window, pay through Razorpay and park with a 6-character code or QR.
**Owners** list parkings, set vehicle types, prices and overtime rates, verify drivers at the gate, handle refunds and watch live
dashboards. A **Superadmin** verifies listings, sets platform rules and fees, and pays owners out.

Django 5.2 / MySQL 8 / Bootstrap 5 / Chart.js / Cloudinary / Razorpay. Server-rendered, no JavaScript framework.

## Features

**Driver**: search by area, vehicle, price, amenities, near me and "available at this time"; live availability and price quote; 10-minute
bay hold; Razorpay checkout; booking code + QR; overtime paid at checkout; cancel and refund where allowed; 1-5 star reviews; reliability
points; saved vehicles; notifications.

**Owner**: five-step parking creation (Google Maps link, vehicle types and prices, overtime rules, amenities, up to 5 Cloudinary photos);
verification workflow with a Verified badge; code check-in and checkout with automatic overtime; refund approvals or owner cancellation;
review replies; driver profiles (points, no-shows, past verdicts); earnings ledger; dashboard (year / month / week / custom range, per
parking) with CSV export.

**Superadmin**: verification queue (approve, reject, suspend); owners and drivers (search, history, enable or disable accounts); all bookings with
their payment, refund and ledger trail; platform settings (verification on or off, platform fee %, points, defaults); payments and refunds;
owner payouts; audit log; platform dashboard. Terms of Use and Privacy Policy pages are included as **drafts** to be reviewed before launch.

> **Deploying for free?** See **[docs/FREE_DEPLOYMENT.md](docs/FREE_DEPLOYMENT.md)** (Render + Aiven MySQL + Cloudinary + Razorpay test mode).
>
> **New here?** Follow **[HOW_TO_RUN.md](HOW_TO_RUN.md)** for a step-by-step guide (Windows, macOS, Linux and Docker). Flow diagrams for presentations are in `docs/diagrams/` (open them at app.diagrams.net).

## Quick start (development)

```bash
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt                        # needs the MySQL client libraries, see below
cp .env.example .env                                   # edit DB_*, SUPERADMIN_*, optional Razorpay test keys

mysql -u root -p -e "CREATE DATABASE parkshare CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE USER 'parkshare'@'%' IDENTIFIED BY 'parkshare';
  GRANT ALL ON parkshare.* TO 'parkshare'@'%'; GRANT ALL ON test_parkshare.* TO 'parkshare'@'%';"

python manage.py makemigrations accounts core parkings bookings payments reviews   # first time; commit the migrations/ folders
python manage.py migrate                               # also creates the superadmin from .env
python manage.py seed_demo_data                        # optional: realistic demo data (prints the logins)
python manage.py runserver
```

MySQL client libraries: Ubuntu `sudo apt install build-essential pkg-config default-libmysqlclient-dev`; macOS `brew install mysql-client pkg-config`;
Windows: `pip install mysqlclient` (prebuilt wheel). Cloudinary keys are optional in development (photos go to `media/`).

## Tests and checks

```bash
python manage.py test apps          # whole suite
make verify                         # migrations committed + system checks + tests (what CI runs)
python manage.py reconcile_payments # do payments, refunds, bookings and the owner ledger agree?
```

## Deployment

`docker compose up -d --build` starts MySQL, the web app and the scheduler. Step by step (HTTPS, Razorpay webhook, backups, updates,
troubleshooting): [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). How it works (state machine, availability engine, payment sequence, money
model, security): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Operations cheat sheet

| Task | Command |
|---|---|
| Periodic jobs (holds, no-shows, auto-complete, reconcile) | `python manage.py run_scheduler` (the `scheduler` container) |
| Verify money integrity | `python manage.py reconcile_payments` |
| Re-create / sync the superadmin from the environment | `python manage.py ensure_superadmin` |
| Demo data | `seed_demo_data` / `--reset` / `--clear` (development only) |
| Health check | `GET /healthz/` |

## Layout

```
config/settings/{base,dev,prod}.py     apps/{accounts,core,parkings,bookings,payments,reviews,dashboard}
templates/   static/   docs/   .github/workflows/ci.yml   Dockerfile   docker-compose.yml   Makefile
```

Key settings (all environment variables, see `.env.example`): `SECRET_KEY`, `DB_*`, `SUPERADMIN_EMAIL/PASSWORD`, `CLOUDINARY_*`,
`RAZORPAY_KEY_ID/KEY_SECRET/WEBHOOK_SECRET`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `ADMIN_URL`, `CSP_MODE`, `AUTO_COMPLETE_HOURS`, `REDIS_URL`.

## Business rules at a glance

* Verification required: listing goes pending until the superadmin approves it (Verified badge). Not required: it goes live straight away without a badge. Switching it off approves everything pending.
* Overtime = `ceil((minutes late - free minutes) / block minutes) x charge`, frozen when the owner starts checkout, paid through Razorpay.
* Check-in window: 15 minutes before start until start + grace. Later is a no-show (no refund, points penalty).
* Refunds go through the Razorpay Refund API; the platform fee and owner share are reversed proportionally.
* Driver points start at 100 and change per the superadmin's settings; owners see a driver's history only if the driver has booked with them.
