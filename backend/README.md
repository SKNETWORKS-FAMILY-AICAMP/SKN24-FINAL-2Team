# Policity Backend

Django REST Framework 기반 백엔드 서버입니다.  
AWS RDS(MySQL)를 데이터베이스로 사용하며, AI 토론 기능은 ai_agent FastAPI 서버와 연동됩니다.

---

## 목차

1. [서버 실행](#서버-실행)
2. [디렉토리 구조](#디렉토리-구조)
3. [환경 설정](#환경-설정)
4. [주요 앱](#주요-앱)
5. [API 엔드포인트](#api-엔드포인트)

---

## 서버 실행

### 사전 준비

`backend/` 디렉토리에 `.env` 파일을 생성합니다.

```bash
# AWS RDS
DB_ENGINE=django.db.backends.mysql
DB_NAME=policity_db
DB_USER=admin
DB_PASSWORD=your_password
DB_HOST=your-rds-endpoint.rds.amazonaws.com
DB_PORT=3306

# Django
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

# AI Agent 서버 URL
# 로컬 테스트 시
AI_AGENT_URL=http://localhost:8001
# EC2 운영 시 (ai_agent EC2 내부 IP로 변경)
# AI_AGENT_URL=http://ai-agent-ec2-internal-ip:8001
```

---

### 방법 1 — 로컬 직접 실행 (개발·테스트)

> ai_agent 서버(`http://localhost:8001`)가 먼저 실행된 상태여야 합니다.

```bash
# 1. 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. 의존성 설치
cd backend
pip install -r requirements.txt

# 3. 마이그레이션 (최초 1회)
python manage.py migrate

# 4. 개발 서버 실행 (포트 8000)
python manage.py runserver
```

접속 확인: `http://localhost:8000/api/debates/test/` — 브라우저 기반 토론 테스트 UI

---

### 방법 2 — Gunicorn (운영·스테이징)

SSE 스트리밍을 위해 `uvicorn.workers.UvicornWorker`를 사용합니다.

```bash
cd backend
gunicorn config.wsgi:application --config gunicorn.conf.py
```

| 설정 | 값 | 설명 |
|------|----|------|
| 포트 | 8000 | `gunicorn.conf.py`의 `bind` |
| worker_class | UvicornWorker | SSE async 지원 |
| timeout | 300s | AI 응답 장기 대기 허용 |

---

### 방법 3 — Docker Compose (운영)

Nginx + Gunicorn 구성으로 실행됩니다.

```bash
cd backend
docker-compose up --build -d
```

| 서비스 | 포트 | 설명 |
|--------|------|------|
| `web` | 8000 (내부) | Django + Gunicorn |
| `nginx` | 80 | 리버스 프록시 |

로그 확인:

```bash
docker-compose logs -f web
```

> **주의**: `.env`의 `AI_AGENT_URL`을 ai_agent EC2의 내부 IP로 변경한 뒤 실행하세요.

---

## 디렉토리 구조

```
backend/
├── config/
│   ├── settings.py        # Django 설정
│   ├── urls.py            # 루트 URL 라우팅
│   └── wsgi.py / asgi.py
├── apps/
│   ├── debates/           # AI 토론 앱 (핵심)
│   │   ├── models.py      # DebateSession, DebateMessage
│   │   ├── views.py       # SSE 릴레이 + ai_agent 프록시
│   │   ├── serializers.py
│   │   ├── urls.py
│   │   └── templates/debates/test.html  # 브라우저 테스트 UI
│   ├── cards/             # InfoCard 모델
│   └── users/             # User 모델
├── gunicorn.conf.py
├── manage.py
├── requirements.txt
└── .env
```

---

## 환경 설정

| 변수 | 설명 | 예시 |
|------|------|------|
| `DB_HOST` | AWS RDS 엔드포인트 | `your-db.rds.amazonaws.com` |
| `DB_NAME` | 데이터베이스 이름 | `policity_db` |
| `DB_USER` / `DB_PASSWORD` | RDS 접속 정보 | - |
| `SECRET_KEY` | Django 시크릿 키 | `django-insecure-...` |
| `DEBUG` | 디버그 모드 | `True` (운영: `False`) |
| `ALLOWED_HOSTS` | 허용 호스트 (콤마 구분) | `127.0.0.1,localhost` |
| `AI_AGENT_URL` | ai_agent 서버 주소 | `http://localhost:8001` |

---

## 주요 앱

### `debates`

AI 토론 기능의 핵심 앱. Django가 ai_agent FastAPI 서버와 통신하는 프록시 역할을 합니다.

- **SSE 스트리밍 릴레이**: `httpx.Client.stream()` → `StreamingHttpResponse`
- **DB 저장**: SSE 릴레이 중 `DebateMessage`를 AWS RDS에 실시간 저장
- **모드**: AI vs AI / AI vs User

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/debates/` | 토론 세션 생성 |
| `GET` | `/api/debates/{id}/` | 세션 + 메시지 조회 |
| `GET` | `/api/debates/{id}/stream/` | SSE 스트리밍 (토론 진행) |
| `POST` | `/api/debates/{id}/input/` | 사용자 발언 입력 (AI vs User) |
| `POST` | `/api/debates/{id}/action/` | 사용자 선택 (next / extra / question) |
| `POST` | `/api/debates/{id}/question/` | AI에게 질문 (AI vs AI) |
| `GET` | `/api/debates/test/` | 브라우저 테스트 UI |
