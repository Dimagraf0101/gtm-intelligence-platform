# Deployment — Linux VM + Docker Compose

How to run the GTM Intelligence Platform on a Linux Compute VM using Docker Compose, with the ICP
library + campaign artifacts persisted on **local disk** (a bind-mounted `./data` volume) and the app
protected by a **shared password over HTTPS** (Caddy reverse proxy).

> This is an operational overlay (containerisation + TLS/auth), not a change to the qualification
> logic. The app runs identically to a local `streamlit run app.py`; the container just adds a reverse
> proxy and a persistent data volume.

## Architecture

```
Internet ──▶ Caddy (:443, TLS + password) ──▶ app (Streamlit :8501, internal)
                                                 └─▶ ./data volume  (ICP library + artifacts)
                                                 └─▶ Anthropic API · Vayne API   (secrets via env)
```

- **app** — the Streamlit container (`Dockerfile`). No auth, not published directly.
- **caddy** — terminates TLS and enforces a single shared password (`basic_auth`).
- **Persistence** — local disk. The app writes under `/app/data`, bind-mounted from host `./data`, so
  saved ICPs and campaign output survive container recreates. Back up `./data` to keep it durable.
- Secrets (`ANTHROPIC_API_KEY`, `VAYNE_API_TOKEN`) are injected at runtime via `.env` — never baked
  into the image or committed.

---

## 1. Prerequisites

- A Linux VM with a public IP (any provider; an Always-Free ARM/AMD shape is enough).
- Docker + the Compose plugin installed on the VM.
- A domain name pointed at the VM (optional — without one you get a self-signed cert).

## 2. Test the stack locally first

```bash
cp .env.example .env
# set ANTHROPIC_API_KEY / VAYNE_API_TOKEN (or leave blank for mocks)

# generate a password hash for Caddy — IMPORTANT: escape every '$' as '$$' (docker-compose
# interpolates single '$', which corrupts the bcrypt hash and breaks the login):
docker run --rm --entrypoint caddy caddy:2.8 hash-password --plaintext 'choose-a-password' | sed 's/\$/$$/g'
# paste the escaped value into .env as APP_PASSWORD_HASH=... and set APP_USER=admin.
# For a quick local test over plain HTTP (no cert warnings), set APP_DOMAIN=http:// in .env.

docker compose up --build
# open http://localhost  and log in with  admin / your-password
```

The app writes the library/artifacts under `./data` on the host (bind-mounted at `/app/data`).

> **The `$$` escaping is required.** A bcrypt hash contains `$` characters; unescaped, docker-compose
> treats `$2a`, `$14`, … as variables and blanks them, so the password never matches. Symptom: a login
> that always fails and a compose warning like `The "…" variable is not set`.

## 3. Provision the VM

1. Create the instance (Ubuntu 22.04 or similar). Add your SSH key.
2. **Networking:** in the instance's subnet security list (or firewall/NSG), add ingress rules
   allowing TCP **80** and **443** from `0.0.0.0/0`.
3. SSH in and install Docker:
   ```bash
   sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER && newgrp docker
   ```
4. On Ubuntu, also open the host firewall if enabled:
   ```bash
   sudo iptables -I INPUT -p tcp --dport 80  -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save   # if installed
   ```

## 4. Deploy

```bash
git clone <your-repo> gtm && cd gtm
cp .env.example .env
```

Edit `.env`:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
VAYNE_API_TOKEN=...

APP_DOMAIN=leads.example.com     # real domain -> automatic Let's Encrypt
APP_USER=admin
APP_PASSWORD_HASH=<escaped hash from the command below>
```

Generate the **escaped** password hash and paste it as `APP_PASSWORD_HASH` (the `| sed` doubles every
`$` — see §2):

```bash
docker run --rm --entrypoint caddy caddy:2.8 hash-password --plaintext 'a-strong-password' | sed 's/\$/$$/g'
```

Prepare the persistent data directory (must be writable by the container's `appuser`, uid 10001):

```bash
mkdir -p data && sudo chown -R 10001:10001 data
```

Then:

```bash
docker compose up -d --build
docker compose logs -f
```

Point your domain's DNS **A record** at the VM's public IP. With a real `APP_DOMAIN` (and 80/443
reachable), Caddy fetches a Let's Encrypt certificate automatically. Visit `https://<domain>`, log in,
and confirm the app loads.

### 4b. Purchased certificate (manual TLS)

If you serve a purchased certificate instead of Let's Encrypt (e.g. `gtmintelligence.space` with an
SSL.com cert), install it manually:

- `certs/` (gitignored) holds `<domain>.fullchain.pem` (leaf + intermediate, root deliberately
  omitted) and `<domain>.key` (0600).
- `docker-compose.yml` mounts `./certs:/etc/caddy/certs:ro`.
- `.env` sets `CADDY_TLS=tls /etc/caddy/certs/<domain>.fullchain.pem /etc/caddy/certs/<domain>.key`.

An explicit `tls <cert> <key>` puts Caddy in **manual certificate mode**: it will not contact Let's
Encrypt, will **never renew this cert, and will never warn as expiry approaches** (certmagic skips
unmanaged certs before any expiry check). Renewal is entirely on you — set a calendar reminder.

Renewal:

```bash
# 1. Drop the new leaf + intermediate in place (leaf FIRST; a .crt that ships with no trailing
#    newline glues the PEMs together under a naive `cat` -- normalise via openssl):
openssl x509 -in new.crt       -out /tmp/leaf.pem
openssl x509 -in new-inter.pem -out /tmp/inter.pem
cat /tmp/leaf.pem /tmp/inter.pem > certs/<domain>.fullchain.pem

# 2. Reload. --force is MANDATORY: only the cert files changed, not the Caddyfile, so a plain
#    `caddy reload` sees identical config JSON, logs "config is unchanged" and does nothing --
#    silently continuing to serve the OLD cert.
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile --force

# 3. Verify the SERIAL changed (not just the dates):
echo | openssl s_client -connect <domain>:443 -servername <domain> 2>/dev/null \
  | openssl x509 -noout -dates -serial
```

If the key is replaced too, keep it at 0600 and confirm it pairs with the new cert before reloading:
`openssl x509 -noout -modulus -in <cert> | openssl md5` must equal
`openssl rsa -noout -modulus -in <key> | openssl md5`.

**Verifying TLS — use SNI.** The site block matches a specific hostname and no `default_sni` is set,
so an empty-SNI handshake (`curl https://<ip>`, or `curl -k https://127.0.0.1 -H 'Host: ...'` — a Host
header does *not* set SNI) is rejected with a TLS alert **on a perfectly healthy deploy**. Don't
mistake that for an outage. Verify by name instead:

```bash
curl -sS --resolve <domain>:443:127.0.0.1 -o /dev/null \
  -w 'http=%{http_code} tls_verify=%{ssl_verify_result}\n' https://<domain>/
# expect: http=401 (basic_auth, no creds given) tls_verify=0 (chain trusted)
```

## 5. Verify persistence

Save an ICP in the app, then restart the stack (`docker compose restart`) and confirm it's still
there. On the host you should see files appear under `./data/…`.

## 6. Operate

- **Update:** `git pull && docker compose up -d --build`
- **Logs:** `docker compose logs -f app` / `... caddy`
- **Secrets:** keep them only in `.env` on the VM (add a real vault later if desired). `.env` is
  gitignored; never commit it.
- **Backups:** `./data` holds all persistent state — back it up on a schedule (e.g. `tar`/`rsync` to
  off-box storage). It is the single source of truth for the ICP library and campaign artifacts.
- **Cost guard:** live runs spend Anthropic + Vayne credits. The Vayne step shows the prospect count
  before ordering and requires an explicit credit confirmation; keep the leads cap sensible.

## 7. Notes & limits

- The app is **single-instance** (in-session UI state); don't run multiple replicas behind a load
  balancer expecting shared session state. Persistence lives on the single VM's `./data` volume.
- The runtime image excludes Playwright and dev tooling.
- Persistence is local disk on one VM — durability depends on your VM's disk and your backups. If you
  later need managed/off-box durability, the same image can point at an external object store by
  wiring a storage backend into the persistence path (not enabled in this build).
