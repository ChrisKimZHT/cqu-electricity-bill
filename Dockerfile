FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/app/.cache/matplotlib \
    DOCKER_MODE=true \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --no-compile -r requirements.txt

COPY cqu_electricity ./cqu_electricity

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.cache/matplotlib /data \
    && chown appuser:appuser /app/.cache/matplotlib /data

USER appuser

VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "cqu_electricity"]
CMD ["daemon"]
