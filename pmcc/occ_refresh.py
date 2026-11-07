"""Refresh Ford's daily OCC options-volume archive using the Alpaca calendar."""
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pmcc.ford_evidence import occ_ford_history
from pmcc.runtime import read_json


def main():
    root = Path(__file__).resolve().parents[1]
    local = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    calendar = read_json(root/'state/market_collection_cache.json', {})['calendar']['data']
    sessions = sorted({row['date'] for row in calendar})
    today = local.date().isoformat()
    if not any(day > today for day in sessions):
        raise ValueError('OCC_CALENDAR_UNAVAILABLE')
    days = [day for day in sessions if day < today or day == today and local.hour >= 18][-20:]
    if len(days) != 20:
        raise ValueError('OCC_CALENDAR_INCOMPLETE')
    rows = occ_ford_history(root/'state/occ_ford_volume.json', days)
    print(json.dumps({'source': 'OCC_VOLUME_QUERY', 'sessions': len(rows),
                      'first_session': days[0], 'last_session': days[-1],
                      'average_contracts': sum(r['volume'] for r in rows)/20 if len(rows)==20 else None}))
    return 0 if len(rows) == 20 else 1


if __name__ == '__main__':
    raise SystemExit(main())
