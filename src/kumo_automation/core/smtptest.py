"""SMTP testing — AUTH, STARTTLS, and (optionally) a full MAIL FROM / RCPT TO /
DATA transaction that sends one test message using a chosen IP's SMTP user.

The test runs *on the server* (via python3 over SSH) so the message egresses
from the real sending IP. `build_script` is pure/testable; `run_smtp_test`
executes it and parses the JSON result.
"""
from __future__ import annotations

import json

from .models import IPEntry, ServerProfile
from .ssh import SSHClient

_TEMPLATE = r'''
import smtplib, ssl, time, email.utils, json
host="127.0.0.1"; port=__PORT__
user=__USER__; pw=__PW__
send=__SEND__; rcpt=__RCPT__; mailfrom=__FROM__
res={}
t0=time.time()
def dec(r): return r.decode() if isinstance(r, bytes) else str(r)
try:
    s=smtplib.SMTP(host, port, timeout=15); s.ehlo()
    res["starttls"]="yes" if s.has_extn("starttls") else "no"
    ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    if s.has_extn("starttls"):
        s.starttls(context=ctx); s.ehlo()
    s.login(user, pw); res["auth"]="ok"
    if send and rcpt:
        mid=email.utils.make_msgid()
        c,r=s.mail(mailfrom); res["mail_from"]=str(c)+" "+dec(r)
        c,r=s.rcpt(rcpt); res["rcpt_to"]=str(c)+" "+dec(r)
        body=("From: "+mailfrom+"\r\nTo: "+rcpt+"\r\nSubject: KumoMTA automation test"
              "\r\nMessage-ID: "+mid+"\r\n\r\nTest message from the KumoMTA automation tool.")
        c,r=s.data(body); res["data"]=str(c)+" "+dec(r); res["delivery"]=str(c)+" "+dec(r)
        res["message_id"]=mid
    s.quit(); res["ok"]=True
except Exception as e:
    res["error"]=repr(e)[:200]; res["ok"]=(res.get("auth")=="ok")
res["time"]=round(time.time()-t0,2)
print("SMTPTEST"+json.dumps(res))
'''


def build_script(port: int, user: str, pw: str, send: bool,
                 recipient: str | None, mailfrom: str) -> str:
    return (_TEMPLATE
            .replace("__PORT__", str(int(port)))
            .replace("__USER__", repr(user))
            .replace("__PW__", repr(pw))
            .replace("__SEND__", "True" if send else "False")
            .replace("__RCPT__", repr(recipient))
            .replace("__FROM__", repr(mailfrom)))


def run_smtp_test(profile: ServerProfile, ssh: SSHClient, ip: IPEntry,
                  recipient: str | None = None, send: bool = False,
                  smtp_port: int | None = None, smtp_username: str | None = None,
                  smtp_password: str | None = None) -> dict:
    return_path = f"bounces@{ip.domain}"
    port = smtp_port if smtp_port is not None else profile.smtp_port
    username = smtp_username if smtp_username is not None else profile.effective_username(ip)
    password = smtp_password if smtp_password is not None else profile.effective_password(ip)
    script = build_script(port, username, password, send, recipient, return_path)
    r = ssh.run("python3 - <<'PY'\n" + script + "\nPY", timeout=60)
    out = r.stdout or ""
    idx = out.find("SMTPTEST")
    if idx >= 0:
        try:
            data = json.loads(out[idx + len("SMTPTEST"):])
        except Exception:  # noqa: BLE001
            data = {"error": "could not parse result", "ok": False}
    else:
        data = {"error": (r.stderr or out or "no output")[:200], "ok": False}
    data.setdefault("sending_ip", ip.ip)
    data.setdefault("smtp_host", "127.0.0.1")
    data.setdefault("smtp_port", port)
    data.setdefault("smtp_username", username)
    data.setdefault("return_path", return_path)
    data.setdefault("auth", "fail" if not data.get("ok") else data.get("auth", "ok"))
    return data
