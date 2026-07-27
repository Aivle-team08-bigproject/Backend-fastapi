FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# gunicorn이 uvicorn worker를 실행하는 구조
# FastAPI 앱 객체는 app/main.py 의 `app` → 모듈 경로는 app.main:app
CMD ["gunicorn", "app.main:app", \
"--workers", "1", \
"--worker-class", "uvicorn.workers.UvicornWorker", \
"--bind", "0.0.0.0:8000"]
