# How to run Parkin360/ParkShare

Pick **Option A** (run on your computer, best for development and demos) or **Option B** (Docker, closest to production).

---

## Option A: run locally

### 1. Install the prerequisites
| Tool | Version | Notes |
|---|---|---|
| Python | 3.11 or 3.12 | tick "Add Python to PATH" on Windows |
| MySQL Server | 8.0 | remember the root password you set |
| (optional) Git | any | only if you clone instead of unzipping |

### 2. Unzip and open a terminal in the project folder
The folder that contains `manage.py`.

### 3. Create a virtual environment and install the packages
```bash
python -m venv venv
# Windows (PowerShell):  venv\Scripts\Activate.ps1      Windows (cmd):  venv\Scripts\activate.bat
# macOS / Linux:         source venv/bin/activate
pip install -r requirements.txt
```
If `mysqlclient` fails to install:
* **Windows:** use Python 3.11 or 3.12 and run `pip install --only-binary :all: mysqlclient`; if that still fails use Option B (Docker).
* **Ubuntu/Debian:** `sudo apt install build-essential pkg-config default-libmysqlclient-dev`, then repeat.
* **macOS:** `brew install mysql-client pkg-config`, then `export PKG_CONFIG_PATH="$(brew --prefix mysql-client)/lib/pkgconfig"`, then repeat.

### 4. Create the database
Open the MySQL shell (`mysql -u root -p`) and run:
```sql
CREATE DATABASE parkshare CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'parkshare'@'%' IDENTIFIED BY 'parkshare';
GRANT ALL ON parkshare.* TO 'parkshare'@'%';
GRANT ALL ON test_parkshare.* TO 'parkshare'@'%';   -- lets the test suite create its own database
FLUSH PRIVILEGES;
```

### 5. Create your settings file
```bash
cp .env.example .env          # Windows: copy .env.example .env
```
Open `.env` and set at least:
```
SECRET_KEY=any-long-random-text
DEBUG=True
DB_NAME=parkshare
DB_USER=parkshare
DB_PASSWORD=parkshare
DB_HOST=127.0.0.1
SUPERADMIN_EMAIL=you@example.com
SUPERADMIN_PASSWORD=Choose-A-Strong-Password-1
```
Optional now, needed to test payments: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` (free **test** keys from the Razorpay dashboard).
Cloudinary keys are optional in development (photos are saved in the local `media/` folder).

### 6. Create the database tables  (**do not skip: migrations are not pre-generated**)
```bash
python manage.py makemigrations accounts core parkings bookings payments reviews
python manage.py migrate
```
`migrate` also creates your superadmin account from `.env`. Keep the new `migrations/` folders; commit them if you use Git.

### 7. (Recommended) load demo data
```bash
python manage.py seed_demo_data
```
This creates 3 owners, 14 drivers, 8 parkings and a few months of bookings, payments, refunds and reviews, and prints the logins.
Re-run with `--reset` to rebuild it or `--clear` to remove it. (It refuses to run unless `DEBUG=True`.)

### 8. Start the server
```bash
python manage.py runserver
```
Open **http://127.0.0.1:8000/**

### 9. Log in
| Role | Email | Password |
|---|---|---|
| Superadmin | the `SUPERADMIN_EMAIL` from `.env` | the `SUPERADMIN_PASSWORD` from `.env` |
| Owner (demo) | `owner1@demo.parkshare.test` | `Demo@12345` |
| Driver (demo) | `driver1@demo.parkshare.test` | `Demo@12345` |

Password-reset emails are printed in the terminal window (no mail server needed in development).

### 10. Run the automated tests
```bash
python manage.py test apps
```
The suite has ~195 tests and takes several minutes (it creates its own `test_parkshare` database, then removes it). All tests should pass.
To re-run just one file while fixing something: `python manage.py test apps.bookings.tests.test_lifecycle`.
If anything fails, copy the failing test name and its traceback (the lines starting at `ERROR:` or `FAIL:`) when asking for help.

### 11. Try the full booking flow
1. Superadmin: open **Verification queue** and approve a pending parking (or switch verification off in **Platform settings**).
2. Driver: **Find parking**, open a parking, pick a time, enter a plate, **Hold this bay**, then **Pay**. Without Razorpay keys the Pay button says payments are not set up; add test keys to `.env` and restart.
3. Owner: **Verify code**, enter the 6-character code, check in, later check out (overtime is charged if late).
4. Owner or driver: try a refund; superadmin: see **Payments & refunds** and **Owner payouts**.

### Background jobs (holds expiring, no-shows, auto-complete)
In a second terminal with the same virtual environment: `python manage.py run_scheduler`. Without it, unpaid holds still lapse correctly (checked on every request) but no-shows and auto-completion will not run.

---

## Option B: run with Docker (web + MySQL + scheduler)
1. Install Docker Desktop.
2. `cp .env.example .env` and edit it: set `SECRET_KEY` (32+ characters), `DEBUG=False`, `ALLOWED_HOSTS=localhost`, `CSRF_TRUSTED_ORIGINS=http://localhost:8000`, `SECURE_SSL_REDIRECT=False`, `DB_PASSWORD`, `DB_ROOT_PASSWORD`, the `SUPERADMIN_*` values, and **Cloudinary keys (required in production mode)**.
3. Generate migrations once (needs the local setup from Option A steps 3 to 6), then:
4. `docker compose up -d --build`, then open http://localhost:8000/
5. Logs: `docker compose logs -f web scheduler`

For a real server (HTTPS, Razorpay webhook, backups) follow **docs/DEPLOYMENT.md**.

---

## Common problems
| Problem | Fix |
|---|---|
| `Access denied for user` | `DB_USER` / `DB_PASSWORD` in `.env` do not match step 4 |
| `Table ... doesn't exist` | you skipped step 6 |
| `django.db.utils.OperationalError: (2002)` | MySQL is not running, or `DB_HOST` is wrong |
| `Error: That port is already in use` | `python manage.py runserver 8001` |
| `SECRET_KEY` / `config` error on start | `.env` is missing or not in the folder containing `manage.py` |
| Pay button says payments are not set up | add Razorpay **test** keys to `.env`, restart the server |
| Tests cannot create `test_parkshare` | run the `GRANT ... test_parkshare.*` line from step 4 |
