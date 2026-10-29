# KumoMTA Automation — Phase 1 (MVP)

Desktop + CLI tool to provision, deploy, and verify KumoMTA servers for **Testing** and
**Production** environments. Reduces manual setup from ~1 hour to a few minutes.

## Phase 1 scope (this MVP)
- Customer & server **project management** (multiple customers, each with 1..N servers)
- **SSH** connection (Paramiko)
- **Sudo-backed deployment** for non-root SSH users (enable "Use sudo for remote deployment commands")
- **Server inventory** (hostname, IPs, OS, interfaces)
- **Config generation** from a profile (single- or multi-IP, per-IP hostname/username,
  shared or per-IP password, one-SMTP-user-per-IP for production)
- **Safe deployment**: pre-flight → backup → validate → restart → verify → **rollback on failure**
- **DNS validation** (A/MX/SPF/DKIM/DMARC/PTR) with missing/incorrect highlighting
- **Basic health checks** (service, listeners, metrics endpoint)
- **Deployment report** (HTML, exportable)
- **Save project** (YAML; secrets go to the OS keyring, never plaintext)

Later phases: DNS generation, full health suite, monitoring auto-onboarding
(Loki/Prometheus), bulk onboarding, warm-up, fleet management, backup/restore.

## Architecture
```
src/kumo_automation/
  core/          # UI-AGNOSTIC domain logic (no Qt) — reused by UI, CLI, tests
    models.py        # Pydantic: Customer, ServerProfile, IPEntry, results
    ssh.py           # Paramiko wrapper (run/put/get)
    templating.py    # Jinja2 environment
    generators/      # plugin registry -> config files
    templates/       # Jinja2 config + report templates
    inventory.py     # collect server facts
    preflight.py     # blockers (bindable IPs, port 25, kumod installed)
    deploy.py        # SAFE pipeline (backup/validate/restart/verify/rollback)
    dns.py           # DNS validation (dnspython)
    healthchecks.py  # post-deploy checks
    report.py        # HTML report
  services/       # app services
    project_store.py # customers <-> YAML
    secrets.py       # keyring-backed secret store
    logging_setup.py
  ui/            # PySide6 (thin; calls core via workers)
  cli.py         # same core, scriptable
```

## Run (dev)
```bash
uv sync                       # install deps
uv run kumo-automation        # launch the desktop UI
# or drive the core headless:
uv run kumo-cli deploy --project examples/sample_customer.yaml --server "Test Server 01" --dry-run
```

### Non-root deployment

Edit the server and enable **Use sudo for remote deployment commands**. The SSH
user must be allowed to run sudo. If sudo is configured with `NOPASSWD`, leave
the SSH password prompt disabled; otherwise enable the password prompt and use
the account's sudo password. Uploads are staged in the user's home directory
and installed with sudo, so SFTP never needs direct write access to KumoMTA's
root-owned directories.

## Extending in later phases
- New config type / KumoMTA version → add a **generator** + **template pack**; core untouched.
- New health check → add a function in `healthchecks.py`.
- Monitoring onboarding (Phase 3) → add `generators/monitoring.py` + a deploy step.

Templates live in `core/templates/` and are driven by `config/defaults.yaml` — naming
conventions, ports, and toggles change **without editing code**.
