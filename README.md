# job-alerts

Checks every company on the watchlist every 2 hours and alerts on new postings
that match the filters. Email plus a phone push. Runs on GitHub Actions, so nothing
needs to be open on a laptop.

## What it checks

Fifty-one companies across four board types, each slug verified against the live API:

| Type | Endpoint |
| --- | --- |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` |
| Lever | `api.lever.co/v0/postings/{slug}?mode=json` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}` |
| `page` | a careers page parsed with a supplied regex |
| `generic` | a careers page, with every link that looks like a job treated as one |

Three big employers have their own handlers, because they do not use any of the above:

- **Microsoft**: `apply.careers.microsoft.com/api/pcsx/search`, JSON.
- **Amazon**: `amazon.jobs/en/search.json`, JSON.
- **Google**: no API. The results page is server-rendered and the job id plus a slug
  of the title sit in the result hrefs, so the handler regexes those out. Titles come
  from the slug and are title-cased, which loses the original punctuation.

These three are query-driven rather than "list everything", so they take `queries`
and `locations` lists in `watchlist.json`.

**LinkedIn is not checked.** It requires authentication and blocks automated access,
and scraping it breaks their terms. Most of these companies post to their ATS at the
same time or earlier, so the gap is smaller than it looks. If you want LinkedIn
coverage, set up saved-search alerts inside LinkedIn and let them land in the same
inbox.

## Setup

### 1. Create the repo

Public, so Actions minutes are free. At 15-minute cadence a private repo costs about
3,700 minutes a month beyond the free tier.

```
cd job-alerts
git remote add origin https://github.com/<you>/job-alerts.git
git push -u origin main
```

### 2. Add the secrets

Settings, then Secrets and variables, then Actions, then New repository secret.

| Secret | What it is |
| --- | --- |
| `GMAIL_USER` | the Gmail address sending the alerts |
| `GMAIL_APP_PASSWORD` | a 16-character app password, not your account password |
| `ALERT_TO` | where alerts go. Defaults to `GMAIL_USER` if unset |
| `NTFY_TOPIC` | your ntfy topic name, or a full URL |
| `NOTION_TOKEN` | integration token, only used by the weekly gap check |
| `DATABASE_ID` | the Outreach database id, same |

**Gmail app password.** Two-factor authentication has to be on first. Go to
myaccount.google.com, Security, 2-Step Verification, then App passwords at the
bottom. Generate one for "Mail", and paste the 16 characters with no spaces. A normal
Gmail password will not work over SMTP.

**ntfy.** Pick a topic name that nobody would guess, for example
`emilia-jobs-7k2p9x`, because anyone who knows the topic can read and publish to it.
Install the ntfy app, subscribe to that topic, and put the same string in the secret.

**Notion.** notion.so/my-integrations, create an internal integration, copy the token.
Then open the Outreach database, and under the three-dot menu connect the
integration. The database id is the 32-character string in the database URL.

### 3. Enable Actions

Actions tab, enable workflows. Then run **job watch** manually once from
workflow_dispatch to confirm the secrets work.

## Day to day

**Editing what gets alerted.** `config.json`. `want` is the role families, `early`
bypasses the role filter for MBA and new-grad reqs that open once and close fast,
`skip` screens on the title alone and handles seniority. Commit and the next run
uses it.

**Where she would work.** `location_buckets` in `config.json` maps a posting's
location string to a bucket, and `keep_locations` lists the buckets that get
alerted: `NY`, `London`, `Paris`, `SF`, `Remote`. Bucket order is priority, so a
posting listing both New York and San Francisco counts as NY and sorts to the top
of the alert. Anything else, Seattle, Arlington, Dublin, Tokyo, Bangalore, is
dropped. Remote is the one bucket that gets a second test: `remote_anywhere()` in
`checker.py` strips the words that carry no geography and requires nothing to be
left, so `Remote (US)` and `Fully remote` qualify while `Remote - India` and
`Remote, Dublin` do not. Boards set a remote flag on city-anchored roles all the
time, and that flag alone no longer passes a posting.

Google, Microsoft and Amazon have no public board to list, so they are queried per
city. Their `locations` lists in `watchlist.json` must be kept in step with
`keep_locations` by hand: adding a bucket to the config does not make those three
search for it.

Widening `keep_locations` floods the next run, because every posting in the new
bucket is unseen. Run `python3 run.py --seed` first to absorb them silently, then
commit `state.json` with the config change.

**When GitHub's scheduler does not run.** The cron in `watch.yml` is best effort.
GitHub throttles frequent schedules on public repos and sometimes drops them for
hours at a stretch, which is invisible: the workflow still reads active and manual
runs still work. A `*/15` cron here was dropped for six hours straight, which is why
the schedule is now every 2 hours at :17 past, off the congested top of the hour. Check with
`curl -s https://api.github.com/repos/eae2150-a11y/job-alerts/actions/runs | grep -c schedule`.

If it has gone quiet, run the watcher from the Mac instead:

```
./local-fallback.sh install <ntfy-topic>
```

That writes a launchd agent to `~/Library/LaunchAgents`, outside this repo, so the
topic stays private. It runs `run.py` every 2 hours while the Mac is awake and
pushes to the same ntfy topic. `./local-fallback.sh status` shows the last runs,
`uninstall` removes it.

Local runs update `state.json` without committing it, so run both the local agent
and a live GitHub cron and each will re-alert on what the other already claimed.
Pick one. To drop the GitHub side, delete the `schedule:` block from
`.github/workflows/watch.yml` and keep `workflow_dispatch` for manual runs.

**Adding a company.** Add to `watchlist.json` with its ATS type and slug. Verify the
slug first, for example `curl -s -o /dev/null -w "%{http_code}" https://api.ashbyhq.com/posting-api/job-board/SLUG`
should return 200. If no board type fits, use `{"ats": "generic", "url": "<careers page>"}`.

**Companies that fail** are written to `unresolved.json` after every run, and the
weekly Notion gap check emails the list.

**The weekly gap check** runs Mondays and compares the Company column in Notion
against `watchlist.json`, so nothing sits in the pipeline unwatched. It is read-only
against Notion.

## Running it locally

```
python3 run.py --dry-run    # show what would be alerted, change nothing
python3 run.py --seed       # mark everything as seen, send nothing
python3 run.py              # normal run
```

## Things worth knowing

- **Cron is best effort.** GitHub fires scheduled workflows late under load,
  routinely by 5 to 20 minutes and occasionally more. Real latency is 15 to 35
  minutes, not 15.
- **Scheduled workflows get disabled after 60 days** of no repository activity.
  The state commits count as activity, so this only bites if the watcher is already
  broken. If alerts go quiet, check the Actions tab first.
- **The first run seeds.** With no `state.json`, everything currently open is
  recorded as seen and nothing is sent. `state.json` is committed in this repo
  already, seeded on the day it was built.
- **State is only committed when it changes**, so the history stays readable instead
  of filling with 96 empty commits a day.
- **This repo is public.** The company list, the filters and the seen-job history are
  all visible. Referral contacts and notes about people are deliberately not in here.
