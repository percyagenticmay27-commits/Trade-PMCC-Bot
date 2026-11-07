"""Option Bot-compatible Robinhood research snapshot boundary; no credentials/orders.

Unlike OPTION-001, PMCC keeps every supplied expiration and validates dynamic
standard equity chains. Today's volume is never labeled prior-session volume.
"""
from pmcc.strategy import num
from uuid import UUID
from urllib.parse import urlparse

def delivery_verified(chain,symbol):
    rows=chain.get("underlying_instruments")
    if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):return False
    row=rows[0]
    try:
        parts=urlparse(str(row.get("instrument",""))).path.strip("/").split("/")
        UUID(parts[-1])
        return len(parts)>=2 and parts[-2]=="instruments" and row.get("symbol") in [None,"",symbol] and num(row.get("quantity",100))==100
    except (ValueError,IndexError):return False

METADATA_SOURCE='https://www.optionseducation.org/optionsoverview/options-basics'

def normalize_document(d):
    if not isinstance(d,dict):return {'symbols':{},'reasons':['RESEARCH_SNAPSHOT_MALFORMED'],'session':None}
    result={'symbols':{},'reasons':[],'session':d.get('session') if isinstance(d.get('session'),dict) else None}
    for key,expected,reason in [('source','ROBINHOOD_MCP_RESEARCH','RESEARCH_SOURCE_UNVERIFIED'),
            ('research_only',True,'RESEARCH_ONLY_REQUIRED'),
            ('order_submission_enabled',False,'RESEARCH_ORDER_AUTHORITY_FORBIDDEN')]:
        if d.get(key)!=expected or isinstance(expected,bool) and d.get(key) is not expected:
            return {**result,'reasons':[reason]}
    raw=d.get('symbols')
    if not isinstance(raw,dict):return {**result,'reasons':['RESEARCH_SNAPSHOT_MALFORMED']}
    for symbol,payload in raw.items():
        if not isinstance(payload,dict):continue
        chain_rows=payload.get('chains')
        chains={c['id']:c for c in chain_rows if isinstance(c,dict) and isinstance(c.get('id'),str)} if isinstance(chain_rows,list) else {}
        contracts=[];errors=[]
        raw_contracts=payload.get('contracts')
        if not isinstance(raw_contracts,list):raw_contracts=[];errors.append('MALFORMED_CONTRACT_COLLECTION')
        for r in raw_contracts:
            if not isinstance(r,dict):errors.append('MALFORMED_CONTRACT');continue
            q=r.get('quote') or {}
            if not isinstance(q,dict):q={}
            chain=chains.get(r.get('chain_id'),{}) if isinstance(r.get('chain_id'),str) else {}
            try:
                verified=(isinstance(q.get('instrument_id'),str) and isinstance(r.get('instrument_id') or r.get('id'),str) and q.get('instrument_id')==(r.get('instrument_id') or r.get('id')) and bool(q.get('instrument_id')) and r.get('chain_symbol')==symbol and r.get('underlying_type')=='equity'
                    and chain.get('symbol')==symbol and num(chain.get('trade_value_multiplier'))==100
                    and 'cash_component' in chain and chain['cash_component'] is None
                    and delivery_verified(chain,symbol) and chain.get('settle_on_open') is False)
            except (ValueError,TypeError):verified=False
            c=dict(id=r.get('instrument_id') or r.get('id'),symbol=symbol,
                strike=r.get('strike_price'),expiration=r.get('expiration_date'),right=r.get('type'),
                bid=q.get('bid_price'),ask=q.get('ask_price'),bid_size=q.get('bid_size'),ask_size=q.get('ask_size'),
                delta=q.get('delta'),gamma=q.get('gamma'),theta=q.get('theta'),vega=q.get('vega'),iv=q.get('implied_volatility'),
                rho=q.get('rho'),volume=q.get('volume'),open_interest=q.get('open_interest'),
                volume_previous=r.get('volume_previous'),previous_volume_date=r.get('previous_volume_date'),previous_session_date=(result['session'] or {}).get('previous_session_date'),
                open_interest_date=r.get('open_interest_date'),quote_time=q.get('updated_at'),quote_instrument_id=q.get('instrument_id'),
                multiplier=chain.get('trade_value_multiplier'),standard=verified,metadata_verified=verified,
                exercise_style='american' if verified else None,settlement='physical' if verified else None,
                state=r.get('state'),tradability=r.get('tradability'),chain_id=r.get('chain_id'),
                can_open_position=chain.get('can_open_position'),metadata_provenance={'source':'STANDARD_US_EQUITY_SPECIFICATION',
                    'source_url':METADATA_SOURCE,'chain_evidence':'ROBINHOOD_MCP_RESEARCH'},
                source='ROBINHOOD_MCP_RESEARCH',chance_of_profit_short=q.get('chance_of_profit_short'))
            contracts.append(c)
        result['symbols'][symbol]={'contracts':contracts,'evidence':payload.get('evidence') if isinstance(payload.get('evidence'),dict) else {},'reasons':errors}
    return result
