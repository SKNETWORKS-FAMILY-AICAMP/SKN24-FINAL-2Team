"""
app.py — POLICITY 데모 (Streamlit)
실행: streamlit run app.py  (team2_final/ 루트에서)
플로우: 홈(키워드 선택) → 챗봇 → 카드 상세보기 → 토론
"""
import os, sys, json, time
from pathlib import Path

import streamlit as st
from openai import OpenAI
from sqlalchemy import create_engine
from qdrant_client import QdrantClient  # 👈 QdrantClient 로드

# 🎯 [맥락 유지 반영] 랭그래프의 add_messages 리듀서 싱크를 위한 메시지 객체 임포트
from langchain_core.messages import HumanMessage, AIMessage

ROOT_DIR     = Path(__file__).parent
PIPELINE_DIR = ROOT_DIR / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(PIPELINE_DIR / "embedding_hf"))

from pipeline.config import DB_URL, OPENAI_API_KEY
from pipeline.db.rdb import (
    get_engine, init_tables,
    load_cards, load_card_tabs,
    create_chat_session,
)
# 👈 순정 Chroma 대신 마이그레이션된 Qdrant 검색 로더 연동
from pipeline.db.vectordb_qdrant import get_qdrant_client
from pipeline.pipelines.chatbot_rag import ChatbotRAGPipeline
from pipeline.pipelines.debate import DebatePipeline

st.set_page_config(
    page_title="POLICITY",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# app.py 상단 init_resources 함수 정밀 고도화본

@st.cache_resource
def init_resources():
    engine = get_engine(DB_URL)
    
    # 1. 프로젝트 루트 기준 절대 경로로 qdrant_storage 위치 강제 고정
    current_root = Path(__file__).parent.absolute()
    actual_storage_path = str(current_root / "qdrant_storage")
    
    print(f"🔗 [Qdrant 인프라] {actual_storage_path} 폴더를 스캔합니다.")
    qdrant_instance = get_qdrant_client(actual_storage_path)
    
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    init_tables(engine)
    
    # 2. 멀티에이전트 파이프라인 빌드 (ko-sroberta 키 매핑)
    chat_pipe = ChatbotRAGPipeline(
        engine=engine, 
        chroma=qdrant_instance, 
        openai_client=openai_client,
        strategy="sentence", 
        model_key="ko-sroberta"
    )
    debate_pipe = DebatePipeline(
        engine=engine, 
        chroma=qdrant_instance, 
        openai_client=openai_client,
        strategy="sentence", 
        model_key="ko-sroberta"
    )
    
    # 🔥 [복구 완료] Streamlit 서버가 처음 켜질 때 임베딩 모델 가중치 미리 메모리에 적재
    with st.spinner("🚀 시연용 지능형 정치 교육 AI 엔진 워밍업 중..."):
        try:
            from embed_hf import get_embedder
            embedder = get_embedder("ko-sroberta")
            # 더미 텍스트 연산으로 CUDA 가속 장치 미리 깨우기
            embedder.encode_query("시작")
            print("🚀 [POLICITY System] ko-sroberta 임베딩 모델 사전 적재 대성공!")
        except Exception as e:
            print(f"⚠ 임베딩 모델 사전 로드 중 예외 로그 (무시 가능): {e}")
            
    return engine, qdrant_instance, openai_client, chat_pipe, debate_pipe

engine, chroma, openai_client, chat_pipeline, debate_pipeline = init_resources()

@st.cache_data(ttl=60)
def get_all_cards():
    cards = load_cards(engine, status="DRAFT", limit=100)
    cards += load_cards(engine, status="PUBLISHED", limit=100)
    seen, unique = set(), []
    for c in cards:
        if c["id"] not in seen:
            seen.add(c["id"])
            unique.append(c)
    return unique

def get_card_tabs(card_id):
    tabs = load_card_tabs(engine, card_id)
    parsed = {}
    for k, v in tabs.items():
        try:    parsed[k] = json.loads(v)
        except: parsed[k] = v
    return parsed

def get_card_title(card_id):
    try:
        tabs = load_card_tabs(engine, card_id)
        summ = json.loads(tabs.get("SUMMARY", "{}"))
        return summ.get("title", f"카드 #{card_id}")
    except:
        return f"카드 #{card_id}"

# ── 세션 상태 초기화 ──────────────────────────────────────────────────────
DEFAULTS = {
    "page": "home",
    "selected_keyword": None,
    "session_id": None,
    "chat_messages": [],
    "recommended_cards": [],
    "active_card_id": None,
    "debate_id": None,
    "debate_pro_id": None,
    "debate_con_id": None,
    "debate_user_pid": None,
    "debate_ai_pid": None,
    "debate_turn": 1,
    "debate_history": [],
    "debate_mode": None,
    "debate_topic": None,
    "debate_stance": "pro",
    "debate_diff": "hard",
    "debate_finished": False,
    "debate_summary": None,
    "chat_inline_card_id": None,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

KEYWORDS      = ["일자리", "주거", "교육", "금융", "생활복지", "문화"]
KEYWORD_EMOJI = {"일자리":"💼","주거":"🏠","교육":"📚","금융":"💰","생활복지":"🤝","문화":"🎭"}

def filter_cards_by_keyword(cards, kw):
    result = []
    for c in cards:
        try:
            tabs  = load_card_tabs(engine, c["id"])
            summ  = json.loads(tabs.get("SUMMARY","{}"))
            if kw in summ.get("category","") or kw in summ.get("title",""):
                c["_title"]   = summ.get("title","")
                c["_summary"] = summ
                result.append(c)
        except: pass
    return result

def reset_debate():
    for k in ["debate_id","debate_pro_id","debate_con_id","debate_user_pid","debate_ai_pid"]:
        st.session_state[k] = None
    st.session_state.debate_turn     = 1
    st.session_state.debate_history  = []
    st.session_state.debate_finished = False
    st.session_state.debate_summary  = None
    st.session_state.debate_mode     = None

TYPE_LABEL = {"POLICY":"📋 정책","BILL":"⚖️ 법안","NEWS":"📰 뉴스"}
TYPE_ICON  = {"POLICY":"📋","BILL":"⚖️","NEWS":"📰"}

# ════════════ 사이드바 ════════════
with st.sidebar:
    st.title("⚖️ POLICITY")
    st.caption("청년 정책·뉴스 정치교육 AI 플랫폼")
    st.markdown("---")
    if st.button("🏠 홈", use_container_width=True):
        st.session_state.page = "home"; st.rerun()
    if st.button("💬 챗봇", use_container_width=True):
        if not st.session_state.session_id:
            st.session_state.session_id = chat_pipeline.create_session(user_id=1, title="POLICITY 챗봇")
        st.session_state.page = "chat"; st.rerun()
    if st.button("🥊 토론장", use_container_width=True):
        st.session_state.page = "debate"; st.rerun()
    st.markdown("---")
    st.caption("SK네트웍스 Family AI 24기 2팀\n중간발표 데모")

# ════════════ 홈 ════════════
if st.session_state.page == "home":
    st.title("⚖️ POLICITY")
    st.subheader("청년 정치교육 AI 플랫폼")
    st.markdown("관심 있는 키워드를 선택하면 관련 카드와 함께 챗봇을 시작할 수 있어요.")
    st.markdown("---")
    st.markdown("### 🔍 관심 키워드 선택")
    cols = st.columns(3)
    for i, kw in enumerate(KEYWORDS):
        with cols[i % 3]:
            if st.button(f"{KEYWORD_EMOJI[kw]} {kw}", use_container_width=True, key=f"kw_{kw}"):
                st.session_state.selected_keyword = kw
                st.session_state.session_id = chat_pipeline.create_session(user_id=1, title=f"{kw} 관련 문의")
                st.session_state.chat_messages = [{"role":"assistant","content":
                    f"{KEYWORD_EMOJI[kw]} **{kw}** 키워드를 선택하셨네요!\n\n관련 정책·뉴스에 대해 무엇이든 물어보세요."}]
                st.session_state.page = "chat"; st.rerun()

    st.markdown("---")
    st.markdown("### 📋 최신 카드")
    all_cards = get_all_cards()
    if not all_cards:
        st.info("아직 생성된 카드가 없어요.")
    else:
        cols2 = st.columns(3)
        for i, card in enumerate(all_cards[:6]):
            with cols2[i % 3]:
                try:
                    tabs  = load_card_tabs(engine, card["id"])
                    summ  = json.loads(tabs.get("SUMMARY","{}"))
                    title = summ.get("title", f"카드 #{card['id']}")
                    cat   = summ.get("category","")
                    pts   = summ.get("summary_points",[])
                    tl    = TYPE_LABEL.get(card.get("card_type",""),"")
                    st.markdown(f"**{tl}** `{cat}`")
                    st.markdown(f"**{title}**")
                    if pts: st.caption(pts[0][:60]+"...")
                    if st.button("자세히 보기", key=f"home_card_{card['id']}", use_container_width=True):
                        st.session_state.active_card_id = card["id"]
                        st.session_state.page = "card"
                    st.markdown("---")
                except: pass

# ════════════ 챗봇 ════════════
elif st.session_state.page == "chat":
    st.title("💬 POLICITY 챗봇")
    if st.session_state.selected_keyword:
        st.caption(f"선택 키워드: **{st.session_state.selected_keyword}**")

    chat_col, card_col = st.columns([3, 2])

    with chat_col:
        # 1. 저장된 대화 히스토리부터 순서대로 출력
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # 2. 유저 입력 처리
        if user_input := st.chat_input("질문을 입력하세요..."):
            # 유저 메시지 화면에 즉시 출력 및 기록
            with st.chat_message("user"):
                st.markdown(user_input)
            st.session_state.chat_messages.append({"role": "user", "content": user_input})
            
            # 🔥 생성 중 레이아웃 꼬임을 막기 위해 비어있는 AI 말풍선 자리를 화면 최하단에 먼저 선언
            with st.chat_message("assistant"):
                message_placeholder = st.empty()  # 👈 입력창 위에 공간 고정
                with st.spinner("중립성 검수 및 Qdrant 문서 탐색 중..."):
                    try:
                        card_ctx = None
                        
                        # 🔧 [정밀 타격 버그 픽스]: 인라인 카드 뷰어 가동 중일 때는 'chat_inline_card_id' 컨텍스트를 안정적으로 임포트합니다.
                        target_card_id = st.session_state.get("chat_inline_card_id") or st.session_state.active_card_id
                        if target_card_id:
                            t = get_card_tabs(target_card_id)
                            s = t.get("SUMMARY", {})
                            card_ctx = json.dumps(s, ensure_ascii=False) if isinstance(s, dict) else str(s)
                        
                        # 랭그래프의 add_messages 리듀서 전용 대화 이력 빌더
                        langchain_messages = []
                        for m in st.session_state.chat_messages[:-1]:
                            if m["role"] == "user":
                                langchain_messages.append(HumanMessage(content=m["content"]))
                            else:
                                langchain_messages.append(AIMessage(content=m["content"]))
                                
                        langchain_messages.append(HumanMessage(content=user_input))
                        
                        # 백엔드 파이프라인 가동 (수정된 messages 및 정밀 card_ctx 매핑 전송)
                        reply, recs = chat_pipeline.chat(
                            session_id=st.session_state.session_id,
                            user_message=user_input,
                            card_context_json=card_ctx,
                            messages=langchain_messages
                        )
                        
                        # 미리 확보한 안쪽 공간에 답변 렌더링
                        message_placeholder.markdown(reply)
                        
                        # 세션 동기화
                        st.session_state.chat_messages.append({"role": "assistant", "content": reply})
                        st.session_state.recommended_cards = recs
                        
                    except Exception as e:
                        msg = f"오류: {e}"
                        message_placeholder.error(msg)
                        st.session_state.chat_messages.append({"role": "assistant", "content": msg})
            
            # 리런하여 UI 완전 동기화
            st.rerun()

    with card_col:
        # ── 인라인 카드 상세 보기 모드 ──────────────────────────────────────
        if st.session_state.get("chat_inline_card_id"):
            iid   = st.session_state.chat_inline_card_id
            itabs = get_card_tabs(iid)
            isumm = itabs.get("SUMMARY", {})
            if isinstance(isumm, str):
                try: isumm = json.loads(isumm)
                except: isumm = {}

            ititle = isumm.get("title", f"카드 #{iid}")
            icat   = isumm.get("category", "")
            ipts   = isumm.get("summary_points", [])
            iyouth = isumm.get("youth_connection", "")
            idq    = isumm.get("discussion_question", "")
            idet   = isumm.get("policy_details", {})

            all_c  = get_all_cards()
            ici    = next((c for c in all_c if c["id"] == iid), {})
            itl    = TYPE_LABEL.get(ici.get("card_type", ""), "")

            # 헤더
            hc, bc = st.columns([3,1])
            with hc:
                st.markdown(f"### {ititle}")
                st.caption(f"{itl}  `{icat}`")
            with bc:
                if st.button("✕ 닫기", key="inline_close"):
                    st.session_state.chat_inline_card_id = None

            it1, it2, it3 = st.tabs(["📝 요약", "🔍 본문", "⚖️ 찬반"])

            with it1:
                if ipts:
                    for p in ipts: st.markdown(f"- {p}")
                if iyouth: st.info(iyouth)
                if idet and isinstance(idet, dict):
                    for k, v in idet.items():
                        if v and v != "확인 필요":
                            st.markdown(f"**{k}**: {v}")

            with it2:
                icore = itabs.get("CORE", "")
                if isinstance(icore, str) and icore:
                    st.markdown(icore[:1500])
                    if len(icore) > 1500:
                        st.caption(f"... (총 {len(icore)}자)")
                if idq:
                    st.markdown(f"💬 **{idq}**")

            with it3:
                iopinion = itabs.get("OPINION", [])
                if isinstance(iopinion, str):
                    try: iopinion = json.loads(iopinion)
                    except: iopinion = []
                pros = [o for o in iopinion if o.get("stance") == "찬성"]
                cons = [o for o in iopinion if o.get("stance") == "반대"]
                if pros or cons:
                    for o in pros: st.success(o.get("argument","")[:300])
                    for o in cons: st.error(o.get("argument","")[:300])
                else:
                    for o in iopinion:
                        st.markdown(f"**{o.get('media','')}**")
                        st.info(o.get("stance","")[:200])

            if st.button("🥊 이 주제로 토론", key="inline_debate", use_container_width=True):
                st.session_state.debate_topic = ititle
                st.session_state.page = "debate"; st.rerun()

        # ── 카드 목록 모드 ───────────────────────────────────────────────────
        else:
            st.markdown("### 📋 관련 카드")
            all_cards = get_all_cards()
            kw        = st.session_state.selected_keyword
            show_cards = []
            if st.session_state.recommended_cards:
                rec_ids    = {r.get("card_id") for r in st.session_state.recommended_cards}
                show_cards = [c for c in all_cards if c["id"] in rec_ids]
            if not show_cards and kw:
                show_cards = filter_cards_by_keyword(all_cards, kw)[:5]
            if not show_cards:
                show_cards = all_cards[:5]

            for card in show_cards[:5]:
                try:
                    tabs  = load_card_tabs(engine, card["id"])
                    summ  = json.loads(tabs.get("SUMMARY","{}"))
                    title = summ.get("title", f"카드 #{card['id']}")
                    pts   = summ.get("summary_points",[])
                    icon  = TYPE_ICON.get(card.get("card_type",""),"")
                    st.markdown(f"{icon} **{title}**")
                    if pts: st.caption(pts[0][:60])
                    ca, cb = st.columns(2)
                    with ca:
                        if st.button("상세 보기", key=f"chat_d_{card['id']}", use_container_width=True):
                            st.session_state.chat_inline_card_id = card["id"]
                    with cb:
                        if st.button("토론", key=f"chat_t_{card['id']}", use_container_width=True):
                            st.session_state.debate_topic = title
                            st.session_state.page = "debate"
                    st.markdown("---")
                except: pass

# ════════════ 카드 상세 ════════════
elif st.session_state.page == "card":
    card_id = st.session_state.active_card_id
    if not card_id:
        st.session_state.page = "home"; st.rerun()

    tabs     = get_card_tabs(card_id)
    summ     = tabs.get("SUMMARY", {})
    if isinstance(summ, str):
        try: summ = json.loads(summ)
        except: summ = {}

    title   = summ.get("title", f"카드 #{card_id}")
    cat     = summ.get("category","")
    pts     = summ.get("summary_points",[])
    youth   = summ.get("youth_connection","")
    dq      = summ.get("discussion_question","")
    details = summ.get("policy_details",{})

    all_cards  = get_all_cards()
    card_info  = next((c for c in all_cards if c["id"] == card_id), {})
    tl         = TYPE_LABEL.get(card_info.get("card_type",""),"")

    st.title(title)
    st.caption(f"{tl}  |  `{cat}`")
    st.markdown("---")

    tab1, tab2, tab3 = st.tabs(["📝 요약", "🔍 본문", "⚖️ 찬반 의견"])

    with tab1:
        if pts:
            st.markdown("**핵심 요약**")
            for p in pts: st.markdown(f"- {p}")
        if youth:
            st.markdown("**청년과의 연관성**")
            st.info(youth)
        if details and isinstance(details, dict):
            st.markdown("**정책 상세**")
            dc = st.columns(2)
            items = [(k,v) for k,v in details.items() if v and v != "확인 필요"]
            for i,(k,v) in enumerate(items):
                with dc[i%2]: st.markdown(f"**{k}**: {v}")

    with tab2:
        core = tabs.get("CORE","")
        if isinstance(core, str) and core:
            st.markdown(core)
        else:
            st.info("본문 내용이 없습니다.")
        if dq:
            st.markdown("---")
            st.markdown(f"💬 **토론 질문**: {dq}")

    with tab3:
        opinion = tabs.get("OPINION",[])
        if isinstance(opinion, str):
            try: opinion = json.loads(opinion)
            except: opinion = []
        if opinion:
            pros = [o for o in opinion if o.get("stance") == "찬성"]
            cons = [o for o in opinion if o.get("stance") == "반대"]
            if pros or cons:
                cp, cc = st.columns(2)
                with cp:
                    st.markdown("### 👍 찬성")
                    for o in pros: st.success(o.get("argument",""))
                with cc:
                    st.markdown("### 👎 반대")
                    for o in cons: st.error(o.get("argument",""))
            else:
                for o in opinion:
                    st.markdown(f"**{o.get('media','')}**")
                    st.info(o.get("stance",""))
        else:
            st.info("의견 데이터가 없습니다.")

    st.markdown("---")
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("💬 챗봇에서 질문", use_container_width=True):
            # 항상 새 세션 생성
            st.session_state.session_id = chat_pipeline.create_session(user_id=1, title=title)
            # 해당 카드 열린 상태로 설정
            st.session_state.active_card_id = card_id
            st.session_state.chat_inline_card_id = card_id
            # AI 첫 메시지
            st.session_state.chat_messages = [{
                "role": "assistant",
                "content": f"📋 **{title}** 카드를 열어드렸어요!\n\n이 정책에 대해 궁금한 것을 무엇이든 물어보세요.",
            }]
            st.session_state.page = "chat"; st.rerun()
    with b2:
        if st.button("🥊 이 주제로 토론", use_container_width=True):
            st.session_state.debate_topic = title
            st.session_state.page = "debate"; st.rerun()
    with b3:
        if st.button("🏠 홈으로", use_container_width=True):
            st.session_state.page = "home"; st.rerun()

# ════════════ 토론장 ════════════
elif st.session_state.page == "debate":
    st.title("🥊 POLICITY 토론장")

    # 설정 화면
    if st.session_state.debate_id is None:
        st.markdown("### 토론 설정")
        all_cards   = get_all_cards()
        card_titles, card_id_map = [], {}
        for c in all_cards:
            try:
                t = get_card_title(c["id"])
                card_titles.append(t)
                card_id_map[t] = c["id"]
            except: pass

        default = st.session_state.debate_topic or (card_titles[0] if card_titles else "")
        def_idx = card_titles.index(default) if default in card_titles else 0

        topic = st.selectbox("토론 주제", options=card_titles or ["청년 기본소득"], index=def_idx)
        st.session_state.debate_topic = topic

        mode = st.radio("토론 방식", ["🤖 AI vs AI (참관)", "🧑 AI vs 나 (참여)"], horizontal=True)

        if "AI vs 나" in mode:
            st.session_state.debate_mode = "ai_vs_user"
            cs, cd = st.columns(2)
            with cs:
                stance = st.radio("내 입장", ["찬성","반대"], horizontal=True)
                st.session_state.debate_stance = "pro" if stance == "찬성" else "con"
            with cd:
                st.session_state.debate_diff = st.select_slider("AI 난이도", ["easy","hard"])
        else:
            st.session_state.debate_mode = "ai_vs_ai"

        if st.button("🚀 토론 시작", use_container_width=True):
            sel_id      = card_id_map.get(topic)
            policy_card = {"title": topic}
            if sel_id:
                ct = get_card_tabs(sel_id)
                s  = ct.get("SUMMARY",{})
                if isinstance(s, dict):
                    policy_card["summary_points"] = s.get("summary_points",[])
                core = ct.get("CORE","")
                policy_card["CORE"] = core if isinstance(core, str) else ""

            with st.spinner("토론 세션 생성 중..."):
                if st.session_state.debate_mode == "ai_vs_ai":
                    d_id, pro_id, con_id = debate_pipeline.create_ai_vs_ai(topic)
                    st.session_state.debate_id     = d_id
                    st.session_state.debate_pro_id = pro_id
                    st.session_state.debate_con_id = con_id
                    debate_pipeline.run_position_stage(d_id, policy_card, pro_id, con_id)
                else:
                    d_id, u_pid, ai_pid = debate_pipeline.create_ai_vs_user(
                        user_id=1, selected_policy=topic,
                        user_stance=st.session_state.debate_stance,
                        difficulty=st.session_state.debate_diff,
                    )
                    st.session_state.debate_id       = d_id
                    st.session_state.debate_user_pid = u_pid
                    st.session_state.debate_ai_pid   = ai_pid
                    st.session_state.debate_turn     = 1
                    debate_pipeline.generate_ai_position(d_id, policy_card, ai_pid, st.session_state.debate_diff)
                st.session_state.debate_history = debate_pipeline.get_history(d_id)
            st.rerun()

    # AI vs AI 진행
    elif st.session_state.debate_mode == "ai_vs_ai":
        st.markdown(f"### 🤖 AI vs AI | **{st.session_state.debate_topic}**")
        for msg in st.session_state.debate_history:
            role = "assistant" if "찬성" in msg.get("role_name","") else "user"
            with st.chat_message(role):
                st.caption(f"{msg.get('role_name','')} | {msg.get('message_type','')}")
                st.markdown(msg.get("content",""))

        if not st.session_state.debate_finished:
            c1, c2, c3 = st.columns(3)
            pc = {"title": st.session_state.debate_topic}
            with c1:
                if st.button("▶ 찬성 라운드 (3턴)", use_container_width=True):
                    with st.spinner("찬성 측 라운드 진행 중..."):
                        debate_pipeline.run_base_turns(
                            st.session_state.debate_id, pc, "pro",
                            st.session_state.debate_pro_id, st.session_state.debate_con_id)
                    st.session_state.debate_history = debate_pipeline.get_history(st.session_state.debate_id)
                    st.rerun()
            with c2:
                if st.button("▶ 반대 라운드 (3턴)", use_container_width=True):
                    with st.spinner("반대 측 라운드 진행 중..."):
                        debate_pipeline.run_base_turns(
                            st.session_state.debate_id, pc, "con",
                            st.session_state.debate_con_id, st.session_state.debate_pro_id)
                    st.session_state.debate_history = debate_pipeline.get_history(st.session_state.debate_id)
                    st.rerun()
            with c3:
                if st.button("🏁 주장 다지기", use_container_width=True):
                    with st.spinner("요약 생성 중..."):
                        st.session_state.debate_summary  = debate_pipeline.generate_summary(st.session_state.debate_id, pc)
                        st.session_state.debate_finished = True
                    st.rerun()

        if st.session_state.debate_summary:
            s = st.session_state.debate_summary
            st.markdown("---"); st.markdown("### 📊 주장 다지기")
            if s.get("overview"): st.info(s["overview"])
            cp2, cc2 = st.columns(2)
            with cp2:
                st.markdown("**✅ 찬성 핵심**")
                for a in s.get("pro_summary",{}).get("key_arguments",[]): st.markdown(f"- {a}")
            with cc2:
                st.markdown("**❌ 반대 핵심**")
                for a in s.get("con_summary",{}).get("key_arguments",[]): st.markdown(f"- {a}")

        st.markdown("---")
        if st.button("🔄 새 토론", use_container_width=True):
            reset_debate(); st.rerun()

    # AI vs User 진행
    elif st.session_state.debate_mode == "ai_vs_user":
        sl = "찬성" if st.session_state.debate_stance == "pro" else "반대"
        st.markdown(f"### 🧑 AI vs 나 | **{st.session_state.debate_topic}** | 내 입장: {sl}")

        for msg in st.session_state.debate_history:
            is_user = msg.get("participant_type") == "USER"
            with st.chat_message("user" if is_user else "assistant"):
                st.caption(f"{msg.get('role_name','')} | {msg.get('message_type','')}")
                st.markdown(msg.get("content",""))

        if not st.session_state.debate_finished:
            if uc := st.chat_input("주장을 입력하세요..."):
                pc = {"title": st.session_state.debate_topic}
                with st.spinner("AI 반론 생성 중..."):
                    debate_pipeline.process_user_claim(
                        debate_id=st.session_state.debate_id,
                        user_participant_id=st.session_state.debate_user_pid,
                        ai_participant_id=st.session_state.debate_ai_pid,
                        user_claim=uc,
                        policy_card=pc,
                        turn_num=st.session_state.debate_turn,
                        difficulty=st.session_state.debate_diff,
                    )
                st.session_state.debate_turn += 1
                st.session_state.debate_history = debate_pipeline.get_history(st.session_state.debate_id)
                st.rerun()

            if st.button("🏁 주장 다지기", use_container_width=True):
                with st.spinner("요약 생성 중..."):
                    st.session_state.debate_summary  = debate_pipeline.generate_summary(
                        st.session_state.debate_id, {"title": st.session_state.debate_topic})
                    st.session_state.debate_finished = True
                st.rerun()

        if st.session_state.debate_summary:
            s = st.session_state.debate_summary
            st.markdown("---"); st.markdown("### 📊 주장 다지기")
            if s.get("overview"): st.info(s["overview"])
            fb = s.get("user_feedback",{})
            if fb.get("strong_points"):
                st.markdown("**💪 잘한 부분**")
                for p in fb["strong_points"]: st.success(p)
            if fb.get("weak_points"):
                st.markdown("**📌 보완할 부분**")
                for p in fb["weak_points"]: st.warning(p)

        st.markdown("---")
        if st.button("🔄 새 토론", use_container_width=True):
            reset_debate(); st.rerun()