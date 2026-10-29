"""Download Center — export everything generated for a server to one folder:
config files, DNS records (CSV/Excel/TXT), DKIM keys, reports, and logs.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core import dnsgen
from ..core.credentials import sending_credentials_csv
from ..core.generators import generate_all
from ..core.models import ServerProfile
from .widgets import h2, muted


class DownloadDialog(QDialog):
    def __init__(self, profile: ServerProfile, reports_dir: Path, logs_dir: Path, parent=None,
                 keys: dict | None = None):
        super().__init__(parent)
        self.profile = profile
        self.reports_dir = Path(reports_dir)
        self.logs_dir = Path(logs_dir)
        self.keys = keys
        self.setWindowTitle(f"Download Center — {profile.name}")
        self.resize(480, 380)

        v = QVBoxLayout(self)
        v.addWidget(h2("Download everything"))
        v.addWidget(muted("Choose what to export, then pick a destination folder."))
        self.chk_config = QCheckBox("Generated config files (init.lua, sources, queues, ip_map, smtp-auth)")
        self.chk_dns = QCheckBox("DNS records (CSV + Excel + TXT) + DKIM keys")
        self.chk_creds = QCheckBox("Sending credentials (CSV) — IP, host, port, username, password, domain")
        self.chk_reports = QCheckBox("Deployment reports for this server")
        self.chk_logs = QCheckBox("Logs")
        for c in (self.chk_config, self.chk_dns, self.chk_creds, self.chk_reports, self.chk_logs):
            c.setChecked(True)
            v.addWidget(c)
        v.addWidget(muted("⚠ The credentials CSV contains plaintext SMTP passwords — "
                          "share it securely and delete it after testing."))
        v.addStretch(1)

        self.status = muted("")
        v.addWidget(self.status)
        b = QPushButton("Download to folder…")
        b.setObjectName("Primary")
        b.clicked.connect(self._download)
        v.addWidget(b)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)

    def _download(self):
        d = QFileDialog.getExistingDirectory(self, "Choose destination folder")
        if not d:
            return
        out = Path(d) / f"{self.profile.name.replace(' ', '_')}_export"
        out.mkdir(parents=True, exist_ok=True)
        written = 0
        self.setCursor(Qt.WaitCursor)
        try:
            if self.chk_config.isChecked():
                cfg = out / "config"; cfg.mkdir(exist_ok=True)
                for name, content in generate_all(self.profile).items():
                    (cfg / name).write_text(content, encoding="utf-8")
                    written += 1
            if self.chk_dns.isChecked():
                dns_dir = out / "dns"; dns_dir.mkdir(exist_ok=True)
                self.status.setText("Generating DNS + DKIM keys…")
                records, keys = dnsgen.generate_records(self.profile, keys=self.keys)
                (dns_dir / "dns-records.csv").write_text(dnsgen.records_to_csv(records), encoding="utf-8")
                (dns_dir / "dns-records.txt").write_text(dnsgen.records_to_txt(records), encoding="utf-8")
                try:
                    dnsgen.records_to_xlsx(records, dns_dir / "dns-records.xlsx")
                except Exception:  # noqa: BLE001
                    pass
                for domain, pem in keys.items():
                    (dns_dir / f"dkim-{domain}.key").write_text(pem, encoding="utf-8")
                written += 3 + len(keys)
            if self.chk_creds.isChecked():
                (out / "sending-credentials.csv").write_text(
                    sending_credentials_csv(self.profile), encoding="utf-8")
                written += 1
            if self.chk_reports.isChecked() and self.reports_dir.exists():
                rep = out / "reports"; rep.mkdir(exist_ok=True)
                stem = self.profile.name.replace(" ", "_")
                for p in self.reports_dir.glob(f"*{stem}*.html"):
                    shutil.copyfile(p, rep / p.name); written += 1
            if self.chk_logs.isChecked() and self.logs_dir.exists():
                lg = out / "logs"; lg.mkdir(exist_ok=True)
                for p in list(self.logs_dir.glob("*.log")) + list(self.logs_dir.glob("*.jsonl")):
                    shutil.copyfile(p, lg / p.name); written += 1
        finally:
            self.unsetCursor()
        self.status.setText(f"✓ Exported {written} file(s) to {out}")
        QMessageBox.information(self, "Download complete", f"Wrote {written} file(s) to:\n{out}")
