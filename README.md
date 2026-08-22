# gov-tender-search

A self-hosted FastAPI service that collects Taiwanese government tenders and ministry grant programmes into one SQLite database, tags and summarises them with a local LLM CLI, and serves a password-protected search dashboard.

## What it does / why

Taiwan publishes government procurement through one system and grant programmes through a few dozen ministry and city sites, each with its own layout, its own idea of a deadline, and no shared API. Finding the ones relevant to you means checking many places repeatedly. This crawls them on a schedule into one table, lets you search by keyword, tag and deadline, and — only when asked — spends LLM tokens turning a bid notice into four plain-language answers: who is it for, what is the threshold, when does it close, what do I do next.

- **Tenders** — keyword (title, agency, case number), tag, deadline range, "still open only". Sourced from the g0v mirror of the government procurement site.
- **Grants** — keyword, tag, eligible applicant, deadline. A pluggable source framework with three working crawlers and a JSON seed importer.
- **AI enrichment is deliberately stingy.** Tagging and summarising run only during a batch backfill, or the first time a detail page is opened. Results are cached in the database. Nothing calls a model on an ordinary search.
- **Access control** — one shared password plus a signed session cookie, intended to sit behind a Cloudflare Tunnel.

## Requirements

- Python 3.10 or newer, Windows or otherwise (the `.ps1` launchers are Windows-only; the app is not).
- The packages in `requirements.txt`.
- For the Ministry of Economic Affairs grant crawler only: Playwright plus an installed Google Chrome. Every other source uses plain HTTP.
- For AI enrichment only: a `codex` CLI on `PATH`, already signed in. **Set `CODEX_DISABLED=true` and the whole application still works** — search, crawling and browsing are unaffected; you simply get no tags or summaries.

## Install

```powershell
git clone <repo-url> gov-tender-search
cd gov-tender-search
.\run.ps1
```

`run.ps1` creates `.venv`, installs the requirements, copies `.env.example` to `.env`, initialises the database and starts the server.

Manually:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python scripts\init_db.py
python -m uvicorn app.main:app --reload
```

### Configuration

Edit `.env` before first use:

```ini
APP_PASSWORD=choose-a-password
SECRET_KEY=<a long random string>
```

Generate the secret with:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**`SECRET_KEY` has no default.** If `APP_PASSWORD` is set and `SECRET_KEY` is not, the application refuses to start rather than falling back to a predictable key. With `APP_PASSWORD` empty — no password gate at all — a random key is generated per process, which is fine locally and means sessions do not survive a restart.

Other useful settings: `CODEX_DISABLED`, `CODEX_MODEL`, `CODEX_TIMEOUT`, `DB_PATH`, `COOKIE_SECURE` (set it to `true` behind HTTPS), `SESSION_MAX_AGE`, `CRAWL_DELAY`, `PCC_API_BASE`.

## Usage

A freshly initialised database is empty. Fetch some tenders:

```powershell
python scripts\crawl_tenders.py --query 資安 --query 軟體 --pages 2
python scripts\crawl_tenders.py --queries 工程,顧問,雲端,醫療 --pages 3
python scripts\crawl_tenders.py --query 採購 --no-deadline   # list only, fastest, no deadlines
```

Fetch grants:

```powershell
python scripts\crawl_grants.py --all
python scripts\crawl_grants.py --source moc --pages 50
python scripts\crawl_grants.py --source startup_sme --pages 40
python scripts\crawl_grants.py --source moea                 # opens a real Chrome window
copy data\grants_seed.example.json data\grants_seed.json
python scripts\crawl_grants.py --seed data\grants_seed.json
```

Backfill tags and summaries:

```powershell
python scripts\enrich_ai.py --kind tenders --do both --limit 50
python scripts\enrich_ai.py --kind grants  --do both --limit 50
```

Then open `http://127.0.0.1:8000`, enter the password, and search. `launch_board.ps1` starts the server on port 8011 if it is not already answering and opens your default browser at it.

### Running enrichment across several machines

`scripts/dispatch_enrich.py` spreads enrichment jobs over several `codex` workers — a local one and any number reached over SSH — with retry and a health check.

Workers are described in `workers.json`, which **is not in version control** because it names your own machines. Copy `workers.example.json`, edit it, and point `GTS_WORKERS_FILE` elsewhere if you prefer. With no `workers.json` present you get a single local worker and no configuration is needed.

Worker commands are built without any approval- or sandbox-bypass flag. They process text taken straight from crawled bid notices, so that is deliberate — do not add one back without thinking about prompt injection.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/search/tenders?q=&tags=&deadline_from=&deadline_to=&type=&only_active=&page=` | Tender search |
| GET | `/api/search/grants?q=&tags=&target=&deadline_from=&only_active=&page=` | Grant search |
| GET | `/api/tenders/{id}` · `/api/grants/{id}` | Detail |
| POST | `/api/tenders/{id}/summarize` · `/api/grants/{id}/summarize` | Produce or return a cached AI summary |
| GET | `/api/meta/tags` | Known tags |
| GET | `/healthz` | Health check, including whether `codex` is reachable |

`tags` is comma-separated.

## Where the data actually comes from

This section is deliberately blunt, because the answer is messier than "there is an API".

- **Tenders** — `https://pcc-api.openfun.app`, the g0v-maintained mirror of the government procurement site (verified working as of 2026-06). Search results do **not** include the deadline; that requires a second call to the `/api/tender` detail endpoint and parsing "截止投標", which is in Republic-of-China calendar years and is converted automatically. The API returns 403 without a browser `User-Agent` and `Referer`, both of which are sent.
- **Grants** — there is no single clean API. The `data.gov.tw` REST endpoints returned 405/404 in testing. Three sources are implemented (`crawl_grants.py --all`, verified 2026-06):
  - **Startup SME portal** (`startup.sme.gov.tw`) — the SME Administration's cross-ministry roll-up, paginated HTML, roughly 265 records.
  - **Ministry of Culture grants** (`grants.moc.gov.tw`) — its `API/PointListData.jsp` JSON endpoint, reverse-engineered, no viewstate needed, includes opening and closing dates, roughly 44 records.
  - **Ministry of Economic Affairs portal** (`service.moea.gov.tw`) — behind Cloudflare. Needs Playwright driving a real, headed Chrome to pass the challenge before the `Plan/Plan` list can be read, roughly 30 records. Headless is blocked. The cleared token is kept in a persistent browser profile under `data/`.
  - Government sites commonly use TWCA certificates, which are not in `certifi`, so `httpx` fails TLS verification. `app/httpclient.py` uses `truststore` to verify against the OS certificate store instead.
  - Checked and empty: the SME Administration's `list-tw-2411` "grants" listing genuinely returns zero records; its content is reached through the Startup SME portal instead.
  - To add a ministry: fill in a `HtmlSource` in `crawlers/grants.py`, or import JSON with `--seed`.

## Known weaknesses

The test suite records issues it knows about as `xfail`, rather than pretending they are not there. Five remain, and they are correctness rather than security:

- `upsert_tender` and `upsert_award` use nullable identity columns, so SQLite's `UNIQUE` and `=` do not treat two rows with a NULL key as the same record — duplicates are possible.
- Dispatch writes an enrichment result unconditionally, so two workers finishing on the same row can overwrite each other.
- `codex_client.extract_json` returns any JSON value, while its callers expect an object.
- `summarize_tender` calls the external model while the SQLite connection context is still open, holding the connection for the duration of a network round trip.

Four issues that *were* recorded the same way have been fixed, and their tests are now ordinary assertions: the session key no longer defaults to a predictable value, worker commands no longer bypass approvals and the sandbox, and `db.set_tags`, `db.set_summary`, `search.get_one` and `dispatch_enrich.collect_jobs` now validate table and kind names against an allowlist before anything reaches SQL.

## Other limitations

- **Scraped sources break.** These are HTML scrapers against sites that redesign without notice. When a source returns nothing, assume the layout moved.
- **One shared password, no accounts, no rate limiting.** It is a lock on a personal dashboard, not an authorisation system. Put it behind Cloudflare Access or an equivalent before exposing it.
- **The interface, tags and AI summaries are in Traditional Chinese.**
- **Crawling is polite but unthrottled per host beyond `CRAWL_DELAY`.** Be considerate; these are public-sector servers.
- **The MOEA crawler opens a visible browser window** and cannot run unattended on a headless box.
- **AI output is model output.** Tags and the four-part summaries are generated and can be wrong. The original notice is linked from every record; read it before acting on a deadline or an eligibility claim.

## License

MIT. See [LICENSE](LICENSE).
