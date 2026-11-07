"""Paper-only ITM comparison tied to a confirmed classic paper open."""
from pmcc.ledger import Ledger
from pmcc.strategy import evaluate_candidate, stamp

PROFILE = 'CLASSIC_LINKED_ITM'


def candidate_for_open(symbol, payload, now, config):
    contracts = [c for c in payload.get('contracts', ())
                 if c.get('id') and c.get('quote_instrument_id') == c['id']]
    try:
        return evaluate_candidate(symbol,payload['evidence'],contracts,now,config,'DYNAMIC_ITM')
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return {'status':'DATA_UNAVAILABLE','reasons':['SHADOW_EVIDENCE_UNAVAILABLE']}


def record_openings(root, classic, now, config):
    from pmcc.research import performance
    from pmcc.runtime import read_json

    ledger = Ledger(root/'state/research'/f'{PROFILE}.sqlite3',
                    {**config,'initial_capital':5000,'strategy_id':PROFILE})
    seen = {event['key'] for event in ledger.snapshot()['events']}
    for event in classic['events']:
        if event['status'] != 'OPEN' or 'linked_itm_intent' not in event:
            continue
        key = 'classic-open:' + event['position_id']
        if key in seen:
            continue
        candidate = event['linked_itm_intent']
        reasons = candidate.get('reasons', [])
        if stamp(event['at']) != now:
            reasons = ['ORIGINAL_OPENING_CYCLE_MISSED']
        elif candidate['status'] == 'PASS':
            evidence = {**candidate['evidence'],'classic_position_id':event['position_id'],
                        'research_variant':PROFILE}
            fill = ledger.open({**candidate,'evidence':evidence,
                                'classic_position_id':event['position_id']},key,now)
            if fill['status'] == 'OPEN':
                continue
            reasons = [fill['reason']]
        ledger.transact(key,now,lambda db:dict(status='SHADOW_SKIPPED',symbol=event['symbol'],
            classic_position_id=event['position_id'],classic_opened_at=event['at'],reasons=reasons))

    state = ledger.snapshot()
    prior = read_json(root/'state/research/performance.json',{}).get(PROFILE,{})
    metrics = performance(state,prior.get('equity_peak',5000),prior.get('max_drawdown_dollars',0))
    return {'id':PROFILE,'paper_only':True,'performance':metrics,
            'positions':state['positions'],'events':state['events']}
