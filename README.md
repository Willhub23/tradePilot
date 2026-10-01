# TradePilot frontend

A modular React + TypeScript frontend for a future trading research platform. This version contains only a dark, responsive demo dashboard. All prices, balances, positions, and agent activity are fictional. The agent is **INACTIVE**, and the workspace is visibly marked **PAPER TRADING**. Nothing connects to a broker or executes trades.

## Run locally

Use Node.js 22 LTS and npm.

```sh
npm install
npm run dev
```

Open the local URL printed by Vite (normally http://localhost:5173).

```sh
npm run typecheck   # Strict TypeScript validation
npm run build       # TypeScript validation and production bundle in dist/
npm run preview     # Serve the production build locally
```

## Structure and decisions

```text
src/
  components/ui/       Shared cards, badges, formatting, and feature headings
  components/layout/   Application shell, sidebar, and header
  features/dashboard/  Dashboard composition, summary cards, data-loading hook
  features/market/     Market page and lightweight SVG price chart
  features/strategies/ Strategy placeholder
  features/agent/      Inactive agent overview and fictional activity
  features/trading/    Simulated open positions table
  features/settings/   Read-only workspace configuration
  services/            Async data access implementation
  types/               Shared models and service contract
```

`App.tsx` only selects the page and connects the layout to loaded data. Hash navigation supports direct links and browser history without a routing dependency. Feature components receive typed data through props. `ResearchService` defines the async data boundary; replace the implementation in `services/researchService.ts` with an API adapter later while retaining the UI contracts. The loading hook handles errors and ignores responses after unmount.

The chart uses responsive SVG rather than a charting library. Tailwind CSS v4 is integrated through Vite; shared CSS provides the dark theme and responsive component styling. Lucide supplies icons. There are no external fonts or remotely loaded assets.

Dashboard includes portfolio balance, daily P/L, position count, inactive agent status, XAU/USD history, open positions, and sample agent activity. Other navigation pages intentionally remain minimal. The fixture is a fixed fictional snapshot, not current market information; balance and daily P/L are separate mock account metrics rather than calculations from open positions.

No authentication, database, backend, LLM integration, live market feeds, broker connections, backtesting, or automated execution is implemented.
