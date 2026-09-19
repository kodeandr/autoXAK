from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.core.database import get_db
from app.models.db_models import Trip, User
from app.schemas.analytics import PeriodAnalyticsResponse, TripsPeriodSummaryMetrics, TripSummaryItem

router = APIRouter(prefix="/api/v1/users", tags=["Analytics"])


@router.get("/{user_id}/analytics/period", response_model=PeriodAnalyticsResponse)
async def get_period_analytics(
    user_id: str,
    start_date: Optional[datetime] = Query(
        None, description="Начало периода (ISO 8601). По умолчанию: 30 дней назад"
    ),
    end_date: Optional[datetime] = Query(
        None, description="Конец периода (ISO 8601). По умолчанию: текущий момент"
    ),
    db: AsyncSession = Depends(get_db)
):
    """
    Возвращает сводные агрегированные данные о поездках пользователя за заданный интервал
    и хронологический список завершенных сессий.
    """
    now = datetime.now(timezone.utc)
    if end_date is None:
        end_date = now
    if start_date is None:
        start_date = end_date - timedelta(days=30)

    if start_date > end_date:
        raise HTTPException(
            status_code=400, 
            detail="Параметр start_date не может быть позже end_date"
        )

    # Приведение временных зон к naive UTC для корректной фильтрации в БД
    start_naive = start_date.astimezone(timezone.utc).replace(tzinfo=None) if start_date.tzinfo else start_date
    end_naive = end_date.astimezone(timezone.utc).replace(tzinfo=None) if end_date.tzinfo else end_date

    # 1. Агрегация ключевых показателей на уровне СУБД (1 SQL-запрос)
    agg_stmt = select(
        func.count(Trip.id).label("total_trips"),
        func.coalesce(func.sum(Trip.distance_km), 0.0).label("total_dist"),
        func.coalesce(func.sum(Trip.duration_seconds), 0.0).label("total_seconds"),
        func.coalesce(func.sum(Trip.total_savings_rub), 0.0).label("total_savings"),
        func.coalesce(func.sum(Trip.oil_wear_percent), 0.0).label("total_oil_wear"),
        func.coalesce(func.avg(Trip.idle_ratio), 0.0).label("avg_idle")
    ).where(
        and_(
            Trip.user_id == user_id,
            Trip.created_at >= start_naive,
            Trip.created_at <= end_naive
        )
    )

    agg_result = await db.execute(agg_stmt)
    agg_row = agg_result.one()

    total_trips = int(agg_row.total_trips or 0)
    total_dist = float(agg_row.total_dist or 0.0)
    total_secs = float(agg_row.total_seconds or 0.0)
    total_savings = float(agg_row.total_savings or 0.0)
    total_wear = float(agg_row.total_oil_wear or 0.0)
    avg_idle = float(agg_row.avg_idle or 0.0)

    total_hours = total_secs / 3600.0
    avg_speed = (total_dist / total_hours) if total_hours > 0 else 0.0

    summary_metrics = TripsPeriodSummaryMetrics(
        total_trips=total_trips,
        total_distance_km=round(total_dist, 2),
        total_duration_hours=round(total_hours, 2),
        total_savings_rub=round(total_savings, 2),
        total_oil_wear_percent=round(total_wear, 4),
        avg_speed_kmh=round(avg_speed, 1),
        avg_idle_ratio=round(avg_idle, 3)
    )

    # 2. Выборка списка поездок с сортировкой от новых к старым
    trips_stmt = (
        select(Trip)
        .where(
            and_(
                Trip.user_id == user_id,
                Trip.created_at >= start_naive,
                Trip.created_at <= end_naive
            )
        )
        .order_by(Trip.created_at.desc())
        .limit(200)
    )

    trips_result = await db.execute(trips_stmt)
    trips_rows = trips_result.scalars().all()

    trip_items = [
        TripSummaryItem(
            session_id=str(t.id),
            created_at=t.created_at,
            duration_seconds=round(t.duration_seconds, 1),
            distance_km=round(t.distance_km, 2),
            idle_ratio=round(t.idle_ratio, 3),
            oil_wear_percent=round(t.oil_wear_percent, 4),
            total_savings_rub=round(t.total_savings_rub, 2)
        )
        for t in trips_rows
    ]

    return PeriodAnalyticsResponse(
        user_id=user_id,
        start_date=start_naive,
        end_date=end_naive,
        summary=summary_metrics,
        trips=trip_items
    )