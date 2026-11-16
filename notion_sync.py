#!/usr/bin/env python3
"""Compare the companies in the Notion Outreach database against watchlist.json.

The watchlist is the source of truth for what gets checked, because it holds a
verified ATS type and slug per company. Notion is the source of truth for who she
is actually pursuing. This reconciles the two and tells her what is untracked, so a
company never sits in her pipeline while nothing watches its board.

Read-only against Notion. Writes nothing back.
"""
import json, os, pathlib, urllib.request, urllib.error

import notify

HERE = pathlib.Path(__file__).parent
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def notion(path, payload=None):
    token = os.environ.get("NOTION_TOKEN", "").strip()
    if not token:
        raise SystemExit("NOTION_TOKEN is not set")
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Notion-Version": VERSION,
                 "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def company_names():
    """Every Company title in the Outreach database, following pagination."""
    db = os.environ.get("DATABASE_ID", "").strip()
    if not db:
        raise SystemExit("DATABASE_ID is not set")
    names, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = notion(f"/databases/{db}/query", body)
        for row in data.get("results", []):
            for prop in (row.get("properties") or {}).values():
                if prop.get("type") == "title":
                    text = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                    if text.strip():
                        names.append(text.strip())
                    break
        if not data.get("has_more"):
            return names
        cursor = data.get("next_cursor")


def norm(s):
    return "".join(c for c in s.lower() if c.isalnum())


def main():
    watch = {k: v for k, v in json.loads((HERE / "watchlist.json").read_text()).items()
             if not k.startswith("_")}
    watched = {norm(k) for k in watch}
    rows = company_names()

    missing = sorted({r for r in rows if norm(r) not in watched})
    extra = sorted({k for k in watch if norm(k) not in {norm(r) for r in rows}})

    print(f"Notion rows: {len(rows)}   watchlist: {len(watch)}")
    print(f"In Notion but not watched ({len(missing)}): {', '.join(missing) or 'none'}")
    print(f"Watched but not in Notion ({len(extra)}): {', '.join(extra) or 'none'}")

    unresolved = {}
    f = HERE / "unresolved.json"
    if f.exists():
        d = json.loads(f.read_text())
        unresolved = {**d.get("unresolved_ats", {}), **d.get("fetch_errors", {})}

    if not (missing or unresolved):
        print("Nothing to report.")
        return

    body = []
    if missing:
        body.append("In your Notion pipeline but no board is being watched:\n  "
                    + "\n  ".join(missing))
        body.append("\nFix: find the board, verify the slug, add it to watchlist.json.")
    if unresolved:
        body.append("\nBoards that failed on the last run:\n  "
                    + "\n  ".join(f"{k}: {v}" for k, v in sorted(unresolved.items())))
    text = "\n".join(body)
    notify.send_email(f"Job watch gaps: {len(missing)} untracked, {len(unresolved)} failing", text)
    notify.send_ntfy("Job watch gaps", text[:3500], tags="warning")
    print(text)


if __name__ == "__main__":
    main()
