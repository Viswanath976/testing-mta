"""Thin, dependency-light SSH wrapper over Paramiko.

Kept UI-agnostic: raises on connection errors, returns structured command
results, and supports uploading generated file *content* (no temp files).
"""
from __future__ import annotations

import shlex
import uuid
from dataclasses import dataclass
from typing import Optional

import paramiko


@dataclass
class CmdResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class SSHError(RuntimeError):
    pass


class SSHClient:
    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        key_path: Optional[str] = None,
        password: Optional[str] = None,
        use_sudo: bool = False,
        timeout: int = 15,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.key_path = key_path
        self.password = password
        self.use_sudo = use_sudo
        self.timeout = timeout
        self._client: Optional[paramiko.SSHClient] = None

    # -- lifecycle ---------------------------------------------------------- #
    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(
            hostname=self.host,
            port=self.port,
            username=self.username,
            timeout=self.timeout,
            allow_agent=True,
            look_for_keys=self.key_path is None and self.password is None,
        )
        if self.key_path:
            kwargs["key_filename"] = self.key_path
        if self.password:
            kwargs["password"] = self.password
        try:
            client.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface a clean message to the UI
            raise SSHError(f"SSH connect to {self.host}:{self.port} failed: {exc}") from exc
        self._client = client

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SSHClient":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- operations --------------------------------------------------------- #
    def run(self, command: str, timeout: int = 120) -> CmdResult:
        if not self._client:
            raise SSHError("SSH not connected")
        remote_command = command
        if self.use_sudo:
            sudo_mode = "-S" if self.password else "-n"
            remote_command = f"sudo {sudo_mode} -p '' -- sh -c {shlex.quote(command)}"
        stdin, stdout, stderr = self._client.exec_command(remote_command, timeout=timeout)
        if self.use_sudo and self.password:
            stdin.write(self.password + "\n")
            stdin.flush()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return CmdResult(command, code, out, err)

    def put_content(self, remote_path: str, content: str) -> None:
        if not self._client:
            raise SSHError("SSH not connected")
        sftp = self._client.open_sftp()
        temp_path = None
        try:
            path = remote_path
            if self.use_sudo:
                home = sftp.normalize(".")
                temp_path = f"{home}/.kumo-upload-{uuid.uuid4().hex}"
                path = temp_path
            with sftp.file(path, "w") as fh:
                fh.write(content)
        finally:
            sftp.close()
        if temp_path:
            result = self.run(
                f"install -m 0644 {shlex.quote(temp_path)} {shlex.quote(remote_path)}; "
                f"rm -f {shlex.quote(temp_path)}"
            )
            if not result.ok:
                raise SSHError(result.stderr.strip() or "sudo upload failed")

    def get_content(self, remote_path: str) -> Optional[str]:
        if not self._client:
            raise SSHError("SSH not connected")
        if self.use_sudo:
            result = self.run(f"cat {shlex.quote(remote_path)}")
            return result.stdout if result.ok else None
        sftp = self._client.open_sftp()
        try:
            with sftp.file(remote_path, "r") as fh:
                return fh.read().decode("utf-8", "replace")
        except IOError:
            return None
        finally:
            sftp.close()

    def exists(self, path: str) -> bool:
        return self.run(f"test -e {shlex.quote(path)}").ok

    def mkdirs(self, path: str) -> None:
        self.run(f"mkdir -p {shlex.quote(path)}")
