FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 autoxak \
    && useradd --uid 10001 --gid autoxak --no-create-home autoxak \
    && mkdir -p /var/log/autoxak \
    && chown autoxak:autoxak /var/log/autoxak
COPY --from=builder /install /usr/local
WORKDIR /app
COPY --chown=autoxak:autoxak app ./app
COPY --chown=autoxak:autoxak static ./static
COPY --chown=autoxak:autoxak seed_fleet.py seed_fuel_prices.py ./
COPY --chown=autoxak:autoxak deploy/entrypoint.py ./deploy/entrypoint.py
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    MPLCONFIGDIR=/tmp/matplotlib \
    LOG_DIR=/var/log/autoxak \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1
USER autoxak
ENTRYPOINT ["python", "/app/deploy/entrypoint.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
