"""Read-only Ford pilot for issuer evidence and a closing IV history."""

import fcntl
import csv
import io
import json
import os
import subprocess
import urllib.request
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from pmcc.runtime import atomic, read_json
from pmcc.strategy import fresh, num, stamp

IV_SERIES = '30_DAY_ATM_CONSTANT_MATURITY'
IV_METHOD = 'CALL_ATM_VARIANCE_INTERPOLATION'
CIK = '0000037996'
OCC_COVERAGE = 'ALL_STANDARD_CONTRACTS_ALL_EXPIRATIONS'


def occ_ford_contract_volume(text, day):
    rows = csv.DictReader(io.StringIO(text))
    columns = 'quantity underlying symbol actype porc exchange actdate'.split()
    if rows.fieldnames != columns:
        raise ValueError('OCC_VOLUME_SCHEMA')
    expected = date.fromisoformat(day).strftime('%m/%d/%Y')
    totals, seen = Counter(), set()
    for row in rows:
        key = row['exchange'], row['porc'], row['actype']
        if (row.get(None) not in (None, ['']) or row['symbol'] != 'F' or row['underlying'] != 'F'
                or row['actdate'] != expected or not key[0] or key[1] not in ('C','P')
                or key[2] not in ('C','F','M') or key in seen):
            raise ValueError('OCC_VOLUME_ROW')
        qty = int(row['quantity'])
        if qty < 0:
            raise ValueError('OCC_VOLUME_QUANTITY')
        seen.add(key)
        totals[key[:2]] += qty
    # OCC account-type quantities count both clearing sides of each contract.
    if not totals or any(qty % 2 for qty in totals.values()):
        raise ValueError('OCC_VOLUME_SIDES_INCOMPLETE')
    return sum(totals.values()) // 2


def occ_ford_history(path, days):
    history = read_json(path, {})
    if not isinstance(history, dict):
        raise ValueError('OCC_VOLUME_CACHE')
    for day in days:
        if day in history:
            continue
        url = ('https://marketdata.theocc.com/volume-query?reportDate=' + day.replace('-', '')
               + '&format=csv&volumeQueryType=O&symbolType=O&symbol=F'
                 '&reportType=D&accountType=ALL&productKind=OSTK&porc=BOTH')
        try:
            data = subprocess.run(['curl','-fsSL','--max-time','5',url], check=True,
                                  capture_output=True, text=True, timeout=6).stdout
            volume = occ_ford_contract_volume(data, day)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        history[day] = {'date': day, 'volume': volume, 'coverage': OCC_COVERAGE, 'source': 'OCC_VOLUME_QUERY'}
    atomic(path, {day: history[day] for day in sorted(history)[-40:]})
    return [history[day] for day in days if day in history]


def event_evidence(today, earnings, dividends):
    def upcoming(rows, field):
        dates = []
        for row in rows:
            try:
                day = date.fromisoformat(row[field])
                if day >= today:
                    dates.append(day)
            except (TypeError, ValueError, KeyError):
                continue
        return min(dates).isoformat() if dates else None

    confirmed = [row['report'] for row in earnings if row.get('symbol') == 'F'
                 and isinstance(row.get('report'), dict) and row['report'].get('verified') is True]
    next_earnings = upcoming(confirmed, 'date')
    next_dividend = upcoming([row for row in dividends if row.get('symbol') == 'F'], 'ex_date')
    result = {}
    if next_earnings:
        result.update(earnings_verified=True, next_earnings=next_earnings)
    if next_dividend:
        result.update(ex_dividend_verified=True, next_ex_dividend=next_dividend)
    return result


def ford_financials(company, filings, today):
    if 'F' not in filings['tickers'] or company['cik'] != int(CIK) or filings['cik'] != CIK:
        raise ValueError('FORD_SEC_IDENTITY_MISMATCH')
    recent = filings['filings']['recent']
    form, filed, period, accession = next(
        (form, filed, period, accession)
        for form, filed, period, accession in zip(
            recent['form'], recent['filingDate'], recent['reportDate'], recent['accessionNumber'])
        if form in ('10-Q', '10-K') and date.fromisoformat(filed) <= today
    )
    end = date.fromisoformat(period)
    if not 0 <= (today - end).days <= 135:
        raise ValueError('FORD_SEC_FINANCIALS_STALE')
    facts = company['facts']['us-gaap']

    def fact(tag, start, end, current=False):
        rows = [row for row in facts[tag]['units']['USD']
                if row.get('start') == start and row.get('end') == end
                and row.get('form') in ('10-K', '10-Q') and row['filed'] <= today.isoformat()
                and (not current or row['accn'] == accession)]
        if not rows:
            raise ValueError('FORD_SEC_FACT_UNAVAILABLE_' + tag)
        return max(rows, key=lambda row: row['filed'])['val']

    def ttm(tag):
        value = fact(tag, f'{end.year}-01-01', period, current=True)
        if form == '10-K':
            return value
        previous = end.year - 1
        return (value + fact(tag, f'{previous}-01-01', f'{previous}-12-31')
                - fact(tag, f'{previous}-01-01', f'{previous}-{end:%m-%d}'))

    interest = ttm('InterestExpenseNonoperating')
    pretax = ttm('IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest')
    return {
        'security_type': 'COMMON_STOCK', 'exchange': filings['exchanges'][filings['tickers'].index('F')],
        'sector': 'Consumer Discretionary', 'assets': fact('Assets', None, period, current=True),
        'equity': fact('StockholdersEquity', None, period, current=True),
        'operating_cash_flow_ttm': ttm('NetCashProvidedByUsedInOperatingActivities'),
        'interest_expense': interest, 'ebit': pretax + interest, 'financials_current': True,
        'financials_source': 'SEC_EDGAR', 'financials_filed': filed, 'financials_accession': accession,
    }


def sec_get(path):
    agent = os.environ.get('PMCC_SEC_USER_AGENT', 'PMCC Research (read-only Ford filing lookup)')
    request = urllib.request.Request('https://data.sec.gov/' + path, headers={'User-Agent': agent})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def collect_ford(root, api, today, now):
    facts, dividends, earnings = {}, [], []
    status = {}
    try:
        facts = ford_financials(sec_get(f'api/xbrl/companyfacts/CIK{CIK}.json'),
                                sec_get(f'submissions/CIK{CIK}.json'), today)
        status['financials'] = 'VERIFIED'
    except (ValueError, OSError, KeyError, TypeError, StopIteration):
        status['financials'] = 'SEC_EVIDENCE_UNAVAILABLE'
    try:
        response = api.get('market', '/v1/corporate-actions', {
            'symbols': 'F', 'types': 'cash_dividend', 'start': str(today - timedelta(days=30)),
            'end': str(today + timedelta(days=90)),
        })
        if response.get('next_page_token'):
            raise ValueError('INCOMPLETE_CORPORATE_ACTIONS')
        dividends = response['corporate_actions'].get('cash_dividends', [])
        if not isinstance(dividends, list):
            raise ValueError('MALFORMED_DIVIDEND_RESPONSE')
        ex_dates = sorted({date.fromisoformat(row['ex_date']).isoformat() for row in dividends
                           if row['symbol'] == 'F'})
        facts['paper_dividend_review'] = {
            'source': 'ALPACA_CORPORATE_ACTIONS', 'observed_at': now.isoformat(),
            'coverage_through': str(today + timedelta(days=90)), 'ex_dates': ex_dates,
        }
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        status['dividends'] = 'ALPACA_CORPORATE_ACTIONS_UNAVAILABLE'
    try:
        source = read_json(root / 'inputs/ford_robinhood_earnings.json', {})
        if source.get('source') == 'ROBINHOOD_MCP_RESEARCH' and fresh(source.get('observed_at'), now, 86400):
            earnings = source['results']
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        pass
    facts.update(event_evidence(today, earnings, dividends))
    status.setdefault('dividends', 'VERIFIED' if facts.get('ex_dividend_verified') else 'NO_CONFIRMED_DATE')
    status['earnings'] = 'VERIFIED' if facts.get('earnings_verified') else 'NO_CONFIRMED_DATE'
    if facts:
        facts['evidence_at'] = now.isoformat()
    return {'evidence': facts, 'status': status}


def record_iv_close(path, session, now, current):
    if current is None or not session or session.get('verified') is not True:
        return False
    close = stamp(session.get('close'))
    if not close - timedelta(minutes=10) <= now < close or num(current) <= 0:
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        history = read_json(path, {})
        if not isinstance(history, dict):
            raise ValueError('INVALID_IV_HISTORY')
        day = session['date']
        rows = {row['date']: row for row in history.get('F', [])}
        prior = rows.get(day)
        if prior and prior.get('observed_at') and stamp(prior['observed_at']) > now:
            return False
        rows[day] = {'date': day, 'iv': float(current), 'series': IV_SERIES, 'method': IV_METHOD,
                     'source': 'ALPACA_MARKET_DATA', 'observed_at': now.isoformat()}
        history['F'] = [rows[key] for key in sorted(rows)]
        atomic(path, history)
    return True
