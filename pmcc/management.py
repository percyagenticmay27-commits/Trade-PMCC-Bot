"""Exit precedence and short-call management using executable, fresh quotes."""
from datetime import date
from pmcc.strategy import (num,fresh,stamp,contract_reasons,stock_reasons,pair_reasons,
    ford_paper_event_reasons,reviewed_earnings_window,uses_ford_event_review)

def same_contract(a,b):
    try:
        return (a.get('symbol')==b.get('symbol') and a.get('expiration')==b.get('expiration')
            and a.get('right')==b.get('right') and num(a['strike'])==num(b['strike']) and num(a['multiplier'])==num(b['multiplier']))
    except (ValueError,KeyError,TypeError):return False

def current_contract(known,contracts,now,side):
    candidates=[]
    for c in contracts:
        if not isinstance(c,dict):continue
        direct=c.get('id')==known.get('id')
        cross=(c.get('source') in ['ALPACA_MARKET_DATA','ROBINHOOD_MCP_RESEARCH'] and c.get('metadata_verified') is True and same_contract(c,known))
        if direct or cross:candidates.append(c)
    def priority(c):
        try:age=abs((now-stamp(c.get('quote_time'))).total_seconds())
        except (ValueError,TypeError):age=float('inf')
        try:executable=num(c.get(side))>0 and num(c.get(side+'_size'))>=1
        except (ValueError,TypeError):executable=False
        return (c.get('quote_instrument_id')!=c.get('id'),not executable,not fresh(c.get('quote_time'),now),age)
    return min(candidates,key=priority) if candidates else None

def decide(p,e,contracts,now,cfg,session):
    l=current_contract(p['long'],contracts,now,'bid');s=current_contract(p['short'],contracts,now,'ask') if p.get('short') else None
    reasons=[]
    if not l or p.get('short') and not s:return {'action':'REFRESH','reason':'HELD_CONTRACT_QUOTES_MISSING'}
    # Risk-reducing exits need executable prices and held identity, not new-entry Greeks.
    for current,known,side in [(l,p['long'],'bid')]+([(s,p['short'],'ask')] if s else []):
        if current.get('quote_instrument_id')!=current.get('id'):reasons.append('QUOTE_INSTRUMENT_MISMATCH')
        if not same_contract(current,known):reasons.append('HELD_CONTRACT_IDENTITY')
        if not fresh(current.get('quote_time'),now):reasons.append('STALE_QUOTES')
        try:
            if num(current.get(side))<=0 or num(current.get(side+'_size'))<1:reasons.append('NO_EXECUTABLE_EXIT_QUOTE')
        except (ValueError,TypeError):reasons.append('NO_EXECUTABLE_EXIT_QUOTE')
    if not reasons and s and abs((stamp(l['quote_time'])-stamp(s['quote_time'])).total_seconds())>5:reasons.append('UNSYNCHRONIZED_QUOTES')
    if reasons:return {'action':'REFRESH','reason':','.join(sorted(set(reasons)))}
    def answer(action,reason,**extra):return dict(action=action,reason=reason,long=l,short=s,**extra)
    if p.get('short_stock'):return answer('RECONCILE_ASSIGNMENT','DELIVERY_LIABILITY')
    lb=num(l['bid']);sa=num(s['ask']) if s else num(0);fee=num(cfg.get('fee_per_contract',.65))+num(cfg.get('regulatory_fee_per_contract',0))
    close_value=(lb-sa)*100;pnl=close_value-num(p['cash_basis'])-num(p['fees'])-fee*(2 if s else 1)
    if lb<=num(p['long_entry'])*(1-num(cfg.get('long_stop',.25))):return answer('CLOSE_DIAGONAL','LONG_STOP')
    if pnl<=-num(p['initial_debit'])*num(cfg.get('campaign_stop',.20)):return answer('CLOSE_DIAGONAL','CAMPAIGN_STOP')
    if e.get('below_sma200_days',0)>=2:return answer('CLOSE_DIAGONAL','TREND_EXIT')
    if e.get('thesis_invalid') is True:return answer('CLOSE_DIAGONAL','THESIS_INVALID')
    if fresh(e.get('evidence_at'),now,86400) and e.get('earnings_verified') is True and e.get('next_earnings') and now.date().isoformat()<=e['next_earnings']<=session['next_session_date']:return answer('CLOSE_DIAGONAL','EARNINGS_EXIT')
    paper_events=uses_ford_event_review(p['symbol'],e)
    if paper_events:
        try:
            start,end=reviewed_earnings_window(e,now)
            if now.date()<=end and start<=date.fromisoformat(session['next_session_date']):
                return answer('CLOSE_DIAGONAL','EARNINGS_WINDOW_EXIT')
        except (ValueError,KeyError,TypeError,AttributeError):pass
    if s and fresh(e.get('evidence_at'),now,86400) and e.get('ex_dividend_verified') is True and e.get('next_ex_dividend') and now.date().isoformat()<=e['next_ex_dividend']<=session['next_session_date']:return answer('CLOSE_SHORT','EX_DIVIDEND_EXIT')
    long_dte=(date.fromisoformat(l['expiration'])-now.date()).days
    if long_dte<=120:return answer('CLOSE_DIAGONAL','LONG_EXPIRY_EXIT')
    target=cfg.get('campaign_profit_target')
    if target is not None and pnl>=num(p['initial_debit'])*num(target):
        return answer('CLOSE_DIAGONAL','CAMPAIGN_PROFIT_TARGET')
    events_ok=e.get('events_verified') is True and e.get('earnings_verified') is True and e.get('ex_dividend_verified') is True
    if paper_events:
        events_ok=not ford_paper_event_reasons(e,now.date().isoformat(),now)
    if s and num(s['ask'])<=num(p['short_entry'])*(1-num(cfg.get('short_profit_capture',.60))) and not fresh(e.get('price_time'),now):return answer('CLOSE_SHORT','SHORT_PROFIT')
    if s and (date.fromisoformat(s['expiration'])-now.date()).days<=21 and not fresh(e.get('price_time'),now):return answer('CLOSE_SHORT','SHORT_TIME_EXIT')
    if not fresh(e.get('price_time'),now):return answer('ALERT','UNDERLYING_REFRESH_REQUIRED')
    try:short_delta=num(s.get('delta')) if s else None
    except (ValueError,TypeError):short_delta=None
    tested=s and (num(e['price'])>=num(s['strike']) or short_delta is not None and short_delta>=num('.50'))
    time_exit=s and (date.fromisoformat(s['expiration'])-now.date()).days<=21
    profit=s and sa<=num(p['short_entry'])*(1-num(cfg.get('short_profit_capture',.60)))
    if tested or not s or time_exit or profit:
        replacements=[]
        if events_ok and not [r for r in stock_reasons(e,now,paper_events=paper_events) if r not in {'STOCK_PRICE','ENTRY_EXTENDED'}]:
            for new in contracts:
                if contract_reasons(new,p['symbol'],now,'short'):continue
                if s and (new['id']==s['id'] or num(new['strike'])<=num(s['strike']) or new['expiration']<=s['expiration']):continue
                if contract_reasons(new,p['symbol'],now,'short'):continue
                credit=num(new['bid'])*100-(sa*100 if s else 0)
                if tested and credit<=fee*2:continue
                if not tested and s:continue # take profit first, replace on a later cycle
                basis=num(p['cash_basis'])-credit
                try:bad=pair_reasons(e,l,new,now,cfg,basis,num(p['fees'])+fee*(2 if s else 1),
                    paper_events=paper_events)
                except (ValueError,ArithmeticError):bad=['MALFORMED_PAIR']
                if not bad:replacements.append(new)
        if replacements:
            new=min(replacements,key=lambda c:abs(num(c['delta'])-num('.25')))
            return answer('ROLL_SHORT' if s else 'OPEN_SHORT','TESTED_CREDIT_ROLL' if s else 'REPLACEMENT_SHORT',replacement=new)
        if tested:
            deeply=(short_delta is not None and short_delta>=num('.70')) or (e.get('atr14') is not None and num(e['price'])>=num(s['strike'])+num(e['atr14']))
            if deeply or pnl>=0:return answer('CLOSE_DIAGONAL','TESTED_EXIT')
            return answer('CLOSE_SHORT','TESTED_NO_CREDIT_ROLL')
        if profit:return answer('CLOSE_SHORT','SHORT_PROFIT')
        if time_exit:return answer('CLOSE_SHORT','SHORT_TIME_EXIT')
    if lb<=num(p['long_entry'])*num('.80'):return answer('ALERT','LONG_DRAWDOWN_WARNING')
    if not events_ok:return answer('ALERT','EVENT_DATA_UNAVAILABLE')
    if long_dte<=180:return answer('ALERT','LONG_EXPIRY_REVIEW')
    return answer('HOLD','RULES_HOLD')
