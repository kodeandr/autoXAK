"""Calendar-day progress; only saved trips with a known scoring policy qualify."""
from datetime import datetime, timedelta, timezone
from statistics import mean

POLICY = "smooth-sample-fraction-1"


def summarize_progress(trips, days=7, utc_offset_minutes=0, now=None):
    zone = timezone(timedelta(minutes=utc_offset_minutes))
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    start = today - timedelta(days=days - 1)
    previous_start = start - timedelta(days=days)
    current, previous, buckets = [], [], {}
    for trip in trips:
        result = trip.calculation_result or {}
        score = result.get("smooth_score")
        status = (result.get("metrics") or {}).get("math_status") or {}
        if status.get("style_score_policy") != POLICY or not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
            continue
        date = trip.created_at.astimezone(zone).date()
        if start <= date <= today:
            current.append(score)
            buckets.setdefault(date, []).append(score)
        elif previous_start <= date < start:
            previous.append(score)
    # One trip is an observation, not evidence of a trend across days.
    enough = len(buckets) >= 2
    return {"days": days, "policy": POLICY, "trip_count": len(current),
            "average": round(mean(current), 1) if current else None,
            "previous_average": round(mean(previous), 1) if previous else None,
            "delta": round(mean(current) - mean(previous), 1) if enough and len(previous) >= 2 else None,
            "sufficient_data": enough,
            "points": [{"date": (start + timedelta(days=i)).isoformat(),
                        "score": round(mean(buckets[start + timedelta(days=i)]), 1) if start + timedelta(days=i) in buckets else None}
                       for i in range(days)],
            "period_start": start.isoformat(), "period_end": today.isoformat()}
