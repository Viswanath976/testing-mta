# KumoMTA Automation — Live Validation Checklist (Testing Server)

Follow this top to bottom on your **testing server** (not the 1016-IP box `198.23.177.154`).
Each step: **click → expect → verify → PASS / FAIL → commands → common failures.**
If a step FAILs, stop, capture the output, fix, re-test, then continue.

**Legend**
- `<BOX>` = your testing server IP
- `<HUB>` = la412 hub IP (`209.127.37.246`)
- App = the KumoMTA Automation desktop app (`kumo-automation`)
- "SSH box" = `ssh root@<BOX>` · "SSH hub" = `ssh root@<HUB>`

**Before you start**
- [ ] App launches: `.\.venv\Scripts\Activate.ps1` then `kumo-automation`
- [ ] You can `ssh root@<BOX>` and `ssh root@<HUB>` manually
- [ ] la412 can reach `<BOX>:8000`, and `<BOX>` can reach `<HUB>:3100` (Loki) — see Step 0
- [ ] Rotate the previously-exposed SMTP password before using it live

---

## STEP 0 — Reachability preflight (do this first)

**Commands**
```bash
# From the BOX, can it reach the hub's Loki?
ssh root@<BOX> "curl -s -o /dev/null -w '%{http_code}\n' --max-time 8 http://<HUB>:3100/ready"
# From the HUB, can it reach the box's metrics port? (after deploy this returns 200)
ssh root@<HUB> "curl -s -o /dev/null -w '%{http_code}\n' --max-time 8 http://<BOX>:8000/metrics"
```
- **PASS:** Loki `/ready` returns `200`/`ready`; box reachable.
- **FAIL:** timeouts or connection refused.
- **Common failures:** cloud firewall/security group blocks the port; `ufw` on the box blocks `<HUB>` → `8000`; wrong hub IP.

---

## STEP 1 — Create Project

- **Click:** `＋ New Testing Server` → on Step 1 (Project), type a project name (e.g. `NutriFlora`).
- **Expect:** if no projects exist, the name field is shown directly.
- **Verify:** after Finish, the left tree shows `📁 NutriFlora`.
- **PASS:** project appears in the tree.
- **FAIL:** nothing appears / error dialog.
- **Common failures:** duplicate project name (blocked with a message) — pick a unique name.

---

## STEP 2 — Create Testing Server (wizard)

- **Click:** through the 7 steps:
  1. **Project** → NutriFlora
  2. **Environment** → **Testing**
  3. **Server Details** → name, **SSH host = `<BOX>`** (a pasted `ssh root@<BOX>` is auto-cleaned), shared SMTP password, DKIM selector `s1`, tick **Register with central monitoring**
  4. **Import IPs** → CIDR/CSV/Manual (e.g. `<BOX-range>/30,yourdomain.com`)
  5. **Assign Domains** → one per range
  6. **Hostnames & Users** → pick a preset (`s{n}` / `smtp{n}`)
  7. **Preview** → check the **Import Summary** (0 duplicates, 0 invalid, 0 missing) → **Finish**
- **Expect:** you land on the **Server Overview** with stat cards (Environment, SSH, IPs, Domains, SMTP Users, Status), a "Next step" banner, and a Readiness %.
- **Verify:** IP table lists your IPs with hostnames/usernames; double-click a row → details dialog.
- **PASS:** server saved, Overview shows correct IP/domain counts, no "duplicates" warning.
- **FAIL:** Finish does nothing, or duplicates/invalid flagged.
- **Common failures:** invalid IP/CIDR, duplicate usernames/hostnames, missing domain — the wizard blocks with the exact reason; fix and re-preview.

---

## STEP 3 — Configure the monitoring hub (do this BEFORE deploy)

> This matters: the metrics endpoint only binds `0.0.0.0:8000` when a hub is configured. Do it before the real deploy so `init.lua` is generated correctly.

- **Click:** select the server → **Monitoring Setup** → fill:
  - Hub SSH host = `<HUB>`, user `root`
  - Prometheus file_sd dir = `/opt/monitoring/prometheus/targets`
  - Prometheus URL = `http://127.0.0.1:9090`
  - Loki push URL = `http://127.0.0.1:3100/loki/api/v1/push`
  - **Metrics scrape source IP** = `<HUB>` (the IP Prometheus scrapes from)
  - Prometheus reload = **docker**
  → **Save hub**
- **Verify (in the Preview at Step 5):** `init.lua` shows `listen = '0.0.0.0:8000'` and `trusted_hosts = { ..., '<HUB>' }`.
- **PASS:** "Monitoring hub settings saved"; preview shows `0.0.0.0:8000`.
- **FAIL:** preview still shows `127.0.0.1:8000`.
- **Common failures:** server's "Register with central monitoring" not ticked (Server Settings → enable), or hub not saved.

---

## STEP 4 — Server Setup (prerequisites)

- **Click:** **Server Setup** (enter SSH password when prompted).
- **Expect:** prerequisite rows appear with PASS/WARN/FAIL.
- **Verify:** Ubuntu / SSH / Internet / Firewall / Ports / Disk / Memory / CPU all evaluated; KumoMTA line shows installed version or "not installed".
- **PASS:** no **FAIL** rows (WARN on firewall/hostname is acceptable).
- **FAIL:** Ubuntu = fail (not Ubuntu), Disk/Memory = fail (too small), Internet = fail.
- **Commands (cross-check):**
  ```bash
  ssh root@<BOX> ". /etc/os-release; echo \$VERSION_ID; nproc; free -m | awk '/Mem/{print \$2\"MB\"}'; df -Pk /opt | awk 'NR==2{print int(\$4/1024)\"MB free\"}'"
  ```
- **Common failures:** no internet to `openrepo.kumomta.com` (needed for install); `/opt` nearly full; not Ubuntu.

---

## STEP 5 — Install KumoMTA (only if "not installed")

- **Click:** **View install script** (review it), then **Install KumoMTA** → confirm.
- **Expect:** streamed install log; on success it re-checks and shows a version.
- **Verify:**
  ```bash
  ssh root@<BOX> "/opt/kumomta/sbin/kumod --version; systemctl is-enabled kumomta"
  ```
- **PASS:** version prints; service `enabled`.
- **FAIL:** install log ends in an apt/repo error; no `kumod` binary.
- **Common failures:** wrong/unreachable KumoMTA repo URL (verify against KumoMTA docs), apt lock held, no internet. *(If already installed, skip this step.)*

---

## STEP 6 — Preview Generated Configuration

- **Click:** **Preview Generated Configuration**.
- **Expect:** dropdown with `init.lua`, `sources.toml`, `queues.toml`, `ip_map.toml`, `smtp-auth.json`; content shows instantly (no SSH).
- **Verify in the content:**
  - `init.lua`: `rsa_sha256_signer`, `selector = 's1'`, `msg:dkim_sign(...)`, `0.0.0.0:8000`, trusted `<HUB>`
  - `sources.toml`: one `source_address` per IP
  - `queues.toml`: one `tenant-<ip> → egress_pool pool-<ip>`
  - `smtp-auth.json`: `users`, `passwords`, `domains` maps
- **PASS:** all 5 files present and correct.
- **FAIL:** a file missing or empty.
- **Common failures:** none expected (local generation) — if a Jinja error appears, capture it.

---

## STEP 7 — Pre-Deployment Validation

- **Click:** **Pre-Deployment Validation** (enter SSH password).
- **Expect:** a **Ready for deployment** or **Deployment blocked** verdict, with structural + SSH/KumoMTA rows.
- **Verify:** Unique IPs/usernames/hostnames/pools/tenants = PASS; DNS syntax = PASS; SSH connectivity = PASS; KumoMTA installed = PASS.
- **PASS:** verdict = **Ready for deployment**.
- **FAIL:** verdict = **Deployment blocked** (reasons listed).
- **Common failures:** KumoMTA not installed (do Step 5), duplicate pools/tenants (from duplicate IPs), SSH auth failure.

---

## STEP 8 — Dry-Run Deploy

- **Click:** ensure **Dry-run** is ticked → **Deploy →**.
- **Expect:** progress checklist runs **Connect → Generate → Validate** then stops (no changes made); report opens.
- **Verify:** no "restart" happened; server config unchanged.
- **PASS:** Generate + Validate = OK; "Completed (dry-run)".
- **FAIL:** validate error.
- **Common failures:** a Lua error in init.lua (capture the validate output).

---

## STEP 9 — Real Deploy

- **Click:** untick **Dry-run** → **Deploy →** (enter SSH password).
- **Expect:** checklist Connect → Backup → Upload → **dkim-keys** → Validate → Restart → Verify listeners → Health → (Monitoring).
- **Verify on the box:**
  ```bash
  ssh root@<BOX> "systemctl is-active kumomta; ss -ltnp | grep -E ':25|:587|:8000'; ls -l /opt/kumomta/etc/dkim/; /opt/kumomta/sbin/kumod --validate --policy /opt/kumomta/etc/policy/init.lua --user kumod && echo VALIDATE_OK"
  ```
- **PASS:** `kumomta active`; `:587` and `:8000` listening; DKIM `.key` files present (one per domain); VALIDATE_OK.
- **FAIL:** service not active, or rollback happened.
- **Common failures:** validate fails → **auto-rollback** restores the backup (check the report); DKIM key path mismatch; `kumod` user can't read the key (perms) — the deploy sets `chmod 600` + `chown kumod`.

---

## STEP 10 — DNS: Generate, Verify, Export

- **Click:** **DNS Records** → wait for generation → **Verify live DNS** → **Export (CSV + Excel + TXT + DKIM)…**
- **Expect:** records table for A/MX/SPF/DKIM/DMARC/PTR; verify shows PASS/WARN/FAIL per record; export writes `dns-records.csv`, `.xlsx`, `.txt`, and `dkim-<domain>.key`.
- **Verify the DKIM public matches the server key:**
  ```bash
  # public key the app published (from export) vs the private key on the box — the selector record must resolve once you add it:
  dig +short TXT s1._domainkey.<yourdomain>
  ssh root@<BOX> "ls -l /opt/kumomta/etc/dkim/<yourdomain>.key"
  ```
- **PASS:** records generated; exports written; after you publish them, SPF/DKIM/DMARC verify = PASS.
- **FAIL:** DKIM/PTR = FAIL before you've published (expected) → publish records at your DNS/host, re-verify.
- **Common failures:** PTR must be set at the **IP provider** (marked "review"); DNS propagation delay; SPF needs merging (the app merges automatically).

---

## STEP 11 — SMTP Test (AUTH, STARTTLS, and Send)

- **Click:** **SMTP Test** → pick an IP/user → **Verify AUTH + STARTTLS**. Then enter a recipient → **Send Test Email** → confirm.
- **Expect:** rows for SMTP AUTH, STARTTLS, MAIL FROM, RCPT TO, DATA, Message-ID, Sending IP, Return-Path, Delivery response, Time.
- **Verify on the box:**
  ```bash
  ssh root@<BOX> "cd /var/log/kumomta && ls -t | head; tail -n 20 \$(ls -t | head -1)"
  ```
- **PASS:** AUTH = ok, STARTTLS = yes; on Send: MAIL FROM/RCPT/DATA all `2xx`, a Message-ID returned, and the recipient receives it.
- **FAIL:** AUTH fail (bad password), STARTTLS no, or DATA `4xx/5xx`.
- **Common failures:** wrong SMTP password; sender domain not allowed for the account; recipient MX rejects an unsigned/unaligned message (publish DNS first).

---

## STEP 12 — Health Check

- **Click:** **Server Health**.
- **Expect:** rows for service, listeners, metrics endpoint, submission port, **CPU, memory, disk**, SMTP AUTH+STARTTLS, queue, return-path, DNS.
- **PASS:** service/listeners/metrics/CPU/memory/disk/smtp-auth all PASS.
- **FAIL:** any core row FAIL.
- **Common failures:** metrics endpoint not 200 (init.lua not deployed / not restarted); high load/low disk warnings.

---

## STEP 13 — Monitoring: Install (forwarder + Prometheus target)

- **Prep:** **Monitoring Setup → Import forwarder source…** → select the folder with your **production Python Loki forwarder** (script, `requirements.txt`, and its `.service` if any). Label should read "Forwarder source staged: …".
- **Click:** **Install Monitoring** (enter box + hub SSH passwords).
- **Expect:** log shows forwarder uploaded, deps installed, config written, systemd enabled+started, Prometheus target registered.
- **Verify:**
  ```bash
  ssh root@<BOX> "systemctl is-active kumo-loki-forwarder; journalctl -u kumo-loki-forwarder -n 20 --no-pager"
  ssh root@<HUB> "ls -l /opt/monitoring/prometheus/targets/ | grep -i <server>"
  ```
- **PASS:** forwarder `active`; target file present on the hub.
- **FAIL:** forwarder not active; "forwarder source not provided".
- **Common failures:** source not staged; forwarder expects a different config path/format than `/etc/kumo-loki-forwarder/config.json` (send me a working config and I'll align it); Python dep missing.

---

## STEP 14 — Monitoring: Verify (every component PASS)

- **Click:** **Verify Monitoring**.
- **Expect:** rows: **Forwarder service**, **Prometheus target**, **Metrics**, **Loki logs**, **Grafana dashboard** — each PASS/WARN/FAIL. The step only completes when the core rows PASS.
- **Verify manually:**
  ```bash
  # Prometheus target UP:
  ssh root@<HUB> "curl -s 'http://127.0.0.1:9090/api/v1/targets' | grep -o '\"health\":\"[a-z]*\"' | sort | uniq -c"
  ssh root@<HUB> "curl -s -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=up{server_name=\"<server>\"}'"
  # Loki has logs for this server:
  ssh root@<HUB> "curl -s 'http://127.0.0.1:3100/loki/api/v1/label/server_name/values'"
  # Grafana healthy:
  ssh root@<HUB> "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/api/health"
  ```
- **PASS:** target `up` value `1`; `server_name` list includes your server; Grafana `200`.
- **FAIL:** target `down`; server not in Loki labels.
- **Common failures:**
  - Target **DOWN** → box `:8000` not reachable from hub (firewall) OR `init.lua` still bound `127.0.0.1` (redeploy after Step 3).
  - **No Loki logs** → forwarder not running, wrong Loki URL (must be `http://<HUB>:3100/...`, not `127.0.0.1`), box can't reach `<HUB>:3100`, or KumoMTA hasn't logged anything yet (send a test email first).
  - Prometheus not reloaded → use reload = **docker** on la412.

---

## STEP 15 — Confirm in Grafana (metrics + the test email in logs)

- **Do:** open Grafana on la412 → your KumoMTA dashboard; use the `display`/`server_name` dropdown to pick this server.
- **Verify:**
  - **Metrics:** panels populate for this server (message counts, connections) and update over ~30–60s.
  - **Logs:** open Explore → Loki → query `{server_name="<server>"}` → you should see KumoMTA log lines, including the reception/delivery of your **Step 11 test email** (search its Message-ID).
- **PASS:** metrics visible + updating; the test email's log line appears in Loki/Grafana.
- **FAIL:** empty panels or no logs.
- **Common failures:** dropdown label mismatch (`display` label) — set the Grafana label in Server Settings/monitoring; time range too narrow; send another test email and refresh.

---

## STEP 16 — Promote to Production

- **Click:** **⭐ Promote to Production** → confirm.
- **Expect:** environment flips to Production **in place** (server not duplicated); tree moves it under Production; next step becomes "Generate Production DNS".
- **PASS:** env = Production; Promote button hidden afterward.
- **FAIL:** duplicate server created / env unchanged.
- **Common failures:** none expected.

---

## STEP 17 — Production DNS → STEP 18 — Production Deploy

- **Click:** **DNS Records** (now labeled Production) → export. Then **Deploy →** (real).
- **Verify:** same as Steps 9–10 but on the production profile.
- **PASS:** prod config validated, restarted, DKIM keys present; prod DNS exported.
- **FAIL:** validate/restart failure → auto-rollback.
- **Common failures:** same as Step 9.

---

## STEP 19 — Deployment History → STEP 20 — Dashboard → STEP 21 — Download Center

- **History:** open **Deployment History** → rows show When / Type / Status / Operator / OS user / Version (config hash) / Report. Double-click → opens the HTML report.
  - **PASS:** each deploy recorded with correct type (Dry Run / Production) and status.
- **Dashboard:** click **🏠 Dashboard** → cards update (Servers, Production, Total IPs, Domains, Healthy, Pending*, Production Ready, Last Deployment); Recent Deployments lists your runs.
  - **PASS:** counts match reality; Last Deployment shows your prod deploy.
- **Download Center:** **Download Center** → tick all → **Download to folder…** → check the folder has `config/`, `dns/` (CSV+Excel+TXT+DKIM keys), `reports/`, `logs/`.
  - **PASS:** all artifacts exported.

---

# FINAL ACCEPTANCE CHECKLIST — production-ready

The server is production-ready only when **every** box is checked:

**Provisioning & config**
- [ ] Testing server created entirely from the app (no manual policy editing)
- [ ] KumoMTA installed (or already present) and service `active` + `enabled`
- [ ] `init.lua`, `sources.toml`, `queues.toml`, `ip_map.toml`, `smtp-auth.json` deployed
- [ ] `kumod --validate` = OK on the deployed config
- [ ] Per-IP egress verified (source_address per IP) and tenant→pool routing present

**DKIM & DNS**
- [ ] DKIM private keys present at `/opt/kumomta/etc/dkim/<domain>.key`
- [ ] Published DKIM public key matches the server key; **mail is DKIM-signed**
- [ ] A / MX / SPF / DKIM / DMARC generated; PTR requested at the IP provider
- [ ] DNS verification = PASS after publishing; exports (CSV/Excel/TXT) produced

**SMTP**
- [ ] AUTH + STARTTLS = PASS
- [ ] Test email sent: MAIL FROM / RCPT TO / DATA all 2xx, Message-ID returned, delivered
- [ ] Return-Path / Envelope-From correct

**Health**
- [ ] CPU / Memory / Disk / Queue / SMTP / Ports / Return-Path = PASS

**Monitoring (all must PASS)**
- [ ] **Your** production forwarder installed and `active` (not an alternate)
- [ ] Prometheus target = **UP**; metrics scraped
- [ ] Loki receiving logs for this server
- [ ] Grafana shows the server; metrics panels update
- [ ] The test email appears in Grafana/Loki logs
- [ ] Monitoring step marked complete in the app (readiness reflects it)

**Lifecycle & records**
- [ ] Promote to Production worked in place (no duplicate)
- [ ] Production deploy succeeded (or rolled back cleanly on failure)
- [ ] Deployment History records operator, type, status, version
- [ ] Dashboard statistics + Readiness update correctly
- [ ] Download Center exports config, DNS, DKIM keys, reports, logs
- [ ] **No manual server configuration was required after Deploy**, except staging the forwarder package

When all boxes are checked, the same workflow is validated to run against production.

---

### Safety reminders
- Do **not** run a real (non-dry-run) deploy against the 1016-IP box `198.23.177.154`.
- Rotate the previously-exposed SMTP password before/after testing.
- Don't paste passwords into chat — the app prompts for SSH/SMTP credentials directly.
