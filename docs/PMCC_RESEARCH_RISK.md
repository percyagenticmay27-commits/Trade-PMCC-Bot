# Paper research risk monitor

The research monitor manages only the three shadow-study ledgers. It does not open new campaigns or restart short-call income cycles, transfer capital, place broker orders, or manage the funded PMCC account. `python3 -m pmcc research-risk` refreshes held symbols through the existing read-only Alpaca market-data connection, then runs research management with entries disabled. The DigitalOcean user cron now requests a check every minute only inside the verified regular New York session. On a normal day this is 8:30 a.m.–3:00 p.m. Central (390 minute checks, with the close excluded from risk fills). A verified half-day ending 1 p.m. Eastern stops minute checks at noon Central (210 checks). Official holidays get no minute checks. Calendar planning runs at 8:15 a.m. Central on weekdays, with 8:20/8:25 retries; after successful planning, retries use the cached daily plan without another calendar request. The cron entry template is `deploy/pmcc-research-risk.cron`. Actual latency includes collection time, and a file lock prevents overlapping runs. This is polling, not continuous streaming or a guaranteed stop price.

## Initial policy

These thresholds are starting research assumptions, not optimized results. The authoritative defaults live in `pmcc/research_risk.py`; optional overrides live under `risk_policy` in `configs/pmcc-research.json` and are included in the report.

| Trigger | Paper action |
|---|---|
| Long bid down 25% from its purchase ask, or long DTE at/below 180 | Close both legs |
| Combined campaign liquidation P/L at/below -25% of initial net debit | Close both legs |
| Combined campaign liquidation P/L at/above +20% of initial net debit | Close both legs |
| OTM short: spot at/above strike OR current delta at/above 0.60 | Evaluate roll; close both legs if none qualifies |
| Intentionally ITM short: delta rises by at least 0.15 from that short's entry delta | Evaluate roll; close both legs if none qualifies |
| Legacy ITM holding missing entry delta | Close campaign rather than assume a baseline |
| Maximum two tested-short rolls per campaign reached | Close campaign when short is tested again |
| OTM short has earned 60% of original cash premium | Buy back short, retain LEAPS |
| Short at/below one DTE, or ITM within one calendar day of verified ex-dividend | Close short; do not extend via a risk roll |

The combined P/L uses long bid minus short ask, accumulated cash basis, all booked fees and projected closing fees. It includes prior short cycles and roll losses; receiving a credit is not counted as profit. Priced campaign exits require fresh identity-matched option quotes and an officially verified open session; they do not require Greeks or a fresh stock quote. Tested-short decisions and rolls additionally require a fresh stock quote. New replacement contracts still require Greeks. A missing short delta closes a priced campaign conservatively if no earlier exit applies. Gaps and missing data can cause worse outcomes or prevent a simulated fill.

## Roll eligibility

A roll buys the current short at ask and sells the replacement at bid in one local ledger transaction. The replacement must have a higher strike and later expiration, extend no more than 28 calendar days, and expire before the LEAPS. Weekly profiles use replacement DTE 7–21; monthly uses 30–60. OTM replacements must be above spot with delta 0.15–0.35. ITM-profile replacements require delta at least 0.50, below 0.80 and below the current short delta; they may move above spot.

The roll must provide at least $1 credit **after both roll fees**, a spread no wider than $0.05, open interest at least 100, verified standard-contract metadata and synchronized quotes. Earnings/event evidence must clear the replacement expiration. A verified ex-dividend date within that expiration window excludes a roll. Malformed ex-dividend dates conservatively close a priced campaign without aborting other studies. Post-roll strike width must exceed adjusted campaign cash basis, accumulated fees, projected closing fees and $10 reserve. This strict roll safety margin applies even to the ITM study's original-entry width exception. Select the earliest eligible expiration, then the highest eligible strike, then largest credit. If no replacement meets every condition, close both legs at current executable-side paper prices.

## Failure and assignment handling

The heartbeat is `results/pmcc_research_risk_status.json`; decisions, cash changes and fees are in `results/pmcc_research_report.json` and each study's ledger events. REFRESH_FAILED, DATA_UNAVAILABLE and reconciliation states are unresolved risks, not successful exits. No external alert delivery is configured. No guarantee of avoiding early assignment is made.

The collector uses separate risk snapshot/cache/status paths and only held shadow symbols; it does not use CIO/Board discovery. It requests 1–60 DTE replacement contracts plus exact held contracts even outside entry windows. Provider contract identity must match the stored holding. Robinhood UUID holdings require a matching refreshed Robinhood feed; the monitor does not silently relabel an Alpaca OCC identifier as a Robinhood instrument. Missing issuer/event facts prevent rolls, but do not prevent a protective close when fresh held quotes permit it.

Already modeled assignment liabilities and missed expiration halt normal trading for verified reconciliation. Automatic assignment settlement is not implemented. Existing risk checks remain in the manual research command, too. No new entry in the same research invocation is permitted for a symbol just closed by risk management.

## Session scheduling and idle resources

`research-schedule --install` fetches the official Alpaca trading calendar once per successful date and installs date-bound minute entries plus one `research-close` entry at that day's close. It preserves unrelated cron entries. The server timezone must be verified as America/Chicago before installation; Eastern and Central daylight-saving shifts are handled with IANA timezones. Generated day/month entries use an unrestricted day-of-week to avoid cron's OR semantics. Each `research-risk --scheduled` invocation independently validates today's calendar/session before reading positions or fetching quotes. A stale or unavailable plan does not permit a risk cycle. If calendar planning fails, old minute entries are removed and the three morning planner attempts remain; temporary calendar-only recovery runs every five minutes from 8 a.m.–2:55 p.m. Central on weekdays until the calendar recovers. Successful planning removes those recovery jobs. The failure is recorded in `results/pmcc_research_schedule_status.json`.

When no positions are open, the monitor writes a lightweight heartbeat and skips both market-data collection and the full symbol/candidate rescan. The monitor does not scan for new entries. This eliminates 24-hour position polling and most idle-cycle work; it is not a claim of measured CPU or dollar savings.

The one end-of-session review reads the shadow ledgers without refreshing quotes, simulating fills, closing options, or opening trades. It records unresolved overnight positions, short-call assignment exposure, expiry review needs and reconciliation requirements in `results/pmcc_research_close_review.json`. It preserves the last mark timestamp rather than presenting closing evidence as current. A daily receipt and lock prevent duplicate reviews. A close review is an exposure audit, not a verified assignment/settlement feed.

Manual `research-risk` remains available for diagnostics; only the scheduled form applies the preflight session guard. The paper execution engine itself still requires a verified open regular session for any fill.
