# Independent PMCC research studies

Created November 4, 2026. This is a separate research command, not a replacement for PMCC-001 entry governance. It does not consume CIO/Board rankings, alter their stock filters, request additional capital, or submit broker orders.

## Three profiles

| Profile | Short calls | Purpose |
|---|---|---|
| WEEKLY_OTM | 7–14 DTE, OTM, delta 0.15–0.50 | Compare weekly time-premium income and transaction drag |
| MONTHLY_OTM | 30–45 DTE, OTM, delta 0.15–0.50 | Compare monthly income and lower transaction frequency |
| WEEKLY_ITM | 7–14 DTE, ITM, delta 0.50–1.00 | Separately study low net delta, intrinsic cashflows and assignment exposure |

All use long calls 365–730 DTE, below the stock price, minimum observed delta 0.80 with no 0.90 ceiling, and no more than 20% of ask in extrinsic value. Missing long delta can produce a labeled arithmetic observation when the intrinsic-value test is met, but blocks paper entry. Selected-stock price must be $5–$20. A million average daily shares is required for entry; no fundamental-growth or momentum score is used. The broad discovery list reports its average-volume measurement period explicitly.

Three independent $5,000 shadow benchmarks are bookkeeping counterfactuals. They are not $15,000 of additional platform allocation. Per study: gross long cost cap 25%, sector cap 40%, cash reserve 20%, maximum four symbols; one contract pair per symbol. Fee default $0.65 per option contract. Existing funded PMCC ledger and allocation registry are not modified.

## Economics versus performance

Buying uses ask; selling uses bid. The report separates short intrinsic value from time premium, shows the exercise-resolution cushion, estimated round-trip spread/fee cost, and a per-cycle amortization of long-leg spread cost. The arithmetic score is a month-equivalent time-premium-less-cost proxy, not expected net profit, annual return, fill probability or a performance winner. Long-spread amortization assumes the holding could support repeated cycles; it is not a guaranteed realized cost allocation.

Initial and replacement ITM shorts may have negative exercise cushion only when allow_itm_width_risk=true in the isolated research config. Every initial exception is recorded in position evidence, and replacement exceptions have explicit event reasons. This does not relax the funded PMCC strategy. ITM cases with bid below intrinsic or time premium below the estimated cycle costs cannot open even in the shadow study.

Observed cash premium is never treated as net profit. Forward performance uses ledger equity including the long call and short liability, realized campaign P/L after fees, win rate only after closed campaigns, peak-to-trough dollar drawdown, and assignment-avoidance events. Stale open positions produce DATA_UNAVAILABLE performance; their equity remains the last observed mark, not a current liquidation claim. No automatic winner or promotion is declared.

## Commands

From Trade-PMCC-Bot:

    python3 -m pmcc research

Publishes results/pmcc_research_report.json, initializes state/research/{PROFILE}.sqlite3 and observes/manages any existing shadow positions. It does not open positions by default. It reads inputs/pmcc_research_observations.json and compatible normalized Robinhood/Alpaca inputs. It does not read the host market shortlist.

    python3 -m pmcc research --paper-entry

Allows only fresh, eligible local paper entries/replacement shorts during a verified open session. Missing metadata, quote identity, required Greeks, event evidence, sector or liquidity blocks entry. IV Rank is reported when available; it is not invented and is not a hard gate in these research variants. Missing IV Rank means the IV hypothesis is uncontrolled. Standard contract metadata must be verified rather than inferred from a 100 multiplier.

New-entry research remains manual. A separate `research-risk` monitor now refreshes and manages held shadow positions without opening new campaigns; see [PMCC_RESEARCH_RISK.md](PMCC_RESEARCH_RISK.md). Closing snapshots do not start a forward study.

## Management boundaries

Retain the long while closing/replacing shorts. Long bid down 25% from entry or long DTE <=180 closes the campaign using bid/ask paper fills. OTM shorts close after 60% gross short-premium capture. ITM shorts are not treated as equivalent premium-decay trades. Short DTE <=1 closes the short; an ITM short also closes within one calendar day before a verified ex-dividend date. New shorts require event clearance and the profile's entry checks. Early assignment can occur before these preventive exits; this is not a guarantee of avoiding assignment.

Missed expiration does not fabricate expiry value or exercise. The position becomes EXPIRY_RECONCILIATION_REQUIRED, preserves cash and the last equity mark, blocks fresh entry on the symbol and requests verified resolution. Automatic settlement/assignment reconciliation is not implemented by this feature. Assignment evaluation is currently exposure disclosure and preventive management, not a calibrated assignment simulator.

## Initial observation run

Closing/as-of November 4 data: 57 priced/liquid stock rows in the independent list; detailed option samples on F, PCG, CAG, ACI, MAT, HBAN, WU and RITM. Some names lack weekly expirations or eligible low-extrinsic high-delta longs. The saved observation run produces 16 weekly OTM, 28 monthly OTM and 21 weekly ITM arithmetic combinations. All three accounts are NOT_STARTED with no fills and no winner. Closing quotes, missing verified contract metadata and incomplete event data prevent entry. These counts refer to sampled expirations/strikes, not exhaustive market scans.

F: weekly OTM $7 long / $13 short showed $589 debit, $11 time premium and $4.18 estimated amortized cycle cost. Monthly OTM $7/$13 showed $574 debit, $26 time premium and $7.24 estimated cycle cost. Weekly ITM $7/$12.50 showed $567 debit, $15.50 time premium and $6.18 estimated cycle cost, with a negative exercise cushion. These are observations, not current executable offers or realized returns.

Ford-like follow-up candidates include PCG and CAG, with ACI, MAT, HBAN, WU and RITM retained for further chain checks. Similar price/liquidity does not imply equivalent business risk or option quality.
