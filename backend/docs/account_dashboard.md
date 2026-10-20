# Read-only cTrader DEMO Account Dashboard

Route: `/#account`, navigation **Broker Account**. This page is distinct from the Decision Journal and the dashboard's fictional research workspace. No balances or trades are fabricated.

## Launch manually; do not start the trading runtime

The account connection is **disabled by default**. No real authentication was attempted during implementation or automated tests.

With the existing `.env` holding the PostgreSQL URL, cTrader application credentials and access token, use:

```sh
cd /Users/admin/Documents/Repos.nosync/tradePilot/backend
source .venv/bin/activate
export ACCOUNT_DASHBOARD_ENABLED=true
export CTRADER_ENVIRONMENT=demo
export CTRADER_ACCOUNT_ID=49167599
uvicorn app.journal_main:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
```

In a second terminal:

```sh
cd /Users/admin/Documents/Repos.nosync/tradePilot
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173/#account` (use the port Vite reports if different). The existing Vite proxy points `/api` to port 8000. For journal-only access without any broker authentication, leave `ACCOUNT_DASHBOARD_ENABLED=false`.

Use **`app.journal_main:app`**, never `app.main:app` for this workflow. No scheduler, runtime recovery/management worker, order handler, Ollama, strategy cycle or account mutation endpoints are initialized. Existing DEMO identity verification checks the configured account against broker evidence (`isLive=false`) and the access-token response. View-scoped tokens are accepted by the new read-only client mode; existing trading-client scope requirements remain unchanged.

The server takes the existing PostgreSQL advisory ownership lock **before starting authentication**. If a trading/observation process owns that lock, the account connection is refused and endpoints return 503; journal access remains available. Do not stop or resume an existing runtime automatically. There is no cross-process connection sharing in this feature. Use one serving worker, no reload, and do not expose this unauthenticated local dashboard publicly.

## Broker sources and normalization

- `ProtoOATraderReq/Res`: account identity, balance and monetary exponent.
- `ProtoOAAssetListReq/Res`: resolves `depositAssetId` to the actual account currency. No USD/EUR default or local FX conversion.
- `ProtoOAReconcileReq/Res`: current positions, asset-unit volumes, entry/protection levels and broker timestamps.
- `ProtoOAGetPositionUnrealizedPnLReq/Res`: broker-calculated **net** unrealized P&L, in deposit currency. Position IDs must exactly match the reconciliation snapshot before aggregation.
- `ProtoOASymbolsListReq/Res`: symbol labels. Unknown/archived labels remain IDs rather than guessed instruments.
- `ProtoOADealListReq/Res`: filled/partially filled closing deals with entry/exit prices, gross realized profit, swap, commission, conversion fee and closed volume.

Sources: [official model definitions](https://help.ctrader.com/open-api/model-messages/), [P&L calculation](https://help.ctrader.com/open-api/profit-loss-calculation/), [protocol messages](https://help.ctrader.com/open-api/messages/). Installed SDK protobuf descriptors are used directly; no SDK replacement or dependency was added.

Monetary values use Decimal and each relevant message's own `moneyDigits`, then travel as exact decimal strings. Missing scaling means unavailable; optional default protobuf zeros are not silently treated as reported fields. Volumes are divided by 100 into **asset units**, never assumed to be lots; lot values remain unavailable without verified lotSize. Broker millisecond timestamps normalize to UTC. Retrieval timestamps are local response receipt times, not market-event timestamps.

Equity is derived as balance + broker net unrealized P&L only when all three broker bonus fields are explicitly zero and inputs/scaling are known. Otherwise it is unavailable with a reason. The API does not pretend to receive a direct broker equity field.

Realized net after **reported** costs is gross + signed swap − commission charge magnitude − available conversion fee. Raw broker commission is retained. Closing-detail commission is counted once; top-level deal commission is not added again. A missing optional conversion fee is null and `costs_complete=false`; the partial net is explicitly labelled after available costs, not a fully verified all-cost return. Missing required monetary scaling makes derived net/performance unavailable. Unrelated cash movements/rebates/copy charges are not reconstructed as deal P&L.

## API contracts

All methods are GET-only:

- `/api/account/overview`
- `/api/account/positions`
- `/api/account/trades?page=1&page_size=20&since=...&until=...`
- `/api/account/performance?since=...&until=...`

History/performance accept timezone-aware `since` inclusive, `until` exclusive. Default window is 30 days; maximum 90 days. Page size is 1–100. History follows broker `hasMore` by dividing time windows without dropping equal-timestamp boundary deals, deduplicates deal IDs and caps reads at 31 requests plus a 20-second between-request budget. A broker truncation that cannot be safely exhausted returns unavailable rather than incomplete statistics. A single SDK send retains its existing bounded timeout. Historical reads are paced to at most four starts per second.

One lock serializes retrieval. Identical results are cached for up to 30 seconds (maximum 16 keys); disconnected, unverified, generation-changed or ownership-lost sessions cannot return cached values as fresh. Envelope fields include account ID, environment, retrieval time, cache state and age. The frontend has manual refresh and snapshot labels; no background account polling. Transport, rate-limit, incomplete-response and authentication errors return 503, not old data or zeros.

Performance groups realized closing deals **by position within the window**, including partial closes. This avoids counting multiple partial fills as independent wins, but is not a whole-lifetime closed-position strategy report. Zero-realization groups stay in the win-rate denominator. Profit factor requires both winning and losing groups; no infinite value is invented. Missing historical mark-to-market prevents an equity curve, drawdown or cashflow-adjusted return from being reconstructed. The frontend states the window and calculation basis.

## TradePilot links

Persisted order/position ID matches produce links to existing execution/decision/evaluation IDs without modifying records. A known conflicting stored account ID is excluded. Legacy execution requests lack account scope, so their matching IDs are explicitly **unverified**, not confirmed attribution. A matching TradePilot-opened position does not prove that TradePilot initiated its closing deal. Unmatched trades remain broker-only activity. No attribution from symbols, times, labels or direction.

## Safety and checks

The existing CTraderClient has an opt-in read-only request allowlist checked both before enqueueing and on the reactor. Order creation, position close, SL/TP amendment and all other unlisted requests are refused. Both order and close guards remain false. Incidental execution events are ignored by this inspection session rather than mutating PostgreSQL history. The ownership lease is released on authentication failure and graceful shutdown. Existing trading behavior defaults are unchanged. No schema changes.

```sh
# backend, isolated SQLite/protobuf mocks only
python -m pytest
# repository root
npm run test:journal
npm run test:account
npm run typecheck
npm run build
```

A real DEMO handshake and actual account numbers remain an operator verification step. Do not run them as part of automated checks. No Uvicorn process, real authentication, broker order or AI inference was started during implementation.
