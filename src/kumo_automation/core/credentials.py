"""Sending-credentials export — a client-ready CSV of the SMTP login for each IP.

For testing, the mailer needs, per sending IP: which host to connect to, the
port, the SMTP username + password, and the sending domain. This produces one
CSV they can plug straight into their sending tool.

SECURITY: this file contains plaintext SMTP passwords. Share it over a secure
channel and delete it when the client is done testing.
"""
from __future__ import annotations

import csv
import io

from .models import ServerProfile

CREDENTIAL_COLUMNS = [
    "ip",
    "hostname",
    "port",
    "starttls",
    "username",
    "password",
    "domain",
]


def credential_rows(profile: ServerProfile) -> list[dict]:
    """One row per IP with the effective SMTP login (shared or per-IP password)."""
    rows: list[dict] = []
    for ip in profile.ips:
        rows.append(
            {
                "ip": ip.ip,
                "hostname": ip.hostname,
                "port": profile.smtp_port,
                "starttls": "yes" if profile.starttls else "no",
                "username": profile.effective_username(ip),
                "password": profile.effective_password(ip),
                "domain": ip.domain,
            }
        )
    return rows


def sending_credentials_csv(profile: ServerProfile) -> str:
    """Client-ready CSV: ip, hostname, port, starttls, username, password, domain."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CREDENTIAL_COLUMNS)
    writer.writeheader()
    for row in credential_rows(profile):
        writer.writerow(row)
    return buf.getvalue()
