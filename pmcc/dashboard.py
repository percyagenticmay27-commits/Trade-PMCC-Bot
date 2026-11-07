"""Accessible PMCC workspace using the CIO's strategy/tab interaction model."""
import html,json

def esc(v):return html.escape(str(v),quote=True)
def money(v):return 'Unavailable' if v is None else f'${float(v):,.2f}'
def table(rows,columns):
    if not rows:return '<p class="empty">No records published.</p>'
    head=''.join('<th scope="col">'+esc(label)+'</th>' for _,label in columns)
    body=''
    for row in rows:
        cells=[]
        for key,_ in columns:
            value=row.get(key)
            if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False)
            cells.append('<td>'+esc('Unavailable' if value is None else value)+'</td>')
        body+='<tr>'+''.join(cells)+'</tr>'
    return '<div class="table-wrap"><table><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div>'

def sections(r):
    positions=[]
    for p in r['positions']:
        l,s=p['long'],p.get('short') or {}
        positions.append(dict(symbol=p['symbol'],status=p['status'],price=p.get('evidence',{}).get('price'),
            long_strike=l['strike'],long_expiration=l['expiration'],long_entry=p['long_entry'],long_current=l['bid'],
            long_delta=l.get('delta'),long_gamma=l.get('gamma'),short_strike=s.get('strike'),short_expiration=s.get('expiration'),
            short_entry=p.get('short_entry'),short_current=s.get('ask'),short_delta=s.get('delta'),short_gamma=s.get('gamma'),
            cash_basis=p['cash_basis'],market_value=p['market_value'],fees=p['fees'],
            pnl=round(p['market_value']-p['cash_basis']-p['fees'],2),quote_status=p.get('quote_status'),last_mark_at=p.get('last_mark_at')))
    discovery=table(r['discovery'],[('symbol','Stock'),('status','Status'),('reasons','Gate reasons')])
    holdings=table(positions,[(k,k.replace('_',' ').title()) for k in ['symbol','status','price','long_strike','long_expiration','long_entry','long_current','long_delta','long_gamma','short_strike','short_expiration','short_entry','short_current','short_delta','short_gamma','cash_basis','market_value','fees','pnl','quote_status','last_mark_at']])
    board=''
    for b in r['board']:
        board+='<h3>'+esc(b['symbol'])+' · '+esc(b['status'])+'</h3>'+table(b['committees'],[('committee','Committee'),('status','Decision'),('reasons','Evidence / reasons')])
    allocation='<p>Separate $5,000 initial paper allocation. Allocate by opportunity rank; 25% gross long-cost cap per stock, 40% sector cap, 20% cash reserve, maximum four stocks. One diagonal per stock. No averaging down.</p>'
    allocation+=table([{'allocated':money(r['allocated_capital']),'cash':money(r['cash_balance']),'equity':money(r['portfolio_value']),'reserve':money(r['portfolio_value']*.2)}],[('allocated','Allocation'),('cash','Cash'),('equity','Equity'),('reserve','Required reserve')])
    performance=table([r],[('realized_gain','Closed campaign P/L'),('unrealized_gain','Open campaign P/L'),('total_pnl','Total P/L'),('win_rate_percent','Win rate %'),('completed_count','Completed')])
    overview=f'<p><strong>{esc(r["status"])}</strong> · Market {esc(r["market_session"])} · Updated {esc(r["display_time"])}</p><p>Robinhood data is read-only. Missing or stale evidence blocks new paper entries. Quote timestamps remain unchanged when collected.</p>'+allocation
    settings='<p>Paper simulation only. Live trading, broker execution, broker order placement and capital movement are locked off.</p><pre>'+esc(json.dumps(r['settings'],indent=2))+'</pre>'
    return [
        ('overview','Overview',overview),('discovery','Discovery',discovery),
        ('market-data','Market Data','<p>Observed contract prices and Greeks. These rows require all entry gates before a paper position can open.</p>'+table(r.get('contract_observations',[]),[(k,k.replace('_',' ').title()) for k in ['symbol','source','id','expiration','strike','bid','ask','delta','gamma','theta','vega','iv','volume_previous','open_interest','quote_time','quote_status']])),
        ('watchlist','Watchlist',table(r['watchlist'],[('symbol','Stock'),('rank','Rank'),('opportunity_quality','Opportunity score'),('net_debit','Debit $'),('width_cushion','Width cushion $')])),
        ('board','Executive Board',board or '<p>No board evidence available.</p>'),
        ('allocation','Allocation',allocation),('holdings','Holdings',holdings),
        ('history','Trade History',table(r['completed'],[('symbol','Stock'),('opened_at','Opened'),('closed_at','Closed'),('exit_reason','Exit'),('realized_pnl','Net P/L $')])),
        ('performance','Performance',performance),
        ('journal','Journal',table(r['events'][-100:],[('at','Time'),('symbol','Stock'),('status','Action'),('reason','Reason'),('fees','Fees $')])),
        ('alerts','Alerts',table(r['alerts'],[('symbol','Stock'),('reason','Reason')])),('settings','Settings',settings)]

def render(r):
    panels=sections(r);buttons='';bodies=''
    for i,(key,label,body) in enumerate(panels):
        buttons+=f'<button id="tab-{key}" role="tab" aria-selected="{str(i==0).lower()}" aria-controls="panel-{key}" tabindex="{0 if i==0 else -1}" data-tab="{key}">{esc(label)}</button>'
        bodies+=f'<section id="panel-{key}" role="tabpanel" aria-labelledby="tab-{key}" {"hidden" if i else ""}><h2>{esc(label)}</h2>{body}</section>'
    metrics=''.join(f'<article><span>{esc(label)}</span><strong>{esc(value)}</strong></article>' for label,value in [('Paper allocation',money(r['allocated_capital'])),('Cash available',money(r['cash_balance'])),('Portfolio value',money(r['portfolio_value'])),('Open diagonals',r['open_count'])])
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Trade PMCC Bot | AI CIO</title><style>
    :root{color-scheme:dark;font-family:system-ui,sans-serif;background:#0c1422;color:#e7edf5}body{margin:0}header,main{max-width:1400px;margin:auto;padding:24px}header{border-bottom:1px solid #2a3b50}a{color:#8bc5ff}h1{font-size:30px;margin:10px 0}h2{font-size:24px}p{line-height:1.6;color:#bfccdc}.badge{color:#9de8cc;background:#16372f;border-radius:20px;padding:6px 12px;font-size:13px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px;margin-bottom:24px}article,section{background:#142034;border:1px solid #2a3b50;border-radius:12px;padding:22px}article span{display:block;color:#a7b7cd;font-size:14px}article strong{display:block;margin-top:8px;font-size:28px}nav{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}button{background:#16283f;color:#dbe7f7;border:1px solid #344a64;padding:12px 16px;border-radius:8px;cursor:pointer}button[aria-selected=true]{background:#346dda;color:white}button:focus-visible,a:focus-visible{outline:3px solid #f2c879;outline-offset:3px}.table-wrap{overflow:auto;max-height:70vh}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:12px;text-align:left;border-bottom:1px solid #2a3b50;vertical-align:top;min-width:100px}th{position:sticky;top:0;background:#1a2c44}td{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}section[hidden]{display:none}.empty{padding:20px;background:#102033;border-radius:8px}@media(max-width:600px){header,main{padding:16px}h1{font-size:25px}section{padding:16px}}
    </style></head><body><header><a href="/executive-dashboard?strategy=PMCC-001#strategy-PMCC-001">AI CIO Executive Dashboard</a><h1>Trade PMCC Bot</h1><span class="badge">PMCC-001 · PAPER ONLY</span><p>Long-dated calls + short calls · Independent allocation and governance</p></header><main><div class="metrics">'''+metrics+'</div><nav role="tablist" aria-label="PMCC workspace">'+buttons+'</nav>'+bodies+'''</main><script>
    const tabs=[...document.querySelectorAll('[role=tab]')];function activate(t){for(const b of tabs){const on=b===t;b.setAttribute('aria-selected',on);b.tabIndex=on?0:-1;document.getElementById(b.getAttribute('aria-controls')).hidden=!on;}history.replaceState(null,'','#'+t.dataset.tab)}for(const t of tabs){t.addEventListener('click',()=>activate(t));t.addEventListener('keydown',e=>{let n=tabs.indexOf(t);if(e.key==='ArrowRight')n=(n+1)%tabs.length;else if(e.key==='ArrowLeft')n=(n-1+tabs.length)%tabs.length;else if(e.key==='Home')n=0;else if(e.key==='End')n=tabs.length-1;else return;e.preventDefault();activate(tabs[n]);tabs[n].focus()})}const target=tabs.find(t=>t.dataset.tab===location.hash.slice(1));if(target)activate(target);
    </script></body></html>'''
