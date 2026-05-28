"""
pipeline/db/rdb.py
SQLAlchemy 기반 RDB CRUD
지원 DB: MySQL (운영) / SQLite (로컬 개발·테스트)

실제 스키마 기반 테이블:
  입력: ARTICLES, POLICIES, BILLS
  카드: CARDS, CARD_TABS, CARD_ARTICLES, CARD_POLICIES, CARD_BILLS, BIAS_LOGS
  챗봇: CHAT_SESSIONS, CHAT_MESSAGES
  토론: DEBATES, DEBATE_MODES, DEBATE_PARTICIPANTS, DEBATE_MESSAGES
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Generator

from sqlalchemy import create_engine, text, Engine
from sqlalchemy.pool import NullPool


# ══════════════════════════════════════════════════════════════════════════════
# 엔진 & 초기화
# ══════════════════════════════════════════════════════════════════════════════

def get_engine(db_url: str | None = None) -> Engine:
    """
    SQLAlchemy 엔진 반환
    db_url 예시:
      MySQL : "mysql+pymysql://user:pw@localhost:3306/politalk?charset=utf8mb4"
      SQLite: "sqlite:///./politalk_dev.db"
    """
    from config import DB_URL
    url = db_url or DB_URL
    # SQLite는 멀티스레드 체크 비활성화, MySQL은 커넥션 풀 사용
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)


@contextmanager
def get_conn(engine: Engine) -> Generator:
    """트랜잭션 컨텍스트 매니저"""
    with engine.begin() as conn:
        yield conn


def init_tables(engine: Engine) -> None:
    """
    파이프라인에 필요한 테이블 생성 (IF NOT EXISTS)
    MySQL과 SQLite 양쪽 호환 — ENUM → VARCHAR, AUTO_INCREMENT → 방언별 처리
    """
    is_sqlite = str(engine.url).startswith("sqlite")

    # SQLite: INTEGER PRIMARY KEY AUTOINCREMENT
    # MySQL:  BIGINT AUTO_INCREMENT PRIMARY KEY
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "BIGINT AUTO_INCREMENT PRIMARY KEY"
    ts_default = "CURRENT_TIMESTAMP"

    ddl_list = [
        f"""CREATE TABLE IF NOT EXISTS articles (
            id           {pk},
            title        VARCHAR(500) NOT NULL,
            content      TEXT         NOT NULL,
            press        VARCHAR(100) NOT NULL,
            published_at DATETIME,
            url          VARCHAR(1000) NOT NULL,
            created_at   DATETIME     NOT NULL DEFAULT {ts_default},
            UNIQUE (url)
        )""",

        f"""CREATE TABLE IF NOT EXISTS policies (
            id           {pk},
            name         VARCHAR(255) NOT NULL,
            content      TEXT         NOT NULL,
            apply_method TEXT,
            organization VARCHAR(255),
            url          VARCHAR(1000) NOT NULL,
            created_at   DATETIME     NOT NULL DEFAULT {ts_default},
            updated_at   DATETIME     NOT NULL DEFAULT {ts_default},
            UNIQUE (url)
        )""",

        f"""CREATE TABLE IF NOT EXISTS bills (
            id           {pk},
            name         VARCHAR(255) NOT NULL,
            proposed_at  DATE,
            proposer     VARCHAR(255),
            status       VARCHAR(100) NOT NULL,
            url          VARCHAR(1000) NOT NULL,
            created_at   DATETIME     NOT NULL DEFAULT {ts_default},
            updated_at   DATETIME     NOT NULL DEFAULT {ts_default},
            UNIQUE (url)
        )""",

        # CARDS — 카드 메인 테이블
        f"""CREATE TABLE IF NOT EXISTS cards (
            id         {pk},
            card_type  VARCHAR(20)  NOT NULL,
            status     VARCHAR(20)  NOT NULL DEFAULT 'DRAFT',
            created_at DATETIME     NOT NULL DEFAULT {ts_default}
        )""",

        # CARD_TABS — 탭별 생성 콘텐츠 (SUMMARY/CORE/OPINION/POLICY/ARTICLE/SOURCE)
        f"""CREATE TABLE IF NOT EXISTS card_tabs (
            id         {pk},
            card_id    BIGINT       NOT NULL,
            tab_type   VARCHAR(30)  NOT NULL,
            content    TEXT         NOT NULL,
            created_at DATETIME     NOT NULL DEFAULT {ts_default}
        )""",

        # 연결 테이블
        f"""CREATE TABLE IF NOT EXISTS card_articles (
            id         {pk},
            card_id    BIGINT NOT NULL,
            article_id BIGINT NOT NULL
        )""",

        f"""CREATE TABLE IF NOT EXISTS card_policies (
            id        {pk},
            card_id   BIGINT NOT NULL,
            policy_id BIGINT NOT NULL
        )""",

        f"""CREATE TABLE IF NOT EXISTS card_bills (
            id      {pk},
            card_id BIGINT NOT NULL,
            bill_id BIGINT NOT NULL
        )""",

        # BIAS_LOGS
        f"""CREATE TABLE IF NOT EXISTS bias_logs (
            id            {pk},
            card_id       BIGINT      NOT NULL,
            tab_type      VARCHAR(30) NOT NULL,
            is_detected   INTEGER     NOT NULL DEFAULT 0,
            detected_text TEXT,
            action        VARCHAR(20),
            created_at    DATETIME    NOT NULL DEFAULT {ts_default}
        )""",

        # CHAT_SESSIONS
        f"""CREATE TABLE IF NOT EXISTS chat_sessions (
            id                BIGINT       NOT NULL,
            user_id           BIGINT       NOT NULL,
            active_newscard_id BIGINT,
            title             VARCHAR(255) NOT NULL,
            created_at        DATETIME     NOT NULL DEFAULT {ts_default},
            {"PRIMARY KEY (id)" if is_sqlite else "PRIMARY KEY (id)"}
        )""" if is_sqlite else
        f"""CREATE TABLE IF NOT EXISTS chat_sessions (
            id                {pk},
            user_id           BIGINT       NOT NULL,
            active_newscard_id BIGINT,
            title             VARCHAR(255) NOT NULL,
            created_at        DATETIME     NOT NULL DEFAULT {ts_default}
        )""",

        f"""CREATE TABLE IF NOT EXISTS chat_messages (
            id         {pk},
            session_id BIGINT      NOT NULL,
            role       VARCHAR(20) NOT NULL,
            content    TEXT        NOT NULL,
            created_at DATETIME    NOT NULL DEFAULT {ts_default}
        )""",

        # DEBATE_MODES
        f"""CREATE TABLE IF NOT EXISTS debate_modes (
            mode_id     {pk},
            mode_name   VARCHAR(50) NOT NULL,
            description TEXT
        )""",

        # DEBATES
        f"""CREATE TABLE IF NOT EXISTS debates (
            debate_id       {pk},
            mode_id         BIGINT      NOT NULL,
            selected_policy TEXT        NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'ONGOING',
            stage           VARCHAR(30) NOT NULL DEFAULT 'position',
            created_at      DATETIME    NOT NULL DEFAULT {ts_default},
            ended_at        DATETIME
        )""",

        # DEBATE_PARTICIPANTS
        f"""CREATE TABLE IF NOT EXISTS debate_participants (
            participant_id   {pk},
            debate_id        BIGINT      NOT NULL,
            participant_type VARCHAR(10) NOT NULL,
            stance           VARCHAR(20) NOT NULL,
            role_name        VARCHAR(50) NOT NULL,
            user_id          BIGINT,
            difficulty       VARCHAR(10) NOT NULL DEFAULT 'hard'
        )""",

        # DEBATE_MESSAGES
        f"""CREATE TABLE IF NOT EXISTS debate_messages (
            message_id     {pk},
            debate_id      BIGINT      NOT NULL,
            participant_id BIGINT      NOT NULL,
            content        TEXT        NOT NULL,
            message_type   VARCHAR(20) NOT NULL,
            turn_num       INTEGER,
            created_at     DATETIME    NOT NULL DEFAULT {ts_default}
        )""",
    ]

    with engine.begin() as conn:
        for ddl in ddl_list:
            conn.execute(text(ddl))
    print("DB 테이블 초기화 완료")


# ══════════════════════════════════════════════════════════════════════════════
# ARTICLES / POLICIES / BILLS  — 로드
# ══════════════════════════════════════════════════════════════════════════════

def load_articles(
    engine: Engine,
    limit: int = 200,
    min_content_len: int = 80,
) -> list[dict]:
    """RAW 기사 로드 (content 길이 필터)"""
    with get_conn(engine) as conn:
        rows = conn.execute(text(
            "SELECT id, title, content, press, published_at, url, created_at "
            "FROM articles "
            f"WHERE LENGTH(content) >= {min_content_len} "
            "ORDER BY created_at DESC "
            f"LIMIT {limit}"
        )).mappings().all()
    return [dict(r) for r in rows]


def upsert_article(engine: Engine, art: dict) -> int:
    """
    기사 upsert (URL 기준 중복 방지, REQ-CARD-003.3)
    Returns: article id
    """
    with get_conn(engine) as conn:
        is_sqlite = str(engine.url).startswith("sqlite")
        if is_sqlite:
            sql = text(
                "INSERT OR IGNORE INTO articles (title, content, press, published_at, url) "
                "VALUES (:title, :content, :press, :published_at, :url)"
            )
        else:
            sql = text(
                "INSERT IGNORE INTO articles (title, content, press, published_at, url) "
                "VALUES (:title, :content, :press, :published_at, :url)"
            )
        conn.execute(sql, {
            "title":        art.get("title", ""),
            "content":      art.get("content", ""),
            "press":        art.get("publisher", art.get("press", "")),
            "published_at": art.get("published_at"),
            "url":          art.get("url", ""),
        })
        row = conn.execute(
            text("SELECT id FROM articles WHERE url = :url"),
            {"url": art.get("url", "")},
        ).fetchone()
    return row[0] if row else -1


def load_policies(engine: Engine, limit: int = 50) -> list[dict]:
    with get_conn(engine) as conn:
        rows = conn.execute(text(
            "SELECT id, name, content, apply_method, organization, url "
            f"FROM policies ORDER BY created_at DESC LIMIT {limit}"
        )).mappings().all()
    return [dict(r) for r in rows]


def load_bills(engine: Engine, limit: int = 50) -> list[dict]:
    with get_conn(engine) as conn:
        rows = conn.execute(text(
            "SELECT id, name, proposed_at, proposer, status, url "
            f"FROM bills ORDER BY created_at DESC LIMIT {limit}"
        )).mappings().all()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# CARDS / CARD_TABS / BIAS_LOGS  — 카드 저장 & 조회
# ══════════════════════════════════════════════════════════════════════════════

def save_card(
    engine: Engine,
    card_type: str,           # "NEWS" | "POLICY" | "BILL"
    tab_contents: dict,       # {tab_type: content_str, ...}
    source_ids: list[int],    # article_ids or policy_ids or bill_ids
    status: str = "DRAFT",
) -> int:
    """
    CARDS + CARD_TABS + CARD_ARTICLES/POLICIES/BILLS 저장
    Returns: card_id
    """
    link_table = {"NEWS": "card_articles", "POLICY": "card_policies", "BILL": "card_bills"}
    link_col   = {"NEWS": "article_id",    "POLICY": "policy_id",     "BILL": "bill_id"}

    with get_conn(engine) as conn:
        # 1) CARDS
        result = conn.execute(
            text("INSERT INTO cards (card_type, status) VALUES (:ct, :st)"),
            {"ct": card_type, "st": status},
        )
        card_id = result.lastrowid

        # 2) CARD_TABS
        for tab_type, content in tab_contents.items():
            conn.execute(
                text("INSERT INTO card_tabs (card_id, tab_type, content) "
                     "VALUES (:cid, :tt, :ct)"),
                {"cid": card_id, "tt": tab_type, "ct": content},
            )

        # 3) 연결 테이블
        tbl = link_table.get(card_type)
        col = link_col.get(card_type)
        if tbl and col:
            for sid in source_ids:
                conn.execute(
                    text(f"INSERT INTO {tbl} (card_id, {col}) VALUES (:cid, :sid)"),
                    {"cid": card_id, "sid": sid},
                )
    return card_id


def save_bias_log(
    engine: Engine,
    card_id: int,
    tab_type: str,
    is_detected: bool,
    detected_text: str = "",
    action: str | None = None,
) -> None:
    """편향 검토 이력 저장 (REQ-CARD-004.4-4)"""
    with get_conn(engine) as conn:
        conn.execute(
            text("INSERT INTO bias_logs (card_id, tab_type, is_detected, detected_text, action) "
                 "VALUES (:cid, :tt, :det, :dtxt, :act)"),
            {"cid": card_id, "tt": tab_type, "det": int(is_detected),
             "dtxt": detected_text, "act": action},
        )


def load_cards(
    engine: Engine,
    card_type: str | None = None,
    status: str = "PUBLISHED",
    limit: int = 50,
) -> list[dict]:
    """카드 목록 조회 — 탭 content 포함 (SUMMARY 탭만 기본 반환)"""
    where = "WHERE c.status = :st"
    params: dict = {"st": status}
    if card_type:
        where += " AND c.card_type = :ct"
        params["ct"] = card_type

    with get_conn(engine) as conn:
        rows = conn.execute(text(
            f"SELECT c.id, c.card_type, c.status, c.created_at, "
            f"       t.content AS summary_content "
            f"FROM cards c "
            f"LEFT JOIN card_tabs t ON t.card_id = c.id AND t.tab_type = 'SUMMARY' "
            f"{where} "
            f"ORDER BY c.created_at DESC LIMIT :lim"
        ), {**params, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def load_card_tabs(engine: Engine, card_id: int) -> dict:
    """카드 전체 탭 content 반환 {tab_type: content}"""
    with get_conn(engine) as conn:
        rows = conn.execute(text(
            "SELECT tab_type, content FROM card_tabs WHERE card_id = :cid"
        ), {"cid": card_id}).mappings().all()
    return {r["tab_type"]: r["content"] for r in rows}


def publish_card(engine: Engine, card_id: int) -> None:
    with get_conn(engine) as conn:
        conn.execute(
            text("UPDATE cards SET status='PUBLISHED' WHERE id=:cid"),
            {"cid": card_id},
        )


# ══════════════════════════════════════════════════════════════════════════════
# CHAT_SESSIONS / CHAT_MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

def create_chat_session(
    engine: Engine,
    user_id: int,
    title: str,
    active_card_id: int | None = None,
) -> int:
    """채팅 세션 생성 (REQ-CHAT-001.1)"""
    import time as _time
    session_id = int(_time.time() * 1000) % 2147483647
    with get_conn(engine) as conn:
        conn.execute(
            text("INSERT INTO chat_sessions (id, user_id, title) "
                 "VALUES (:id, :uid, :title)"),
            {"id": session_id, "uid": user_id, "title": title},
        )
        return session_id


def save_chat_message(
    engine: Engine,
    session_id: int,
    role: str,          # "user" | "assistant"
    content: str,
) -> int:
    with get_conn(engine) as conn:
        result = conn.execute(
            text("INSERT INTO chat_messages (session_id, role, content) "
                 "VALUES (:sid, :role, :content)"),
            {"sid": session_id, "role": role, "content": content},
        )
        return result.lastrowid


def load_chat_history(engine: Engine, session_id: int) -> list[dict]:
    """세션의 전체 메시지 이력 반환 (시간순)"""
    with get_conn(engine) as conn:
        rows = conn.execute(text(
            "SELECT id, role, content, created_at "
            "FROM chat_messages WHERE session_id = :sid "
            "ORDER BY created_at ASC"
        ), {"sid": session_id}).mappings().all()
    return [dict(r) for r in rows]


def delete_chat_session(engine: Engine, session_id: int) -> None:
    """세션 + 메시지 삭제 (REQ-CHAT-005)"""
    with get_conn(engine) as conn:
        conn.execute(text("DELETE FROM chat_messages WHERE session_id = :sid"),
                     {"sid": session_id})
        conn.execute(text("DELETE FROM chat_sessions WHERE id = :sid"),
                     {"sid": session_id})


def list_chat_sessions(engine: Engine, user_id: int) -> list[dict]:
    """사용자 채팅 히스토리 목록 (REQ-CHAT-003)"""
    with get_conn(engine) as conn:
        rows = conn.execute(text(
            "SELECT id, title, active_newscard_id, created_at "
            "FROM chat_sessions WHERE user_id = :uid "
            "ORDER BY created_at DESC"
        ), {"uid": user_id}).mappings().all()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# DEBATES / DEBATE_PARTICIPANTS / DEBATE_MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

def get_or_create_debate_mode(engine: Engine, mode_name: str) -> int:
    """토론 모드 ID 반환 (없으면 생성)"""
    with get_conn(engine) as conn:
        row = conn.execute(
            text("SELECT mode_id FROM debate_modes WHERE mode_name = :name"),
            {"name": mode_name},
        ).fetchone()
        if row:
            return row[0]
        result = conn.execute(
            text("INSERT INTO debate_modes (mode_name) VALUES (:name)"),
            {"name": mode_name},
        )
        return result.lastrowid


def create_debate(
    engine: Engine,
    mode_name: str,         # "AI vs AI" | "AI vs 사용자"
    selected_policy: str,   # 정책 제목 또는 내용 요약
) -> int:
    """토론방 생성 → debate_id 반환"""
    mode_id = get_or_create_debate_mode(engine, mode_name)
    with get_conn(engine) as conn:
        result = conn.execute(
            text("INSERT INTO debates (mode_id, selected_policy) VALUES (:mid, :pol)"),
            {"mid": mode_id, "pol": selected_policy},
        )
        return result.lastrowid


def add_debate_participant(
    engine: Engine,
    debate_id: int,
    participant_type: str,  # "AI" | "USER"
    stance: str,            # "AGREE" | "DISAGREE"
    role_name: str,         # "찬성 AI" | "반대 AI" | "사용자"
    user_id: int | None = None,
    difficulty: str = "hard",
) -> int:
    """토론 참여자 등록 → participant_id 반환"""
    with get_conn(engine) as conn:
        result = conn.execute(
            text("INSERT INTO debate_participants "
                 "(debate_id, participant_type, stance, role_name, user_id, difficulty) "
                 "VALUES (:did, :pt, :st, :rn, :uid, :diff)"),
            {"did": debate_id, "pt": participant_type, "st": stance,
             "rn": role_name, "uid": user_id, "diff": difficulty},
        )
        return result.lastrowid


def save_debate_message(
    engine: Engine,
    debate_id: int,
    participant_id: int,
    content: str,
    message_type: str,      # CLAIM / QUESTION / REBUTTAL / ANSWER / OPINION / SUMMARY
    turn_num: int | None = None,
) -> int:
    """토론 메시지 저장 (복구 완료)"""
    with get_conn(engine) as conn:  # 👈 누락되었던 핵심 트랜잭션 구문 추가!
        result = conn.execute(
            text("INSERT INTO debate_messages "
                 "(debate_id, participant_id, content, message_type, turn_num) "
                 "VALUES (:did, :pid, :content, :mtype, :turn)"),
            {"did": debate_id, "pid": participant_id, "content": content,
             "mtype": message_type, "turn": turn_num},
        )
        return result.lastrowid  # 👈 들여쓰기 라인 완벽 일치


def load_debate_history(engine: Engine, debate_id: int) -> list[dict]:
    """토론 전체 발언 이력 (시간순)"""
    with get_conn(engine) as conn:
        rows = conn.execute(text(
            "SELECT m.message_id, m.content, m.message_type, m.turn_num, m.created_at, "
            "       p.participant_type, p.stance, p.role_name "
            "FROM debate_messages m "
            "JOIN debate_participants p ON p.participant_id = m.participant_id "
            "WHERE m.debate_id = :did "
            "ORDER BY m.created_at ASC, m.message_id ASC"
        ), {"did": debate_id}).mappings().all()
    return [dict(r) for r in rows]


def update_debate_stage(engine: Engine, debate_id: int, stage: str) -> None:
    """토론 진행 단계 업데이트"""
    with get_conn(engine) as conn:
        conn.execute(
            text("UPDATE debates SET stage = :stage WHERE debate_id = :did"),
            {"stage": stage, "did": debate_id},
        )


def end_debate(engine: Engine, debate_id: int) -> None:
    with get_conn(engine) as conn:
        conn.execute(
            text("UPDATE debates SET status='ENDED', ended_at=CURRENT_TIMESTAMP, stage='done' "
                 "WHERE debate_id = :did"),
            {"did": debate_id},
        )


def list_debates(engine: Engine, user_id: int) -> list[dict]:
    """사용자 토론 히스토리 목록 (REQ-DEBATE-007)"""
    with get_conn(engine) as conn:
        rows = conn.execute(text(
            "SELECT d.debate_id, d.selected_policy, d.status, d.stage, d.created_at, "
            "       dm.mode_name "
            "FROM debates d "
            "JOIN debate_modes dm ON dm.mode_id = d.mode_id "
            "JOIN debate_participants p ON p.debate_id = d.debate_id "
            "WHERE p.participant_type = 'USER' AND p.user_id = :uid "
            "ORDER BY d.created_at DESC"
        ), {"uid": user_id}).mappings().all()
    return [dict(r) for r in rows]
