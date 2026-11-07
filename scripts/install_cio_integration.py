"""Surgical PMCC-only integration into existing dirty CIO checkout.

Run on the user's server. Existing file contents are backed up before mutation.
No broker calls or modifications to other strategies' policies are made.
"""
import json,sys,shutil
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
HOST=Path('/home/tradebot/Trade-Dividend-Bot')
now=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup=ROOT/'docs'/'integration_backups'/now
changes={}

def patch(relative,old,new):
    p=HOST/relative;s=changes.get(p,p.read_text())
    if new in s:return
    if old not in s:raise RuntimeError('HOST_PATCH_ANCHOR_MISSING:'+relative)
    changes[p]=s.replace(old,new,1)

patch('src/dividend_bot/paper_capital_allocation_server.py',
    'DEFAULT_OWNER_REVIEW_CSRF_SECRET_ENV =',
    'DEFAULT_PMCC_DASHBOARD_HTML = Path("/home/tradebot/Trade-PMCC-Bot/results/pmcc_dashboard.html")\nDEFAULT_OWNER_REVIEW_CSRF_SECRET_ENV =')
patch('src/dividend_bot/paper_capital_allocation_server.py',
    '    option_dashboard_html: str | Path = DEFAULT_OPTION_DASHBOARD_HTML,',
    '    option_dashboard_html: str | Path = DEFAULT_OPTION_DASHBOARD_HTML,\n    pmcc_dashboard_html: str | Path = DEFAULT_PMCC_DASHBOARD_HTML,')
patch('src/dividend_bot/paper_capital_allocation_server.py',
    '    option_dashboard_file = Path(option_dashboard_html)',
    '    option_dashboard_file = Path(option_dashboard_html)\n    pmcc_dashboard_file = Path(pmcc_dashboard_html)')
patch('src/dividend_bot/paper_capital_allocation_server.py',
    '            elif method == "GET" and path == "/option-dashboard":',
    '''            elif method == "GET" and path == "/pmcc-dashboard":
                if not pmcc_dashboard_file.is_file():
                    payload = b"PMCC dashboard unavailable"
                    status = "503 Service Unavailable"
                else:
                    payload = pmcc_dashboard_file.read_bytes()
                    status = "200 OK"
            elif method == "GET" and path == "/option-dashboard":''')
patch('src/dividend_bot/enterprise_strategy_kpi_adapter.py',
    '        option_summary = next(',
    '''        pmcc_report = next((dict(item) for item in payloads if isinstance(item, Mapping) and item.get("strategy_id") == "PMCC-001" and isinstance(item.get("workspace_panels"), list)), {}) if strategy_id == "PMCC-001" else {}
        option_summary = next(''')
patch('src/dividend_bot/enterprise_strategy_kpi_adapter.py',
    '            "option_summary": option_summary,',
    '            "pmcc_report": pmcc_report,\n            "option_summary": option_summary,')
patch('src/dividend_bot/enterprise_portal_strategy_ui_v2.py',
    '        holdings_body = render_holdings_panel(row)',
    '''        if raw_sid.upper() == "PMCC-001":
            pmcc = row.get("pmcc_report") or {}
            pmcc_panels = {item[0]: item for item in pmcc.get("workspace_panels", []) if isinstance(item, list) and len(item) == 3}
            for target, source in {"executive-summary":"overview", "portfolio":"allocation", "holdings":"holdings", "watchlist":"watchlist", "performance":"performance", "alerts":"alerts", "decisions":"journal"}.items():
                if source in pmcc_panels:
                    output = _replace_panel(output, sid=sid, tab=target, body=pmcc_panels[source][2])
            output = _replace_panel(output, sid=sid, tab="income", body="<h3>Premium accounting</h3><p>Short-call credits are cash receipts with an offsetting option liability. Net profit is shown in Performance.</p>")
            for key in ("discovery", "board", "history", "settings"):
                if key in pmcc_panels:
                    output = _insert_workspace_tab(output, sid=sid, tab_id=key, label=pmcc_panels[key][1], body=pmcc_panels[key][2])
            output = _append_panel(output, sid=sid, tab="executive-summary", body="<p><a href='/pmcc-dashboard'>View full PMCC workspace →</a></p>")
            continue
        holdings_body = render_holdings_panel(row)''')
registry=HOST/'config/enterprise_strategy_registry.json'
d=json.loads(registry.read_text())
safety={'paper_trade_mode':True,'paper_compatible':True,'advisory_only':True,'reporting_only':True,
    'live_trading_enabled':False,'broker_execution_enabled':False,'order_placement_enabled':False,
    'capital_movement_enabled':False,'automatic_activation_allowed':False,'direct_database_writes':False}
artifacts=[{'artifact_id':n,'artifact_type':n,'path':p,'format':'json','scope':'STRATEGY','authoritative':True,'required':True}
    for n,p in [('executive_dashboard','results/pmcc_report.json'),('strategy_kpis','results/pmcc_summary.json'),
        ('watchlist','results/pmcc_watchlist.json'),('position_lifecycle','results/pmcc_positions.json'),('executive_board','results/pmcc_board.json')]]
plugin=dict(strategy_id='PMCC-001',plugin_id='PMCC-001',display_name='Trade PMCC Bot',
    strategy_type='PMCC',service_type='STRATEGY_PLUGIN',plugin_type='STRATEGY_PLUGIN',strategy_plugin=True,
    version='1.0.0',lifecycle_status='REGISTERED',runtime_status='ONLINE',health_status='DATA_UNAVAILABLE',
    readiness='AWAITING_VERIFIED_MARKET_EVIDENCE',enabled=True,plugin_contract_version='2.0',
    repository_root=str(ROOT),allocated_capital=5000,capabilities=['executive_dashboard','discovery','watchlist',
        'executive_board','portfolio_allocations','position_lifecycle','strategy_kpis','trade_analytics',
        'decision_journal','alerts','market_data_status','settings'],dashboard_artifacts=artifacts,**safety)
prior=[x for x in d['strategy_plugins'] if x.get('strategy_id')!='PMCC-001'];d['strategy_plugins']=prior+[plugin]
changes[registry]=json.dumps(d,indent=2)+'\n'
for p,s in changes.items():
    rel=p.relative_to(HOST);b=backup/rel;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,b)
    p.write_text(s)
(ROOT/'configs/pmcc_strategy_service_registration.json').write_text(json.dumps(plugin,indent=2)+'\n')
print(json.dumps({'status':'INSTALLED','backup':str(backup),'touched':[str(p) for p in changes]}))
