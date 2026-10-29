"""Sending-credentials CSV: correct columns, shared vs per-IP password."""
import csv
import io

from kumo_automation.core.credentials import (
    CREDENTIAL_COLUMNS,
    sending_credentials_csv,
)
from kumo_automation.core.models import (
    Environment,
    IPEntry,
    PasswordStrategy,
    ServerProfile,
    SSHTarget,
)


def _profile(strategy=PasswordStrategy.shared):
    return ServerProfile(
        name="T1",
        environment=Environment.production,
        ssh=SSHTarget(host="h", use_password=False),
        password_strategy=strategy,
        shared_password="Shared#Pass",
        smtp_port=587,
        starttls=True,
        ips=[
            IPEntry(ip="1.1.1.1", hostname="s1.example.com", username="s1", domain="example.com"),
            IPEntry(ip="1.1.1.2", hostname="s2.shop.com", username="s2",
                    password="OwnPass2", domain="shop.com"),
        ],
    )


def _read(csv_text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_columns_and_shared_password():
    rows = _read(sending_credentials_csv(_profile(PasswordStrategy.shared)))
    assert list(rows[0].keys()) == CREDENTIAL_COLUMNS
    assert len(rows) == 2
    assert rows[0] == {
        "ip": "1.1.1.1", "hostname": "s1.example.com", "port": "587",
        "starttls": "yes", "username": "s1", "password": "Shared#Pass",
        "domain": "example.com",
    }
    # shared strategy => both users get the shared password
    assert rows[1]["password"] == "Shared#Pass"


def test_per_ip_password():
    rows = _read(sending_credentials_csv(_profile(PasswordStrategy.per_ip)))
    # per-IP strategy => each row uses its own IPEntry.password
    assert rows[1]["password"] == "OwnPass2"
    assert rows[1]["username"] == "s2" and rows[1]["hostname"] == "s2.shop.com"
