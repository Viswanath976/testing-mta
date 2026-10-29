# KumoMTA Automation — Project Report & Testing Guide
*Covers Phase 1 → Phase 3. Goal: cut KumoMTA server setup from ~1 hour to 5–10 minutes.*

---

# PART A — What has been built

## Architecture (why it's structured this way)

```
src/kumo_automation/
├── core/              ← PURE LOGIC, no Qt. Reused by GUI, CLI, and tests.
│   ├── models.py          Pydantic data model (single source of truth)
│   ├── ssh.py             Paramiko SSH wrapper (run/put/get/exists/mkdirs)
│   ├── inventory.py       Collect facts about a remote box
│   ├── preflight.py       Pre-deploy blockers (kumod installed, IP bindable, port 25…)
│   ├── generators/        Plugin config generators (KumoMTA files + SMTP users)
│   ├── templating.py      Jinja2 renderer (StrictUndefined)
│   ├── templates/*.j2     init.lua / sources / queues / ip_map / report
│   ├── deploy.py          Safe pipeline: generate→backup→upload→validate→restart→rollback
│   ├── dns.py             DNS *validation* (checks live records)
│   ├── dnsgen.py          DNS *generation* (A/MX/SPF/DKIM/DMARC/PTR + DKIM keygen + SPF merge)
│   ├── ipplan.py          CIDR expansion, naming patterns, CSV import, validation, preview
│   ├── healthchecks.py    Post-deploy checks (SMTP AUTH+STARTTLS, queue, return-path, DNS)
│   ├── monitoring.py      Phase 3: Prometheus file_sd + Loki + register/verify on hub
│   └── report.py          HTML deployment report
├── services/          ← Persistence & cross-cutting
│   ├── project_store.py   Save/load customer projects (YAML); secrets stripped to keyring
│   ├── secrets.py         Keyring wrapper (in-memory fallback)
│   ├── app_settings.py    Phase 3: shared monitoring-hub config
│   └── logging_setup.py   loguru
├── ui/                ← PySide6 (Qt). Thin — all real work is in core/.
│   ├── main_window.py     Customer/server tree + all action buttons
│   ├── import_wizard.py   New-server wizard (CIDR/CSV → preview → create)
│   ├── server_edit.py     Edit server form + IP table (no YAML)
│   ├── config_diff.py     Side-by-side config diff (current vs generated)
│   ├── monitoring_dialog.py  Hub config + register/verify
│   └── workers.py         QThread workers (deploy / preview / monitoring) keep UI responsive
├── app.py             GUI entry point   → `kumo-automation`
└── cli.py             Headless CLI      → `kumo-cli`
```

**Design principle:** the GUI never contains logic. Anything the GUI does, the CLI can do too, and tests exercise the same `core/` functions. That's why Phases 2 and 3 plugged in without rewrites.

---

## Phase 1 — MVP (deploy pipeline)

| Capability | Where |
|---|---|
| SSH connect + collect server facts | `core/ssh.py`, `core/inventory.py` |
| Generate KumoMTA config (5 files) | `core/generators/kumomta.py` + `templates/*.j2` |
| **Safe deploy**: generate → backup → upload → `kumod --validate` → restart → verify → **auto-rollback** | `core/deploy.py` |
| DNS validation (A/MX/SPF/DKIM/DMARC/PTR) | `core/dns.py` |
| Basic health checks | `core/healthchecks.py` |
| HTML deployment report | `core/report.py` |
| Save projects, secrets to keyring | `services/project_store.py`, `services/secrets.py` |

**Generated files:** `init.lua`, `sources.toml`, `queues.toml`, `ip_map.toml`, `smtp-auth.json` → deployed to `/opt/kumomta/etc/policy/`.

---

## Phase 2 — five priorities + server-creation redesign

| # | Feature | Where |
|---|---|---|
| 1 | **DNS generation** — full records + DKIM RSA keygen + SPF merge-safety | `core/dnsgen.py` |
| 2 | **Full health checks** — SMTP AUTH+STARTTLS login, queue, return-path, DNS | `core/healthchecks.py` |
| 3 | **Import Wizard** — CIDR (/24,/25…) auto-expanded, assign domains, multiple domains, naming patterns (`s{n}`/`smtp{n}`/`mail{n}`/`mx{n}`/`word`), CSV import, validate-and-prompt, preview totals | `core/ipplan.py`, `ui/import_wizard.py` |
| 4 | **Config preview + side-by-side diff** (current server vs generated) | `ui/config_diff.py` |
| 5 | **Persist projects + deployment history** | `services/project_store.py` |
| + | **Customer/Server management** — Add/Edit/Delete via forms, no YAML | `ui/server_edit.py`, `main_window.py` |

**Server-creation logic (modular, testable):**
- `expand_range()` — `ipaddress.hosts()` (skips network/broadcast; handles /31,/32)
- `build_ip_entries()` — per-domain running index, unique hostnames/usernames
- `parse_input()` — headered CSV **or** plain IP/CIDR list
- `validate_plan()` — returns a list of prompts (never crashes)
- `preview()` — total IPs, per-domain counts, sample, duplicate detection

---

## Phase 3 — centralized monitoring (la412 hub)

| Capability | Where |
|---|---|
| **Prometheus file_sd** target JSON (scrapes box `:8000`) with labels `server_name`/`display`/`environment`/`hostname`/`ip` | `core/monitoring.py` |
| Friendly **Grafana `display`** label (dropdown) | `monitoring.default_display()` |
| **Loki promtail** config (log shipping, same labels) | `monitoring.promtail_config()` |
| **Register / deregister** on hub + reload Prometheus (http / docker-SIGHUP / sighup) | `monitoring.register_server()` |
| **Verify** scrape via hub Prometheus `up{server_name="…"}` | `monitoring.verify_target()` |
| Hub config saved app-wide (password → keyring) | `services/app_settings.py` |
| GUI: **Monitoring…** button; CLI: `monitor` command | `ui/monitoring_dialog.py`, `cli.py` |

---

## Security properties (built in)
- Passwords never written to disk — stripped to keyring on save, re-hydrated on load.
- `init.lua` metrics endpoint binds `0.0.0.0:8000` **only** when a monitoring hub is set (else `127.0.0.1`).
- Deploy is always reversible (timestamped backup + rollback on any failure).
- `kumod --validate` false-alarm on the legacy `dkim_sign.lua` self-test is handled (treated as pass).

---
---

# PART B — Complete testing guide (first → last)

> Run everything from the project directory:
> `C:\Users\DINESHKUMAR METTA\Downloads\kumomta-automation`

## STEP 0 — One-time setup

```bash
cd "C:\Users\DINESHKUMAR METTA\Downloads\kumomta-automation"
uv sync
```
**Pass:** completes with no error; creates `.venv`.

If `kumo-cli` / `kumo-automation` aren't found afterwards, activate the venv:
```bash
.venv\Scripts\activate
```

---

## STEP 1 — Automated tests (fastest confidence check)

```bash
uv run --extra dev pytest -q
```
**Pass:** all tests pass across `test_generation.py`, `test_dnsgen.py`, `test_ipplan.py`, `test_monitoring.py`.
This proves config generation, DNS generation, CIDR planning, and monitoring generation all work — no server needed.

---

## STEP 2 — CLI: config generation (Phase 1)

```bash
kumo-cli preview --project examples/sample_customer.yaml --server "Test Server 01"
```
**Pass:** prints `init.lua`, `sources.toml`, `queues.toml`, `ip_map.toml`, `smtp-auth.json`. Check `init.lua` has your listeners and the `:8000` metrics block.

---

## STEP 3 — CLI: CIDR planning / import (Phase 2, Feature 3)

```bash
kumo-cli plan --csv examples/import_ranges.csv
```
**Pass:** reports **394 usable IPs** — shopmail.com (254) + newsmail.com (126) + promo.com (14) — each with a different naming pattern, **no duplicates**.

Single CIDR:
```bash
kumo-cli plan --cidr 203.0.113.0/24 --domain test.com --pattern smtp{n}
```
**Pass:** 254 IPs; hostnames `smtp1.test.com` … `smtp254.test.com`.

---

## STEP 4 — CLI: DNS generation (Phase 2, Feature 1)

```bash
kumo-cli dns-gen --project examples/sample_customer.yaml --server "Test Server 01" --out ./out
```
**Pass:** prints A/MX/SPF/DKIM/DMARC/PTR records; writes `out/dns-records.csv` + one `out/dkim-<domain>.key` per domain. Open the CSV — SPF should be a single merged record; DKIM should be `v=DKIM1; k=rsa; p=…`.

---

## STEP 5 — CLI: monitoring config (Phase 3, offline)

```bash
kumo-cli monitor --project examples/sample_customer.yaml --server "Test Server 01"
```
**Pass:** prints Prometheus file_sd JSON (targets `host:8000`, labels incl. `display`) and a promtail config with `server_name`, `display`, and `__path__: /var/log/kumomta/*.log`.

---

## STEP 6 — GUI: full end-to-end walkthrough

```bash
kumo-automation
```

| # | Action | Pass criteria |
|---|---|---|
| 6.1 | **New Customer** → type a name | Appears in the left tree |
| 6.2 | Select customer → **New Server (Wizard)…** | Wizard opens on "Server basics" |
| 6.3 | Fill basics (name, SSH host, shared password) → **Next** | Missing name/host is blocked with a prompt |
| 6.4 | On "IP ranges": **Load CSV…** → `examples/import_ranges.csv` → **Next** | Validates; advances to Preview |
| 6.5 | Preview page | Shows **394 IPs**, per-domain counts, table of hostnames/usernames; **Finish** creates the server |
| 6.6 | Select the new server | Right panel shows IPs, SSH, strategy |
| 6.7 | **Edit Server…** | Form loads with all fields + IP table; add/remove a row → **Save** persists |
| 6.8 | **Preview Config…** | 5 files in dropdown; generated content shown instantly |
| 6.9 | In preview → **Fetch current from server** (enter SSH pw) | Side-by-side colored diff — OR a graceful "generated only" message if the box is unreachable |
| 6.10 | **Monitoring…** → fill hub host/URLs → **Save hub** | "Monitoring hub settings saved" |
| 6.11 | Keep **Dry-run ✅** → **Deploy →** | Log runs generate + validate, **stops before any change**; HTML report opens with DNS statuses |
| 6.12 | **Delete Selected** on a server, then on a customer | Removed from tree and disk (with confirmation) |
| 6.13 | Close the app, reopen `kumo-automation` | Customers/servers/edits are still there (persistence) |

---

## STEP 7 — LIVE tests (only when you're ready; involves real servers)

> ⚠️ **Do NOT run a non-dry-run deploy against `198.23.177.154`** — that's the 1016-IP test box and a real deploy would overwrite its live config. Use dry-run there.

**7a. Real deploy to a safe/scratch KumoMTA box** (not the 1016-IP box):
- In the GUI, uncheck Dry-run → **Deploy →**.
- **Pass:** steps show backup → upload → validate → restart → verify all OK; health checks run (service, listeners, **smtp-auth+starttls**, queue, return-path, dns); a `DeploymentRecord` is saved to the customer.

**7b. Register with the live monitoring hub (la412):**
```bash
kumo-cli monitor --project examples/sample_customer.yaml --server "Test Server 01" \
  --register --verify --hub-host <la412-ip> --hub-password '***' --reload docker
```
- **`--reload docker`** is correct for la412 (Prometheus runs in a container; SIGHUP reloads it). Use `--reload http` only if Prometheus was started with `--web.enable-lifecycle`.
- Confirm the file_sd path in the dialog/default matches your hub: `/opt/monitoring/prometheus/targets/`.
- **Pass:** `prometheus-register OK`, then `monitoring-scrape UP`. The server appears in the Grafana dropdown under its `display` label.

Or do the same from the GUI: **Monitoring… → Register + Verify this server**.

---

## Quick pass/fail summary sheet

| Step | Command / action | Expected |
|---|---|---|
| 1 | `pytest` | all pass |
| 2 | `preview` | 5 files printed |
| 3 | `plan --csv` | 394 IPs, no dupes |
| 4 | `dns-gen` | CSV + DKIM keys written |
| 5 | `monitor` | file_sd + promtail printed |
| 6 | GUI walkthrough | wizard→edit→diff→dry-run→report all work |
| 7 | live deploy + register | (optional) real box OK, hub shows UP |

---

## Status

- ✅ Phase 1 — MVP deploy pipeline
- ✅ Phase 2 — DNS gen, health checks, import wizard, config diff, customer/server mgmt, persistence
- ✅ Phase 3 — centralized monitoring (Prometheus/Loki/Grafana)
- ⏭️ Phase 4 (next) — reusable customer templates, Deployment History view, PDF report export
