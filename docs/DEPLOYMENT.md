# Deployment guide

Target: one Linux server (2 GB RAM is plenty to start) running Docker, with HTTPS in front. Everything below uses the files in this repo.

## 1. Accounts you need

| Service | Why | Notes |
|---|---|---|
| Cloudinary | parking photos and avatars | free tier is fine; copy cloud name, API key, API secret |
| Razorpay | payments and refunds | start with **test** keys; switch to live keys only after a full test round trip |
| SMTP provider | password-reset email | any provider (SES, Brevo, Gmail app password...) |
| A domain | HTTPS | point an A record at the server |

## 2. Configure

```bash
git clone <your repo> parkshare && cd parkshare
cp .env.example .env
```

Edit `.env`:

* `SECRET_KEY`: a long random string (`python -c "import secrets; print(secrets.token_urlsafe(64))"`)
* `DEBUG=False`, `ALLOWED_HOSTS=parkshare.example.com`, `CSRF_TRUSTED_ORIGINS=https://parkshare.example.com`, `SITE_URL=https://parkshare.example.com`
* `DB_PASSWORD`, `DB_ROOT_PASSWORD` (compose reads both)
* `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD`: your platform admin login
* Cloudinary keys, SMTP settings, Razorpay keys
* `ADMIN_URL=staff-<random>/`, so the Django admin is not at a guessable address

## 3. Start

```bash
docker compose up -d --build
docker compose logs -f web      # wait for "Listening at: http://0.0.0.0:8000"
```

`web` waits for MySQL, migrates (which also creates the superadmin), collects static files, runs `check --deploy`, then starts Gunicorn.
`scheduler` runs the periodic jobs. Make sure migrations were generated and committed first (`python manage.py makemigrations accounts core parkings bookings payments reviews`).

## 4. HTTPS reverse proxy

The app speaks plain HTTP on port 8000 and trusts `X-Forwarded-Proto`. The simplest option is Caddy (automatic certificates):

```
parkshare.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

Then bind the web port to localhost only (`ports: ["127.0.0.1:8000:8000"]` in `docker-compose.yml`) and open ports 80 and 443 on the firewall.
Nginx works too: proxy to `127.0.0.1:8000` and set `X-Forwarded-Proto $scheme`.

## 5. Razorpay webhook

Dashboard -> Settings -> Webhooks -> Add:

* URL `https://parkshare.example.com/payments/webhook/`
* Secret: put the same value in `RAZORPAY_WEBHOOK_SECRET`, then `docker compose up -d`
* Events: `payment.captured`, `payment.failed`, `order.paid`, `refund.processed`, `refund.failed`

**Test round trip before going live** (test keys): book a bay and pay with a test UPI/card; confirm the code appears; request a refund,
approve it and confirm it reaches `REFUNDED`; run `docker compose exec web python manage.py reconcile_payments`.

## 6. Content-Security-Policy

`CSP_MODE=report-only` never blocks anything. With the browser console open, click through: landing page, search, a parking page, a
Razorpay checkout, the owner QR scanner, both dashboards. If the console shows no CSP warnings, set `CSP_MODE=enforce` and redeploy.

## 7. Backups

```bash
# nightly, from the host's crontab
0 2 * * * docker compose -f /srv/parkshare/docker-compose.yml exec -T db sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction parkshare' | gzip > /srv/backups/parkshare-$(date +\%F).sql.gz
```

Keep at least 7 daily copies off the server. Test a restore once. Photos are on Cloudinary, so they are not part of the database dump.

## 8. Updating

```bash
git pull
docker compose up -d --build        # migrations run automatically on start
docker compose exec web python manage.py check --deploy
```

With more than one `web` replica set `RUN_MIGRATIONS=0` on all but one. Never run more than one `scheduler`.

## 9. Monitoring and routine checks

* `GET /healthz/` returns `{"status": "ok"}` when the database answers (use it for uptime checks and load balancers).
* `docker compose logs scheduler` shows job runs and any `RECONCILE` warnings.
* Weekly: `docker compose exec web python manage.py reconcile_payments` should print `OK`.
* Optional: `pip install sentry-sdk` and set `SENTRY_DSN` for error tracking.

## 10. Troubleshooting

| Symptom | Check |
|---|---|
| Container exits at start | `docker compose logs web`: usually a missing env var, or `check --deploy` naming the problem |
| Payment succeeded but booking is "Awaiting payment" | webhook URL/secret wrong; the browser callback may have failed. The next webhook retry settles it; check the Razorpay webhook log |
| Refund stuck on "Processing" | webhook not arriving, or Razorpay balance too low; see the Razorpay dashboard; `reconcile_payments` flags refunds stuck over a day |
| CSRF error on login | `CSRF_TRUSTED_ORIGINS` must include the `https://` origin |
| Photos not uploading | Cloudinary keys; production refuses to start without them |
| "Too many requests" for real users | rate limits are per user/IP per hour; behind a proxy make sure the real client IP is forwarded |

## 11. Demo data

On a **development** machine only: `python manage.py seed_demo_data` creates three owners, 14 drivers, eight parkings and a few months of
bookings, payments, refunds, reviews and payouts (login details are printed). `--reset` rebuilds it, `--clear` removes it.
The command refuses to run when `DEBUG` is off unless you pass `--force`.
