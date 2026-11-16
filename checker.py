#!/usr/bin/env python3
"""Watch target companies' job boards for roles Emilia would actually want.

Reads watchlist.json, hits each company's public ATS API, keeps the postings whose
titles match the role families she is recruiting for, and diffs against the last run.
Writes a digest of what is NEW since last time.

  python3 watch.py            # normal run: report new postings, update state
  python3 watch.py --all      # report every current match, ignore state
  python3 watch.py --seed     # record current postings as the baseline, report nothing
"""
import json, re, sys, urllib.request, urllib.parse, datetime, pathlib

HERE = pathlib.Path(__file__).parent
OUT = HERE / "digest.md"
UA = {"User-Agent": "job-watch/1.0"}

# Role families she is recruiting into.
WANT = re.compile(r"""
    forward.deployed | applied\s+ai
  | solutions?\s+(architect|engineer|consultant|specialist)
  | customer\s+(engineer|success\s+manager|solutions)
  | technical\s+account | sales\s+engineer | deployment | implementation
  | gtm\s+(strategy|programs|operations) | go.to.market\s+(strategy|programs|operations)
  | account\s+executive | business\s+development\s+representative
  | partnerships?\s+(manager|lead|associate)? | product\s+manager
""", re.I | re.X)

# Titles that match WANT by accident but are not for her.
SKIP = re.compile(r"""
    ^(head\s+of|director|vp\b|vice\s+president|manager[,\s]+of\b|manager,)
  | \b(senior\s+manager|principal|director|vice\s+president)\b
  | (software|ml|machine\s+learning|infrastructure|security|platform|backend|frontend|data)\s+engineer
  | research\s+(scientist|engineer) | \brecruiter\b | recruiting
  | accountant | controller | \bcounsel\b | legal | paralegal
  | data\s+scientist | hardware | mechanical | \bintern\b
  | communications | sourcing | systems\s+analyst | finance
  | ^(sr\.?|senior|staff|lead|principal|head|chief)\b
  | \b(sr\.?|senior|staff|principal)\s
  | \b(iii|iv|v|vi)\b
  | engineering\s+manager | \bmanager\s+(ii|iii|iv)\b
  | executive\s+assistant | \bsde\b | \btax\b | \bpayroll\b
""", re.I | re.X)

# Early-career titles worth surfacing whatever the role family says. These are the reqs
# that open once a year and close fast, which is the whole reason this watcher exists.
EARLY = re.compile(r"""
    \bmba\b | university\s+grad | new\s+grad | graduate\s+(program|programme|role)
  | early\s+(career|in\s+career|in\s+profession) | campus\s+hire | rotational
  | \baspire\b | associate\s+product\s+manager | \bapm\b
""", re.I | re.X)

# Big-company boards are query-driven rather than "list everything", so each one is
# searched for these phrases and the results put through the same WANT/SKIP filter.
DEFAULT_QUERIES = ["forward deployed", "applied ai", "customer engineer",
                   "solutions architect", "solutions engineer", "product manager",
                   "go to market", "partnerships", "MBA"]

def load_config(path=None):
    """Rebuild WANT / EARLY / SKIP from config.json so filters are editable without code."""
    global WANT, EARLY, SKIP, KEEP_LOCATIONS, CONFIG
    cfg = json.loads((pathlib.Path(path) if path else HERE / "config.json").read_text())
    joined = lambda key: re.compile("|".join(f"(?:{x})" for x in cfg[key]), re.I)
    WANT, EARLY, SKIP = joined("want"), joined("early"), joined("skip")
    KEEP_LOCATIONS = tuple(cfg.get("keep_locations", ["NY", "Remote", "US"]))
    CONFIG = cfg
    return cfg


CONFIG = {}
KEEP_LOCATIONS = ("NY", "Remote", "US")


class UnresolvedATS(Exception):
    """Raised when a company's board type is unknown, so run.py can log it for fixing."""


NY = re.compile(r"new york|nyc|\bny\b|brooklyn|manhattan", re.I)
REMOTE = re.compile(r"remote|anywhere|distributed", re.I)
US = re.compile(r"united states|\bu\.?s\.?a?\b|san francisco|seattle|boston|austin|chicago|los angeles", re.I)


def fetch(url, tries=4):
    """Fetch JSON, retrying transient network and rate-limit failures with backoff.

    A scheduled run gets one shot a day, so a board that fails silently is a board
    that is not watched. Only a persistent failure is allowed to raise.
    """
    import time
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as f:
                return json.loads(f.read().decode())
        except Exception as e:
            last = e
            if attempt < tries - 1:
                time.sleep(2 ** attempt)   # 1s, 2s, 4s
    raise last


def fetch_text(url, tries=4):
    """Same retry policy as fetch(), for pages that are HTML rather than JSON."""
    import time
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as f:
                return f.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
    raise last


def postings(company, cfg):
    """Return [{id,title,team,location,remote,url,posted}] for one company."""
    ats, slug = cfg["ats"], cfg.get("slug", "")
    out = []
    if ats == "greenhouse":
        d = fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        for j in d.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            out.append(dict(id=str(j["id"]), title=j.get("title", ""), team="", blurb="",
                            location=loc, remote=bool(REMOTE.search(loc)),
                            url=j.get("absolute_url", ""), posted=j.get("updated_at", "")))
    elif ats == "ashby":
        d = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        for j in d.get("jobs", []):
            if j.get("isListed") is False:
                continue
            out.append(dict(id=j["id"], title=j.get("title", ""), team=j.get("team", "") or "", blurb="",
                            location=j.get("location", "") or "", remote=bool(j.get("isRemote")),
                            url=j.get("jobUrl", ""), posted=j.get("publishedAt", "")))
    elif ats == "lever":
        d = fetch(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        for j in d:
            cats = j.get("categories") or {}
            loc = cats.get("location", "") or ""
            out.append(dict(id=j["id"], title=j.get("text", ""), team=cats.get("team", "") or "", blurb="",
                            location=loc, remote=bool(REMOTE.search(loc)),
                            url=j.get("hostedUrl", ""), posted=""))
    elif ats == "page":
        # Hand-rolled careers page with no applicant-tracking system behind it.
        # cfg["url"] is the page; cfg["pattern"] pulls out one posting per match.
        import html as _html
        raw = fetch_text(cfg["url"])
        found = re.findall(cfg["pattern"], raw, re.S)
        if not found:
            raise RuntimeError("careers page parsed to 0 roles; the page markup probably changed")
        def _text(x):
            return _html.unescape(re.sub(r"<[^>]+>", " ", x)).strip()
        for title, loc in found:
            blurb = _text(title.partition('<span class="d">')[2])
            title = _text(re.split(r"<span\b", title)[0])
            loc = _text(loc)
            out.append(dict(id=title.lower(), title=title, team=blurb, blurb=blurb, location=loc,
                            remote=bool(REMOTE.search(loc)), url=cfg["url"], posted=""))
    elif ats == "microsoft":
        got = set()
        for q in cfg.get("queries", DEFAULT_QUERIES):
            for loc in cfg.get("locations", ["New York"]):
                u = ("https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com"
                     f"&query={urllib.parse.quote(q)}&location={urllib.parse.quote(loc)}"
                     "&start=0&hl=en")
                try:
                    d = fetch(u)
                except Exception:
                    continue
                for j in (d.get("data") or {}).get("positions", []):
                    jid = str(j.get("id") or j.get("displayJobId") or "")
                    if not jid or jid in got:
                        continue
                    got.add(jid)
                    locs = j.get("locations") or j.get("standardizedLocations") or []
                    locs = [x if isinstance(x, str) else (x.get("name") or "") for x in locs]
                    lt = ", ".join(x for x in locs if x) or loc
                    out.append(dict(id=jid, title=j.get("name", ""),
                                    team=j.get("department", "") or "", blurb="",
                                    location=lt, remote=bool(REMOTE.search(lt)),
                                    url=j.get("positionUrl") or
                                    f"https://apply.careers.microsoft.com/careers?pid={jid}",
                                    posted=str(j.get("postedTs", ""))))
    elif ats == "amazon":
        got = set()
        for q in cfg.get("queries", DEFAULT_QUERIES):
            for loc in cfg.get("locations", ["New York"]):
                u = ("https://www.amazon.jobs/en/search.json"
                     f"?base_query={urllib.parse.quote(q)}"
                     f"&loc_query={urllib.parse.quote(loc)}&result_limit=100")
                try:
                    d = fetch(u)
                except Exception:
                    continue
                for j in d.get("jobs", []):
                    jid = str(j.get("id_icims") or j.get("id") or "")
                    if not jid or jid in got:
                        continue
                    got.add(jid)
                    lt = j.get("normalized_location") or j.get("location") or loc
                    path = j.get("job_path") or ""
                    out.append(dict(id=jid, title=j.get("title", ""),
                                    team=j.get("business_category", "") or "", blurb="",
                                    location=lt, remote=bool(REMOTE.search(lt)),
                                    url=("https://www.amazon.jobs" + path) if path else "",
                                    posted=j.get("posted_date", "") or ""))
    elif ats == "google":
        # Google's results page is server-rendered, so there is no JSON API to call.
        # The job id and a slug of the title are both in the result hrefs.
        got = set()
        for q in cfg.get("queries", DEFAULT_QUERIES):
            for loc in cfg.get("locations", ["New York"]):
                u = ("https://www.google.com/about/careers/applications/jobs/results/"
                     f"?q={urllib.parse.quote(q)}&location={urllib.parse.quote(loc)}")
                try:
                    raw = fetch_text(u)
                except Exception:
                    continue
                for jid, slug in re.findall(r"jobs/results/(\d+)-([a-z0-9-]+)", raw):
                    if jid in got:
                        continue
                    got.add(jid)
                    out.append(dict(id=jid, title=slug.replace("-", " ").strip().title(),
                                    team="", blurb="", location=loc, remote=False,
                                    url="https://www.google.com/about/careers/applications/"
                                        f"jobs/results/{jid}-{slug}",
                                    posted=""))
    elif ats == "generic":
        # No known ATS. Fetch the careers page and treat each plausible job link as a
        # posting. Crude by design: the point is to notice that something appeared,
        # and the link in the alert takes her to the real page.
        import html as _html
        raw = fetch_text(cfg["url"])
        seen_href = set()
        for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', raw, re.S | re.I):
            title = _html.unescape(re.sub(r"<[^>]+>", " ", text))
            title = re.sub(r"\s+", " ", title).strip()
            if not (4 < len(title) < 120):
                continue
            if not re.search(r"job|career|position|opening|role|apply|gh_jid|lever|ashby",
                             href, re.I):
                continue
            if href in seen_href:
                continue
            seen_href.add(href)
            url = href if href.startswith("http") else cfg["url"].rstrip("/") + "/" + href.lstrip("/")
            out.append(dict(id=title.lower(), title=title, team="", blurb="",
                            location=cfg.get("location", ""), remote=False,
                            url=url, posted=""))
        if not out:
            raise UnresolvedATS("careers page parsed to 0 links that look like jobs")
    else:
        raise UnresolvedATS(f"unknown ats {ats!r}")
    return out


def keep_location(j):
    return where(j) in KEEP_LOCATIONS


def wanted(j):
    """Match the role family on the title plus any blurb; screen seniority on the title alone."""
    t = j["title"]
    hay = f"{t} {j.get('blurb', '')}".strip()
    if EARLY.search(hay) and not re.search(r"\bintern(ship)?\b", t, re.I):
        return True
    return bool(WANT.search(hay)) and not SKIP.search(t)


def where(j):
    loc = j["location"]
    if NY.search(loc):
        return "NY"
    if j["remote"] or REMOTE.search(loc):
        return "Remote"
    if US.search(loc):
        return "US"
    return "Other"


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    watch = {k: v for k, v in json.loads((HERE / "watchlist.json").read_text()).items()
             if not k.startswith("_")}
    statef = HERE / "state.json"
    state = json.loads(statef.read_text()) if statef.exists() else {}
    reff = HERE / "referrals.json"
    refs = {k: v for k, v in (json.loads(reff.read_text()) if reff.exists() else {}).items()
            if not k.startswith("_")}

    new, errors, counts, fresh_state = {}, [], {}, {}
    for company, cfg in sorted(watch.items()):
        try:
            all_jobs = postings(company, cfg)
        except Exception as e:
            errors.append(f"{company}: {type(e).__name__} {e}")
            fresh_state[company] = state.get(company, [])
            continue
        matches = [j for j in all_jobs if wanted(j)]
        counts[company] = (len(matches), len(all_jobs))
        fresh_state[company] = sorted(j["id"] for j in matches)
        seen = set(state.get(company, []))
        picked = matches if mode == "--all" else [j for j in matches if j["id"] not in seen]
        picked = [j for j in picked if where(j) in ("NY", "Remote", "US")]
        if picked:
            new[company] = sorted(picked, key=lambda j: ({"NY": 0, "Remote": 1, "US": 2}[where(j)],
                                                         j["title"]))

    stamp = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
    lines = [f"# Job watch", "", f"Run {stamp}. "
             f"{sum(len(v) for v in new.values())} new matching roles across "
             f"{len(watch)} companies.", ""]
    if new:
        for company, jobs in new.items():
            lines.append(f"## {company}")
            r = refs.get(company) or {}
            who, st, note = r.get("contact", ""), r.get("status", ""), r.get("note", "")
            if who:
                lines.append(f"Referral: {who} ({st})." + (f" {note}" if note else ""))
            else:
                lines.append("NO REFERRAL CONTACT. Find one before applying, "
                             + (note or "nobody identified yet."))
            lines.append("")
            for j in jobs:
                tag = where(j)
                team = f" · {j['team']}" if j["team"] else ""
                lines.append(f"- **[{j['title']}]({j['url']})** · {tag} · {j['location']}{team}")
            lines.append("")
    else:
        lines += ["Nothing new since the last run.", ""]

    gaps = [c for c in sorted(watch) if not (refs.get(c) or {}).get("contact")]
    if gaps:
        lines += ["", "## No referral contact yet", "",
                  "Every application into these goes in cold. This is the outreach queue.", ""]
        lines += [f"- {c}" for c in gaps]
        lines.append("")

    lines.append("## Board sizes this run")
    lines.append("")
    for c, (m, t) in sorted(counts.items()):
        lines.append(f"- {c}: {m} matching of {t} open")
    if errors:
        lines += ["", "## Boards that failed", ""] + [f"- {e}" for e in errors]

    OUT.write_text("\n".join(lines) + "\n")
    if mode != "--all":
        statef.write_text(json.dumps(fresh_state, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
