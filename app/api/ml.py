"""Anonymous model management, enabled explicitly in the test environment only."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.services.model_registry import Registry, RegistryError, enabled, strict_json
from app.services.ml_runs import Runs, example_archive

MAX_REQUEST_BYTES = 28 * 1024 * 1024
router = APIRouter()


def available():
    if not enabled():
        raise HTTPException(404, "Not found")


def mutation(request: Request):
    available()
    # No account or password. Prevent another website from issuing browser mutations.
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Откройте страницу управления на этом сайте")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Запрос с другого сайта запрещён")
    if request.headers.get("x-model-manager") != "1":
        raise HTTPException(400, "Не указан заголовок X-Model-Manager")


@router.get("/ml", dependencies=[Depends(available)], include_in_schema=False)
def page():
    return FileResponse(Path(__file__).resolve().parents[2] / "static" / "ml.html",
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})


@router.get("/api/v3/ml/models", dependencies=[Depends(available)])
def models(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return Registry().listing()


@router.post("/api/v3/ml/models", dependencies=[Depends(mutation)], status_code=201)
async def upload(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Нужен application/json")
    registry = Registry()
    try:
        with registry.lock("upload", blocking=False):
            data = bytearray()
            try:
                async with asyncio.timeout(30):
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
                            raise HTTPException(413, "Загрузка превышает допустимый размер")
                        data.extend(chunk)
            except TimeoutError as error:
                raise HTTPException(408, "Время загрузки превысило 30 секунд") from error
            try:
                body = strict_json(data)
            except (ValueError, UnicodeError, RecursionError) as error:
                raise HTTPException(422, "Некорректный JSON загрузки") from error
            del data
            return await run_in_threadpool(registry.import_bundle, body)
    except RegistryError as error:
        raise HTTPException(error.status, str(error)) from error


@router.delete("/api/v3/ml/models/{model_id}", dependencies=[Depends(mutation)], status_code=204)
def delete(model_id: str):
    try:
        Registry().delete(model_id)
    except RegistryError as error:
        raise HTTPException(error.status, str(error)) from error
    return Response(status_code=204)


@router.get("/api/v3/ml/example-package", dependencies=[Depends(available)])
def example_package():
    return Response(example_archive(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="synthetic-example.zip"', "Cache-Control": "no-store"})


@router.get("/api/v3/ml/example-data", dependencies=[Depends(available)])
def example_data():
    return {"schema_version": 2, "trip_id": "synthetic-interface-example", "started_at_ms": 1000000,
            "ended_at_ms": 1060000,
            "vehicle": {"vehicle_id": "synthetic-car", "fuel_type": "gasoline"},
            "gps": [{"timestamp_ms": 1000000+i*1000, "speed_mps": 10, "accuracy_m": 5,
                     "speed_source": "gps"} for i in range(61)],
            "control": {"distance_km": .6, "average_l_per_100km": 8,
                        "source": "trip_computer", "same_interval": True}}


@router.get("/api/v3/ml/runs", dependencies=[Depends(available)])
def runs(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return Runs().listing()


@router.get("/api/v3/ml/runs/{run_id}", dependencies=[Depends(available)])
def run_detail(run_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return Runs().read(run_id)
    except RegistryError as error:
        raise HTTPException(error.status, str(error)) from error


@router.post("/api/v3/ml/runs", dependencies=[Depends(mutation)], status_code=202)
async def start_run(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Нужен application/json")
    data = bytearray()
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(data) + len(chunk) > 6 * 1024 * 1024:
                    raise HTTPException(413, "Тестовые данные превышают 5 МиБ")
                data.extend(chunk)
    except TimeoutError as error:
        raise HTTPException(408, "Время загрузки превысило 30 секунд") from error
    try:
        body = strict_json(data)
        return await run_in_threadpool(Runs().submit, body)
    except RegistryError as error:
        raise HTTPException(error.status, str(error)) from error
    except (ValueError, UnicodeError, RecursionError) as error:
        raise HTTPException(422, "Некорректные тестовые данные JSON") from error


@router.delete("/api/v3/ml/runs/{run_id}", dependencies=[Depends(mutation)], status_code=204)
def remove_run(run_id: str):
    try:
        Runs().delete(run_id)
    except RegistryError as error:
        raise HTTPException(error.status, str(error)) from error
    return Response(status_code=204)
