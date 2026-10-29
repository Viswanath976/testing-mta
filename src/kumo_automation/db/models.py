"""ORM tables (8) — the full data model in PostgreSQL.

customer 1─∞ server 1─∞ {ip, whitelist, deployment}; cpanel_account + app_setting
are global. Nested config (monitoring/dns/shaping/naming/progress) is stored as
JSON columns; flat fields are real columns; passwords/tokens are encrypted.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    servers: Mapped[list["Server"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan")
    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan")


class Server(Base):
    __tablename__ = "servers"
    __table_args__ = (UniqueConstraint("customer_id", "name", name="uq_server_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    environment: Mapped[str] = mapped_column(String(20), default="testing")

    ssh_host: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_username: Mapped[str] = mapped_column(String(100), default="root")
    ssh_key_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ssh_use_password: Mapped[bool] = mapped_column(Boolean, default=False)

    password_strategy: Mapped[str] = mapped_column(String(20), default="shared")
    shared_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    starttls: Mapped[bool] = mapped_column(Boolean, default=True)
    submission_ports: Mapped[list] = mapped_column(JSON, default=list)
    hide_received: Mapped[bool] = mapped_column(Boolean, default=True)
    mss_clamp: Mapped[int] = mapped_column(Integer, default=1360)
    test_ips_per_range: Mapped[int] = mapped_column(Integer, default=15)
    ips_per_domain: Mapped[int] = mapped_column(Integer, default=16)

    domains: Mapped[list] = mapped_column(JSON, default=list)
    monitoring: Mapped[dict] = mapped_column(JSON, default=dict)
    dns: Mapped[dict] = mapped_column(JSON, default=dict)
    naming: Mapped[dict] = mapped_column(JSON, default=dict)
    shaping: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)

    customer: Mapped[Customer] = relationship(back_populates="servers")
    ips: Mapped[list["IP"]] = relationship(
        back_populates="server", cascade="all, delete-orphan")
    whitelist: Mapped[list["WhitelistEntry"]] = relationship(
        back_populates="server", cascade="all, delete-orphan")


class IP(Base):
    __tablename__ = "ips"
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    ip: Mapped[str] = mapped_column(String(45), index=True)
    hostname: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(100), index=True)
    domain: Mapped[str] = mapped_column(String(255))
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    server: Mapped[Server] = relationship(back_populates="ips")


class WhitelistEntry(Base):
    __tablename__ = "whitelist_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    ip: Mapped[str] = mapped_column(String(64))
    server: Mapped[Server] = relationship(back_populates="whitelist")


class CpanelAccount(Base):
    __tablename__ = "cpanel_accounts"
    __table_args__ = (UniqueConstraint("host", "username", name="uq_cpanel_host_user"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(100))
    domains: Mapped[list] = mapped_column(JSON, default=list)
    token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)


class Deployment(Base):
    __tablename__ = "deployments"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    server_name: Mapped[str] = mapped_column(String(200))
    environment: Mapped[str] = mapped_column(String(20), default="testing")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    deploy_type: Mapped[str] = mapped_column(String(30), default="")
    status_label: Mapped[str] = mapped_column(String(30), default="")
    backup_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    report_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    operator_name: Mapped[str] = mapped_column(String(200), default="")
    config_hash: Mapped[str] = mapped_column(String(80), default="")
    app_version: Mapped[str] = mapped_column(String(40), default="")
    customer: Mapped[Customer] = relationship(back_populates="deployments")


class AppSetting(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    operator_name: Mapped[str] = mapped_column(String(200), default="")
    monitoring_hub: Mapped[dict | None] = mapped_column(JSON, nullable=True)
