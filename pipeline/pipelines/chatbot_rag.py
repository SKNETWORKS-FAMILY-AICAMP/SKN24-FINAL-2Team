"""
pipeline/pipelines/chatbot_rag.py
RAG 기반 챗봇 — LangGraph 멀티에이전트 (LangGraph Message State 맥락 유지 + Qdrant 정밀 로그 결합본)
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Tuple, TypedDict, Annotated

from sqlalchemy import Engine
from openai import OpenAI
from qdrant_client import QdrantClient
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages  # 👈 랭그래프 메시지 누적 리듀서 추가
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from config import (
    LLM_MODEL, LLM_MODEL_FAST,
    CHAT_MAX_INPUT_CHARS, CHAT_TOP_K,
    CHAT_RECOMMEND_COUNT, CHAT_HISTORY_WINDOW,
    EMBEDDING_MODELS,
)
if "ko-sroberta" not in EMBEDDING_MODELS:
    EMBEDDING_MODELS["ko-sroberta"] = EMBEDDING_MODELS.get("small", "jhgan/ko-sroberta-multitask")

from utils import llm
from db.rdb import (
    create_chat_session, save_chat_message,
    load_chat_history, delete_chat_session, list_chat_sessions,
)

from db.vectordb_qdrant import retrieve, retrieve_from_cards, COLLECTION_NEWS

logger = logging.getLogger(__name__)

def _log(msg: str) -> None:
    logger.info(msg)
    print(msg, flush=True)


# ══════════════════════════════════════════════════════════════════════════
# State 정의 (Annotated 메시지 버퍼 구조 도입)
# ══════════════════════════════════════════════════════════════════════════

class ChatState(TypedDict, total=False):
    session_id:        int
    user_message:      str
    # 🎯 [맥락 유지 핵심] 전체 대화 시퀀스를 유실 없이 추적하고 라우터/RAG 에이전트와 상시 공유합니다.
    messages:          Annotated[List[BaseMessage], add_messages]
    card_context_json: Optional[str]
    intent:            str
    rag_context:       str
    reply:             str
    reply_retry:       int
    bias_passed:       bool
    recommended_cards: List[Dict]
    blocked:           bool
    error:             Optional[str]


# ══════════════════════════════════════════════════════════════════════════
# 프롬프트 정의존
# ══════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
당신은 대한민국 청년(20~30대)을 위한 정책·뉴스 해설 챗봇 'POLICITY'입니다.

규칙:
1. 제공된 검색 결과 범위 내에서만 답변. 사안이 없거나 모르면 "제공된 정보 내에서는 답변하기 어렵습니다"로 안내
2. 특정 정당·후보·정책을 지지하거나 비방하는 답변 금지 (완전 중립 유지)
3. 답변은 쉽고 친근한 대화체로 풀어서 구성
4. 뉴스·정책과 관련 없는 질문은 정중히 거절"""

_PROFANITY_PROMPT = """\
다음 사용자 입력에 아래 금칙어 범주가 포함되어 있는지 확인하세요.
범주: 성적 비하·비속어 / 특정 정치인·정당 비하 / 특정 인종·성별·종교 혐오

입력: {user_input}

JSON: {{"contains_profanity": true 또는 false, "reason": "이유"}}"""

_ROUTER_PROMPT = """\
사용자의 현재 질문과 전체 대화 맥락을 분석해서 아래 중 하나로 분류하세요.

[RECOMMEND] 다음 중 하나에 해당하면:
- 비슷한 카드·정책·뉴스를 더 보여달라는 요청
- 특정 주제의 카드를 찾아달라는 요청
- "더 있어?", "관련 카드", "다른 것도" 같은 표현

[ANSWER] 그 외 정책·뉴스에 대한 일반 질문 및 꼬리 질문

사용자 질문: {user_message}
전체 대화 맥락 요약: {chat_context}

JSON: {{"intent": "RECOMMEND" 또는 "ANSWER", "reason": "한 줄 이유"}}"""

_ANSWER_PROMPT = """\
[검색된 관련 정보]
{rag_context}

[사용자 질문 및 대화 흐름]
{user_message}

위 정보를 바탕으로 청년 친화적으로 답변하세요."""

_BIAS_CHECK_PROMPT = """\
다음 답변에 특정 정당·후보·정치인을 지지하거나 비방하는 표현이 있는지 검토하세요.

[답변]
{reply}

JSON: {{"has_bias": true 또는 false, "detected_text": "감지된 표현 (없으면 빈 문자열)", "reason": "한 줄 이유"}}"""

_REGENERATE_PROMPT = """\
아래 답변에 정치적 편향이 감지되었습니다.
편향 표현: {detected_text}

편향을 제거하고 중립적으로 재작성하세요.

[원본 답변]
{reply}

[검색된 관련 정보]
{rag_context}

[사용자 질문]
{user_message}"""


# ══════════════════════════════════════════════════════════════════════════
# ChatbotRAGPipeline 실전 클래스
# ══════════════════════════════════════════════════════════════════════════

class ChatbotRAGPipeline:

    def __init__(
        self,
        engine: Engine,
        chroma: QdrantClient,
        openai_client: OpenAI,
        strategy: str = "sentence",
        model_key: str = "ko-sroberta",
    ):
        self.engine     = engine
        self.chroma     = chroma
        self.client     = openai_client
        self.strategy   = strategy
        self.model_key  = model_key
        
        try:
            self.model_name = EMBEDDING_MODELS[model_key]
        except KeyError:
            self.model_name = "jhgan/ko-sroberta-multitask"
            
        self.graph      = self._build_graph()

    def create_session(self, user_id: int, title: str, active_card_id: Optional[int] = None) -> int:
        return create_chat_session(self.engine, user_id, title, active_card_id)

    def get_history(self, session_id: int) -> List[Dict]:
        return load_chat_history(self.engine, session_id)

    def delete_session(self, session_id: int) -> None:
        delete_chat_session(self.engine, session_id)

    def list_sessions(self, user_id: int) -> List[Dict]:
        return list_chat_sessions(self.engine, user_id)

    def chat(
        self,
        session_id: int,
        user_message: str,
        active_card_id: Optional[int] = None,
        card_context_json: Optional[str] = None,
        messages: Optional[List[BaseMessage]] = None,
    ) -> Tuple[str, List[Dict]]:
        if len(user_message) > CHAT_MAX_INPUT_CHARS:
            return (f"입력은 {CHAT_MAX_INPUT_CHARS}자 이내로 해주세요.", [])

        # ─────────────────────────────────────────────────────────────────
        # 🎯 [챗봇 로직 내부 보정 가드레일]
        # 유저가 "이 개정안 언제 나와?"처럼 대명사형 꼬리 질문을 던졌을 때,
        # 우측에 인라인 카드가 켜져 있다면 질문 끝에 강제로 컨텍스트 키워드를 임베딩 힌트로 더해줍니다.
        # ─────────────────────────────────────────────────────────────────
        refined_message = user_message
        if card_context_json and ("개정안" in user_message or "이 정책" in user_message or "언제" in user_message or len(user_message) < 15):
            try:
                # card_context_json 내부에 들어있는 title(예: 출산휴가 개정안)을 파싱
                ctx_dict = json.loads(card_context_json)
                card_title = ctx_dict.get("title", "")
                if card_title and card_title not in user_message:
                    # Qdrant 벡터 검색기가 찰떡같이 알아먹도록 쿼리를 보정합니다.
                    refined_message = f"{user_message} (참조 정책 카드: {card_title})"
                    _log(f"🔮 [챗봇 로직 보정] 대명사 검색어 우회 가드 가동 -> 변경된 쿼리: {refined_message}")
            except Exception:
                pass
        # ─────────────────────────────────────────────────────────────────

        # 초기 그래프 상태 구조 바인딩
        initial: ChatState = {
            "session_id":        session_id,
            "user_message":      refined_message,  # 👈 보정된 쿼리를 검색 엔진 노드로 토스!
            "messages":          messages or [HumanMessage(content=refined_message)],
            "card_context_json": card_context_json,
            "intent":            "",
            "rag_context":       "",
            "reply":             "",
            "reply_retry":       0,
            "bias_passed":       False,
            "recommended_cards": [],
            "blocked":           False,
            "error":             None,
        }
        result = self.graph.invoke(initial)

        reply = result.get("reply", "")
        recs  = result.get("recommended_cards", [])
        return reply, recs

    def _build_graph(self) -> StateGraph:
        g = StateGraph(ChatState)

        g.add_node("profanity_check", self._profanity_check_node)
        g.add_node("router",          self._router_node)
        g.add_node("rag_retrieve",    self._rag_retrieve_node)
        g.add_node("answer",          self._answer_node)
        g.add_node("bias_check",      self._bias_check_node)
        g.add_node("regenerate",      self._regenerate_node)
        g.add_node("save_answer",     self._save_answer_node)
        g.add_node("recommend",       self._recommend_node)
        g.add_node("save_recommend",  self._save_recommend_node)

        g.add_edge(START, "profanity_check")

        g.add_conditional_edges(
            "profanity_check",
            lambda s: END if s.get("blocked") else "router",
        )

        g.add_conditional_edges(
            "router",
            lambda s: s.get("intent", "ANSWER"),
            {"ANSWER": "rag_retrieve", "RECOMMEND": "recommend"},
        )

        g.add_edge("rag_retrieve", "answer")
        g.add_edge("answer",       "bias_check")

        g.add_conditional_edges(
            "bias_check",
            self._route_bias,
            {"ok": "save_answer", "retry": "regenerate", "skip": "save_answer"},
        )
        g.add_edge("regenerate", "save_answer")
        g.add_edge("save_answer", END)

        g.add_edge("recommend",      "save_recommend")
        g.add_edge("save_recommend", END)

        return g.compile()

    def _route_bias(self, state: ChatState) -> str:
        if state.get("bias_passed"):
            return "ok"
        if state.get("reply_retry", 0) < 1:
            return "retry"
        _log("⚠️  [bias_check] 재생성 후에도 편향 감지 → 그대로 저장")
        return "skip"

    # ─── 각 에이전트 노드 구현 ────────────────────────────────────────────────

    def _profanity_check_node(self, state: ChatState) -> Dict:
        try:
            raw = llm(
                [{"role": "user", "content": _PROFANITY_PROMPT.format(
                    user_input=state["user_message"]
                )}],
                self.client, model=LLM_MODEL_FAST, max_tokens=80, json_mode=True,
            )
            result = json.loads(raw)
            blocked = result.get("contains_profanity", False)
        except Exception:
            blocked = False

        if blocked:
            _log("🚫 [profanity_check] 금칙어 감지 → 차단")
            reply = "⚠️ 부적절한 표현이 포함되어 있어 답변할 수 없습니다."
            save_chat_message(self.engine, state["session_id"], "user",      state["user_message"])
            save_chat_message(self.engine, state["session_id"], "assistant", reply)
            return {"blocked": True, "reply": reply}

        _log("✅ [profanity_check] 통과")
        return {"blocked": False}

    def _router_node(self, state: ChatState) -> Dict:
        card_ctx = state.get("card_context_json", "") or "없음"
        
        # 🔧 대화 히스토리 맥락 문자열 요약 가공
        history_str = "\n".join([f"[{type(m).__name__}]: {m.content}" for m in state["messages"][:-1]])
        
        try:
            raw = llm(
                [{"role": "user", "content": _ROUTER_PROMPT.format(
                    user_message  = state["user_message"],
                    chat_context  = history_str[-1000:],  # 최근 맥락 위주 전송
                )}],
                self.client, model=LLM_MODEL_FAST, max_tokens=80, json_mode=True,
            )
            result = json.loads(raw)
            intent = result.get("intent", "ANSWER")
        except Exception:
            intent = "ANSWER"

        _log(f"🔀 [router] intent={intent}")
        return {"intent": intent}

    def _rag_retrieve_node(self, state: ChatState) -> Dict:
        """기사 + 카드 컬렉션 Qdrant 실시간 하이브리드 검색 노드 (정밀 로그 추적 강화형)"""
        # 🔧 "서울시 산다고 하면 어때?" 와 같은 생략형 질문 방어
        # 직전 대화 2턴과 결합한 맥락 기반 프롬프트 힌트 생성
        recent_turns = state["messages"][-3:]
        context_hint = " ".join([m.content for m in recent_turns])
        
        query = state["user_message"]
        parts: List[str] = []

        if state.get("card_context_json"):
            parts.append(f"[현재 열람 카드 컨텍스트]\n{state['card_context_json'][:600]}")

        _log("\n" + "="*60)
        _log(f"🛰️  [Qdrant RAG Engine] 벡터 하이브리드 리트리벌 개시")
        _log(f"🔑 [실제 유저 인풋 질문]: {query}")
        _log(f"🧠 [추적된 최근 대화 맥락 힌트]: {context_hint[:150]}...")
        _log("-" * 60)

        try:
            # 1. 뉴스 기사(NEWS) 컬렉션 스캔
            art_results = retrieve(
                query=query,
                client=self.chroma,
                collection=COLLECTION_NEWS,
                model_key=self.model_key,
                top_k=CHAT_TOP_K
            )
            
            _log(f"📰 [NEWS 컬렉션 매칭 결과: 총 {len(art_results)}건 스캔 성공]")
            for idx, r in enumerate(art_results, 1):
                meta = r.get("metadata", {})
                pub  = meta.get("press", meta.get("publisher", "알 수 없는 언론사"))
                title = meta.get("title", "제목 없음")
                score = r.get("score", 0.0)
                
                # 🎯 [로그 추가] 터미널에 가져온 데이터 청크와 유사도 점수 상세 출력
                _log(f"  ({idx}) [출처: {pub}] {title} | 🎯 유사도 점수: {score:.4f}")
                _log(f"      ㄴ [청크 원문 일부]: {r['content'][:150]}...")
                
                parts.append(f"[데이터 출처: {pub}] {r['content'][:300]}")
                
        except Exception as e:
            art_results = []
            _log(f"⚠ Qdrant 기사 RAG 검색 중 예외 발생: {e}")

        _log("-" * 60)

        try:
            # 2. 정책 카드 컬렉션 스캔
            card_results = retrieve_from_cards(
                query=query,
                client=self.chroma,
                model_key=self.model_key,
                top_k=3
            )
            
            _log(f"📋 [CARDS 컬렉션 매칭 결과: 총 {len(card_results)}건 스캔 성공]")
            for idx, r in enumerate(card_results, 1):
                meta = r.get("metadata", {})
                title = meta.get("title", "카드 제목 없음")
                score = r.get("score", 0.0)
                
                # 🎯 [로그 추가] 터미널에 가져온 정책 카드 정보와 유사도 점수 상세 출력
                _log(f"  ({idx}) [정책명: {title}] | 🎯 유사도 점수: {score:.4f}")
                _log(f"      ㄴ [카드 원문 일부]: {r['content'][:120]}...")
                
                parts.append(f"[추천 카드 인라인 문맥] {r['content'][:200]}")
                
        except Exception as e:
            card_results = []
            _log(f"⚠ Qdrant 카드 RAG 검색 중 예외 발생: {e}")

        _log("="*60 + "\n")

        rag_context = "\n\n".join(parts) if parts else "관련 정보를 찾지 못했습니다."
        return {"rag_context": rag_context}

    def _answer_node(self, state: ChatState) -> Dict:
        # 🔧 대화 세션 기반 랭체인 메시지 이력 정렬 구조 빌드
        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
        
        # 랭그래프의 누적된 messages 상태를 OpenAI API 호환 규격으로 변환하여 주입
        for m in state["messages"][:-1]:
            role = "user" if isinstance(m, HumanMessage) else "assistant"
            messages.append({"role": role, "content": m.content})
            
        messages.append({"role": "user", "content": _ANSWER_PROMPT.format(
            rag_context  = state.get("rag_context", ""),
            user_message = state["user_message"],
        )})

        try:
            reply = llm(messages, self.client, model=LLM_MODEL, max_tokens=800)
        except Exception as e:
            reply = "죄송합니다, 답변 생성 중 오류가 발생했습니다."
            _log(f"⚠️  [answer] 실패: {e}")

        _log(f"💬 [answer] {len(reply)}자 답변 생성 완료")
        return {"reply": reply, "messages": [AIMessage(content=reply)]}  # AIMessage 누적

    def _bias_check_node(self, state: ChatState) -> Dict:
        try:
            raw = llm(
                [{"role": "user", "content": _BIAS_CHECK_PROMPT.format(
                    reply=state.get("reply", "")[:2000]
                )}],
                self.client, model=LLM_MODEL_FAST, max_tokens=150, json_mode=True,
            )
            result       = json.loads(raw)
            has_bias     = result.get("has_bias", False)
            detected_txt = result.get("detected_text", "")
        except Exception:
            has_bias, detected_txt = False, ""

        if has_bias:
            _log(f"⚠️  [bias_check] 편향 감지: '{detected_txt[:40]}'")
            return {
                "bias_passed":    False,
                "bias_detected":  detected_txt,
            }

        _log("✅ [bias_check] 통과")
        return {"bias_passed": True, "bias_detected": ""}

    def _regenerate_node(self, state: ChatState) -> Dict:
        _log("🔄 [regenerate] 편향 제거 후 재생성")
        try:
            reply = llm(
                [{"role": "user", "content": _REGENERATE_PROMPT.format(
                    detected_text = state.get("bias_detected", ""),
                    reply         = state.get("reply", ""),
                    rag_context   = state.get("rag_context", ""),
                    user_message  = state["user_message"],
                )}],
                self.client, model=LLM_MODEL, max_tokens=800,
            )
            _log(f"✅ [regenerate] {len(reply)}자 재생성 완료")
        except Exception as e:
            reply = state.get("reply", "")
            _log(f"⚠️  [regenerate] 실패, 원본 사용: {e}")

        return {
            "reply":       reply,
            "reply_retry": state.get("reply_retry", 0) + 1,
            "bias_passed": True,
            "messages":    [AIMessage(content=reply)]
        }

    # pipeline/pipelines/chatbot_rag.py 하단 노드 메서드 수정본

    def _save_answer_node(self, state: ChatState) -> Dict:
        reply = state.get("reply", "")
        save_chat_message(self.engine, state["session_id"], "user",      state["user_message"])
        save_chat_message(self.engine, state["session_id"], "assistant", reply)

        # 🎯 [추천 팩트 보정] 우측 인라인 카드가 켜져 있다면, 질문 텍스트 대신 '현재 카드 제목'을 추천 쿼리로 전달합니다.
        recommend_query = state["user_message"]
        if state.get("card_context_json"):
            try:
                ctx_dict = json.loads(state["card_context_json"])
                card_title = ctx_dict.get("title", "")
                if card_title:
                    recommend_query = card_title  # 👈 추천 소스를 현재 켜진 카드로 강제 맵핑!
                    _log(f"🎯 [추천 로직 보정] 켜져 있는 카드 '{card_title}' 기반으로 연관 카드를 역추적합니다.")
            except Exception:
                pass

        recs = self._get_recommendations(recommend_query)
        _log(f"💾 [save_answer] 저장 완료 | 추천 카드 {len(recs)}개")
        return {"recommended_cards": recs}

    def _recommend_node(self, state: ChatState) -> Dict:
        # RECOMMEND 의도: 사용자 질문을 그대로 검색 쿼리로 사용
        # (열려있는 카드나 키워드와 무관하게 사용자가 요청한 내용 기준으로 추천)
        query = state["user_message"]

        recs  = self._get_recommendations(query, n=CHAT_RECOMMEND_COUNT + 1)
        reply = self._format_recommend_reply(recs)
        _log(f"📚 [recommend] 카드 {len(recs)}개 추천")
        return {"reply": reply, "recommended_cards": recs, "messages": [AIMessage(content=reply)]}

    def _save_recommend_node(self, state: ChatState) -> Dict:
        save_chat_message(self.engine, state["session_id"], "user",      state["user_message"])
        save_chat_message(self.engine, state["session_id"], "assistant", state.get("reply", ""))
        _log("💾 [save_recommend] 저장 완료")
        return {}

    # ─── 헬퍼 ─────────────────────────────────────────────────────────────────

    def _get_recommendations(self, query: str, n: int = CHAT_RECOMMEND_COUNT) -> List[Dict]:
        results = retrieve_from_cards(
            query=query,
            client=self.chroma,
            model_key=self.model_key,
            top_k=n
        )
        return [
            {
                "card_id":   int(r["metadata"].get("card_id", 0)),
                "card_type": r["metadata"].get("card_type", ""),
                "title":     r["metadata"].get("title", ""),
                "intro":     r["content"][:150],
                "score":     round(r["score"], 4),
            }
            for r in results
        ]

    def _format_recommend_reply(self, recs: List[Dict]) -> str:
        if not recs:
            return "관련 카드를 찾지 못했습니다. 다른 키워드로 검색해보세요."
        lines = ["관련 카드를 찾았어요! 아래 카드들을 확인해보세요. 👇"]
        for i, r in enumerate(recs[:CHAT_RECOMMEND_COUNT], 1):
            card_type = "📋 정책" if r["card_type"] == "POLICY" else \
                        "⚖️ 법안" if r["card_type"] == "BILL" else "📰 뉴스"
            lines.append(f"\n{i}. {card_type} **{r['title']}**\n   {r['intro']}")
        return "\n".join(lines)