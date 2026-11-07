"""Independent Alpaca research normalization and source-aware failover."""
from datetime import datetime,date
from zoneinfo import ZoneInfo
from uuid import UUID
from pmcc.strategy import num,fresh,stamp
from pmcc.market_clock import market_session


def occ(symbol,expiration,strike,right='call'):
    suffix=datetime.strptime(expiration,'%Y-%m-%d').strftime('%y%m%d')
    value=num(strike)*1000
    if value!=value.to_integral_value() or value<=0:raise ValueError('INVALID_OCC_STRIKE')
    if right not in ['call','put']:raise ValueError('INVALID_RIGHT')
    return symbol+suffix+right[0].upper()+f'{int(value):08d}'


def normalize_alpaca(d):
    empty={'symbols':{},'reasons':[],'session':None,'source':'ALPACA_MARKET_DATA'}
    if not isinstance(d,dict):return {**empty,'reasons':['RESEARCH_SNAPSHOT_MALFORMED']}
    if d.get('source')!='ALPACA_MARKET_DATA' or d.get('research_only') is not True or d.get('order_submission_enabled') is not False:
        return {**empty,'reasons':['ALPACA_RESEARCH_AUTHORITY_UNVERIFIED']}
    result={**empty,'session':d.get('session') if isinstance(d.get('session'),dict) else None}
    symbols=d.get('symbols')
    if not isinstance(symbols,dict):return {**result,'reasons':['RESEARCH_SNAPSHOT_MALFORMED']}
    for symbol,p in symbols.items():
        if not isinstance(p,dict):continue
        metadata=p.get('metadata');snapshots=p.get('snapshots');reasons=list(p.get('reasons',[]));contracts=[]
        if not isinstance(metadata,list) or not isinstance(snapshots,dict):
            result['symbols'][symbol]={'contracts':[],'evidence':{},'reasons':['MALFORMED_ALPACA_COLLECTION']};continue
        for m in metadata:
            if not isinstance(m,dict):continue
            key=m.get('symbol')
            if not isinstance(key,str):continue
            snapshot=snapshots.get(key)
            if not isinstance(snapshot,dict):continue
            q=snapshot.get('latestQuote') or {};g=snapshot.get('greeks') or {}
            if not isinstance(q,dict) or not isinstance(g,dict):continue
            try:
                UUID(m['underlying_asset_id'])
                verified=(occ(symbol,m['expiration_date'],m['strike_price'],m['type'])==key
                    and m.get('underlying_symbol')==symbol and m.get('root_symbol')==symbol
                    and num(m['size'])==100 and num(m.get('multiplier',m['size']))==100
                    and m.get('style')=='american')
            except (ValueError,TypeError,KeyError,ArithmeticError):verified=False
            prev=snapshot.get('prevDailyBar') or {};daily=snapshot.get('dailyBar') or {}
            if not isinstance(prev,dict):prev={}
            if not isinstance(daily,dict):daily={}
            def bar_date(bar):
                try:return stamp(bar['t']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
                except (ValueError,KeyError,TypeError):return None
            contracts.append(dict(id=key,symbol=symbol,strike=m.get('strike_price'),expiration=m.get('expiration_date'),right=m.get('type'),
                bid=q.get('bp'),ask=q.get('ap'),bid_size=q.get('bs'),ask_size=q.get('as'),quote_time=q.get('t'),quote_instrument_id=key,
                delta=g.get('delta'),gamma=g.get('gamma'),theta=g.get('theta'),vega=g.get('vega'),rho=g.get('rho'),iv=snapshot.get('impliedVolatility'),
                volume=daily.get('v'),volume_date=bar_date(daily),volume_previous=prev.get('v'),previous_volume_date=bar_date(prev),
                previous_session_date=(result['session'] or {}).get('previous_session_date'),open_interest=m.get('open_interest'),open_interest_date=m.get('open_interest_date'),
                multiplier=m.get('size'),standard=verified,metadata_verified=verified,exercise_style=m.get('style'),settlement='physical' if verified else None,
                state=m.get('status'),tradability='tradable' if m.get('tradable') is True else 'untradable',can_open_position=m.get('tradable') is True,
                source='ALPACA_MARKET_DATA',metadata_provenance={'source':'ALPACA_CONTRACT_METADATA','source_url':'https://docs.alpaca.markets/us/reference/get-options-contracts'}))
        result['symbols'][symbol]={'contracts':contracts,'evidence':p.get('evidence') if isinstance(p.get('evidence'),dict) else {},'reasons':reasons}
    return result


def fresh_entry_surface(payload,now,long_dte=(365,540)):
    if not payload or payload.get('reasons') or not fresh(payload.get('evidence',{}).get('price_time'),now):return False
    horizons=set()
    for c in payload.get('contracts',[]):
        try:
            if not fresh(c.get('quote_time'),now) or c.get('quote_instrument_id')!=c.get('id') or c.get('metadata_verified') is not True:continue
            if num(c['bid'])<=0 or num(c['ask'])<num(c['bid']) or min(num(c['bid_size']),num(c['ask_size']))<1:continue
            for key in ['delta','gamma','theta','vega','iv']:num(c[key])
            dte=(date.fromisoformat(c['expiration'])-now.date()).days
            if 30<=dte<=45:horizons.add('short')
            if long_dte[0]<=dte<=long_dte[1]:horizons.add('long')
        except (ValueError,KeyError,TypeError,ArithmeticError):continue
    return horizons=={'short','long'}

def combine(primary,fallback,now,long_dte=(365,540)):
    """Choose entry evidence from one source; keep both sources for held contracts."""
    pgood=not primary.get('reasons');fgood=not fallback.get('reasons')
    result={'symbols':{},'reasons':[] if pgood or fgood else primary.get('reasons',[])+fallback.get('reasons',[]),
        'session':primary.get('session') if market_session(primary.get('session'),now)!='DATA_UNAVAILABLE' else fallback.get('session'),'source':'ROBINHOOD_AND_ALPACA_READ_ONLY'}
    for symbol in sorted(set(primary.get('symbols',{}))|set(fallback.get('symbols',{}))):
        p=primary.get('symbols',{}).get(symbol);f=fallback.get('symbols',{}).get(symbol)
        if pgood and fresh_entry_surface(p,now,long_dte):chosen=p
        elif fgood and fresh_entry_surface(f,now,long_dte):chosen=f
        else:chosen=p if p and pgood and fresh(p.get('evidence',{}).get('price_time'),now) and not f else f if f and fgood else p or f
        result['symbols'][symbol]={**chosen,'management_contracts':(p or {}).get('contracts',[])+(f or {}).get('contracts',[])}
    return result
