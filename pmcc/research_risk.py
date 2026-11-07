"""Deterministic paper research risk decisions; no broker or network access."""
from datetime import date
from pmcc.strategy import num,stamp,contract_reasons

DEFAULT_POLICY=dict(loss_fraction=.25,profit_fraction=.20,otm_test_delta=.60,
                    itm_delta_increase=.15,max_rolls=2,max_roll_extension_days=28,
                    min_roll_net_credit_dollars=1,width_reserve_dollars=10)

def dividend_date(value):
    if value is None:return None
    if not isinstance(value,str):raise ValueError('INVALID_DIVIDEND_DATE')
    return date.fromisoformat(value)

def policy(config):
    result={**DEFAULT_POLICY,**config.get('risk_policy',{})}
    for key in ['loss_fraction','profit_fraction','otm_test_delta','itm_delta_increase']:
        if not 0<num(result[key])<=1:raise ValueError('INVALID_RISK_POLICY:'+key)
    for key in ['max_rolls','max_roll_extension_days']:
        if not isinstance(result[key],int) or isinstance(result[key],bool) or not 0<=result[key]<=60:
            raise ValueError('INVALID_RISK_POLICY:'+key)
    for key in ['min_roll_net_credit_dollars','width_reserve_dollars']:
        if num(result[key])<0:raise ValueError('INVALID_RISK_POLICY:'+key)
    return result

def campaign_exit(position,long,short,config):
    """A priced whole-campaign exit does not depend on Greeks or the stock quote."""
    p=policy(config);fee=num(config.get('fee_per_contract',.65))+num(config.get('regulatory_fee_per_contract',0))
    value=(num(long['bid'])-(num(short['ask']) if short else 0))*100
    net=value-num(position['cash_basis'])-num(position['fees'])-fee*(2 if short else 1)
    basis=num(position['initial_debit'])
    if net<=-basis*num(p['loss_fraction']):return dict(action='CLOSE',reason='POSITION_LOSS_LIMIT',net_pnl=float(net))
    if net>=basis*num(p['profit_fraction']):return dict(action='CLOSE',reason='POSITION_PROFIT_TARGET',net_pnl=float(net))
    return dict(action='HOLD',net_pnl=float(net))

def decision(position,long,short,evidence,contracts,variant,events,now,config,event_blockers):
    """Caller supplies identity-matched, current held quotes and verified open session."""
    exit=campaign_exit(position,long,short,config)
    if exit['action']=='CLOSE':return exit
    net=exit['net_pnl'];p=policy(config)
    fee=num(config.get('fee_per_contract',.65))+num(config.get('regulatory_fee_per_contract',0))
    if not short:return dict(action='HOLD')
    try:
        delta=num(short['delta'])
        if not 0<=delta<=1:raise ValueError('INVALID_DELTA')
    except (KeyError,ValueError,TypeError,ArithmeticError):return dict(action='CLOSE',reason='RISK_DELTA_UNAVAILABLE')
    entry=position.get('short_entry_delta')
    tested=(num(evidence['price'])>=num(short['strike']) or delta>=num(p['otm_test_delta'])) if variant['short_type']=='OTM' else (
        entry is not None and delta>=num(entry)+num(p['itm_delta_increase']))
    if variant['short_type']=='ITM' and entry is None:
        return dict(action='CLOSE',reason='ITM_ENTRY_DELTA_UNAVAILABLE')
    if not tested:return dict(action='HOLD')
    rolls=sum(e.get('position_id')==position['id'] and e.get('reason')=='TESTED_SHORT_ROLL' for e in events)
    if rolls>=p['max_rolls']:return dict(action='CLOSE',reason='TESTED_SHORT_ROLL_LIMIT')
    current_exp=date.fromisoformat(short['expiration']);long_exp=date.fromisoformat(long['expiration'])
    candidates=[]
    for c in contracts:
        try:
            exp=date.fromisoformat(c['expiration']);dte=(exp-now.date()).days
            if c['id'] in [short['id'],long['id']] or c.get('quote_instrument_id')!=c['id']:continue
            if contract_reasons(c,position['symbol'],now) or event_blockers(evidence,c,now):continue
            ex=dividend_date(evidence.get('next_ex_dividend'))
            if ex and now.date()<=ex<=exp:continue
            if c.get('can_open_position') is not True or num(c.get('open_interest',0))<100:continue
            if c['exercise_style']!=long['exercise_style'] or c['settlement']!=long['settlement']:continue
            if not current_exp<exp<long_exp or not 1<=(exp-current_exp).days<=p['max_roll_extension_days']:continue
            lo,hi=variant.get('roll_dte',(30,60) if variant['id']=='MONTHLY_OTM' else (7,21))
            if not lo<=dte<=hi:continue
            if num(c['strike'])<=num(short['strike']):continue
            if num(c['ask'])-num(c['bid'])>num('.05'):continue
            if abs((stamp(c['quote_time'])-stamp(short['quote_time'])).total_seconds())>5:continue
            if abs((stamp(c['quote_time'])-stamp(long['quote_time'])).total_seconds())>5:continue
            if variant['short_type']=='OTM':
                if num(c['strike'])<=num(evidence['price']) or not num('.15')<=num(c['delta'])<=num('.35'):continue
            elif not num('.50')<=num(c['delta'])<min(delta,num('.80')):continue
            credit=(num(c['bid'])-num(short['ask']))*100
            if credit-2*fee<num(p['min_roll_net_credit_dollars']):continue
            new_basis=num(position['cash_basis'])-credit
            cushion=(num(c['strike'])-num(long['strike']))*100-new_basis
            if cushion<=num(position['fees'])+4*fee+num(p['width_reserve_dollars']):continue
            candidates.append((exp,-num(c['strike']),-credit,c['id'],c))
        except (KeyError,ValueError,TypeError,ArithmeticError):continue
    if not candidates:return dict(action='CLOSE',reason='TESTED_SHORT_NO_SAFE_ROLL',net_pnl=float(net))
    chosen=min(candidates,key=lambda r:r[:4])[-1]
    return dict(action='ROLL',reason='TESTED_SHORT_ROLL',replacement=chosen,net_pnl=float(net))
