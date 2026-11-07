"""Track weekly entry eligibility under the existing PMCC cycle lock."""
from datetime import date, timedelta

from pmcc.market_clock import market_session
from pmcc.strategy import ford_paper_event_reasons, num, stamp, stock_reasons, trend_passes, uses_ford_event_review


def week_of(day):
    return day - timedelta(days=day.weekday())


def phase(session, now):
    market = market_session(session, now)
    if market == "DATA_UNAVAILABLE" or session.get("is_trading_day") is False:
        return None
    day = date.fromisoformat(session["date"])
    following = date.fromisoformat(session["next_session_date"])
    previous = date.fromisoformat(session["previous_session_date"])
    opened, closed = stamp(session["open"]), stamp(session["close"])
    if week_of(day) < week_of(following):
        if closed - timedelta(minutes=30) <= now < closed:
            return "CAPTURE"
        if closed <= now <= closed + timedelta(minutes=30):
            return "FINALIZE"
    if week_of(previous) < week_of(day) and opened - timedelta(minutes=90) <= now < opened:
        return "REVIEW"
    return None


def completed_trend(evidence, session_date):
    try:
        return (evidence.get("daily_bars_as_of") == session_date
                and trend_passes(*(num(evidence[key]) for key in
                    ("close", "sma50", "sma200", "sma50_20_ago"))))
    except (TypeError, ValueError, KeyError, ArithmeticError):
        return False


def review_reasons(evidence, now, prior_date, symbol, short_expiration):
    paper_events = uses_ford_event_review(symbol, evidence)
    reasons = stock_reasons(evidence, now, paper_events=paper_events)
    if paper_events:
        reasons.extend(ford_paper_event_reasons(evidence, short_expiration, now))
    if not completed_trend(evidence, prior_date):
        reasons.append("WEEK_END_TREND_UNVERIFIED")
    try:
        if num(evidence["price"]) <= num(evidence["sma50"]):
            reasons.append("PRICE_BELOW_SMA50")
    except (TypeError, ValueError, KeyError, ArithmeticError):
        pass
    return sorted(set(reasons))


def advance(queue, session, now, ranked, closing_evidence, live_evidence):
    action = phase(session, now)
    if action == "CAPTURE":
        return {
            "queue_date": session["date"],
            "entry_date": session["next_session_date"],
            "captured_at": now.isoformat(),
            "state": "CAPTURED",
            "candidates": {
                row["symbol"]: {
                    "rank": row["rank"],
                    "long": {k: row["long"][k] for k in ("id", "strike", "expiration", "quote_time")},
                    "short": {k: row["short"][k] for k in ("id", "strike", "expiration", "quote_time")},
                }
                for row in ranked
            },
        }
    if (action == "FINALIZE" and queue.get("queue_date") == session["date"]
            and queue.get("entry_date") == session["next_session_date"]
            and queue.get("state") in ("CAPTURED", "FINALIZED")
            and isinstance(queue.get("candidates"), dict)):
        symbols = [s for s in queue["candidates"]
                   if completed_trend(closing_evidence.get(s, {}), session["date"])]
        return {**queue, "state": "FINALIZED", "finalized_at": now.isoformat(),
                "finalized_symbols": symbols}
    if action == "REVIEW" and matches_session(queue, session):
        candidates = queue.get("candidates", {})
        reviews = {s: review_reasons(live_evidence.get(s, {}), now, queue["queue_date"], s,
                                    candidates.get(s, {}).get("short", {}).get("expiration"))
                   for s in queue["finalized_symbols"]}
        return {**queue, "reviewed_at": now.isoformat(), "reviews": reviews}
    return queue


def matches_session(queue, session):
    return (queue.get("state") == "FINALIZED" and queue.get("entry_date") == session["date"]
            and queue.get("queue_date") == session["previous_session_date"]
            and isinstance(queue.get("finalized_symbols"), list))


def approved_symbols(queue, session, now, ranked):
    if not session or market_session(session, now) != "OPEN" or not matches_session(queue, session):
        return set()
    try:
        reviewed = stamp(queue["reviewed_at"])
        opened = stamp(session["open"])
        if not opened - timedelta(minutes=90) <= reviewed < opened:
            return set()
        reviews = queue["reviews"]
        return {row["symbol"] for row in ranked
                if row["symbol"] in queue["finalized_symbols"] and reviews.get(row["symbol"]) == []
                and not review_reasons(row["evidence"], now, queue["queue_date"], row["symbol"],
                                       row["short"]["expiration"])}
    except (TypeError, ValueError, KeyError, AttributeError, ArithmeticError):
        return set()
