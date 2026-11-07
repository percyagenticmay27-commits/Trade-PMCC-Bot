# PMCC-001 implementation handoff

November 3, 2026. The approved PMCC paper module is deployed at
`/home/tradebot/Trade-PMCC-Bot` on `ai-cio-platform`. Current-data collection
operates automatically. Full entry readiness and the running portal route
remain incomplete for the reasons below.

## Implemented and verified

- Independent $5,000 virtual allocation, executive-board gate reports, ranked
  allocation, paper ledger, holdings/history/performance/journal/alerts/settings.
- Existing host accounting recorded the approved additional $5,000 paper funding
  and allocation. Current other allocations are Dividend $5,000, Swing $4,500,
  Option $4,000; unallocated host paper cash $1,500.
- Paired local fills, integer-cent cash transactions, short-liability valuation,
  25% gross-long cap, 40% sector cap, 20% reserve, duplicate-run protection,
  stops, short profit/time exits, tested-short credit rolls and event/expiry exits.
- Isolated assignment scenarios create and reconcile 100-share delivery and
  dividend liabilities; they never become production trades.
- All live trading, broker execution, broker order placement and capital movement
  flags remain false. There is no order submission implementation.
- Full PMCC workspace and CIO registration/KPI/report integration. Market Data
  shows observed per-strike prices, Greeks, liquidity, sources and timestamps.
- Active user systemd timer runs every five minutes. Its ExecStartPre performs
  `python3 -m pmcc refresh`; ExecStart performs `python3 -m pmcc cycle`. Entries
  require 10:00–10:04 ET and a verified open session. The user manager has linger.

## Automatic market data

PMCC inspected and reused the existing Option Bot/Barney Alpaca market-data
access. Credential values are read in memory, never copied into artifacts or
printed. Only fixed-host, whitelisted GET market/calendar/contract endpoints
exist; account and order endpoints are forbidden.

Robinhood research snapshots remain supported and preferred when their stock
and both option horizons have fresh usable data. Alpaca supplies persistent
fallback quotes, Greeks, contract metadata, completed SIP daily bars and the
exchange calendar. Provider attribution and quote timestamps remain unchanged.
A newer unusable primary quote cannot hide a verified executable fallback for
a held contract. Provider IDs are matched only through verified equivalent
underlying/strike/expiry/right/multiplier metadata; original entry IDs remain
in the paper position and audit events.

A live server run returned 155 Ford option snapshots and 344 completed daily
bars. Fresh counts vary by run; the independently observed 20:30:01Z run had
121 quotes within the freshness limit. Stale contracts remain blocked.

## Remaining commissioning requirements

1. The portal code passes in-process WSGI checks, but its already-running
   service still uses the previous route table. The live local route returned
   HTTP404. An administrator must run this in the DigitalOcean root console:

   ```bash
   systemctl restart dividend-allocation.service
   ```

   Ordinary `tradebot` execution returned `Interactive authentication required`.
   No authentication or privilege boundary was bypassed. After reload verify
   `/pmcc-dashboard` and the PMCC-001 executive workspace. No new public listener
   or authentication bypass was added.

2. Entry evidence still lacks verified issuer/event facts, 252 completed sessions
   of a consistent 30-day ATM IV series, and 20 completed sessions of full-chain
   option volume. Current prices/Greeks/calendar work automatically, but these
   histories are not synthesized from stock volatility or a partial option
   universe. Status remains DATA_UNAVAILABLE until the approved gates pass.

Optional input files are keyed by symbol:

- `inputs/issuer_evidence.json`: current issuer financial/event facts matching
  the canonical evidence fields in `tests/test_strategy.py`.
- `inputs/iv_history.json`: rows with date, iv, series
  `30_DAY_ATM_CONSTANT_MATURITY`, method `CALL_ATM_VARIANCE_INTERPOLATION`, source
  `ALPACA_MARKET_DATA`. Dates must match the last 252 completed exchange sessions.
- `inputs/chain_volume_history.json`: date, volume, coverage
  `ALL_STANDARD_CONTRACTS_ALL_EXPIRATIONS`, for the last 20 completed sessions.

External issuer fields cannot overwrite the collector's prices or calculated
technical indicators. A historical-data source must satisfy the agreed series
and coverage definitions. Missing history is a data commissioning requirement,
not permission to relax the filters.

## Verification and practical limits

64 PMCC tests pass, including source failover, malformed-input isolation,
wrong-UUID rejection, priced exits with missing Greeks, contract metadata,
previous-session volume, stops, rolling and cash/short-liability accounting.
The affected existing CIO registry/KPI/workspace and cash/governance/route suites
passed 96 tests. Final source hashes and live status are in `docs/acceptance.json`.
Independent read-only verification matched all eleven deployed module hashes,
confirmed the automatic collector/service/timer, and confirmed no production
paper positions with $5,000 cash/equity.

Browser navigation to the public route encountered an upstream connection
failure. Mobile/desktop rendered layout and browser interactions are unverified.
Application-level HTML and CIO workspace assembly were checked; they do not
prove the running public page is available.

Stops are triggers, not guaranteed loss caps through gaps. Paper commissions
are an explicit assumption, default $.65/contract/side. No performance or win-rate
claim is made before validation on suitable historical data.

## Operations

```bash
cd /home/tradebot/Trade-PMCC-Bot
python3 -m pmcc refresh
python3 -m pmcc manage
python3 -m pmcc status
python3 -m pmcc collection-plan
python3 -m pmcc ingest --source /absolute/path/actual-robinhood-snapshot.json
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status pmcc-cycle.timer
```

Runtime snapshots, credentials, ledgers and original host backups are excluded
from source distribution. Test fixtures use fictional TEST contracts in isolated
ledgers. No GitHub repository, commit, push or real-money allocation was created.

Primary API references:
https://docs.alpaca.markets/us/reference/get-options-contracts
https://docs.alpaca.markets/us/reference/optionsnapshots
https://docs.alpaca.markets/us/reference/optionchain
