"""Server edit screen (Phase 2 — Customer/Server management, no YAML).

A form for server-level settings plus an editable IP table (add/remove rows).
Used to Edit an existing server or fine-tune one created by the Import Wizard.
Returns an updated ServerProfile as `dialog.result_profile`.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.models import (
    DnsConfig,
    Environment,
    CredentialStrategy,
    IPEntry,
    PasswordStrategy,
    ServerProfile,
    ShapingConfig,
    ShapingPreset,
    SSHTarget,
)
from ..core import validation
from .widgets import sanitize_host

_COLS = ["IP", "Hostname", "Username", "Domain", "Password (per-IP)"]


class ServerEditDialog(QDialog):
    def __init__(self, profile: ServerProfile, parent=None):
        super().__init__(parent)
        self._orig_name = profile.name
        self.result_profile: ServerProfile | None = None
        self.setWindowTitle(f"Edit server — {profile.name}")
        self.resize(860, 620)

        v = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(profile.name)
        self.env = QComboBox()
        self.env.addItems(["testing", "production"])
        self.env.setCurrentText(profile.environment.value)
        self.host = QLineEdit(profile.ssh.host)
        self.user = QLineEdit(profile.ssh.username)
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(profile.ssh.port)
        self.use_pw = QCheckBox("Ask for SSH password at deploy time")
        self.use_pw.setChecked(profile.ssh.use_password)
        self.use_sudo = QCheckBox("Use sudo for remote deployment commands")
        self.use_sudo.setChecked(profile.ssh.use_sudo)
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(profile.smtp_port)
        self.strategy = QComboBox()
        self.strategy.addItem("Single username and password for all IPs", "shared")
        self.strategy.addItem("Different username and password per IP", "per_ip")
        self.strategy.setCurrentIndex(0 if profile.credential_strategy is CredentialStrategy.shared else 1)
        self.shared_user = QLineEdit(profile.shared_username or "")
        self.shared_pw = QLineEdit(profile.shared_password or "")
        self.shared_pw.setEchoMode(QLineEdit.Password)
        self.dkim = QLineEdit(profile.dns.dkim_selector)
        self.dkim_on = QCheckBox("Enable DKIM signing (turn OFF for bare test boxes with no keys)")
        self.dkim_on.setChecked(profile.dns.dkim_enabled)

        # traffic shaping (per-destination-ISP rate limits)
        self.shaping_on = QCheckBox("Enable traffic shaping (per-ISP rate limits)")
        self.shaping_on.setChecked(profile.shaping.enabled)
        self.shaping_preset = QComboBox()
        self.shaping_preset.addItems(["conservative", "balanced", "aggressive"])
        self.shaping_preset.setCurrentText(profile.shaping.preset.value)
        self.shaping_preset.setEnabled(profile.shaping.enabled)
        self.shaping_on.toggled.connect(self.shaping_preset.setEnabled)

        form.addRow("Server name *", self.name)
        form.addRow("Environment", self.env)
        form.addRow("SSH host *", self.host)
        form.addRow("SSH user", self.user)
        form.addRow("SSH port", self.ssh_port)
        form.addRow("", self.use_pw)
        form.addRow("", self.use_sudo)
        form.addRow("SMTP port", self.smtp_port)
        form.addRow("Credential strategy", self.strategy)
        form.addRow("Shared SMTP username", self.shared_user)
        form.addRow("Shared SMTP password", self.shared_pw)
        form.addRow("DKIM selector", self.dkim)
        form.addRow("", self.dkim_on)
        form.addRow("", self.shaping_on)
        form.addRow("Shaping preset", self.shaping_preset)
        v.addLayout(form)

        v.addWidget(QLabel(f"IP addresses ({len(profile.ips)}):"))
        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.table)
        for ip in profile.ips:
            self._add_row(ip)

        row = QHBoxLayout()
        b_add = QPushButton("Add IP")
        b_add.clicked.connect(lambda: self._add_row(None))
        b_del = QPushButton("Remove selected")
        b_del.clicked.connect(self._remove_row)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row.addStretch(1)
        v.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add_row(self, ip: IPEntry | None):
        r = self.table.rowCount()
        self.table.insertRow(r)
        vals = [ip.ip, ip.hostname, ip.username, ip.domain, ip.password or ""] if ip else ["", "", "", "", ""]
        for c, val in enumerate(vals):
            self.table.setItem(r, c, QTableWidgetItem(val))

    def _remove_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _cell(self, r: int, c: int) -> str:
        item = self.table.item(r, c)
        return item.text().strip() if item else ""

    def _save(self):
        host = sanitize_host(self.host.text())
        errs = []
        if not self.name.text().strip():
            errs.append("Server name is required")
        if not host:
            errs.append("SSH host is required")
        elif not validation.is_valid_host(host):
            errs.append(f"SSH host '{host}' is not a valid IP or domain")
        if not validation.is_valid_dkim_selector(self.dkim.text().strip() or "s1"):
            errs.append("DKIM selector is invalid")
        ips: list[IPEntry] = []
        hostnames, usernames, ip_addrs = [], [], []
        for r in range(self.table.rowCount()):
            ip = self._cell(r, 0)
            if not ip:
                continue
            if not validation.is_valid_ip(ip):
                errs.append(f"Row {r + 1}: invalid IP '{ip}'")
                continue
            dom = self._cell(r, 3)
            if dom and not validation.is_valid_domain(dom):
                errs.append(f"Row {r + 1}: invalid domain '{dom}'")
            hn = self._cell(r, 1) or ip
            un = self._cell(r, 2) or ip.replace(".", "-")
            hostnames.append(hn)
            usernames.append(un)
            ip_addrs.append(ip)
            ips.append(IPEntry(ip=ip, hostname=hn, username=un, domain=dom,
                               password=self._cell(r, 4) or None))
        for label, vals in [("IP", ip_addrs), ("hostname", hostnames), ("SMTP username", usernames)]:
            dup = validation.duplicates(vals)
            if dup:
                errs.append(f"Duplicate {label}(s): " + ", ".join(dup[:5]))
        if self.strategy.currentData() == "shared":
            if not self.shared_user.text().strip():
                errs.append("Shared strategy needs a shared SMTP username")
            if not self.shared_pw.text():
                errs.append("Shared strategy needs a shared SMTP password")
        if errs:
            QMessageBox.warning(self, "Please fix the following", "\n".join(f"•  {e}" for e in errs))
            return
        try:
            self.result_profile = ServerProfile(
                name=self.name.text().strip(),
                environment=Environment(self.env.currentText()),
                ssh=SSHTarget(
                    host=sanitize_host(self.host.text()),
                    port=self.ssh_port.value(),
                    username=self.user.text().strip() or "root",
                    use_password=self.use_pw.isChecked(),
                    use_sudo=self.use_sudo.isChecked(),
                ),
                ips=ips,
                password_strategy=(PasswordStrategy.shared
                                   if self.strategy.currentData() == "shared"
                                   else PasswordStrategy.per_ip),
                shared_password=(self.shared_pw.text() or None),
                credential_strategy=CredentialStrategy(self.strategy.currentData()),
                shared_username=(self.shared_user.text().strip() or None),
                domains=sorted({e.domain for e in ips if e.domain}),
                smtp_port=self.smtp_port.value(),
                dns=DnsConfig(dkim_selector=self.dkim.text().strip() or "s1",
                              dkim_enabled=self.dkim_on.isChecked()),
                shaping=ShapingConfig(
                    enabled=self.shaping_on.isChecked(),
                    preset=ShapingPreset(self.shaping_preset.currentText()),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Invalid server", str(exc))
            return
        self.accept()

    @property
    def original_name(self) -> str:
        return self._orig_name
