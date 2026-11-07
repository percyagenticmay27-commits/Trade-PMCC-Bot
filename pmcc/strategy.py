"""Deterministic PMCC contract and issuer clearance."""
from datetime import date,datetime,timedelta
from decimal import Decimal,InvalidOperation

def num(v):
    try:
        n=Decimal(str(v))
        if not n.is_finite():raise ValueError('NONFINITE')
        return n
    except InvalidOperation:raise ValueError('INVALID_NUMBER')
def stamp(v):
    d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('TIMEZONE_REQUIRED')
    return d

def fresh(v,now,seconds=60):
    try:return -5<=(now-stamp(v)).total_seconds()<=seconds
    except (ValueError,TypeError):return False

def long_horizon(config):
    days=config.get('long_dte',[365,540])
    if (not isinstance(days,list) or len(days)!=2
            or any(type(x) is not int for x in days) or not 1<=days[0]<=days[1]):
        raise ValueError('INVALID_LONG_DTE_RANGE')
    return tuple(days)


def trend_passes(close,sma50,sma200,previous_sma50):
    return close>sma50>sma200>0 and sma50>previous_sma50


def contract_reasons(c,symbol,now,role=None,closing=False,long_dte=(365,540), profile=None):
    fields='id symbol strike expiration bid ask bid_size ask_size delta gamma theta vega iv quote_time multiplier right standard exercise_style settlement metadata_verified state tradability'.split()
    if closing:fields=[k for k in fields if k not in ['delta','gamma','theta','vega','iv']]
    if role:fields+='volume_previous previous_volume_date previous_session_date open_interest open_interest_date can_open_position'.split()
    r=['MISSING_'+k.upper() for k in fields if c.get(k) is None]
    if r:return r
    try:
        numbers='strike bid ask bid_size ask_size multiplier'.split()
        if not closing:numbers+='delta gamma theta vega iv'.split()
        for k in numbers:num(c[k])
        if not fresh(c['quote_time'],now):r.append('STALE_QUOTES')
        if c['symbol']!=symbol or str(c['right']).lower()!='call':r.append('CONTRACT_IDENTITY')
        if not(c['standard'] is True and c['metadata_verified'] is True and num(c['multiplier'])==100 and c['exercise_style']=='american' and c['settlement']=='physical'):r.append('CONTRACT_METADATA_UNVERIFIED')
        if c['state']!='active' or c['tradability']!='tradable':r.append('CONTRACT_NOT_TRADABLE')
        b,a=num(c['bid']),num(c['ask'])
        if b<=0 or a<b or min(num(c['bid_size']),num(c['ask_size']))<1:r.append('NO_EXECUTABLE_QUOTE')
        if not closing and (not 0<=num(c['delta'])<=1 or num(c['gamma'])<0 or num(c['iv'])<=0):r.append('INVALID_GREEKS')
        dte=(date.fromisoformat(c['expiration'])-now.date()).days
        if dte<0 or (dte==0 and not closing):r.append('CONTRACT_EXPIRED')
        if role:
            if c['can_open_position'] is not True:r.append('CHAIN_OPENING_DISABLED')
            prev=date.fromisoformat(c['previous_volume_date']);oi=date.fromisoformat(c['open_interest_date'])
            if prev!=date.fromisoformat(c['previous_session_date']) or not 1<=(now.date()-prev).days<=7 or not 0<=(now.date()-oi).days<=7:r.append('STALE_LIQUIDITY')
            if profile:
                lo,hi=(200,450) if role=='long' else (7,30)
                low,high=('.75','.85') if role=='long' else (('.65','.75') if profile=='DYNAMIC_ITM' else ('.30','.40'))
                if not lo<=dte<=hi or not num(low)<=num(c['delta'])<=num(high):r.append(role.upper()+'_DELTA_DTE')
                if a-b>num('.05'):r.append(role.upper()+'_SPREAD')
                if num(c['open_interest'])<5000:r.append(role.upper()+'_LIQUIDITY')
            elif role=='long':
                if not long_dte[0]<=dte<=long_dte[1] or not num('.80')<=num(c['delta'])<=num('.90'):r.append('LONG_DELTA_DTE')
                if a-b>num('.15') or (a-b)/((a+b)/2)>num('.05'):r.append('LONG_SPREAD')
                if num(c['volume_previous'])<10 or num(c['open_interest'])<100:r.append('LONG_LIQUIDITY')
            else:
                if not 30<=dte<=45 or not num('.20')<=num(c['delta'])<=num('.30'):r.append('SHORT_DELTA_DTE')
                if a-b>num('.05') or (a-b)/((a+b)/2)>num('.10'):r.append('SHORT_SPREAD')
                if num(c['volume_previous'])<50 or num(c['open_interest'])<500:r.append('SHORT_LIQUIDITY')
                if b<num('.30'):r.append('SHORT_PREMIUM')
    except (ValueError,KeyError,ArithmeticError):r.append('MALFORMED_CONTRACT')
    return sorted(set(r))

def stock_reasons(e,now,profile=None,*,require_iv_history=True,paper_events=False):
    fields='price price_time close sma50 sma200 sma50_20_ago atr14 avg_volume20 avg_dollar_volume20 avg_option_volume20 iv_rank iv_percentile iv_observations iv_series operating_cash_flow_ttm assets equity ebit interest_expense security_type exchange sector going_concern bankruptcy delisting delinquent binary_event financials_current earnings_verified next_earnings events_verified ex_dividend_verified evidence_at source'.split()
    iv_fields={'iv_rank','iv_percentile','iv_observations','iv_series'}
    if not require_iv_history:
        fields=[field for field in fields if field not in iv_fields]
    if paper_events:
        fields=[field for field in fields if field not in {
            'earnings_verified','next_earnings','events_verified','binary_event','ex_dividend_verified'}]
    if profile:
        if require_iv_history:fields.remove('iv_rank')
        fields.append('avg_volume30')
    r=['MISSING_'+k.upper() for k in fields if e.get(k) is None]
    if not paper_events and 'next_ex_dividend' not in e:r.append('MISSING_NEXT_EX_DIVIDEND')
    if r:return r
    try:
        keys='price close sma50 sma200 sma50_20_ago atr14 avg_volume20 avg_dollar_volume20 avg_option_volume20 iv_rank iv_percentile iv_observations operating_cash_flow_ttm assets equity ebit interest_expense'.split()
        if not require_iv_history:
            keys=[key for key in keys if key not in iv_fields]
        if profile:
            if require_iv_history:keys.remove('iv_rank')
            keys.append('avg_volume30')
        n={k:num(e[k]) for k in keys}
        if not fresh(e['price_time'],now):r.append('STALE_UNDERLYING')
        if not fresh(e['evidence_at'],now,86400):r.append('STALE_STOCK_EVIDENCE')
        if not 5<=n['price']<=20:r.append('STOCK_PRICE')
        if e['security_type']!='COMMON_STOCK' or e['exchange'] not in ['NYSE','NASDAQ','AMEX','NYSEAMERICAN']:r.append('UNSUPPORTED_SECURITY')
        if e['sector'] in ['Financials','Financial Services']:r.append('FINANCIAL_COMPANY_EXCLUDED')
        if n['avg_volume20']<1000000 or n['avg_dollar_volume20']<10000000:r.append('STOCK_LIQUIDITY')
        if profile and n['avg_volume30']<1500000:r.append('STOCK_VOLUME30')
        if n['avg_option_volume20']<2000:r.append('CHAIN_LIQUIDITY')
        if require_iv_history:
            if e['iv_series']!='30_DAY_ATM_CONSTANT_MATURITY' or n['iv_observations']<252:r.append('IV_HISTORY_UNAVAILABLE')
            if (not profile and not 0<=n['iv_rank']<40) or not 0<=n['iv_percentile']<40:r.append('IV_TOO_HIGH')
        if not trend_passes(n['close'],n['sma50'],n['sma200'],n['sma50_20_ago']):r.append('TREND')
        if n['price']>n['sma50']*num('1.05'):r.append('ENTRY_EXTENDED')
        if n['operating_cash_flow_ttm']<=0 or n['assets']<=0 or n['equity']<=0 or (n['interest_expense']>0 and n['ebit']/n['interest_expense']<2):r.append('FINANCIAL_HEALTH')
        for k in ('going_concern','bankruptcy','delisting','delinquent'):
            if e[k] is not False:r.append(k.upper())
        if e['financials_current'] is not True:r.append('UNVERIFIED_FINANCIALS_CURRENT')
        if not paper_events:
            if e['binary_event'] is not False:r.append('BINARY_EVENT')
            for k in ('earnings_verified','events_verified','ex_dividend_verified'):
                if e[k] is not True:r.append('UNVERIFIED_'+k.upper())
            if (date.fromisoformat(e['next_earnings'])-now.date()).days<=30:r.append('EARNINGS_WINDOW')
    except (ValueError,ArithmeticError):r.append('MALFORMED_STOCK_EVIDENCE')
    return sorted(set(r))

def uses_ford_event_review(symbol, evidence):
    return symbol=='F' and 'paper_event_review' in evidence


def reviewed_earnings_window(e, now):
    """Return the protected date range from fresh, source-checked Ford evidence."""
    review=e['paper_event_review']
    if review['source']!='FORD_IR_SEC_ROBINHOOD_REVIEW' or not fresh(review['observed_at'],now,86400):
        raise ValueError('PAPER_EVENT_REVIEW_UNAVAILABLE')
    earnings=review['earnings']
    if type(earnings['verified']) is not bool:
        raise ValueError('PAPER_EVENT_REVIEW_UNAVAILABLE')
    day=date.fromisoformat(earnings['date'])
    if e.get('earnings_verified') is True:
        confirmed=date.fromisoformat(e['next_earnings'])
        if earnings['verified'] and confirmed!=day:
            raise ValueError('EARNINGS_EVIDENCE_CONFLICT')
        if earnings['verified'] or confirmed-timedelta(days=confirmed.weekday())==day-timedelta(days=day.weekday()):
            return confirmed,confirmed
        raise ValueError('EARNINGS_EVIDENCE_CONFLICT')
    if earnings['verified']:
        return day,day
    monday=day-timedelta(days=day.weekday())
    return monday,monday+timedelta(days=6)


def ford_paper_event_reasons(e, short_expiration, now):
    try:
        today=now.date()
        through=max(date.fromisoformat(short_expiration),today+timedelta(days=30))
        review=e['paper_event_review']
        if (date.fromisoformat(review['coverage_through'])<through
                or not isinstance(review['known_events'],list)
                or e.get('events_verified') is False or e.get('binary_event') is True):
            return ['PAPER_EVENT_REVIEW_UNAVAILABLE']
        if any(today<=date.fromisoformat(day)<=through for day in review['known_events']):
            return ['PAPER_ISSUER_EVENT_WINDOW']
        starts,ends=reviewed_earnings_window(e,now)
        if ends<today:return ['PAPER_EVENT_REVIEW_UNAVAILABLE']
        if starts<=through:return ['EARNINGS_WINDOW']
        expiry=date.fromisoformat(short_expiration)
        if e.get('ex_dividend_verified') is True and 'next_ex_dividend' in e:
            dates=[e['next_ex_dividend']] if e['next_ex_dividend'] else []
        else:
            dividend=e['paper_dividend_review']
            if (dividend['source']!='ALPACA_CORPORATE_ACTIONS'
                    or not fresh(dividend['observed_at'],now,86400)
                    or date.fromisoformat(dividend['coverage_through'])<expiry
                    or not isinstance(dividend['ex_dates'],list)):
                return ['PAPER_DIVIDEND_REVIEW_UNAVAILABLE']
            dates=dividend['ex_dates']
        if any(today<=date.fromisoformat(day)<=expiry for day in dates):return ['EX_DIVIDEND_WINDOW']
    except ValueError as exc:
        return ['EARNINGS_EVIDENCE_CONFLICT'] if str(exc)=='EARNINGS_EVIDENCE_CONFLICT' else ['PAPER_EVENT_REVIEW_UNAVAILABLE']
    except (KeyError,TypeError,AttributeError):
        return ['PAPER_EVENT_REVIEW_UNAVAILABLE']
    return []


def pair_reasons(e,l,s,now,cfg,basis=None,paid_fees=0,*,paper_events=False):
    r=[];price=num(e['price']);la=num(l['ask']);sb=num(s['bid']);kl=num(l['strike']);ks=num(s['strike'])
    if not kl<price<ks:r.append('STRIKE_ORDER')
    if (date.fromisoformat(l['expiration'])-date.fromisoformat(s['expiration'])).days<180:r.append('EXPIRATION_SEPARATION')
    ext=la-max(price-kl,num(0))
    if ext<0 or ext/la>num('.15'):r.append('LONG_EXTRINSIC')
    fee=num(cfg.get('fee_per_contract',.65))+num(cfg.get('regulatory_fee_per_contract',0))
    if sb*100-fee<la:r.append('SHORT_YIELD')
    debit=(la-sb)*100 if basis is None else num(basis)
    if basis is None and debit<=0:r.append('INVALID_DEBIT')
    if (ks-kl)*100<=debit+num(paid_fees)+fee*4+10:r.append('WIDTH_RULE')
    expiry=date.fromisoformat(s['expiration'])
    if paper_events:
        r.extend(ford_paper_event_reasons(e,s['expiration'],now))
    else:
        if date.fromisoformat(e['next_earnings'])<=expiry:r.append('EARNINGS_WINDOW')
        if e.get('next_ex_dividend') and date.fromisoformat(e['next_ex_dividend'])<=expiry:r.append('EX_DIVIDEND_WINDOW')
    if abs((stamp(l['quote_time'])-stamp(s['quote_time'])).total_seconds())>5:r.append('UNSYNCHRONIZED_QUOTES')
    return r

def dynamic_pair_reasons(e,l,s,now,cfg,profile,*,paper_events=False):
    price,kl,ks=map(num,(e['price'],l['strike'],s['strike']))
    ask,bid=map(num,(l['ask'],s['bid']))
    r=[]
    if not kl<min(price,ks) or not (ks<price if profile=='DYNAMIC_ITM' else price<ks):r.append('STRIKE_ORDER')
    expiry=date.fromisoformat(s['expiration'])
    if date.fromisoformat(l['expiration'])<=expiry:r.append('EXPIRATION_SEPARATION')
    if not 0<=ask-(price-kl)<ask*num('.15'):r.append('LONG_EXTRINSIC')
    debit=(ask-bid)*100
    cost=debit+2*(num(cfg.get('fee_per_contract',.65))+num(cfg.get('regulatory_fee_per_contract',0)))
    if debit<=0:r.append('INVALID_DEBIT')
    if cost>500:r.append('POSITION_DEBIT_CAP')
    if cost>=(ks-kl)*100:r.append('WIDTH_RULE')
    if paper_events:
        r.extend(ford_paper_event_reasons(e,s['expiration'],now))
    else:
        if date.fromisoformat(e['next_earnings'])<=expiry:r.append('EARNINGS_WINDOW')
        if e.get('next_ex_dividend') and date.fromisoformat(e['next_ex_dividend'])<=expiry:r.append('EX_DIVIDEND_WINDOW')
    if abs((stamp(l['quote_time'])-stamp(s['quote_time'])).total_seconds())>5:r.append('UNSYNCHRONIZED_QUOTES')
    return r


def evaluate_candidate(symbol,evidence,contracts,now,config,profile=None,*,long_time_premium_cap=None):
    paper_events=profile in (None,'DYNAMIC_ITM') and uses_ford_event_review(symbol,evidence)
    base=stock_reasons(evidence,now,profile,require_iv_history=long_time_premium_cap is None,
        paper_events=paper_events);rejected=set(base);pairs=[]
    if not base:
        longs=[];shorts=[];horizon=long_horizon(config)
        for c in contracts:
            lr=contract_reasons(c,symbol,now,'long',long_dte=horizon,profile=profile);sr=contract_reasons(c,symbol,now,'short',profile=profile)
            if not lr:longs.append(c)
            if not sr:shorts.append(c)
            rejected.update(lr);rejected.update(sr)
        for l in longs:
            for s in shorts:
                try:
                    r=(dynamic_pair_reasons(evidence,l,s,now,config,profile,paper_events=paper_events)
                       if profile else pair_reasons(evidence,l,s,now,config,paper_events=paper_events))
                    if long_time_premium_cap is not None:
                        time_premium=(num(l['ask'])-max(num(evidence['price'])-num(l['strike']),num(0)))*100
                        if time_premium>long_time_premium_cap:r.append('LONG_TIME_PREMIUM_CAP')
                        if any(c.get('quote_instrument_id')!=c['id'] for c in (l,s)):r.append('QUOTE_IDENTITY')
                except (ValueError,ArithmeticError):r=['MALFORMED_PAIR']
                if r:rejected.update(r)
                else:pairs.append((l,s))
    result=dict(symbol=symbol,strategy_id='PMCC-001',status='PASS',reasons=[],evidence=evidence,evaluated_at=now.isoformat())
    if not pairs:
        reasons=sorted(rejected or {'EMPTY_OPTION_SURFACE'})
        missing=any(x.startswith(('MISSING_','STALE_','UNVERIFIED_','MALFORMED')) or x.endswith('_UNAVAILABLE') or x=='EMPTY_OPTION_SURFACE' for x in reasons)
        return {**result,'status':'DATA_UNAVAILABLE' if missing else 'FAIL','reasons':reasons}
    l,s=min(pairs,key=lambda p:(abs(num(p[0]['delta'])-num('.80' if profile else '.85')),(num(p[0]['ask'])-num(p[0]['bid']))/num(p[0]['ask']),abs(num(p[1]['delta'])-num({None:'.25','DYNAMIC_OTM':'.35','DYNAMIC_ITM':'.70'}[profile]))))
    debit=(num(l['ask'])-num(s['bid']))*100;ext=num(l['ask'])-max(num(evidence['price'])-num(l['strike']),num(0))
    return {**result,'long':l,'short':s,'net_debit':float(debit),'gross_long_cost':float(num(l['ask'])*100),'width_cushion':float((num(s['strike'])-num(l['strike']))*100-debit),'extrinsic_fraction':float(ext/num(l['ask']))}

def rank_candidates(rows):
    rows=[r for r in rows if r['status']=='PASS']
    def metrics(r):
        l,s,e=r['long'],r['short'],r['evidence']
        rel=lambda c:float((num(c['ask'])-num(c['bid']))/((num(c['ask'])+num(c['bid']))/2))
        return [float(num(l['open_interest'])+num(s['open_interest']))/(1+rel(l)+rel(s)),float(num(e['operating_cash_flow_ttm'])/num(e['assets']))+(float(num(e['ebit'])/num(e['interest_expense'])) if num(e['interest_expense'])>0 else 0),float(num(e['sma50'])/num(e['sma200'])+num(e['sma50'])/num(e['sma50_20_ago'])),-r['extrinsic_fraction'],float(num(s['bid'])/num(l['ask']))]
    values=[metrics(r) for r in rows]
    for i,r in enumerate(rows):
        scores=[]
        for k in range(5):
            col=[v[k] for v in values];v=values[i][k]
            scores.append(50 if len(col)==1 else 100*(sum(x<v for x in col)+(sum(x==v for x in col)-1)/2)/(len(col)-1))
        r['opportunity_quality']=round(sum(a*b for a,b in zip(scores,[.30,.25,.20,.15,.10])),2)
    rows.sort(key=lambda r:(-r['opportunity_quality'],(num(r['long']['ask'])-num(r['long']['bid']))/num(r['long']['ask']),r['symbol']))
    for i,r in enumerate(rows):r['rank']=i+1
    return rows
