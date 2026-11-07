"""Persistent read-only market collection using the Option Bot's existing data access.

No order methods, account endpoints, credential output or quote-time rewriting.
Issuer/event facts and historical IV/chain-volume series remain separate evidence.
"""
import json,os,re,shlex,urllib.request,urllib.error
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor,as_completed
from statistics import mean
from math import sqrt
from pmcc.strategy import num,stamp,fresh
from pmcc.alpaca_data import occ,normalize_alpaca
from pmcc.runtime import atomic,read_json
from pmcc.ledger import validate_safety
from pmcc.market_clock import market_session
from pmcc.strategy import long_horizon
from pmcc.weekly_entry import phase
from pmcc.ford_evidence import IV_METHOD, collect_ford, record_iv_close

ET=ZoneInfo('America/New_York')

def read_only_path(kind,path):
    if kind=='metadata':return path in ['/v2/calendar','/v2/options/contracts'] or bool(re.fullmatch(r'/v2/options/contracts/[A-Z0-9]+',path))
    if kind=='market':return path in ('/v1beta1/options/snapshots', '/v1/corporate-actions') or bool(re.fullmatch(r'/v2/stocks/[A-Z.\-]+/(snapshot|bars)',path))
    return False

class ReadOnlyAlpaca:
    def __init__(self,environment_file='/etc/trade-option-bot.env'):
        settings={}
        for line in Path(environment_file).read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1)
                if key.strip() in ['APCA_API_KEY_ID','APCA_API_SECRET_KEY']:
                    parts=shlex.split(value);settings[key.strip()]=parts[0] if parts else ''
        if not all(settings.get(k) for k in ['APCA_API_KEY_ID','APCA_API_SECRET_KEY']):raise ValueError('MARKET_DATA_CREDENTIALS_UNAVAILABLE')
        self.headers={'APCA-API-KEY-ID':settings['APCA_API_KEY_ID'],'APCA-API-SECRET-KEY':settings['APCA_API_SECRET_KEY']}
    def get(self,kind,path,params=None):
        if not read_only_path(kind,path):raise ValueError('READ_ONLY_MARKET_ENDPOINT_REQUIRED')
        base='https://data.alpaca.markets' if kind=='market' else 'https://paper-api.alpaca.markets'
        from urllib.parse import urlencode
        url=base+path+('?' + urlencode(params) if params else '')
        request=urllib.request.Request(url,headers=self.headers,method='GET')
        try:
            with urllib.request.urlopen(request,timeout=8) as response:return json.load(response)
        except urllib.error.HTTPError as e:raise ValueError('ALPACA_HTTP_'+str(e.code)) from None
    def pages(self,kind,path,params,field):
        result=[];tokens=set();query=dict(params)
        for _ in range(12):
            page=self.get(kind,path,query);items=page.get(field,[])
            if not isinstance(items,list):raise ValueError('MALFORMED_PROVIDER_PAGE')
            result.extend(items);token=page.get('next_page_token')
            if not token:return result
            if token in tokens:raise ValueError('REPEATED_PROVIDER_PAGE')
            tokens.add(token);query['page_token']=token
        raise ValueError('PROVIDER_PAGINATION_INCOMPLETE')

def technical_evidence(bars,previous_session):
    complete={}
    for b in bars:
        try:
            day=stamp(b['t']).astimezone(ET).date().isoformat()
            if day<=previous_session:
                for key in ['c','h','l','v']:num(b[key])
                complete[day]=b
        except (ValueError,TypeError,KeyError,ArithmeticError):continue
    days=sorted(complete);rows=[complete[d] for d in days]
    if len(rows)<220 or not days or days[-1]!=previous_session:return {}
    closes=[float(num(b['c'])) for b in rows]
    ranges=[max(float(num(b['h']))-float(num(b['l'])),abs(float(num(b['h']))-closes[i-1]),abs(float(num(b['l']))-closes[i-1])) for i,b in enumerate(rows) if i]
    atr=mean(ranges[:14])
    for value in ranges[14:]:atr=(atr*13+value)/14
    below=0
    for end in range(len(closes),199,-1):
        if closes[end-1]<mean(closes[end-200:end]):below+=1
        else:break
    return {'close':closes[-1],'sma50':mean(closes[-50:]),'sma200':mean(closes[-200:]),
        'sma50_20_ago':mean(closes[-70:-20]),'atr14':atr,'below_sma200_days':below,
        'avg_volume30':mean(float(num(b['v'])) for b in rows[-30:]),
        'avg_volume20':mean(float(num(b['v'])) for b in rows[-20:]),
        'avg_dollar_volume20':mean(float(num(b['v'])*num(b['c'])) for b in rows[-20:]),
        'daily_bars_as_of':previous_session,'daily_bar_source':'ALPACA_SIP_COMPLETED_SESSIONS'}

def iv_metrics(current,history,expected_dates):
    if current is None or len(expected_dates)!=252:return {}
    try:
        dates={r['date']:r for r in history if isinstance(r,dict)}
        rows=[dates[d] for d in expected_dates]
        if not all(r.get('series')=='30_DAY_ATM_CONSTANT_MATURITY' and r.get('method')==IV_METHOD and r.get('source')=='ALPACA_MARKET_DATA' for r in rows):return {}
        values=[num(r['iv']) for r in rows];lo,hi=min(values),max(values);c=num(current)
        if lo<=0 or hi==lo or c<=0:return {}
        return {'iv_rank':float(100*(c-lo)/(hi-lo)),'iv_percentile':100*sum(v<c for v in values)/252,
            'iv_observations':252,'iv_series':'30_DAY_ATM_CONSTANT_MATURITY','current_atm_iv30':float(c)}
    except (KeyError,ValueError,TypeError,ArithmeticError):return {}

def atm_iv30(contracts,price,today,now):
    points={}
    if price is None:return None
    for c in contracts:
        try:
            dte=(date.fromisoformat(c['expiration'])-today).days
            if not 1<=dte<=45 or not fresh(c['quote_time'],now) or num(c['iv'])<=0 or c.get('metadata_verified') is not True:continue
            distance=abs(num(c['strike'])-num(price))
            if dte not in points or distance<points[dte][0]:points[dte]=(distance,float(num(c['iv'])))
        except (ValueError,TypeError,KeyError):continue
    if 30 in points:return points[30][1]
    lower=[x for x in points if x<30];upper=[x for x in points if x>30]
    if not lower or not upper:return None
    a,b=max(lower),min(upper);weight=(30-a)/(b-a)
    return sqrt(((1-weight)*a*points[a][1]**2+weight*b*points[b][1]**2)/30)

def session_from_calendar(calendar,today):
    days=sorted(r['date'] for r in calendar if isinstance(r,dict) and r.get('date'))
    previous=[x for x in days if x<str(today)];following=[x for x in days if x>str(today)]
    if not previous or not following:return None
    current=next((r for r in calendar if r.get('date')==str(today)),None)
    base={'date':str(today),'previous_session_date':previous[-1],'next_session_date':following[0],
        'verified':True,'source':'ALPACA_CALENDAR','is_trading_day':current is not None}
    if current:
        base.update(open=datetime.fromisoformat(str(today)+'T'+current['open']).replace(tzinfo=ET).isoformat(),
            close=datetime.fromisoformat(str(today)+'T'+current['close']).replace(tzinfo=ET).isoformat())
    return base

def collect(root,now,api=None,clock=None,research=False):
    root=Path(root);cfg=read_json(root/'configs/pmcc-001.json',{});validate_safety(cfg)
    if research:validate_safety(read_json(root/'configs/pmcc-research.json',{}))
    status_path=root/'results'/('pmcc_risk_collector_status.json' if research else 'pmcc_collector_status.json')
    output_path=root/'inputs'/('alpaca_pmcc_risk.json' if research else 'alpaca_pmcc_research.json')
    today=now.astimezone(ET).date();day=str(today);cache_path=root/'state'/('research/market_collection_cache.json' if research else 'market_collection_cache.json')
    cache=read_json(cache_path,{})
    if not isinstance(cache,dict):cache={}
    snapshot={'source':'ALPACA_MARKET_DATA','research_only':True,'order_submission_enabled':False,'collected_at':now.isoformat(),'symbols':{},'session':None}
    status={'status':'COLLECTED','observed_at':now.isoformat(),'source':'ALPACA_MARKET_DATA','symbols':{},'credential_values_logged':False}
    try:api=api or ReadOnlyAlpaca(cfg.get('market_data_environment_file','/etc/trade-option-bot.env'))
    except (OSError,ValueError):
        atomic(status_path,{**status,'status':'UNAVAILABLE','reason':'EXISTING_MARKET_DATA_ACCESS_UNAVAILABLE'})
        return {'status':'UNAVAILABLE'}
    def cached(key,fetch):
        old=cache.get(key)
        if isinstance(old,dict) and old.get('day')==day:return old['data']
        value=fetch();cache[key]={'day':day,'data':value};return value
    try:
        calendar=cached('calendar',lambda:api.get('metadata','/v2/calendar',{'start':str(today-timedelta(days=430)),'end':str(today+timedelta(days=10))}))
        snapshot['session']=session_from_calendar(calendar,today)
    except (ValueError,OSError,TypeError,KeyError,AttributeError):calendar=[]
    session=snapshot['session'];previous=session.get('previous_session_date') if session else None
    if research and market_session(session,now)!='OPEN':
        atomic(output_path,snapshot)
        status.update(status='MARKET_CLOSED' if market_session(session,now)=='CLOSED' else 'SESSION_UNAVAILABLE')
        atomic(status_path,status);return status
    dates=[x['date'] for x in calendar if x.get('date','')<day]
    closing_bar=(not research and cfg.get('weekly_entries') is True and phase(session,now)=='FINALIZE')
    bar_date=day if closing_bar else previous
    horizon=long_horizon(cfg)
    universe=set() if research else set(cfg.get('universe',['F']));held=[]
    try:
        import sqlite3
        ledgers=list((root/'state/research').glob('*.sqlite3')) if research else [root/'state/pmcc.sqlite3']
        for ledger in ledgers:
            if not ledger.exists():continue
            with sqlite3.connect('file:'+str(ledger)+'?mode=ro',uri=True) as db:
                held.extend(json.loads(row[0]) for row in db.execute('SELECT payload FROM positions') if json.loads(row[0]).get('status')!='CLOSED')
        universe.update(p['symbol'] for p in held)
    except (ValueError,OSError,sqlite3.Error):pass
    try:
        host={} if research else read_json(cfg.get('discovery_source','/nonexistent'),{})
        universe.update(r['symbol'] for r in host.get('records',[]) if r.get('quote_type') in ['EQUITY','COMMON_STOCK'] and r.get('price') is not None and 5<=num(r['price'])<=20)
    except (ValueError,OSError,TypeError,KeyError,AttributeError):pass
    def mapping_file(path):
        try:value=read_json(path,{})
        except (ValueError,OSError):return {}
        return value if isinstance(value,dict) else {}
    extra=mapping_file(root/'inputs/issuer_evidence.json');iv_history=mapping_file(root/'inputs/iv_history.json');volume_history=mapping_file(root/'inputs/chain_volume_history.json')
    if 'F' in universe:
        occ_history=mapping_file(root/'state/occ_ford_volume.json')
        occ_days=sorted(set(dates))[-20:]
        occ_rows=[occ_history[d] for d in occ_days if d in occ_history]
        status['ford_occ_volume_sessions']=len(occ_rows)
        if len(occ_rows)==20:volume_history['F']=occ_rows
    ford = {}
    if (not research and 'F' in universe and session and session.get('is_trading_day') is True
            and (market_session(session, now) == 'OPEN' or cache.get('ford_issuer_evidence', {}).get('day') == day)):
        ford = cached('ford_issuer_evidence', lambda: collect_ford(root, api, today, now))
    issuer_keys='security_type exchange sector operating_cash_flow_ttm assets equity ebit interest_expense going_concern bankruptcy delisting delinquent binary_event financials_current earnings_verified next_earnings events_verified ex_dividend_verified next_ex_dividend evidence_at thesis_invalid'.split()
    for symbol in sorted(universe):
        if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z.\-]{1,8}',symbol):continue
        reasons=[];metadata=[];stock={};bars=[]
        tasks={}
        def contracts(lo,hi):return api.pages('metadata','/v2/options/contracts',{'underlying_symbols':symbol,'expiration_date_gte':str(today+timedelta(days=lo)),'expiration_date_lte':str(today+timedelta(days=hi)),'type':'call','limit':1000},'option_contracts')
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks[pool.submit(api.get,'market','/v2/stocks/'+symbol+'/snapshot')]='stock'
            tasks[pool.submit(cached,symbol+(':research-short' if research else ':comparison-short-7-45'),lambda:contracts(1,60) if research else contracts(7,45))]='short'
            if not research:tasks[pool.submit(cached,f'{symbol}:comparison-long:{min(horizon[0],200)}:{max(horizon[1],450)}',lambda:contracts(min(horizon[0],200),max(horizon[1],450)))]='long'
            if bar_date:
                fetch_bars=lambda:api.pages('market','/v2/stocks/'+symbol+'/bars',{'timeframe':'1Day','start':str(today-timedelta(days=500)),'end':bar_date+'T23:59:59Z','feed':'sip','adjustment':'all','limit':1000},'bars')
                # Retry through the closing window until the completed bar arrives.
                job=pool.submit(fetch_bars) if closing_bar else pool.submit(cached,symbol+':bars',fetch_bars)
                tasks[job]='bars'
            for future in as_completed(tasks):
                kind=tasks[future]
                try:
                    value=future.result()
                    if kind=='stock':stock=value
                    elif kind=='bars':bars=value
                    else:metadata.extend(value)
                except (ValueError,OSError,TypeError,KeyError,AttributeError):reasons.append('ALPACA_'+kind.upper()+'_UNAVAILABLE')
        # Held contracts remain discoverable after falling outside new-entry DTE ranges.
        for position in [p for p in held if p['symbol']==symbol]:
            for leg in [position['long'],position.get('short')]:
                if not leg:continue
                try:
                    key=occ(symbol,leg['expiration'],leg['strike'],leg['right'])
                    if not any(m.get('symbol')==key for m in metadata):metadata.append(cached(key+':held',lambda key=key:api.get('metadata','/v2/options/contracts/'+key)))
                except (ValueError,OSError,TypeError,KeyError,AttributeError):reasons.append('HELD_METADATA_UNAVAILABLE')
        keys=sorted({m['symbol'] for m in metadata if isinstance(m,dict) and isinstance(m.get('symbol'),str)})
        snapshots={}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(api.get,'market','/v1beta1/options/snapshots',{'symbols':','.join(keys[i:i+100])}) for i in range(0,len(keys),100)]
            for future in as_completed(futures):
                try:
                    value=future.result();snapshots.update(value.get('snapshots',{}))
                    if value.get('next_page_token'):reasons.append('OPTION_SNAPSHOT_PAGINATION_INCOMPLETE')
                except (ValueError,OSError,TypeError,AttributeError):reasons.append('ALPACA_QUOTES_UNAVAILABLE')
        e={k:v for k,v in extra.get(symbol,{}).items() if k in issuer_keys} if isinstance(extra.get(symbol),dict) else {}
        if symbol == 'F':
            e = {**ford.get('evidence', {}), **e}
        if bar_date:e.update(technical_evidence(bars,bar_date))
        if not isinstance(stock,dict):stock={};reasons.append('ALPACA_STOCK_MALFORMED')
        q=stock.get('latestQuote') or {}
        if not isinstance(q,dict):q={}
        try:
            if num(q['bp'])>0 and num(q['ap'])>=num(q['bp']):e.update(price=float((num(q['bp'])+num(q['ap']))/2),price_time=q['t'],price_method='NBBO_MIDPOINT')
        except (KeyError,ValueError,TypeError):reasons.append('UNDERLYING_QUOTE_UNAVAILABLE')
        e['source']='ALPACA_MARKET_DATA'
        payload={'metadata':metadata,'snapshots':snapshots,'evidence':e,'reasons':reasons}
        snapshot['symbols'][symbol]=payload
        normalized=normalize_alpaca(snapshot)['symbols'].get(symbol,{}).get('contracts',[])
        observed=(clock() if clock else datetime.now(timezone.utc))
        current_iv=atm_iv30(normalized,e.get('price'),today,observed)
        if symbol == 'F' and not research and session and session.get('close'):
            try:
                record_iv_close(root/'inputs/iv_history.json', session, observed, current_iv)
            except (OSError, ValueError, KeyError, TypeError):
                reasons.append('IV_CLOSE_ARCHIVE_UNAVAILABLE')
        e.update(iv_metrics(current_iv,iv_history.get(symbol,[]),dates[-252:]))
        try:
            history={r['date']:r for r in volume_history.get(symbol,[])};vs=[history[d] for d in dates[-20:]]
            if len(vs)==20 and all(r.get('coverage')=='ALL_STANDARD_CONTRACTS_ALL_EXPIRATIONS' and num(r['volume'])>=0 for r in vs):
                e['avg_option_volume20']=float(sum(num(r['volume']) for r in vs)/20)
                if all(r.get('source')=='OCC_VOLUME_QUERY' for r in vs):e['option_volume20_source']='OCC_VOLUME_QUERY'
        except (KeyError,TypeError,ValueError):pass
        status['symbols'][symbol]={'metadata_contracts':len(metadata),'quotes_returned':len(snapshots),
            'fresh_quotes':sum(fresh(c.get('quote_time'),observed) for c in normalized),'current_iv30':current_iv,
            'iv_history_observations':len(iv_history.get(symbol,[])),'issuer_evidence_supplied':bool(extra.get(symbol) or symbol == 'F' and ford.get('evidence')),
            'completed_bars':len(bars),'avg_option_volume20':e.get('avg_option_volume20'),'reasons':sorted(set(reasons))}
    if ford:
        status['ford_evidence'] = ford['status']
    atomic(output_path,snapshot)
    atomic(cache_path,cache)
    status['status']='PARTIAL' if not session or any(x['reasons'] for x in status['symbols'].values()) else 'COLLECTED'
    atomic(status_path,status)
    return status
