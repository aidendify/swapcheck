# SwapCheck

Free, self-hosted substitute-part decision log for local service shops. When the exact part isn’t right, techs log the proposed swap with a photo and specs, check your approved matrix, and get a fast office yes/no — with a timestamped job trail and CSV export.

No signup. No license. One Docker Compose service and a SQLite file. About 15 minutes on a 1GB VPS.

## What it does

- Techs open an on-site swap request (from → to, job_ref, optional photo + spec text)
- Owner-maintained approved-substitute **matrix** (CSV import/export); create-time lookup shows **Matrix: pre-approved** vs **needs office decision**
- Office Approve / Deny / Cancel from the dashboard, or via magic `/d/{token}` (optional SMTP/Twilio decide notify)
- Append-only events; per-job chronological trail; export `requests.csv` + `events.csv`
- `GET /health` → HTTP 200 `{"status":"ok","smtp_configured":false,"twilio_configured":false}` even when SMTP/Twilio unset

## What this is not

- **Not PartPing** — no customer `/p/{token}` ETA / milestone page
- **Not ParKit** — no tomorrow’s-jobs × van par SKU restock checklist
- Not a Jobber / Housecall / ServiceTitan API, distributor EDI, or Stripe product

## Privacy

Self-hosted. You run the box; the owner is the data controller for crew contact fields and uploaded photos. No Stripe, no bundled SMS numbers, no third-party analytics SaaS. Data lives in your SQLite file and `/data/uploads` on the Compose volume.

## 15-minute Ubuntu VPS install

Documented on **Ubuntu 22.04 / 24.04**. About 15 minutes.

**Debian 13:** do **not** run the Ubuntu `docker-ce` recipe below on Debian. Use the distro packages instead:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo usermod -aG docker "$USER"
```

Log out and back in (or `newgrp docker`). On Debian, start the stack with `docker-compose` (hyphen) if `docker compose` is not available.

**Amazon Linux:** not documented yet. Use Ubuntu or Debian.

### 1. Install Docker Engine and the Compose plugin (Ubuntu only)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo ${UBUNTU_CODENAME:-$VERSION_CODENAME}) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and back in (or run `newgrp docker`) so `docker` works without `sudo`.

### 2. Clone, configure, start

```bash
git clone https://github.com/aidendify/swapcheck.git
cd swapcheck
cp .env.example .env
```

Edit `.env` and set at least `SECRET_KEY`, `OWNER_PASSWORD`, `TECH_PASSWORD`, `BUSINESS_NAME`, and `PUBLIC_BASE_URL` (no trailing slash — e.g. `http://YOUR_VPS_IP:8080`). Leave `SMTP_*`, Twilio, and `MARKETING_URL` empty unless configured. Optional: `OFFICE_EMAIL` / `OFFICE_PHONE` for decide notify.

```bash
docker compose up --build -d
```

(On Debian, `docker-compose up --build -d` if the Compose plugin is not installed.)

The app binds `0.0.0.0:8080` in the container. Compose maps host `8080:8080`. SQLite lives on the `swapcheck-data` volume at `/data/swapcheck.db`; photos under `/data/uploads`.

### 3. Smoke test

Use this `.env` for a first pass (Verifier values). Production should use a real `SECRET_KEY` and passwords.

```
OWNER_PASSWORD=testpass
TECH_PASSWORD=techpass
BUSINESS_NAME=Harbor HVAC
PUBLIC_BASE_URL=http://127.0.0.1:8080
MARKETING_URL=
SECRET_KEY=change-me
MAX_UPLOAD_MB=5
```

Leave all `SMTP_*` and Twilio vars unset.

1. Healthcheck:

   ```bash
   curl -sf http://127.0.0.1:8080/health
   ```

   Expected: JSON containing `"status":"ok"`, `"smtp_configured":false`, `"twilio_configured":false`, HTTP 200.

2. Owner login with `testpass`. Import `sample-matrix.csv` (Matrix → Import). Add a tech under People.

3. Tech login with `techpass` → **New swap** with photo + spec text. Confirm matrix hit/miss vs sample (e.g. `CAP-45/5` → `CAP-45/5-ALT` is pre-approved).

4. Owner Approve one and Deny one (dashboard and/or magic `/d/{token}`). Same `job_ref` shows both on `/jobs/<job_ref>`.

5. **Export requests.csv** — non-empty with decision columns. Empty `MARKETING_URL` shows no powered-by footer.

## Configuration

| Variable | Purpose |
| --- | --- |
| `PORT` | Documented as 8080. Container always binds gunicorn to `0.0.0.0:8080`. |
| `DATABASE_PATH` | SQLite file. Compose overrides to `/data/swapcheck.db`. |
| `UPLOAD_DIR` | Photo storage. Compose overrides to `/data/uploads`. |
| `SECRET_KEY` | Flask session key. Change on a public VPS. |
| `OWNER_PASSWORD` | Admin / office dashboard. |
| `TECH_PASSWORD` | Shared tech password for create UI. |
| `BUSINESS_NAME` | UI copy. |
| `PUBLIC_BASE_URL` | Absolute base for magic `/d/{token}` — **no trailing slash**. |
| `MAX_UPLOAD_MB` | Photo size cap (default 5). |
| `OFFICE_EMAIL` / `OFFICE_PHONE` | Optional decide notify destinations. |
| `SMTP_*` / `TWILIO_*` | Optional BYO notify. Unset = app still works. |
| `MARKETING_URL` | Optional footer. Leave empty for Verifier. |
