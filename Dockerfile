FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/code

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt

COPY code/ ./code/

USER 10001:10001

EXPOSE 8000

CMD ["python", "-m", "serving.api", "--model-dir", "/model", "--host", "0.0.0.0", "--port", "8000"]
