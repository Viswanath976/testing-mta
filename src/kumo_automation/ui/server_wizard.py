"""7-step New-Server wizard (professional, step-by-step).

  1 Project   2 Environment   3 Server Details   4 Import
  5 Assign Domains   6 Hostnames & SMTP Users   7 Preview

State is collected progressively; a live summary panel updates as you go, and
each step validates + prompts for missing info (never fails silently).
Backend (core/ipplan, core/models) is reused unchanged.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import ipplan, validation
from ..core.models import (
    DnsConfig,
    Environment,
    IPRange,
    MonitoringConfig,
    CredentialStrategy,
    PasswordStrategy,
    ServerProfile,
    SSHTarget,
)
from . import theme
from .ip_table import IPTable
from .widgets import Stepper, h2, muted, sanitize_host

_ENVS = ["testing", "production"]


def _ro(text: str) -> QTableWidgetItem:
    """A read-only table cell (enabled + selectable, not editable)."""
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    return item


class _State:
    def __init__(self):
        self.project_name = ""
        self.is_new_project = False
        self.environment = "testing"
        self.server_name = ""
        self.ssh_host = ""
        self.ssh_user = "root"
        self.ssh_port = 22
        self.use_password = True
        self.use_sudo = False
        self.smtp_port = 587
        self.password_strategy = "shared"
        self.credential_strategy = "shared"
        self.shared_username = ""
        self.shared_password = ""
        self.ip_usernames: dict[str, str] = {}
        self.ip_passwords: dict[str, str] = {}
        self.dkim_selector = "s1"
        self.dkim_enabled = True
        self.monitoring_enabled = False
        self.monitoring_display = ""
        self.ranges: list[IPRange] = []


class ServerWizard(QDialog):
    STEPS = ["Project", "Environment", "Server Details", "Import IPs",
             "Assign Domains", "Hostnames & Users", "Preview"]

    def __init__(self, existing_projects: list[str], parent=None, preset_project: str | None = None,
                 existing_servers: dict[str, list[str]] | None = None):
        super().__init__(parent)
        self.setWindowTitle("New Server")
        self.resize(1000, 660)
        self.state = _State()
        self.result_profile: ServerProfile | None = None
        self.result_project: str | None = None
        self.result_is_new: bool = False

        self.pages = [
            _ProjectPage(existing_projects, preset_project),
            _EnvironmentPage(),
            _ServerDetailsPage(existing_servers or {}),
            _ImportPage(),
            _DomainsPage(),
            _PatternsPage(),
            _PreviewPage(),
        ]
        self._idx = 0

        root = QHBoxLayout(self)
        self.stepper = Stepper(self.STEPS)
        root.addWidget(self.stepper)

        center = QVBoxLayout()
        self.title = h2(self.STEPS[0])
        center.addWidget(self.title)
        self.stack = QStackedWidget()
        for p in self.pages:
            self.stack.addWidget(p)
        center.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        self.b_back = QPushButton("‹ Back")
        self.b_back.clicked.connect(self._back)
        self.b_next = QPushButton("Next ›")
        self.b_next.setObjectName("Primary")
        self.b_next.clicked.connect(self._next)
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self.reject)
        nav.addWidget(b_cancel)
        nav.addStretch(1)
        nav.addWidget(self.b_back)
        nav.addWidget(self.b_next)
        center.addLayout(nav)
        root.addLayout(center, 1)

        root.addWidget(self._summary_panel())
        self._show(0)

    # -- live summary ------------------------------------------------------- #
    def _summary_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setFixedWidth(230)
        v = QVBoxLayout(panel)
        v.addWidget(h2("Live Summary"))
        self._sum = {}
        for key in ["Project", "Environment", "Server", "IPs", "Domains", "SMTP Users"]:
            v.addWidget(muted(key))
            lbl = QLabel("—")
            lbl.setStyleSheet("font-weight: 600; font-size: 15px;")
            v.addWidget(lbl)
            self._sum[key] = lbl
        v.addStretch(1)
        return panel

    def _update_summary(self):
        s = self.state
        total = 0
        for r in s.ranges:
            try:
                total += len(ipplan.expand_range(r))
            except Exception:  # noqa: BLE001
                pass
        domains = sorted({r.domain for r in s.ranges if r.domain})
        self._sum["Project"].setText(s.project_name or "—")
        self._sum["Environment"].setText(s.environment)
        self._sum["Server"].setText(s.server_name or "—")
        self._sum["IPs"].setText(str(total))
        self._sum["Domains"].setText(str(len(domains)))
        self._sum["SMTP Users"].setText(str(total))

    # -- navigation --------------------------------------------------------- #
    def _show(self, idx: int):
        self._idx = idx
        self.stack.setCurrentIndex(idx)
        self.stepper.set_current(idx)
        self.title.setText(self.STEPS[idx])
        self.pages[idx].load(self.state)
        self.b_back.setEnabled(idx > 0)
        self.b_next.setText("Finish" if idx == len(self.pages) - 1 else "Next ›")
        self._update_summary()

    def _back(self):
        if self._idx > 0:
            self._show(self._idx - 1)

    def _next(self):
        page = self.pages[self._idx]
        issues = page.validate(self.state)
        if issues:
            QMessageBox.warning(self, "Please fix the following",
                                "\n".join(f"•  {i}" for i in issues))
            return
        warns = getattr(page, "warnings", lambda _s: [])(self.state)
        if warns:
            if QMessageBox.question(
                self, "Please confirm",
                "\n".join(f"⚠  {w}" for w in warns) + "\n\nContinue anyway?"
            ) != QMessageBox.Yes:
                return
        page.commit(self.state)
        self._update_summary()
        if self._idx == len(self.pages) - 1:
            self._finish()
        else:
            self._show(self._idx + 1)

    def _finish(self):
        s = self.state
        entries = ipplan.build_ip_entries(s.ranges)
        for entry in entries:
            entry.username = s.ip_usernames.get(entry.ip, entry.username)
            if s.password_strategy == "per_ip":
                entry.password = s.ip_passwords.get(entry.ip) or None
        try:
            self.result_profile = ServerProfile(
                name=s.server_name.strip(),
                environment=Environment(s.environment),
                ssh=SSHTarget(host=sanitize_host(s.ssh_host), port=s.ssh_port,
                                  username=s.ssh_user.strip() or "root",
                                  use_password=s.use_password, use_sudo=s.use_sudo),
                ips=entries,
                password_strategy=PasswordStrategy(s.password_strategy),
                shared_password=(s.shared_password or None),
                credential_strategy=CredentialStrategy(s.credential_strategy),
                shared_username=(s.shared_username or None),
                domains=sorted({e.domain for e in entries if e.domain}),
                smtp_port=s.smtp_port,
                dns=DnsConfig(dkim_selector=s.dkim_selector.strip() or "s1",
                              dkim_enabled=s.dkim_enabled),
                monitoring=MonitoringConfig(enabled=s.monitoring_enabled,
                                            display=(s.monitoring_display or None)),
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not build server", str(exc))
            return
        self.result_project = s.project_name
        self.result_is_new = s.is_new_project
        self.accept()


# =========================================================================== #
#  Pages
# =========================================================================== #
class _ProjectPage(QWidget):
    def __init__(self, existing: list[str], preset: str | None):
        super().__init__()
        self.existing = existing
        v = QVBoxLayout(self)
        v.addWidget(muted("A Project groups a customer's servers (Testing, Production, "
                           "Staging, Development). Reuse one — don't create a new project "
                           "for every server."))
        self.combo = QComboBox()
        self.combo.addItems(existing + ["➕ Create new project…"])
        self.combo.currentIndexChanged.connect(self._toggle)
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("New project name (e.g. NutriFlora)")
        form = QFormLayout()
        if existing:
            form.addRow("Project", self.combo)
        form.addRow("New project name", self.new_name)
        v.addLayout(form)
        v.addStretch(1)
        # No projects yet → force create-new
        if not existing:
            self.combo.setCurrentText("➕ Create new project…")
        elif preset and preset in existing:
            self.combo.setCurrentText(preset)
        self._toggle()

    def _creating(self) -> bool:
        return not self.existing or self.combo.currentText().startswith("➕")

    def _toggle(self):
        self.new_name.setVisible(self._creating())

    def load(self, s):  # noqa: D401
        pass

    def validate(self, s) -> list[str]:
        if self._creating():
            name = self.new_name.text().strip()
            if not name:
                return ["Enter a name for the new project"]
            if any(name.lower() == e.lower() for e in self.existing):
                return [f"Project '{name}' already exists — pick it from the list instead."]
        return []

    def commit(self, s):
        if self._creating():
            s.project_name = self.new_name.text().strip()
            s.is_new_project = True
        else:
            s.project_name = self.combo.currentText()
            s.is_new_project = False


class _EnvironmentPage(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.addWidget(muted("Which environment is this server for?"))
        self.group = QButtonGroup(self)
        labels = {"testing": "Testing", "production": "Production",
                  "staging": "Staging", "development": "Development"}
        self.radios = {}
        for env in _ENVS:
            rb = QRadioButton(labels[env])
            self.radios[env] = rb
            self.group.addButton(rb)
            v.addWidget(rb)
        self.radios["testing"].setChecked(True)
        v.addStretch(1)

    def load(self, s):
        self.radios.get(s.environment, self.radios["testing"]).setChecked(True)

    def validate(self, s):
        return []

    def commit(self, s):
        for env, rb in self.radios.items():
            if rb.isChecked():
                s.environment = env
                return


class _ServerDetailsPage(QWidget):
    def __init__(self, existing_servers: dict[str, list[str]]):
        super().__init__()
        self.existing_servers = existing_servers
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.host = QLineEdit()
        self.host.setPlaceholderText("IP or hostname (a pasted 'ssh user@host' is cleaned automatically)")
        self.user = QLineEdit("root")
        self.ssh_port = QSpinBox(); self.ssh_port.setRange(1, 65535); self.ssh_port.setValue(22)
        self.use_pw = QCheckBox("Ask for SSH password at deploy time"); self.use_pw.setChecked(True)
        self.use_sudo = QCheckBox("Use sudo for remote deployment commands")
        self.smtp_port = QSpinBox(); self.smtp_port.setRange(1, 65535); self.smtp_port.setValue(587)
        self.strategy = QComboBox()
        self.strategy.addItem("Single username and password for all IPs", "shared")
        self.strategy.addItem("Different username and password per IP", "per_ip")
        self.strategy.currentTextChanged.connect(self._toggle_pw)
        self.shared_user = QLineEdit()
        self.shared_user.setPlaceholderText("MailWizz delivery-server username")
        self.shared_pw = QLineEdit(); self.shared_pw.setEchoMode(QLineEdit.Password)
        self.pw_note = muted("Username and password will be entered for each IP on the next step.")
        self.dkim = QLineEdit("s1")
        self.dkim_on = QCheckBox("Enable DKIM signing (turn OFF for bare test boxes with no keys)")
        self.dkim_on.setChecked(True)
        self.mon = QCheckBox("Register with central monitoring (Prometheus/Grafana/Loki)")
        self.mon_display = QLineEdit()
        self.mon_display.setPlaceholderText("Grafana label (optional) — defaults to '<name> (<host>)'")
        self.env_note = muted("")

        form.addRow("Server name *", self.name)
        form.addRow("SSH host *", self.host)
        form.addRow("SSH user", self.user)
        form.addRow("SSH port", self.ssh_port)
        form.addRow("", self.use_pw)
        form.addRow("", self.use_sudo)
        form.addRow("SMTP port", self.smtp_port)
        form.addRow("SMTP password strategy", self.strategy)
        form.addRow("Shared SMTP username", self.shared_user)
        form.addRow("Shared SMTP password", self.shared_pw)
        form.addRow("", self.pw_note)
        form.addRow("DKIM selector", self.dkim)
        form.addRow("", self.dkim_on)
        form.addRow("", self.mon)
        form.addRow("Monitoring label", self.mon_display)
        v.addLayout(form)
        v.addWidget(self.env_note)
        v.addStretch(1)
        self._toggle_pw()

    def _toggle_pw(self):
        shared = self.strategy.currentData() == "shared"
        self.shared_user.setVisible(shared)
        self.shared_pw.setVisible(shared)
        self.pw_note.setVisible(not shared)

    def load(self, s):
        if s.environment == "production":
            self.env_note.setText("Production: one SMTP user per IP is recommended "
                                  "(the generator already creates one user per IP).")
        else:
            self.env_note.setText(f"{s.environment.title()} environment.")
        if s.server_name:
            self.name.setText(s.server_name)

    def validate(self, s):
        issues = []
        name = self.name.text().strip()
        if not name:
            issues.append("Server name is required")
        elif name.lower() in [n.lower() for n in self.existing_servers.get(s.project_name, [])]:
            issues.append(f"Server '{name}' already exists in project '{s.project_name}'")
        host = sanitize_host(self.host.text())
        if not host:
            issues.append("SSH host is required")
        elif not validation.is_valid_host(host):
            issues.append(f"SSH host '{host}' is not a valid IP or domain")
        if not validation.is_valid_ssh_port(self.ssh_port.value()):
            issues.append("SSH port must be 1–65535")
        if not validation.is_valid_dkim_selector(self.dkim.text().strip()):
            issues.append("DKIM selector is invalid (letters/digits, . _ - only)")
        if self.strategy.currentData() == "shared":
            if not self.shared_user.text().strip():
                issues.append("Shared SMTP username is required (or switch to per-IP credentials)")
            if not self.shared_pw.text():
                issues.append("Shared SMTP password is required (or switch to per-IP credentials)")
        return issues

    def warnings(self, s):
        warns = []
        if validation.smtp_port_status(self.smtp_port.value()) == "warn":
            warns.append(f"SMTP port {self.smtp_port.value()} is unusual "
                         "(standard: 25, 465, 587, 2525).")
        if self.strategy.currentData() == "shared":
            w = validation.password_warning(self.shared_pw.text())
            if w:
                warns.append(w)
        return warns

    def commit(self, s):
        s.server_name = self.name.text().strip()
        s.ssh_host = sanitize_host(self.host.text())
        s.ssh_user = self.user.text().strip() or "root"
        s.ssh_port = self.ssh_port.value()
        s.use_password = self.use_pw.isChecked()
        s.use_sudo = self.use_sudo.isChecked()
        s.smtp_port = self.smtp_port.value()
        s.password_strategy = self.strategy.currentData()
        s.credential_strategy = self.strategy.currentData()
        s.shared_username = self.shared_user.text().strip()
        s.shared_password = self.shared_pw.text()
        s.dkim_selector = self.dkim.text().strip() or "s1"
        s.dkim_enabled = self.dkim_on.isChecked()
        s.monitoring_enabled = self.mon.isChecked()
        s.monitoring_display = self.mon_display.text().strip()


class _ImportPage(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        modes = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.rb_cidr = QRadioButton("CIDR ranges"); self.rb_cidr.setChecked(True)
        self.rb_csv = QRadioButton("CSV file")
        self.rb_manual = QRadioButton("Manual IPs")
        for rb in (self.rb_cidr, self.rb_csv, self.rb_manual):
            self.group.addButton(rb)
            modes.addWidget(rb)
        modes.addStretch(1)
        v.addLayout(modes)
        for rb in (self.rb_cidr, self.rb_csv, self.rb_manual):
            rb.toggled.connect(self._hint)

        form = QFormLayout()
        self.default_domain = QLineEdit()
        self.default_domain.setPlaceholderText("Optional here — you assign domains in the next step")
        self.preset = QComboBox(); self.preset.addItems(list(ipplan.NAMING_PRESETS))
        form.addRow("Default domain", self.default_domain)
        form.addRow("Naming preset", self.preset)
        v.addLayout(form)

        self.hint = muted("")
        v.addWidget(self.hint)
        self.text = QPlainTextEdit()
        v.addWidget(self.text, 1)
        row = QHBoxLayout()
        self.b_csv = QPushButton("Load CSV…")
        self.b_csv.clicked.connect(self._load_csv)
        row.addWidget(self.b_csv)
        row.addStretch(1)
        v.addLayout(row)
        self._hint()

    def _mode(self) -> str:
        return "csv" if self.rb_csv.isChecked() else ("manual" if self.rb_manual.isChecked() else "cidr")

    def _hint(self, *_):
        m = self._mode()
        self.b_csv.setVisible(m == "csv")
        if m == "cidr":
            self.text.setPlaceholderText("One CIDR per line, optionally `cidr,domain`:\n"
                                         "84.33.217.128/25,nutriflorahub.com\n84.33.20.128/25")
            self.hint.setText("Each CIDR is auto-expanded to usable IPs; each line can go to a different domain.")
        elif m == "manual":
            self.text.setPlaceholderText("One IP per line:\n198.51.100.5\n198.51.100.6")
            self.hint.setText("Individual IPs, all assigned to one domain in the next step.")
        else:
            self.hint.setText("Load a CSV with columns: cidr,domain,hostname_pattern,username_pattern "
                              "(domain/patterns optional).")

    def _load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV (*.csv);;All files (*.*)")
        if path:
            self.text.setPlainText(Path(path).read_text(encoding="utf-8"))

    def _build(self) -> list[IPRange]:
        m = self._mode()
        text = self.text.toPlainText()
        dom = self.default_domain.text().strip()
        if m == "csv":
            return ipplan.parse_input(text, default_domain=dom, preset=self.preset.currentText())
        hp, up = ipplan.NAMING_PRESETS.get(self.preset.currentText(), ipplan.NAMING_PRESETS["s{n}"])
        if m == "manual":
            ips = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not ips:
                return []
            return [IPRange(ips=ips, domain=dom, hostname_pattern=hp, username_pattern=up)]
        # cidr
        ranges = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            parts = [p.strip() for p in ln.split(",")]
            ranges.append(IPRange(cidr=parts[0], domain=(parts[1] if len(parts) > 1 else dom),
                                  hostname_pattern=hp, username_pattern=up))
        return ranges

    def load(self, s):
        pass

    def validate(self, s):
        ranges = self._build()
        if not ranges:
            return ["Add at least one IP or CIDR"]
        issues = []
        for r in ranges:
            if r.cidr and not validation.is_valid_cidr(r.cidr):
                issues.append(f"Invalid CIDR: {r.cidr}")
            for ip in r.ips:
                if not validation.is_valid_ip(ip):
                    issues.append(f"Invalid IP: {ip}")
        # duplicate IPs across everything
        all_ips = []
        for r in ranges:
            if r.cidr and not validation.is_valid_cidr(r.cidr):
                continue
            try:
                all_ips.extend(ipplan.expand_range(r))
            except ValueError:
                pass
        dups = validation.duplicates(all_ips)
        if dups:
            issues.append("Duplicate IP(s): " + ", ".join(dups[:5]))
        return issues

    def commit(self, s):
        s.ranges = self._build()


class _DomainsPage(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.addWidget(muted("Assign a sending domain to each range. Different ranges can use "
                          "different domains."))
        apply_row = QHBoxLayout()
        self.all_domain = QLineEdit()
        self.all_domain.setPlaceholderText("Apply one domain to all rows…")
        b = QPushButton("Apply to all")
        b.clicked.connect(self._apply_all)
        apply_row.addWidget(self.all_domain, 1)
        apply_row.addWidget(b)
        v.addLayout(apply_row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Range", "IPs", "Domain"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.table)

    def load(self, s):
        self.table.setRowCount(0)
        for r in s.ranges:
            row = self.table.rowCount()
            self.table.insertRow(row)
            label = r.cidr or f"{len(r.ips)} IP(s)"
            try:
                count = len(ipplan.expand_range(r))
            except Exception:  # noqa: BLE001
                count = 0
            self.table.setItem(row, 0, _ro(label))
            self.table.setItem(row, 1, _ro(str(count)))
            self.table.setItem(row, 2, QTableWidgetItem(r.domain or ""))

    def _apply_all(self):
        d = self.all_domain.text().strip()
        if not d:
            return
        for row in range(self.table.rowCount()):
            self.table.setItem(row, 2, QTableWidgetItem(d))

    def validate(self, s):
        issues = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            dom = item.text().strip() if item else ""
            label = self.table.item(row, 0).text()
            if not dom:
                issues.append(f"Domain for range {label} is required")
            elif not validation.is_valid_domain(dom):
                issues.append(f"'{dom}' is not a valid domain (e.g. example.com)")
        return issues

    def commit(self, s):
        for row, r in enumerate(s.ranges):
            r.domain = self.table.item(row, 2).text().strip()


class _PatternsPage(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.addWidget(muted("Hostnames and SMTP usernames are generated from patterns. "
                          "Tokens: {n} (index), {domain}, {word}. Custom patterns allowed."))
        quick = QHBoxLayout()
        self.preset = QComboBox(); self.preset.addItems(list(ipplan.NAMING_PRESETS))
        b = QPushButton("Apply preset to all")
        b.clicked.connect(self._apply_all)
        quick.addWidget(QLabel("Preset:"))
        quick.addWidget(self.preset)
        quick.addWidget(b)
        quick.addStretch(1)
        v.addLayout(quick)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Domain", "Hostname pattern", "Username pattern"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemChanged.connect(self._update_preview)
        v.addWidget(self.table)
        self.preview_lbl = muted("")
        self.preview_lbl.setWordWrap(True)
        v.addWidget(self.preview_lbl)
        v.addWidget(muted("Per-IP credentials (required when the password strategy is per_ip):"))
        self.credentials = QTableWidget(0, 4)
        self.credentials.setHorizontalHeaderLabels(["IP", "Hostname", "Username", "Password"])
        self.credentials.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.credentials, 1)
        self._ranges: list = []

    def load(self, s):
        self._ranges = s.ranges
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for r in s.ranges:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, _ro(r.domain))
            self.table.setItem(row, 1, QTableWidgetItem(r.hostname_pattern))
            self.table.setItem(row, 2, QTableWidgetItem(r.username_pattern))
        self.table.blockSignals(False)
        self._update_preview()
        self._load_credentials(s)
        self.credentials.setVisible(s.credential_strategy == "per_ip")

    def _load_credentials(self, s):
        entries = ipplan.build_ip_entries(s.ranges)
        self.credentials.setRowCount(0)
        for entry in entries:
            row = self.credentials.rowCount()
            self.credentials.insertRow(row)
            self.credentials.setItem(row, 0, _ro(entry.ip))
            self.credentials.setItem(row, 1, _ro(entry.hostname))
            self.credentials.setItem(row, 2, QTableWidgetItem(
                s.ip_usernames.get(entry.ip, entry.username)))
            password = s.ip_passwords.get(entry.ip, "")
            password_item = QTableWidgetItem(password)
            password_item.setData(Qt.UserRole, password)
            self.credentials.setItem(row, 3, password_item)

    def _update_preview(self, *_):
        """Live preview of the first few generated hostnames/usernames per row."""
        lines = []
        for row in range(self.table.rowCount()):
            dom = self.table.item(row, 0).text()
            hp = (self.table.item(row, 1).text() if self.table.item(row, 1) else "").strip()
            up = (self.table.item(row, 2).text() if self.table.item(row, 2) else "").strip()
            samples = []
            for n in (1, 2, 3):
                host = ipplan.fill_pattern(hp, n, dom, f"word{n}")
                user = ipplan.fill_pattern(up, n, dom, f"word{n}")
                samples.append(f"{host} / {user}")
            lines.append(f"<b>{dom}</b>: " + "  ·  ".join(samples))
        self.preview_lbl.setText("Preview →  " + "<br>".join(lines) if lines else "")

    def _apply_all(self):
        hp, up = ipplan.NAMING_PRESETS[self.preset.currentText()]
        for row in range(self.table.rowCount()):
            self.table.setItem(row, 1, QTableWidgetItem(hp))
            self.table.setItem(row, 2, QTableWidgetItem(up))

    def validate(self, s):
        issues = []
        for row, r in enumerate(s.ranges):
            hp = self.table.item(row, 1).text().strip()
            up = self.table.item(row, 2).text().strip()
            if not hp or not up:
                issues.append(f"Patterns for {r.domain}")
                continue
            try:
                many = len(ipplan.expand_range(r)) > 1
            except Exception:  # noqa: BLE001
                many = False
            if many and "{n}" not in hp and "{word}" not in hp:
                issues.append(f"Hostname pattern for {r.domain} needs {{n}} or {{word}} (would duplicate)")
            if many and "{n}" not in up and "{word}" not in up:
                issues.append(f"Username pattern for {r.domain} needs {{n}} or {{word}} (would duplicate)")
        usernames = []
        for row in range(self.credentials.rowCount()):
            username = self.credentials.item(row, 2).text().strip()
            if not username:
                issues.append(f"SMTP username for {self.credentials.item(row, 0).text()} is required")
            usernames.append(username)
            if s.password_strategy == "per_ip":
                password = self.credentials.item(row, 3).text()
                if not password:
                    issues.append(f"SMTP password for {self.credentials.item(row, 0).text()} is required")
        dup = validation.duplicates([u for u in usernames if u])
        if dup:
            issues.append("Duplicate SMTP username(s): " + ", ".join(dup[:5]))
        return issues

    def commit(self, s):
        for row, r in enumerate(s.ranges):
            r.hostname_pattern = self.table.item(row, 1).text().strip()
            r.username_pattern = self.table.item(row, 2).text().strip()
        s.ip_usernames = {}
        s.ip_passwords = {}
        for row in range(self.credentials.rowCount()):
            ip = self.credentials.item(row, 0).text().strip()
            s.ip_usernames[ip] = self.credentials.item(row, 2).text().strip()
            s.ip_passwords[ip] = self.credentials.item(row, 3).text()


class _PreviewPage(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        self.table = IPTable()
        v.addWidget(self.table, 1)

    def load(self, s):
        entries = ipplan.build_ip_entries(s.ranges)
        self.table.set_entries(entries)

        # Import Summary — catch mistakes before any config is generated
        cidrs = sum(1 for r in s.ranges if r.cidr)
        invalid_cidrs = sum(1 for r in s.ranges if r.cidr and not validation.is_valid_cidr(r.cidr))
        missing_dom = sum(1 for r in s.ranges if not r.domain)
        ip_vals = [e.ip for e in entries]
        host_vals = [e.hostname for e in entries]
        user_vals = [e.username for e in entries]
        dup_ip = validation.duplicates(ip_vals)
        dup_host = validation.duplicates(host_vals)
        dup_user = validation.duplicates(user_vals)
        domains = sorted({e.domain for e in entries if e.domain})

        # highlight any duplicate cells in the table
        self.table.set_highlight(set(dup_ip) | set(dup_host) | set(dup_user))

        def chip(label, value, bad=False):
            color = theme.BAD if bad and value else theme.OK if not bad else theme.MUTED
            return f"<td style='padding:2px 14px 2px 0'>{label}<br><b style='font-size:15px;color:{color}'>{value}</b></td>"

        rows = (
            "<table><tr>"
            + chip("CIDR ranges", cidrs)
            + chip("Usable IPs", len(entries))
            + chip("Domains", len(domains))
            + chip("SMTP users", len(entries))
            + chip("Config files", 5)
            + "</tr><tr>"
            + chip("Duplicate IPs", len(dup_ip), bad=True)
            + chip("Duplicate hostnames", len(dup_host), bad=True)
            + chip("Duplicate usernames", len(dup_user), bad=True)
            + chip("Invalid CIDRs", invalid_cidrs, bad=True)
            + chip("Missing domains", missing_dom, bad=True)
            + "</tr></table>"
        )
        clean = not (dup_ip or dup_host or dup_user or invalid_cidrs or missing_dom)
        footer = ("<span style='color:%s'>✓ Ready — click Finish.</span>" % theme.OK if clean
                  else "<span style='color:%s'>⚠ Fix the highlighted issues before Finish.</span>" % theme.BAD)
        self.summary.setText("<b>Import Summary</b><br>" + rows + "<br>" + footer)

    def validate(self, s):
        # full plan check: invalid/duplicate IPs, bad domains, colliding names, empty domain
        return ipplan.validate_plan(s.ranges)

    def commit(self, s):
        pass
