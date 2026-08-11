FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home appuser \
    && chown -R appuser:appuser /app

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

# gunicorn이 uvicorn worker를 실행하는 구조
# FastAPI 앱 객체는 app/main.py 의 `app` → 모듈 경로는 app.main:app
CMD ["gunicorn", "app.main:app", \
"--workers", "1", \
"--worker-class", "uvicorn.workers.UvicornWorker", \
"--bind", "0.0.0.0:8000"]
