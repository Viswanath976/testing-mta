"""cPanel DNS — add accounts (host/user/token + domains) and bulk-publish a DNS
Excel sheet. Tokens go to the OS keyring; the account list (host/user/domains) is
saved in app settings. Publishing runs on a worker thread so the UI never freezes.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import dnspublish as dp
from ..core.models import CpanelAccount
from .widgets import h2, muted


def _split_domains(text: str) -> list[str]:
    return [d.strip().lower() for d in text.replace("\n", ",").replace(" ", ",").split(",") if d.strip()]


class AccountForm(QDialog):
    """Add / edit one cPanel account. Token is captured here and stored to the
    keyring by the caller; it is never written to disk."""

    def __init__(self, parent=None, account: CpanelAccount | None = None):
        super().__init__(parent)
        self.setWindowTitle("cPanel account")
        self.resize(460, 320)
        v = QVBoxLayout(self)
        v.addWidget(h2("cPanel account"))
        v.addWidget(muted("The API token is stored in your OS keyring, never in a file."))

        self.host = QLineEdit(account.host if account else "")
        self.host.setPlaceholderText("cPanel host, e.g. server.host.com:2083")
        self.user = QLineEdit(account.user if account else "")
        self.user.setPlaceholderText("cPanel username")
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("API token (leave blank to keep the stored one)")
        self.domains = QPlainTextEdit(", ".join(account.domains) if account else "")
        self.domains.setPlaceholderText("Domains this account owns — comma or newline separated")
        self.domains.setFixedHeight(90)
        for w in (self.host, self.user, self.token):
            v.addWidget(w)
        v.addWidget(muted("Domains:"))
        v.addWidget(self.domains)

        row = QHBoxLayout()
        ok = QPushButton("Save")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)
        self.result_account: CpanelAccount | None = None
        self.result_token: str | None = None

    def _save(self):
        host = self.host.text().strip()
        user = self.user.text().strip()
        domains = _split_domains(self.domains.toPlainText())
        if not host or not user:
            QMessageBox.warning(self, "Missing", "Host and username are required.")
            return
        if not domains:
            QMessageBox.warning(self, "Missing", "List at least one domain this account owns.")
            return
        self.result_account = CpanelAccount(host=host, user=user, domains=domains)
        self.result_token = self.token.text().strip() or None
        self.accept()


class _PublishWorker(QThread):
    line = Signal(str)
    done = Signal(object)

    def __init__(self, accounts, xlsx, confirm, include_dkim):
        super().__init__()
        self.accounts = accounts
        self.xlsx = xlsx
        self.confirm = confirm
        self.include_dkim = include_dkim

    def run(self):
        try:
            routes = dp.routes_from_accounts(self.accounts)
            records = dp.parse_records_xlsx(self.xlsx)
            if not self.include_dkim:
                records = [r for r in records if "_domainkey" not in r.name]
            self.line.emit(f"Loaded {len(records)} record(s) from {Path(self.xlsx).name}")
            res = dp.publish_many(routes, records, dry_run=not self.confirm, log=self.line.emit)
            self.done.emit(res)
        except Exception as exc:  # noqa: BLE001
            self.line.emit(f"ERROR: {exc}")
            self.done.emit(None)


class DnsPublishDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.accounts: list[CpanelAccount] = settings.load_cpanel_accounts()
        self._worker: _PublishWorker | None = None
        self.xlsx_path: str | None = None
        self.setWindowTitle("cPanel DNS — accounts & bulk publish")
        self.resize(720, 640)

        v = QVBoxLayout(self)

        # --- accounts -------------------------------------------------------- #
        v.addWidget(h2("cPanel accounts"))
        v.addWidget(muted("Add each cPanel account once with its API token and the domains it owns."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Host", "User", "Domains"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setFixedHeight(150)
        v.addWidget(self.table)
        arow = QHBoxLayout()
        add = QPushButton("+ Add account")
        add.clicked.connect(self._add_account)
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove_account)
        arow.addWidget(add)
        arow.addWidget(rem)
        arow.addStretch(1)
        v.addLayout(arow)

        # --- publish --------------------------------------------------------- #
        v.addWidget(h2("Publish DNS from Excel"))
        v.addWidget(muted("Pick a DNS sheet (the Download-Center dns-records.xlsx). Each domain "
                          "is routed to the account that owns it."))
        prow = QHBoxLayout()
        self.file_lbl = muted("No file chosen")
        pick = QPushButton("Choose Excel…")
        pick.clicked.connect(self._pick)
        prow.addWidget(pick)
        prow.addWidget(self.file_lbl, 1)
        v.addLayout(prow)

        self.chk_dkim = QCheckBox("Also publish DKIM (only if the key matches the deployed server)")
        v.addWidget(self.chk_dkim)

        brow = QHBoxLayout()
        self.dry = QPushButton("Dry run (preview)")
        self.dry.clicked.connect(lambda: self._run(confirm=False))
        self.pub = QPushButton("Publish")
        self.pub.setObjectName("Primary")
        self.pub.clicked.connect(self._publish_clicked)
        brow.addStretch(1)
        brow.addWidget(self.dry)
        brow.addWidget(self.pub)
        v.addLayout(brow)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        v.addWidget(self.out, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)

        self._refresh_table()

    # -- accounts --------------------------------------------------------- #
    def _refresh_table(self):
        self.table.setRowCount(0)
        for acc in self.accounts:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(acc.host))
            self.table.setItem(r, 1, QTableWidgetItem(acc.user))
            self.table.setItem(r, 2, QTableWidgetItem(", ".join(acc.domains)))

    def _add_account(self):
        form = AccountForm(self)
        if form.exec() != QDialog.Accepted or not form.result_account:
            return
        acc = form.result_account
        if form.result_token:
            dp.store_token(acc.host, acc.user, form.result_token)
        # replace an existing same host+user, else append
        self.accounts = [a for a in self.accounts if not (a.host == acc.host and a.user == acc.user)]
        self.accounts.append(acc)
        self.settings.save_cpanel_accounts(self.accounts)
        self._refresh_table()
        if not form.result_token and not dp.creds_from_keyring(acc.host, acc.user):
            QMessageBox.warning(self, "No token stored",
                                "No API token is stored for this account yet. Edit it and enter the token.")

    def _remove_account(self):
        r = self.table.currentRow()
        if r < 0:
            return
        del self.accounts[r]
        self.settings.save_cpanel_accounts(self.accounts)
        self._refresh_table()

    # -- publish ---------------------------------------------------------- #
    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose DNS Excel", "", "Excel (*.xlsx)")
        if path:
            self.xlsx_path = path
            self.file_lbl.setText(path)

    def _publish_clicked(self):
        if QMessageBox.question(
            self, "Publish DNS",
            "This writes records to cPanel for every domain in the sheet.\nProceed?"
        ) == QMessageBox.Yes:
            self._run(confirm=True)

    def _run(self, confirm: bool):
        if not self.xlsx_path:
            QMessageBox.warning(self, "No file", "Choose a DNS Excel file first.")
            return
        if not self.accounts:
            QMessageBox.warning(self, "No accounts", "Add at least one cPanel account first.")
            return
        self.out.clear()
        self.out.appendPlainText("PUBLISH" if confirm else "DRY-RUN (no changes)")
        self.dry.setEnabled(False)
        self.pub.setEnabled(False)
        self._worker = _PublishWorker(self.accounts, self.xlsx_path, confirm, self.chk_dkim.isChecked())
        self._worker.line.connect(self.out.appendPlainText)
        self._worker.done.connect(self._finished)
        self._worker.start()

    def _finished(self, res):
        self.dry.setEnabled(True)
        self.pub.setEnabled(True)
        if res is None:
            return
        added = sum(r.get("added", 0) or r.get("todo", 0) for r in res["results"].values())
        self.out.appendPlainText(f"\nDone. {added} record(s) across {len(res['results'])} domain(s).")
        if res["unrouted"]:
            self.out.appendPlainText("No account for: " + ", ".join(res["unrouted"]))
