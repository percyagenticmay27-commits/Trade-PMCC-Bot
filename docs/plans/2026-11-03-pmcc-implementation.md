# PMCC-001 Implementation Plan

> **For agentic workers:** Use the host's available task-by-task implementation workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install the approved $5,000 PMCC paper-only strategy with CIO UI and read-only Robinhood research input.

**Architecture:** Independent Python standard-library strategy, local SQLite paper ledger, atomic strategy JSON reports. Existing Dividend host consumes artifacts and serves PMCC workspace; no external strategy imports or broker calls.

**Tech Stack:** Python 3, Decimal, SQLite, unittest, systemd, existing WSGI CIO portal.

## Global Constraints

Paper only; all broker execution, order placement, live trading and capital movement disabled. Separate $5,000 virtual allocation; 25% gross-long position cap, 20% reserve, 25% long stop and 20% campaign stop. Preserve all pre-existing work and other strategies' allocations. No automatic rescue roll or synthetic production quotes. Contract checks apply to real expiry/strike/UUID joins.

---

### Task 1: Candidate and provider clearance
Files: pmcc/provider.py, pmcc/strategy.py; tests/test_provider.py, tests/test_strategy.py.
Consumes raw OPTION-001-compatible Robinhood research snapshots. Produces canonical contracts and evaluate_candidate(symbol,evidence,contracts,now,config) decisions; rank_candidates(rows) sorts only eligible symbols.
- [x] Add fixture-based behavior tests for $390 debit/$110 width cushion, data gaps, price/spread/expiry/Greek/event gates and malformed inputs.
- [x] Observe assertion failures, implement normalized fields without timestamp/Greek substitution, run focused suites to pass.
- [x] Inspect actual Robinhood chain/instruments/quotes for F across two expirations; preserve captured source responses separately from production state.

### Task 2: Transactional paper lifecycle
Files: pmcc/ledger.py, pmcc/management.py; tests/test_ledger.py, tests/test_management.py, tests/test_assignment.py.
Consumes cleared candidates and executable fresh held-contract quotes. Produces atomic paired events, cash, positions and prioritized management decisions.
- [x] Test before implementation: duplicate runs, gross allocation/reserve, short liabilities, stop/profit/roll actions and assignment delivery/dividend accounting.
- [x] Implement integer-cent cash plus Decimal calculations, SQLite BEGIN IMMEDIATE, idempotent keys and no network calls.
- [x] Run focused test commands and repair demonstrated failures.

### Task 3: Runnable strategy and reporting
Files: pmcc/runtime.py, pmcc/dashboard.py, pmcc/__main__.py, configs/pmcc-001.json, deploy/pmcc-cycle.*; tests/test_runtime.py.
Consumes read-only research input and verified exchange session. Produces summary, report, board, watchlist, positions, refresh requests and accessible HTML.
- [x] Test missing/stale snapshots, outside-session rejection, idempotent opening and publication.
- [x] Implement process-held lock, management-first cycles, 10 ET entry window and five-minute timer.
- [ ] Verify responsive render and tab keyboard/click behavior against real empty/data-unavailable state.

### Task 4: Existing CIO integration and deployment
Files: scripts/install_cio_integration.py; target host registry, KPI adapter, workspace renderer and authenticated route. Tests execute actual WSGI response and host discovery/KPI functions.
- [x] Back up exact touched host files; test route before patch, confirm missing route, then apply surgical PMCC-only additions.
- [x] Register PMCC-001 without replacing other strategies. Record approved additional $5,000 simulated deposit/allocation using existing accounting functions and audit logs; preserve the existing budgets.
- [x] Run host route/registry/KPI regression suites, deploy new module, initialize empty production ledger and enable timer.
- [ ] Verify HTTP workspace, four strategy IDs, $5,000 PMCC allocation, paper flags, timer status, refresh requests and no test positions.

Unresolved product decisions: none. External data dependency: the read-only collector must supply all required inputs; absent/stale evidence blocks entries. No git commit or push is required by the user.

### Continuation: persistent current-data collection

- [x] Inspect Option Bot/Barney read-only Alpaca transport and credential availability without outputting values.
- [x] Verify actual metadata/calendar/SIP-bar/option-snapshot GET responses.
- [x] Add alpaca_data.py, collector.py and shared market_clock.py, preserving source attribution, timestamps and approved gates.
- [x] Test source failover, economic-contract identity, usable quote precedence and absence of invented historical inputs.
- [x] Install user-service refresh pre-step and verify automatic collection plus64 PMCC tests on the server.
- [ ] Administrator reload of the existing portal service; live route currently404.
- [ ] Supply verified issuer facts and compatible historical IV/full-chain volume datasets. No strategy gate is relaxed.
