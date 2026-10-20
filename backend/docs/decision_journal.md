# Decision Journal

The React dashboard has a new **Decision Journal** navigation item at `/#journal`. It reads PostgreSQL evaluations and their explicitly linked AI decision and broker execution records. No strategy evaluation, inference, market fetching or order operation is performed by the journal API. No tables or duplicate storage were added.

## Run without starting trading

Run the journal-only application, **not `app.main:app`**, to view records without starting the existing scheduler or cTrader runtime. Use a free port 8000 (the existing Vite proxy target); do not restart or replace any running trading process just to view the journal.

Backend terminal:

```sh
cd /Users/admin/Documents/Repos.nosync/tradePilot/backend
source .venv/bin/activate
uvicorn app.journal_main:app --env-file .env --host 127.0.0.1 --port 8000
```

This uses the existing `DATABASE_URL` from `.env`. The journal-only app does not import the autonomous startup module. It makes SELECT queries only. It does not migrate or create tables. Use an existing read-only database role if preferred. Keep the server on localhost; authentication for remotely exposing journal data is outside this change.

Frontend terminal:

```sh
cd /Users/admin/Documents/Repos.nosync/tradePilot
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173/#journal` (or the local port Vite reports). The journal loads independently of the dashboard's fictional research dataset. No trading activation flag is needed. The API is also mounted in the existing application for deployments that already run it; the isolated journal command is the safe choice for this task.

## API

- `GET /api/journal/evaluations?page=1&page_size=20` — newest first with stable ID ordering; page size 1–100.
- `GET /api/journal/evaluations/{id}` — interpreted details, linked AI/broker records and raw data; missing ID returns 404.
- `GET /api/journal/summary` — counts, observation-only count and rejection-code frequencies.

History and summary share optional `since`, `until`, `outcome`, `setup`, `direction` filters. Dates must be timezone-aware ISO timestamps. `since` is inclusive and `until` exclusive; the UI converts local date input to UTC. Direction filters match recorded proposals only: no directional guess is made from bullish/bearish structure or agent action.

Outcome is the latest known pipeline stage. Persisted broker status overrides a stale cycle status (for example, an asynchronously confirmed fill). Broker rejection/cancellation/uncertainty and prepared requests are distinct from strategy eligibility. An AI HOLD is separate from a strategy rejection; observation is a separate mode label rather than an execution claim. Eligible proposals are not execution approvals. Summary executed counts include submitted/filled stage rows, not prepared intents; partial fills and cancelled/rejected orders retain their exact recorded volume and status in details. Frequencies count a reason code once per evaluation. Metrics use the active filters.

## Details and interpretation

Expandable details show proposal levels, recorded net risk/reward and holding period, setup thesis/invalidation, supporting and conflicting evidence, missing inputs, specific rejection/block reasons, ATR/RSI/ADX, structure and support/resistance, spread/cost evidence, source timestamps and price discrepancy. Structure is displayed separately from the thesis: a short resistance-rejection scalp within bullish structure is not relabelled invalid.

Detection/strategy, AI, validation, submission and fill-confirmation timings are shown only when persisted. Broker event time and local confirmation receipt time remain distinct. Missing historical evidence is labelled `Not recorded`, never zero. Linked records expand inline with their identifiers; evaluation permalinks work even when a record is outside the current page. Full JSON is available in the collapsed technical section.

Limitations: existing records may lack fields; no historical indicators, price comparisons or latency are invented. Refresh is manual; there is no live stream. Overview counts describe the latest pipeline stage, not an additive funnel or profitability. Summary currently reads the selected record set, which is appropriate for the current small journal; use date filters as history grows. No account balance/history, order controls or dashboard trading controls were added.

## Verification

```sh
# Backend; existing tests use isolated SQLite sessions
python -m pytest
# Repository root
npm run test:journal
npm run typecheck
npm run build
```

The frontend uses Node's built-in test runner with the existing TypeScript transpiler; no testing dependency was added. Client tests cover UTC filter encoding, invalid dates, GET-only requests/cancellation and API errors. Backend API tests cover pagination, filters, summaries, sparse records, linked states, countertrend presentation and journal-only startup. Browser smoke testing uses only synthetic SQLite fixtures, not the operator's PostgreSQL records.
