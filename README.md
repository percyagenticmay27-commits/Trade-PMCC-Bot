# Trade PMCC Bot — PMCC-001

Independent $5,000 paper-only PMCC strategy for the existing AI CIO platform.
No broker execution client, credentials, account-order endpoint or live-mode UI.

## Run

Python 3.10+; standard library only.

```
python3 -m unittest discover -s tests -v
python3 -m pmcc ingest --source /absolute/path/actual-robinhood-research.json
python3 -m pmcc cycle
python3 -m pmcc manage
python3 -m pmcc status
python3 -m pmcc collection-plan
```

The server timer runs every five minutes. Entries are restricted to 10:00–10:04 ET
and a verified open exchange session. Position management precedes new entries.
Time windows use America/New_York; UI timestamps use America/Chicago.

## Data boundary

Matches OPTION-001's external read-only Robinhood MCP collection pattern.
The ChatGPT connector is not callable directly by a standalone server process.
A Robinhood collector can read `results/pmcc_refresh_request.json`, fetch real
Robinhood evidence, and ingest it. A timer does not manufacture fresh quotes.
Existing credential values and Option Bot snapshots are not modified. The persistent Alpaca fallback is described below.

Required tool sequence: get_option_chains -> get_option_instruments (calls,
active/tradable, 365–540 and 30–45 DTE, all pages) -> get_option_quotes by UUID.
Join on exact instrument IDs. Fetch held instruments even if they no longer
qualify as new candidates. Keep all original quote times and null Greeks.
Get underlying quotes, completed daily history, issuer filings, verified earnings
and ex-dividend dates, a verified exchange calendar, and 252 sessions of a
consistent 30-day ATM IV series. Current volume is not prior-session volume.

The snapshot envelope is the same as OPTION-001: `source=ROBINHOOD_MCP_RESEARCH`,
`research_only=true`, `order_submission_enabled=false`, and `symbols` mapping.
Each symbol contains `chains`, `contracts` (instrument metadata plus nested
`quote`), and `evidence`. See `tests/test_provider.py` for the source envelope and
`tests/test_strategy.py` for the required canonical evidence fields. Fixtures are
fictional TEST data and never shipped as production positions.

`session` must contain date, timezone-aware open/close, next_session_date,
previous_session_date, verified=true and source ALPACA_CALENDAR or NYSE_CALENDAR.
Prior-session contract volume must match that immediately preceding session.
Missing inputs remain DATA_UNAVAILABLE and produce refresh requests.

Discovery starts with Ford and also imports sub-$20 symbols from the platform's
neutral stock snapshot when available. This is only a shortlist: no symbol
bypasses PMCC issuer, trend, IV, event, contract or allocation gates.

## Rules and accounting

Long calls: 365–540 DTE, delta .80–.90 (target .85), <=15% extrinsic.
Short calls: 30–45 DTE, delta .20–.30, >=.30 bid; width exceeds net debit,
round-trip fees and $10 per diagonal. One standard 100-share call per leg.
25% gross-long position cap, 40% sector cap, 20% cash reserve, <=4 underlyings.
Close shorts at 60% capture or 21 DTE. Credit rolls must pass all replacement
gates and update campaign basis. Stop long at -25% or campaign at -20% initial
debit, whichever triggers first. No automatic averaging down or rescue roll.

SQLite transactions atomically apply paired paper fills and cash changes, with
idempotent event keys. Executable bid/ask marks include short liabilities.
Fees default to .65/contract/side; this is an assumption, not a broker fee quote.
Short cash receipts are not booked as immediate profit. Market-value and P/L
reports disclose missing or stale marks. Stops cannot guarantee gap-loss caps.

Assignment scenarios are modeled in isolated test ledgers, explicitly marked
as scenarios. They create -100 shares, strike proceeds and dividend liabilities,
then reconcile with a stock repurchase and long-call sale. No external broker
assignment is inferred, no automatic long exercise is assumed, and scenario
fixtures never become production trades.

## CIO interface

Registration contract v2, strategy-owned JSON reports, atomic publication and a
full PMCC workspace served through the existing authenticated CIO portal at
`/pmcc-dashboard`. Overview, Discovery, Watchlist, Executive Board, Allocation,
Holdings, Trade History, Performance, Journal, Alerts and Settings are available.
Settings show locked safety flags and current strategy configuration; edits are
made in the configuration file, with no live-mode control.

`inputs`, `results`, `state` and `research` are runtime-only and excluded from git.
The initial production paper ledger has no test fills. Registry health and data
availability are distinct; missing market evidence is never advertised as ready.

## Persistent market-data fallback

The installed user timer now runs `python3 -m pmcc refresh` before each cycle.
This uses the same existing Alpaca market-data access as Option Bot/Barney,
with a whitelist of GET market/calendar/contract endpoints. It cannot submit
orders or query account endpoints. It supplies live stock/option prices,
Greeks, contract metadata, completed SIP daily bars and calendar automatically.
Robinhood snapshots remain supported and preferred when their stock and both
option horizons have fresh usable evidence. Provider attribution stays explicit.

Market Data in the full PMCC workspace shows actual per-strike observations.
Current data is separate from issuer/event evidence and historical eligibility
series. Read the continuation update in docs/IMPLEMENTATION_HANDOFF.md for the
required verified historical input formats. Missing history remains a block.
