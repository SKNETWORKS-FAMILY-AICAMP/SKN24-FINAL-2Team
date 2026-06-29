FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지 (lxml, kiwipiepy 빌드용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 설치 (캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# Fargate는 단발 실행 — 스케줄러 없이 바로 파이프라인 실행
CMD ["python", "main.py", "--now"]
