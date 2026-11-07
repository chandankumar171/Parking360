# ParkShare: free deployment guide

Two free paths. Pick one.

| | **Track A: Render + TiDB Cloud** | **Track B: Oracle Cloud Always Free VM** |
|---|---|---|
| Card needed | No | Yes (identity check; Visa or Mastercard, RuPay reportedly not accepted) |
| Setup time | about 45 minutes | about 90 minutes |
| Always on | No: sleeps after 15 minutes idle (a free cron job keeps it awake) | Yes |
| Database | TiDB Cloud Starter (MySQL-compatible, not real MySQL) | Real MySQL 8 in Docker |
| Background jobs | Free cron service calls `/jobs/run/` | Separate scheduler container |
| HTTPS | Automatic (`*.onrender.com`) | Automatic (Caddy + free DuckDNS name) |
| Main risk | Your booking code uses row locks (`select_for_update`); TiDB supports them but you must test a full booking | Oracle sometimes has no free VMs in your region; retry later |

Aiven's free MySQL is being retired (no new free services after 6 Nov 2026), so this guide does not use it. Free tiers change: check each provider's pricing page before relying on it.

Start with Track A for a link today. If the Step 6 smoke test fails on TiDB, or you want something more solid, use Track B.

---

## Step 0: generate and commit the migrations (required)

The project ships **without** `migrations/` folders, so a new database would get no tables and the app would crash on first start. (The start script now stops with a clear message if they are missing.)

Easiest, using Docker (no MySQL needed on your computer):

```bash
bash scripts/make_migrations_docker.sh        # Windows: scripts\make_migrations_docker.bat
```

Or on your computer after the local setup in `HOW_TO_RUN.md`:

```bash
python manage.py makemigrations accounts core parkings bookings payments reviews
```

Check that each of those six apps now has `migrations/0001_initial.py`. They must be committed to Git.

## Step 1: check it works locally

```bash
python manage.py test apps        # all 199 tests should pass
docker compose up --build         # open http://localhost:8000
```

Do not skip this: nothing in this package was run by the person who prepared it.

## Step 2: push to GitHub

```bash
git init
git add .
git status        # .env must NOT be listed; apps/*/migrations/ MUST be listed
git commit -m "ParkShare"
git branch -M main
git remote add origin https://github.com/YOUR-USER/parkshare.git
git push -u origin main
```

Use a private repository. GitHub does not accept your account password: use GitHub Desktop, `gh auth login` or a personal access token.

## Step 3: free accounts needed by both tracks

- **Cloudinary** (required, production refuses to start without it): note Cloud name, API Key, API Secret.
- **Razorpay, Test Mode**: Settings, API Keys, Generate Test Key. Live payments need KYC and are not part of a free demo.
- **Email (optional)**: Brevo free SMTP gives host `smtp-relay.brevo.com`, port 587, a login and an SMTP key; verify a sender address. Without it, password-reset emails only appear in the logs.

---

# Track A: Render + TiDB Cloud

### A1. Free database (TiDB Cloud Starter)
1. Sign up at tidbcloud.com and create a **Starter** instance (AWS Singapore if offered).
2. Click **Connect**, type **Public**. Note host, port `4000`, user (it has a prefix, like `abc123.root`: keep all of it) and **Generate Password**.
3. In the SQL editor run:
```sql
CREATE DATABASE parkshare CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### A2. Deploy on Render
1. Sign up at render.com with GitHub. **New, Blueprint**, pick your repository. Render reads `render.yaml` and asks for the values below.

| Variable | Value |
|---|---|
| `DB_HOST`, `DB_USER`, `DB_PASSWORD` | from TiDB (user includes the prefix) |
| `DB_NAME` | `parkshare` |
| `SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD` | your admin login (strong password) |
| `CLOUDINARY_*` | three Cloudinary values |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | test keys |
| `RAZORPAY_WEBHOOK_SECRET` | leave empty for now |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | Brevo values, or leave empty |
| `SITE_URL` | leave empty for now |

2. Apply. The first build takes several minutes. Open **Logs**: the app waits for the database, runs migrations, collects static files, runs a production check, and starts Gunicorn.
3. Copy your URL (`https://parkshare-xxxx.onrender.com`). In **Environment**: set `SITE_URL` to it, and change `ADMIN_URL` to something private such as `staff-k39x/`. Save (it redeploys).
4. Optional: set the service's **Health Check Path** to `/healthz/`. If the deploy then keeps failing while the logs show Gunicorn running, clear that setting.

### A3. Background jobs and keep-alive (free cron)
The jobs (expire unpaid holds, mark no-shows, auto-complete stays) run when `/jobs/run/` is called. A free cron service does that and also keeps the free site awake.

1. In Render, **Environment**, copy the generated value of `JOBS_TOKEN`.
2. Sign up at cron-job.org, **Create cronjob**: URL `https://YOUR-URL/jobs/run/`, every 5 minutes, and in the advanced settings add the header `X-Jobs-Token` with your token. (A `?token=` in the URL also works, but then the token shows up in logs.)
3. Optional second job every 6 hours with URL `https://YOUR-URL/jobs/run/?reconcile=1` for the payment reconciliation check.

One always-on service uses about 720 of Render's 750 free hours a month. Without the cron job, the first visitor waits about a minute while the site wakes up, and no jobs run while it sleeps.

### A4. Razorpay webhook
Razorpay Dashboard, Settings, Webhooks, Add: URL `https://YOUR-URL/payments/webhook/`, a secret you make up, events `payment.captured`, `payment.failed`, `order.paid`, `refund.processed`, `refund.failed`. Put the same secret in Render as `RAZORPAY_WEBHOOK_SECRET` and save.

### A5. If TiDB gives trouble
If the build fails on `django-tidb`, or `migrate` fails with a database-specific error, first try setting `DB_ENGINE` to `django.db.backends.mysql` in Render. If that still fails, use Track B.

---

# Track B: Oracle Cloud Always Free VM

### B1. Account and VM
1. Sign up at oracle.com/cloud/free. Choose your home region carefully (it cannot change). If you get "Out of host capacity", retry later or try another availability domain.
2. Create an instance: Image **Ubuntu 22.04 or 24.04**; Shape **VM.Standard.A1.Flex (Ampere)** with **2 OCPU / 12 GB** (safe under both versions of Oracle's free allowance). If unavailable, **VM.Standard.E2.1.Micro** (1 GB) works with the swap file the setup script adds but is tight. Keep it marked **Always Free eligible**. Save the SSH key.
3. In the subnet's Security List add ingress rules for TCP **80** and **443** from `0.0.0.0/0`. Note the public IP.

Oracle can reclaim Always Free VMs that stay almost idle for 7 days; a site with real traffic is normally fine.

### B2. Free domain name
At duckdns.org create a subdomain (for example `parkshare`) pointing to the VM's IP, giving `parkshare.duckdns.org`. Without signing up, `YOUR-IP-WITH-DASHES.sslip.io` also works.

### B3. Prepare the server
```bash
ssh -i path/to/key ubuntu@YOUR_IP
git clone https://github.com/YOUR-USER/parkshare.git && cd parkshare
bash deploy/oracle/setup-server.sh
exit                 # log in again so the docker group applies
```
The script installs Docker, adds swap and opens ports 80/443 in the VM's own firewall.

### B4. Configure and start
```bash
ssh -i path/to/key ubuntu@YOUR_IP
cd parkshare
cp deploy/oracle/env.example .env
python3 -c "import secrets;print(secrets.token_urlsafe(64))"    # paste into SECRET_KEY
nano .env                                                       # fill in every value
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f web
```
Open `https://YOUR-DOMAIN`. In this track the `scheduler` container runs the jobs; you do not need the cron job.

### B5. Backups and updates
```bash
crontab -e      # add:   0 2 * * * /home/ubuntu/parkshare/deploy/oracle/backup.sh
cd parkshare && git pull && docker compose -f docker-compose.prod.yml up -d --build    # to update
```
Copy `backups/*.sql.gz` off the server now and then. Set up the Razorpay webhook as in A4 with your domain.

---

# Step 6: smoke test (both tracks)

1. `https://YOUR-URL/healthz/` returns ok.
2. Log in with the super admin account.
3. As an owner (incognito window): create a parking, upload a photo (proves Cloudinary), submit; approve it as super admin.
4. As a driver: book a bay and pay with a Razorpay test card or UPI (proves keys and webhook). Confirm the code appears.
5. As the owner: verify the code, check in and out. Request and approve a refund.
6. Request a password reset (proves email).
7. With the browser console open, if there are no CSP warnings, set `CSP_MODE=enforce`.
8. Track A only: wait 10 minutes and check that the cron job shows success, and that an unpaid hold expires.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Start script says migrations are missing | Do Step 0 and commit `apps/*/migrations/` |
| `Access denied` on TiDB | `DB_USER` must include the prefix; regenerate the password |
| Cannot connect / SSL error | Keep `DB_SSL_MODE=REQUIRED`; check host and port 4000 |
| 400 `DisallowedHost` | `ALLOWED_HOSTS` must include your hostname (Track A already allows `.onrender.com`) |
| CSRF error on login | `CSRF_TRUSTED_ORIGINS` must contain the full `https://...` origin |
| Container exits at start | Read the log; the production check names the misconfiguration |
| Out of memory on Render | Keep `WEB_CONCURRENCY=1` |
| `/jobs/run/` returns 404 | Token missing or wrong; the endpoint is hidden unless the token matches |
| Paid but booking "Awaiting payment" | Webhook URL or secret wrong; see Razorpay webhook logs |
| Track B unreachable | Oracle security list ports 80/443, DNS record, then `docker compose ... logs caddy` |

## Known limits
- Payments work in Razorpay test mode only until you complete Razorpay's business verification.
- Free Render sleeps when idle, and its disk is wiped on restart (photos live in Cloudinary, data in the database).
- Run exactly one scheduler. On Track A do not also set `RUN_SCHEDULER_IN_WEB=1`; the cron job already does the work.
- Render's free plan has no shell, so `seed_demo_data` must be run from your own computer against the online database.
