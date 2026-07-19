# Deployment — Oracle Cloud (VM + Docker Compose)

How to run the GTM Intelligence Platform on an Oracle Cloud Infrastructure (OCI) Compute VM using
Docker Compose, with the ICP library + campaign artifacts persisted in **OCI Object Storage** and the
app protected by a **shared password over HTTPS** (Caddy reverse proxy).

> This is a departure from the "single local Streamlit app" architecture note in `docs/ARCHITECTURE.md`
> — it is an operational overlay (containerisation + external persistence + TLS/auth), not a change to
> the qualification logic. The app runs identically locally (`STORAGE_BACKEND=local`).

## Architecture

```
Internet ──▶ Caddy (:443, TLS + password) ──▶ app (Streamlit :8501, internal)
                                                 └─▶ OCI Object Storage  (ICP library + artifacts)
                                                 └─▶ Anthropic API · Vayne API   (secrets via env)
```

- **app** — the Streamlit container (`Dockerfile`). No auth, not published directly.
- **caddy** — terminates TLS and enforces a single shared password (`basic_auth`).
- **Object Storage** — durable persistence; the container filesystem is treated as ephemeral.
- Secrets (`ANTHROPIC_API_KEY`, `VAYNE_API_TOKEN`) are injected at runtime via `.env` — never baked
  into the image or committed.

---

## 1. Prerequisites

- An OCI tenancy (the **Always Free** ARM Ampere A1 shape is enough).
- `oci` CLI configured locally (optional but handy), or use the Console.
- A domain name pointed at the VM (optional — without one you get a self-signed cert).

## 2. Test the stack locally first

```bash
cp .env.example .env
# set ANTHROPIC_API_KEY / VAYNE_API_TOKEN (or leave blank for mocks); keep STORAGE_BACKEND=local

# generate a password hash for Caddy — IMPORTANT: escape every '$' as '$$' (docker-compose
# interpolates single '$', which corrupts the bcrypt hash and breaks the login):
docker run --rm --entrypoint caddy caddy:2.8 hash-password --plaintext 'choose-a-password' | sed 's/\$/$$/g'
# paste the escaped value into .env as APP_PASSWORD_HASH=... and set APP_USER=admin.
# For a quick local test over plain HTTP (no cert warnings), set APP_DOMAIN=http:// in .env.

docker compose up --build
# open http://localhost  and log in with  admin / your-password
```

`STORAGE_BACKEND=local` writes the library/artifacts under `./data` (bind nothing = ephemeral in the
container; fine for a local smoke test). Switch to `oci` for the real deployment (below).

> **The `$$` escaping is required.** A bcrypt hash contains `$` characters; unescaped, docker-compose
> treats `$2a`, `$14`, … as variables and blanks them, so the password never matches. Symptom: a login
> that always fails and a compose warning like `The "…" variable is not set`.

## 3. Create the Object Storage bucket

Console → **Storage → Buckets → Create Bucket** (e.g. `gtm-intelligence`), standard tier, private.
Note your **namespace** (Console → Object Storage shows it; or `oci os ns get`) and **region**.

## 4. Provision the VM

1. **Compute → Instances → Create instance.** Shape `VM.Standard.A1.Flex` (Always Free eligible),
   Ubuntu 22.04. Add your SSH key.
2. **Networking:** in the instance's subnet **Security List** (or an NSG), add ingress rules allowing
   TCP **80** and **443** from `0.0.0.0/0`.
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

## 5. Let the VM use the bucket (instance principals — no keys on disk)

This lets the app authenticate to Object Storage using the VM's identity, so **no OCI keys live in the
container**.

1. **Identity → Dynamic Groups → Create.** Rule (use your VM's OCID):
   ```
   ANY {instance.id = 'ocid1.instance.oc1..aaaa...'}
   ```
   (or match by compartment: `ALL {instance.compartment.id = 'ocid1.compartment...'}`).
2. **Identity → Policies → Create** in the bucket's compartment:
   ```
   Allow dynamic-group <your-dynamic-group> to manage objects in compartment <compartment-name> where target.bucket.name = 'gtm-intelligence'
   Allow dynamic-group <your-dynamic-group> to read buckets in compartment <compartment-name>
   ```

> Alternative (no instance principals): set `OCI_AUTH=config_file`, mount `~/.oci` into the container
> (uncomment the volume in `docker-compose.yml`), and set `OCI_CONFIG_FILE=/home/appuser/.oci/config`.

## 6. Deploy

```bash
git clone <your-repo> gtm && cd gtm
cp .env.example .env
```

Edit `.env`:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
VAYNE_API_TOKEN=...
STORAGE_BACKEND=oci
OCI_BUCKET=gtm-intelligence
OCI_REGION=eu-frankfurt-1
OCI_AUTH=instance_principal
# OCI_NAMESPACE=            # optional; auto-detected

APP_DOMAIN=leads.example.com     # real domain -> automatic Let's Encrypt
APP_USER=admin
APP_PASSWORD_HASH=<escaped hash from the command below>
```

Generate the **escaped** password hash and paste it as `APP_PASSWORD_HASH` (the `| sed` doubles every
`$` — see §2):

```bash
docker run --rm --entrypoint caddy caddy:2.8 hash-password --plaintext 'a-strong-password' | sed 's/\$/$$/g'
```

Then:

```bash
docker compose up -d --build
docker compose logs -f
```

Point your domain's DNS **A record** at the VM's public IP. With a real `APP_DOMAIN` (and 80/443
reachable), Caddy fetches a Let's Encrypt certificate automatically. Visit `https://<domain>`, log in,
and confirm the sidebar shows **ICP Workspace** and **Run Campaign**.

### 6b. Purchased certificate (current production setup)

`gtmintelligence.space` does **not** use Let's Encrypt. It serves a purchased SSL.com certificate,
installed manually:

- `certs/` (gitignored) holds `gtmintelligence.space.fullchain.pem` (leaf + SSL.com intermediate,
  root deliberately omitted) and `gtmintelligence.space.key` (0600).
- `docker-compose.yml` mounts `./certs:/etc/caddy/certs:ro`.
- `.env` sets `CADDY_TLS=tls /etc/caddy/certs/gtmintelligence.space.fullchain.pem /etc/caddy/certs/gtmintelligence.space.key`.

An explicit `tls <cert> <key>` puts Caddy in **manual certificate mode**: it will not contact Let's
Encrypt, will **never renew this cert, and will never warn as expiry approaches** (certmagic skips
unmanaged certs before any expiry check). Renewal is entirely on us.

**Current cert expires 2027-01-30.** Set a calendar reminder for early January 2027.

Renewal:

```bash
# 1. Drop the new leaf + intermediate in place (leaf FIRST; the SSL.com .crt ships with no
#    trailing newline, so a naive `cat` glues the PEMs together -- normalise via openssl):
openssl x509 -in new.crt      -out /tmp/leaf.pem
openssl x509 -in new-inter.pem -out /tmp/inter.pem
cat /tmp/leaf.pem /tmp/inter.pem > certs/gtmintelligence.space.fullchain.pem

# 2. Reload. --force is MANDATORY: only the cert files changed, not the Caddyfile, so a plain
#    `caddy reload` sees identical config JSON, logs "config is unchanged" and does nothing --
#    silently continuing to serve the OLD cert.
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile --force

# 3. Verify the SERIAL changed (not just the dates):
echo | openssl s_client -connect gtmintelligence.space:443 -servername gtmintelligence.space 2>/dev/null \
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
curl -sS --resolve gtmintelligence.space:443:127.0.0.1 -o /dev/null \
  -w 'http=%{http_code} tls_verify=%{ssl_verify_result}\n' https://gtmintelligence.space/
# expect: http=401 (basic_auth, no creds given) tls_verify=0 (chain trusted)
```

The cert's SAN also covers `www.gtmintelligence.space`, but that name is currently **NXDOMAIN**. If a
`www` DNS record is ever added, it must also be added to the Caddyfile site block or it will not be
served.

## 7. Verify persistence

Save an ICP in the Workspace, then in Object Storage confirm objects appear under `icp_library/…`.
Run a campaign, click **💾 Save all exports to storage**, and confirm objects under `artifacts/…`.
Restart the stack (`docker compose restart`) — the ICP library is still there.

## 8. Operate

- **Update:** `git pull && docker compose up -d --build`
- **Logs:** `docker compose logs -f app` / `... caddy`
- **Secrets:** keep them only in `.env` on the VM (add a real vault later if desired). `.env` is
  gitignored; never commit it.
- **Backups:** Object Storage is durable; optionally enable versioning on the bucket.
- **Cost guard:** live runs spend Anthropic + Vayne credits. The Vayne step shows the prospect count
  before ordering and requires an explicit credit confirmation; keep the leads cap sensible.

## 9. Notes & limits

- The app is **single-instance** (in-session UI state); don't run multiple replicas behind a load
  balancer expecting shared session state. Persistence (library/artifacts) is shared via Object
  Storage, but active review state is per-browser-session.
- The runtime image excludes Playwright and dev tooling.
- For ZDR/data-residency or a managed alternative, OCI **Container Instances** can run the same image
  (Object Storage persistence carries over unchanged); this runbook covers the VM path chosen for the
  first deployment.
