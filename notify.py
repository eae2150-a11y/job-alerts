#!/usr/bin/env python3
"""Alert delivery: Gmail SMTP and ntfy.sh.

Both are best effort. A failure here is logged and swallowed, because losing an
alert is better than failing the run and losing the state write that goes with it.
"""
import os, smtplib, ssl, urllib.request, urllib.error
from email.message import EmailMessage


def _env(*names):
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def send_email(subject, body_text, body_html=None):
    user = _env("GMAIL_USER")
    password = _env("GMAIL_APP_PASSWORD")
    to = _env("ALERT_TO") or user
    if not (user and password and to):
        print("[notify] email skipped: GMAIL_USER / GMAIL_APP_PASSWORD / ALERT_TO not all set")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
            s.login(user, password)
            s.send_message(msg)
        print(f"[notify] email sent to {to}")
        return True
    except Exception as e:
        print(f"[notify] email FAILED: {type(e).__name__} {e}")
        return False


def send_ntfy(title, body, click_url=None, priority="default", tags="briefcase"):
    topic = _env("NTFY_TOPIC")
    if not topic:
        print("[notify] ntfy skipped: NTFY_TOPIC not set")
        return False
    url = topic if topic.startswith("http") else f"https://ntfy.sh/{topic}"
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if click_url:
        headers["Click"] = click_url
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        print(f"[notify] ntfy pushed to {url}")
        return True
    except Exception as e:
        print(f"[notify] ntfy FAILED: {type(e).__name__} {e}")
        return False
