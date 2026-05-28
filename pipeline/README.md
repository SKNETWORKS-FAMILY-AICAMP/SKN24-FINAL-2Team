# PoliTalk Pipeline

대한민국 청년(20~30대)을 위한 정책·뉴스 서비스 **PoliTalk**의 AI 파이프라인입니다.  
뉴스/정책 카드 자동 생성, RAG 기반 챗봇, AI 토론 기능을 포함합니다.

---

## 목차

1. [디렉토리 구조](#디렉토리-구조)
2. [환경 설정](#환경-설정)
3. [설치](#설치)
4. [파이프라인 개요](#파이프라인-개요)
5. [모듈 설명](#모듈-설명)
6. [노트북 실행 순서](#노트북-실행-순서)
7. [실험 매트릭스](#실험-매트릭스)
8. [주요 설정값](#주요-설정값)

---

## 디렉토리 구조

```
pipeline/
├── config.py                  # 공통 설정 (API 키, DB URL, 실험 파라미터)
├── utils.py                   # 청킹·임베딩·LLM·필터 유틸리티
├── create_notebooks.py        # 노트북 자동 생성 스크립트
│
├── db/
│   ├── rdb.py                 # SQLAlchemy CRUD (MySQL / SQLite)
│   └── vectordb.py            # ChromaDB 컬렉션 빌드 / 검색 / 평가
│
├── pipelines/
│   ├── card_generation.py     # 카드 생성 파이프라인
│   ├── chatbot_rag.py         # RAG 챗봇 파이프라인
│   └── debate.py              # AI 토론 파이프라인
│
├── 01_setup_and_index.ipynb   # 데이터 로드 → RDB → ChromaDB 인덱싱
├── 02_card_generation.ipynb   # 카드 생성 실행 및 결과 확인
├── 03_chatbot_rag.ipynb       # RAG 챗봇 테스트
├── 04_debate.ipynb            # AI vs AI / AI vs User 토론 테스트
│
└── chroma_db/                 # ChromaDB 영구 저장소 (자동 생성)
```

---

## 환경 설정

`.env` 파일 또는 쉘 환경변수로 설정합니다.

```bash
# 필수
OPENAI_API_KEY=sk-...

# RDB (기본값: SQLite 로컬 파일)
POLITALK_DB_URL=sqlite:///./politalk_dev.db

# MySQL 사용 시 (선택)
POLITALK_DB_URL=mysql+pymysql://user:password@localhost:3306/politalk
# 또는 개별 변수로 설정
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=secret
DB_NAME=politalk
```

> **개발 환경**: `POLITALK_DB_URL` 미설정 시 `politalk_dev.db` (SQLite)가 자동 생성됩니다.  
> **운영 환경**: MySQL 연결 문자열로 교체하면 됩니다. 코드 변경 불필요.

---

## 설치

```bash
pip install openai chromadb sqlalchemy pymysql tqdm numpy
```

| 패키지 | 용도 |
|--------|------|
| `openai` | GPT-4o 카드 생성·토론, GPT-4o-mini 필터링, 임베딩 |
| `chromadb` | 벡터 스토어 (RAG 검색) |
| `sqlalchemy` | RDB ORM (MySQL / SQLite) |
| `pymysql` | MySQL 드라이버 |
| `tqdm` | 인덱싱 진행률 표시 |
| `numpy` | 검색 평가 점수 계산 |

---

## 파이프라인 개요

```
뉴스 JSONL / 정책 데이터
        │
        ▼
┌──────────────────────────────┐
│   01 Setup & Index           │
│   · RDB 테이블 초기화         │
│   · 기사 upsert              │
│   · ChromaDB 6개 컬렉션 빌드  │
│   · 검색 품질 평가 (Top-K)    │
└─────────────┬────────────────┘
              │
              ▼
┌──────────────────────────────┐
│   02 Card Generation         │
│   · 청년 관련성 필터           │
│   · 기사 클러스터링            │
│   · GPT-4o 탭 생성           │
│     (SUMMARY/CORE/OPINION    │
│      POLICY/SOURCE)          │
│   · 편향 감지 & 수정           │
│   · RDB + ChromaDB 저장      │
└─────────────┬────────────────┘
              │
    ┌─────────┴──────────┐
    ▼                    ▼
┌───────────────┐  ┌─────────────────┐
│ 03 Chatbot    │  │  04 Debate       │
│ RAG           │  │                 │
│ · 금칙어 필터  │  │ · AI vs AI 토론  │
│ · 기사+카드   │  │ · AI vs User    │
│   RAG 검색    │  │   (easy/hard)   │
│ · GPT-4o 응답 │  │ · 토론 요약      │
│ · 카드 추천 3개│  │ · RDB 전체 저장  │
└───────────────┘  └─────────────────┘
```

---

## 모듈 설명

### `config.py`

모든 설정 상수와 환경변수를 한 곳에서 관리합니다.

```python
from config import LLM_MODEL, EMBEDDING_MODELS, CHAT_TOP_K
from config import article_collection_name, card_collection_name
```

주요 설정:

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_MODEL` | `gpt-4o` | 카드 생성·토론용 메인 모델 |
| `LLM_MODEL_FAST` | `gpt-4o-mini` | 필터링·분류용 경량 모델 |
| `EMBEDDING_MODELS` | small / large | `text-embedding-3-small/large` |
| `CHAT_MAX_INPUT_CHARS` | 500 | 챗봇 입력 최대 글자 수 |
| `CHAT_TOP_K` | 5 | RAG 검색 상위 K |
| `DEBATE_TURNS_PER_ROUND` | 3 | 토론 라운드당 턴 수 |

---

### `utils.py`

청킹, 임베딩, LLM 호출, 필터 함수 모음.

```python
from utils import get_chunker, embed_texts, llm, cluster_articles, is_youth_relevant
```

| 함수 | 설명 |
|------|------|
| `chunk_fixed(text)` | 500자 고정 크기 + 50자 오버랩 |
| `chunk_by_sentence(text)` | 한국어 문장 단위, 최대 600자 |
| `chunk_semantic(text, client)` | 임베딩 기반 의미 단위 분할 (임계값 0.75) |
| `get_chunker(strategy, client)` | `"fixed"/"sentence"/"semantic"` → 청킹 함수 반환 |
| `embed_texts(texts, model, client)` | 배치 임베딩, List[List[float]] 반환 |
| `llm(messages, client, ...)` | OpenAI Chat Completion 래퍼 |
| `is_youth_relevant(article, client)` | 청년 관련성 판별 (GPT-4o-mini) |
| `cluster_articles(articles, ...)` | 코사인 유사도 0.70 기반 Greedy 클러스터링 |
| `check_and_fix_bias(card, client)` | 편향 감지 및 CORE/OPINION 탭 보정 |

---

### `db/rdb.py`

SQLAlchemy 기반 RDB CRUD. MySQL과 SQLite를 모두 지원합니다.

```python
from db.rdb import get_engine, init_tables, upsert_article, save_card, ...

engine = get_engine(DB_URL)
init_tables(engine)   # 테이블 없으면 자동 생성
```

주요 함수:

| 함수 | 설명 |
|------|------|
| `get_engine(db_url)` | SQLAlchemy Engine 생성 |
| `init_tables(engine)` | 전체 테이블 CREATE IF NOT EXISTS |
| `upsert_article(engine, art)` | 기사 중복 없이 삽입 → article_id 반환 |
| `save_card(engine, ...)` | 카드 + 탭 저장 → card_id 반환 |
| `save_bias_log(engine, ...)` | 편향 감지 로그 저장 |
| `create_chat_session(engine, ...)` | 채팅 세션 생성 |
| `save_chat_message(engine, ...)` | 메시지 저장 (user/assistant) |
| `load_chat_history(engine, session_id)` | 세션 메시지 전체 로드 |
| `create_debate(engine, ...)` | 토론 세션 생성 |
| `save_debate_message(engine, ...)` | 토론 메시지 저장 |

**테이블 목록**: `articles`, `policies`, `bills`, `cards`, `card_tabs`, `card_articles`, `card_policies`, `card_bills`, `bias_logs`, `chat_sessions`, `chat_messages`, `debate_modes`, `debates`, `debate_participants`, `debate_messages`

---

### `db/vectordb.py`

ChromaDB 컬렉션 관리 및 RAG 검색.

```python
from db.vectordb import get_chroma_client, build_article_collection, upsert_card, retrieve_from_articles, retrieve_from_cards
```

**컬렉션 구조**:

| 컬렉션 이름 | 내용 | 용도 |
|------------|------|------|
| `articles_{strategy}_{model_key}` | 기사 청크 (6개) | RAG 검색 |
| `cards_{model_key}` | 카드 SUMMARY 탭 (2개) | 카드 추천 |

| 함수 | 설명 |
|------|------|
| `build_article_collection(...)` | 기사 청킹 → 임베딩 → 저장 (증분 인덱싱) |
| `upsert_card(...)` | 카드 SUMMARY를 cards 컬렉션에 upsert |
| `retrieve_from_articles(query, ...)` | 기사 컬렉션 RAG 검색 |
| `retrieve_from_cards(query, ...)` | 카드 컬렉션 유사도 검색 |
| `evaluate_retrieval(queries, ...)` | 평균 Top-K 코사인 유사도 평가 |

검색 결과 형식:
```python
[{"content": str, "metadata": dict, "score": float}, ...]
# score = 1 - cosine_distance  (1.0에 가까울수록 유사)
```

---

### `pipelines/card_generation.py`

뉴스·정책·법안 카드를 자동 생성합니다.

```python
from pipelines.card_generation import CardGenerationPipeline

pipeline = CardGenerationPipeline(
    engine=engine, chroma=chroma, openai_client=client,
    strategy="sentence", model_key="large",
)
result = pipeline.run_daily(article_limit=50, publish=False)
# result = {"news_cards": [...], "policy_cards": [...], "bill_cards": [...]}
```

**처리 흐름**:
1. JSONL에서 기사 로드 → RDB upsert
2. ChromaDB 기사 컬렉션 증분 업데이트
3. GPT-4o-mini로 청년 관련성 필터링
4. 코사인 유사도 기반 기사 클러스터링 (임계값 0.70)
5. 클러스터당 GPT-4o로 6개 탭 생성 (SUMMARY, CORE, OPINION, POLICY, ARTICLE, SOURCE)
6. 편향 감지 → CORE/OPINION 탭 보정
7. RDB에 카드 저장 → ChromaDB cards 컬렉션 upsert

---

### `pipelines/chatbot_rag.py`

RAG 기반 정책·뉴스 해설 챗봇입니다.

```python
from pipelines.chatbot_rag import ChatbotRAGPipeline

pipeline = ChatbotRAGPipeline(engine=engine, chroma=chroma, openai_client=client)
session_id = pipeline.create_session(user_id=1, title="주거 정책 문의")
reply, recommendations = pipeline.chat(session_id, "청년 월세 지원 어떻게 신청해?")
```

**처리 흐름**:
1. 입력 길이 체크 (500자 이내)
2. 금칙어 필터 (성적 비하·정치인 비하·혐오 표현)
3. 기사 컬렉션 + 카드 컬렉션 RAG 검색
4. 최근 N턴 히스토리 로드
5. GPT-4o 응답 생성 (1500자 이내)
6. RDB 메시지 저장
7. 관련 카드 3개 추천

**규칙**: 검색 결과 범위 내 답변만 허용. 정당·후보 지지·비방 금지.

---

### `pipelines/debate.py`

AI vs AI 토론 및 AI vs User 토론을 지원합니다.

```python
from pipelines.debate import DebatePipeline

pipeline = DebatePipeline(engine=engine, chroma=chroma, openai_client=client)

# AI vs AI
debate_id, pro_id, con_id = pipeline.create_ai_vs_ai(selected_policy="청년 기본소득")
result = pipeline.run_ai_vs_ai_full(debate_id, policy_card, pro_id, con_id)

# AI vs User
debate_id, user_pid, ai_pid = pipeline.create_ai_vs_user(
    user_id=1, selected_policy="청년 주거", user_stance="찬성", difficulty="hard"
)
ai_response = pipeline.process_user_message(
    debate_id, user_pid, ai_pid, "저는 찬성합니다", policy_card, "ARGUMENT", turn_num=1, difficulty="hard"
)
summary = pipeline.generate_summary(debate_id, policy_card)
```

**토론 단계**: `position → pro_round → con_round → extra_round(선택) → summary`

| 난이도 | 설명 |
|--------|------|
| `easy` | AI가 사용자 주장을 존중하며 부드럽게 반론 |
| `hard` | AI가 논리·데이터 기반으로 강하게 반론 |

---

## 노트북 실행 순서

**반드시 순서대로 실행**하세요. 이전 노트북에서 생성된 DB와 인덱스를 다음 노트북이 사용합니다.

```
01_setup_and_index.ipynb
  → RDB 초기화 + ChromaDB 6개 컬렉션 빌드 (약 10~30분, 기사 수에 따라 다름)

02_card_generation.ipynb
  → 카드 생성 실행, 탭 내용 확인, 편향 로그 조회

03_chatbot_rag.ipynb
  → 금칙어 테스트, RAG 검색 테스트, 멀티턴 대화, 인터랙티브 루프

04_debate.ipynb
  → AI vs AI 전체 실행, AI vs User 인터랙티브 토론, 요약 출력
```

노트북 재생성이 필요하면:
```bash
cd pipeline
python create_notebooks.py
```

---

## 실험 매트릭스

총 **6개 조합**을 비교하여 최적 설정을 선택합니다.

| 조합 | 청킹 전략 | 임베딩 모델 | ChromaDB 컬렉션 |
|------|-----------|------------|----------------|
| 1 | fixed | small | `articles_fixed_small` |
| 2 | fixed | large | `articles_fixed_large` |
| 3 | sentence | small | `articles_sentence_small` |
| 4 | sentence | large | `articles_sentence_large` |
| 5 | semantic | small | `articles_semantic_small` |
| 6 | semantic | large | `articles_semantic_large` |

| 청킹 전략 | 방식 | 파라미터 |
|-----------|------|---------|
| `fixed` | 글자 수 기준 분할 | 500자, 오버랩 50자 |
| `sentence` | 한국어 문장 단위 분할 | 최대 600자 |
| `semantic` | 임베딩 유사도 기반 분할 | 임계값 0.75, 최소 100자 |

`01_setup_and_index.ipynb`에서 8개 평가 쿼리로 각 조합의 평균 Top-1 코사인 유사도를 비교한 뒤, 가장 높은 조합을 이후 파이프라인에 적용합니다.

---

## 주요 설정값

| 항목 | 값 | 위치 |
|------|-----|------|
| 챗봇 입력 최대 | 500자 | `CHAT_MAX_INPUT_CHARS` |
| 챗봇 응답 최대 | 1500자 | `CHAT_MAX_RESPONSE_CHARS` |
| RAG Top-K | 5 | `CHAT_TOP_K` |
| 카드 추천 수 | 3 | `CHAT_RECOMMEND_COUNT` |
| 히스토리 윈도우 | 6턴 | `CHAT_HISTORY_WINDOW` |
| 일일 뉴스 카드 최대 | 10개 | `MAX_NEWS_CARDS_PER_DAY` |
| 일일 정책 카드 최대 | 5개 | `MAX_POLICY_CARDS_PER_DAY` |
| 클러스터링 임계값 | 0.70 | `cluster_articles()` |
| 시맨틱 분할 임계값 | 0.75 | `SEMANTIC_THRESHOLD` |
| 토론 라운드당 턴 | 3 | `DEBATE_TURNS_PER_ROUND` |
