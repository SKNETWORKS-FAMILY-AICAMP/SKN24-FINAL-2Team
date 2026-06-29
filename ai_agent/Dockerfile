# ────────────────────────────────────────────────────────────────────────────
# Policity AI Agent — Dockerfile
# 빌드: docker build -t policity-ai-agent .
# 실행: docker-compose up -d
# ────────────────────────────────────────────────────────────────────────────

# Python 3.12 slim (torch CPU 빌드 호환)
FROM python:3.10-slim

# ── 시스템 패키지 ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# ── 작업 디렉터리 ─────────────────────────────────────────────────────────────
WORKDIR /app

# ── Python 의존성 설치 (레이어 캐시 최대화) ─────────────────────────────────
# requirements.txt를 먼저 복사해서 소스 변경 시 재설치 방지
COPY requirements.txt .

# torch는 CPU 전용 wheel로 설치 (GPU 불필요 → 이미지 2GB+ 절감)
# sentence-transformers는 requirements.txt에 누락되어 별도 추가
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
        torch==2.2.2 \
        torchvision==0.17.2 \
        --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir \
        sentence-transformers==2.7.0 \
 && pip install --no-cache-dir -r requirements.txt

# ── 소스 복사 ────────────────────────────────────────────────────────────────
# .env는 제외 — 런타임에 env_file 또는 환경변수로 주입
COPY . .

# ── ko-sroberta 모델 빌드 시 미리 다운로드 (선택) ────────────────────────────
# 빌드 시간은 늘지만 첫 컨테이너 기동 속도가 빨라짐.
# 빌드 시간을 줄이려면 이 블록 전체를 주석 처리하면 됨.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('jhgan/ko-sroberta-multitask'); \
print('ko-sroberta 모델 캐시 완료')"

# ── checkpoint 디렉터리 생성 ──────────────────────────────────────────────────
RUN mkdir -p /app/checkpoints

# ── 포트 노출 ────────────────────────────────────────────────────────────────
EXPOSE 8001

# ── 기본 실행 명령 ────────────────────────────────────────────────────────────
# docker-compose의 command: 로 덮어쓸 수 있음
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
