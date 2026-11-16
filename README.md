# Quant

## News API setup

This project includes a free-tier news provider adapter using Finnhub.

1. Copy the example environment file:
   - `copy .env.example .env`
2. Add your key:
   - `FINNHUB_API_KEY=your_key_here`
3. Use a user-maintained ticker universe in `RESEARCH_SYMBOLS` if you want to change the defaults.

Finnhub requests are made only when a refresh or quote fetch is explicitly run. If no key is configured, live price refresh is unavailable.

## Public news feeds

The provider registry also supports the public-feed approach used by
[`trader-news`](https://github.com/Jakeschincariol/trader-skill): Yahoo Finance
headline RSS for an explicitly supplied ticker list, plus SEC EDGAR 8-K feeds
when a valid contact User-Agent is configured. Headlines and filings are
normalized into the existing SQLite news-event pipeline; important 8-K items
are marked in their titles. These are informational sources, not trading
recommendations.

Set `SEC_USER_AGENT` to a real contact string, for example
`Quant Research your.name@example.com`, to enable SEC requests. Without it,
the public provider still fetches Yahoo RSS and skips SEC filings. The provider
does not make requests until `fetch_live_news` is called with symbols.

## Autonomous research cycle

Run one live refresh and research cycle:

```powershell
.\.venv\Scripts\python.exe -m src.autonomous --top 10
```

Keep the process running for hourly cycles:

```powershell
.\.venv\Scripts\python.exe -m src.autonomous --watch --interval 3600
```

Each cycle refreshes up to 90 days of daily candles for `RESEARCH_SYMBOLS`,
selects the most volatile symbols, fetches their news, generates research
signals, resolves matured outcomes, and reports a chronological out-of-sample
evaluation with 5 basis points of estimated cost per side. The evaluator is a
diagnostic for the fixed strategies, not a forecast or investment recommendation.
News can adjust confidence slightly but does not reverse a strategy's signal.

The simulated broker can persist cash, positions, and order IDs across
restarts when constructed with `state_db_path="data/paper_broker.db"`. This is
opt-in; without it, broker state remains in memory. It still does not submit
real orders.
