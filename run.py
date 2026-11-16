#!/usr/bin/env python3
"""Check every board on the watchlist and alert on genuinely new postings.

  python3 run.py            # normal run: alert on what is new, write state
  python3 run.py --seed     # record everything as seen, send nothing
  python3 run.py --dry-run  # show what would be alerted, write nothing

First run with no state.json seeds automatically and sends nothing, so she does not
wake up to 150 notifications on day one.
"""
import json, pathlib, sys, datetime, html

import checker
import notify

HERE = pathlib.Path(__file__).parent
STATE = HERE / "state.json"
UNRESOLVED = HERE / "unresolved.json"


def load_watchlist():
    raw = json.loads((HERE / "watchlist.json").read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def collect(watch):
    """Return (new_by_company, fresh_state, unresolved, errors)."""
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    seeding = state is None
    state = state or {}

    found, fresh, unresolved, errors = {}, {}, {}, {}
    for company, cfg in sorted(watch.items()):
        try:
            jobs = checker.postings(company, cfg)
        except checker.UnresolvedATS as e:
            unresolved[company] = str(e)
            fresh[company] = state.get(company, [])
            continue
        except Exception as e:
            errors[company] = f"{type(e).__name__}: {e}"
            fresh[company] = state.get(company, [])
            continue

        matches = [j for j in jobs if checker.wanted(j) and checker.keep_location(j)]
        fresh[company] = sorted({j["id"] for j in matches})
        if seeding:
            continue
        seen = set(state.get(company, []))
        new = [j for j in matches if j["id"] not in seen]
        if new:
            # Sort by the bucket order in config.json, so New York roles sit at the top.
            order = {name: i for i, name in enumerate(checker.KEEP_LOCATIONS)}
            found[company] = sorted(new, key=lambda j: (order.get(checker.where(j), 99),
                                                        j["title"]))
    return found, fresh, unresolved, errors, seeding


def render(found, cap):
    total = sum(len(v) for v in found.values())
    lines, html_rows, shown = [], [], 0
    for company, jobs in found.items():
        lines.append(f"\n{company}")
        html_rows.append(f"<h3 style='margin:18px 0 6px'>{html.escape(company)}</h3><ul>")
        for j in jobs:
            if shown >= cap:
                break
            shown += 1
            tag = checker.where(j)
            loc = j["location"] or tag
            lines.append(f"  [{tag}] {j['title']}  ({loc})\n       {j['url']}")
            html_rows.append(
                f"<li><a href='{html.escape(j['url'])}'>{html.escape(j['title'])}</a>"
                f" <span style='color:#666'>{html.escape(tag)} &middot; {html.escape(loc)}</span></li>")
        html_rows.append("</ul>")
    if total > cap:
        lines.append(f"\n({total - cap} more not shown)")
        html_rows.append(f"<p style='color:#666'>({total - cap} more not shown)</p>")
    return total, "\n".join(lines), "".join(html_rows)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = checker.load_config()
    watch = load_watchlist()
    found, fresh, unresolved, errors, seeding = collect(watch)

    if unresolved or errors:
        UNRESOLVED.write_text(json.dumps(
            {"checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "unresolved_ats": unresolved, "fetch_errors": errors}, indent=2) + "\n")

    if seeding or mode == "--seed":
        if mode != "--dry-run":
            STATE.write_text(json.dumps(fresh, indent=1, sort_keys=True) + "\n")
        n = sum(len(v) for v in fresh.values())
        print(f"Seeded {n} postings across {len(watch)} companies. No alerts sent.")
        print(f"Unresolved: {len(unresolved)}  Fetch errors: {len(errors)}")
        return

    cap = (cfg.get("alerts") or {}).get("max_per_run", 40)
    total, text, html_body = render(found, cap)

    if total:
        when = datetime.datetime.now().strftime("%d %b, %H:%M")
        subject = (f"{total} new role{'s' if total != 1 else ''}: "
                   + ", ".join(list(found)[:3]) + ("..." if len(found) > 3 else ""))
        body = f"New postings as of {when}\n{text}\n"
        first = next(iter(found.values()))[0]
        if mode == "--dry-run":
            print("[dry-run] would alert:\n" + body)
        else:
            if (cfg.get("alerts") or {}).get("email", True):
                notify.send_email(subject, body,
                                  f"<p style='color:#666'>New postings as of {when}</p>{html_body}")
            if (cfg.get("alerts") or {}).get("ntfy", True):
                notify.send_ntfy(subject, text.strip()[:3500], click_url=first["url"])
        print(body)
    else:
        print("Nothing new.")

    if mode != "--dry-run":
        old = STATE.read_text() if STATE.exists() else ""
        new = json.dumps(fresh, indent=1, sort_keys=True) + "\n"
        if new != old:
            STATE.write_text(new)
            print("state.json updated")
        else:
            print("state.json unchanged")

    if unresolved:
        print(f"\nATS unresolved for {len(unresolved)}: {', '.join(sorted(unresolved))}")
    if errors:
        print(f"Fetch errors for {len(errors)}: {', '.join(sorted(errors))}")


if __name__ == "__main__":
    main()
