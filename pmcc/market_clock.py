"""One authority for verified regular-session availability."""
from datetime import date
from zoneinfo import ZoneInfo
from pmcc.strategy import stamp

def market_session(session,now):
    if not isinstance(session,dict) or session.get('verified') is not True or session.get('source') not in ['ALPACA_CALENDAR','NYSE_CALENDAR']:
        return 'DATA_UNAVAILABLE'
    try:
        if session['date']!=now.astimezone(ZoneInfo('America/New_York')).date().isoformat():return 'DATA_UNAVAILABLE'
        if session.get('is_trading_day') is False:return 'CLOSED'
        op,cl=stamp(session['open']),stamp(session['close'])
        previous=date.fromisoformat(session['previous_session_date']);following=date.fromisoformat(session['next_session_date'])
        local_day=now.astimezone(ZoneInfo('America/New_York')).date()
        if cl<=op or not previous<local_day<following:return 'DATA_UNAVAILABLE'
        return 'OPEN' if op<=now<cl else 'CLOSED'
    except (ValueError,KeyError,TypeError,AttributeError):return 'DATA_UNAVAILABLE'

