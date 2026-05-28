"""
pipeline/utils.py
PoliTalk 파이프라인 공통 유틸리티
- 청킹 전략 3종
- 배치 임베딩
- ChromaDB 인덱싱 & 검색
- LLM 호출 헬퍼
- 편향/금칙어 검사
"""
import re
import time
import json
from typing import List, Dict, Tuple, Optional, Callable

import numpy as np
import chromadb
from openai import OpenAI

from config import (
    FIXED_CHUNK_SIZE, FIXED_CHUNK_OVERLAP,
    SENTENCE_MAX_CHARS, SEMANTIC_THRESHOLD, SEMANTIC_MIN_CHARS,
    EMBEDDING_MODELS, LLM_MODEL, LLM_MODEL_FAST,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. 청킹 전략 3종
# ══════════════════════════════════════════════════════════════════════════════

def chunk_fixed(
    text: str,
    size: int = FIXED_CHUNK_SIZE,
    overlap: int = FIXED_CHUNK_OVERLAP,
) -> List[str]:
    """
    [전략 1] 고정 길이 청킹 (overlap 포함)
    - 가장 단순하고 재현 가능
    - 문장 경계를 무시할 수 있음
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _split_sentences_ko(text: str) -> List[str]:
    """한국어 문장 분리 (regex 기반)"""
    # 마침표/물음표/느낌표 뒤 공백 or 줄바꿈, 혹은 문장 종결 어미 뒤 공백
    pattern = r'(?<=[.!?])\s+|(?<=[다요까야죠네군])\s+'
    parts = re.split(pattern, text)
    return [p.strip() for p in parts if p.strip()]


def chunk_by_sentence(
    text: str,
    max_chars: int = SENTENCE_MAX_CHARS,
) -> List[str]:
    """
    [전략 2] 문장 단위 청킹
    - 문장 경계를 유지하면서 max_chars 이내로 묶음
    - 한국어 자연스러운 단위 유지
    """
    sentences = _split_sentences_ko(text)
    chunks, current = [], ""
    for sent in sentences:
        if len(current) + len(sent) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = sent
        else:
            current += (" " if current else "") + sent
    if current.strip():
        chunks.append(current.strip())
    return chunks if chunks else [text]


def chunk_semantic(
    text: str,
    openai_client: OpenAI,
    model: str = "text-embedding-3-small",
    threshold: float = SEMANTIC_THRESHOLD,
    min_chars: int = SEMANTIC_MIN_CHARS,
) -> List[str]:
    """
    [전략 3] 시맨틱 청킹
    - 문장을 임베딩 후 연속 문장 간 코사인 유사도가 임계값 이하인 지점에서 분할
    - 의미 단위를 보존하지만 API 비용 발생
    """
    sentences = _split_sentences_ko(text)
    if len(sentences) <= 2:
        return [text]

    # 문장 임베딩 (배치)
    embs = embed_texts(sentences, model, openai_client)

    chunks, current = [], sentences[0]
    for i in range(1, len(sentences)):
        sim = _cosine_sim(embs[i - 1], embs[i])
        if sim < threshold and len(current) >= min_chars:
            chunks.append(current.strip())
            current = sentences[i]
        else:
            current += " " + sentences[i]
    if current.strip():
        chunks.append(current.strip())
    return chunks if chunks else [text]


def get_chunker(strategy: str, openai_client: Optional[OpenAI] = None) -> Callable:
    """전략 이름 → 청킹 함수 반환"""
    if strategy == "fixed":
        return chunk_fixed
    elif strategy == "sentence":
        return chunk_by_sentence
    elif strategy == "semantic":
        if openai_client is None:
            raise ValueError("semantic 청킹은 openai_client 필요")
        return lambda text: chunk_semantic(text, openai_client)
    else:
        raise ValueError(f"알 수 없는 청킹 전략: {strategy}")


# ══════════════════════════════════════════════════════════════════════════════
# 2. 임베딩 (배치, rate limit 고려)
# ══════════════════════════════════════════════════════════════════════════════

def embed_texts(
    texts: List[str],
    model: str,
    client: OpenAI,
    batch_size: int = 100,
    sleep_sec: float = 0.1,
) -> List[List[float]]:
    """
    텍스트 배치 임베딩
    - OpenAI rate limit 고려해 배치 간 sleep
    - 빈 문자열은 공백 하나로 대체 (API 오류 방지)
    """
    safe_texts = [t if t.strip() else " " for t in texts]
    all_embeddings: List[List[float]] = []

    for i in range(0, len(safe_texts), batch_size):
        batch = safe_texts[i: i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        all_embeddings.extend([e.embedding for e in resp.data])
        if i + batch_size < len(safe_texts):
            time.sleep(sleep_sec)

    return all_embeddings


def _cosine_sim(a: List[float], b: List[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / (denom + 1e-8))


# ══════════════════════════════════════════════════════════════════════════════
# 3. ChromaDB 인덱싱 & 검색
# ══════════════════════════════════════════════════════════════════════════════

def build_collection(
    chroma_client: chromadb.ClientAPI,
    openai_client: OpenAI,
    collection_name: str,
    articles: List[Dict],
    strategy: str,
    model_key: str,
    batch_size: int = 50,
) -> chromadb.Collection:
    """
    청킹 전략 + 임베딩 모델 조합으로 ChromaDB 컬렉션 빌드
    이미 존재하면 삭제 후 재생성 (실험용)
    """
    model_name = EMBEDDING_MODELS[model_key]
    chunker = get_chunker(strategy, openai_client)

    # 기존 컬렉션 초기화
    try:
        chroma_client.delete_collection(collection_name)
    except Exception:
        pass
    collection = chroma_client.create_collection(collection_name)

    all_docs, all_ids, all_metas = [], [], []
    for art in articles:
        text = art.get("content", "")
        if not text:
            continue
        chunks = chunker(text)
        url_key = art.get("url", "")[:60]
        for j, chunk in enumerate(chunks):
            doc_id = f"{url_key}_{j}"
            # ChromaDB id 중복 방지 (같은 URL 청크 n개)
            if doc_id in all_ids:
                doc_id = f"{doc_id}_{len(all_ids)}"
            all_docs.append(chunk)
            all_ids.append(doc_id)
            all_metas.append({
                "title":        art.get("title", ""),
                "publisher":    art.get("publisher", ""),
                "url":          art.get("url", ""),
                "published_at": str(art.get("published_at", "")),
                "chunk_idx":    j,
                "strategy":     strategy,
                "model_key":    model_key,
            })

    # 배치 임베딩 & 저장
    from tqdm.std import tqdm as _tqdm
    for i in _tqdm(range(0, len(all_docs), batch_size), desc=f"  [{collection_name}] 인덱싱"):
        batch_docs  = all_docs[i: i + batch_size]
        batch_embs  = embed_texts(batch_docs, model_name, openai_client)
        collection.add(
            documents=batch_docs,
            embeddings=batch_embs,
            ids=all_ids[i: i + batch_size],
            metadatas=all_metas[i: i + batch_size],
        )

    print(f"  ✓ {collection_name}: {len(all_docs)}개 청크")
    return collection


def retrieve(
    query: str,
    collection: chromadb.Collection,
    model_name: str,
    openai_client: OpenAI,
    top_k: int = 5,
) -> List[Dict]:
    """
    쿼리를 임베딩 후 컬렉션에서 유사 청크 검색
    반환: [{"content", "metadata", "score"}, ...]
    """
    q_emb = embed_texts([query], model_name, openai_client)[0]
    results = collection.query(
        query_embeddings=[q_emb],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "content":  results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "score":    1.0 - results["distances"][0][i],  # 거리 → 유사도
        }
        for i in range(len(results["documents"][0]))
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 4. LLM 호출 헬퍼
# ══════════════════════════════════════════════════════════════════════════════

def llm(
    messages: List[Dict],
    client: OpenAI,
    model: str = LLM_MODEL,
    max_tokens: int = 1000,
    json_mode: bool = False,
) -> str:
    """OpenAI Chat Completion 래퍼"""
    kwargs: Dict = dict(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


# ══════════════════════════════════════════════════════════════════════════════
# 5. 편향 검사 (REQ-CARD-004.4, REQ-CARD-005.4)
# ══════════════════════════════════════════════════════════════════════════════

BIAS_PROMPT = """\
다음 콘텐츠에 특정 정당·후보·정치인을 지지하거나 비방하는 표현이 있는지 검토하세요.

[콘텐츠]
{content}

JSON 응답:
{{
  "has_bias": true 또는 false,
  "bias_examples": ["편향 표현 예시 1", "..."],
  "corrected_content": "수정된 내용 (편향 없으면 원문 그대로)"
}}"""


def check_and_fix_bias(
    card: Dict,
    client: OpenAI,
) -> Tuple[Dict, bool, List[str]]:
    """
    카드 JSON을 받아 편향 검사 후 수정된 카드 반환
    Returns: (card_dict, has_bias, bias_examples)
    """
    content_str = json.dumps(card, ensure_ascii=False)[:3000]
    raw = llm(
        [{"role": "user", "content": BIAS_PROMPT.format(content=content_str)}],
        client,
        model=LLM_MODEL_FAST,
        max_tokens=1500,
        json_mode=True,
    )
    result = json.loads(raw)
    has_bias = result.get("has_bias", False)
    examples = result.get("bias_examples", [])

    if has_bias:
        corrected = result.get("corrected_content", "")
        try:
            card = json.loads(corrected)
        except (json.JSONDecodeError, TypeError):
            pass  # 파싱 실패 시 원본 유지

    return card, has_bias, examples


# ══════════════════════════════════════════════════════════════════════════════
# 6. 금칙어 검사 (REQ-CHAT-001.3, REQ-DEBATE-002.1-8)
# ══════════════════════════════════════════════════════════════════════════════

PROFANITY_PROMPT = """\
다음 사용자 입력에 아래 금칙어 범주가 포함되어 있는지 확인하세요.

금칙어 범주:
- 성적 비하 표현 및 비속어
- 특정 정치인·정당에 대한 비하 표현
- 특정 인종·성별·종교에 대한 혐오 표현

입력: {user_input}

JSON 응답: {{"contains_profanity": true 또는 false, "reason": "이유"}}"""


def check_profanity(user_input: str, client: OpenAI) -> Tuple[bool, str]:
    """
    금칙어 포함 여부 반환
    Returns: (contains_profanity, reason)
    """
    raw = llm(
        [{"role": "user", "content": PROFANITY_PROMPT.format(user_input=user_input)}],
        client,
        model=LLM_MODEL_FAST,
        max_tokens=80,
        json_mode=True,
    )
    result = json.loads(raw)
    return result.get("contains_profanity", False), result.get("reason", "")


# ══════════════════════════════════════════════════════════════════════════════
# 7. 청년 관련성 필터 (REQ-CARD-001.3, REQ-CARD-002.3)
# ══════════════════════════════════════════════════════════════════════════════

YOUTH_FILTER_PROMPT = """\
다음 기사/정책이 대한민국 청년(20~30대)의 삶과 직접 연관이 있는지 판단하세요.

연관 기준 (하나라도 해당되면 연관):
- 일자리·취업·청년 고용
- 주거·전세·청년 주택·월세
- 교육·장학금·학자금
- 복지·청년 지원·생활 지원
- 금융·청년 대출·신용
- 생활 물가·청년 창업

제목: {title}
내용 요약: {summary}

JSON 응답: {{"relevant": true 또는 false, "reason": "판단 이유 1-2문장", "category": "일자리|교육|주거|금융|생활복지|문화|해당없음"}}"""


def is_youth_relevant(
    article: Dict,
    client: OpenAI,
) -> Tuple[bool, str, str]:
    """
    Returns: (relevant, reason, category)
    """
    raw = llm(
        [{
            "role": "user",
            "content": YOUTH_FILTER_PROMPT.format(
                title=article.get("title", ""),
                summary=article.get("content", "")[:300],
            ),
        }],
        client,
        model=LLM_MODEL_FAST,
        max_tokens=120,
        json_mode=True,
    )
    result = json.loads(raw)
    return (
        result.get("relevant", False),
        result.get("reason", ""),
        result.get("category", "해당없음"),
    )


# ══════════════════════════════════════════════════════════════════════════════
# 8. 기사 클러스터링 (토픽별 묶기)
# ══════════════════════════════════════════════════════════════════════════════

def cluster_articles(
    articles: List[Dict],
    model_name: str,
    openai_client: OpenAI,
    sim_threshold: float = 0.70,
) -> Dict[int, List[Dict]]:
    """
    그리디 코사인 유사도 클러스터링
    - 제목 + 본문 앞 200자 임베딩 후 sim_threshold 이상인 기사끼리 묶음
    Returns: {cluster_id: [article, ...]}
    """
    if len(articles) <= 1:
        return {0: articles}

    texts = [
        f"{a.get('title', '')} {a.get('content', '')[:200]}"
        for a in articles
    ]
    embs = embed_texts(texts, model_name, openai_client)
    emb_matrix = np.array(embs)
    norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    normed = emb_matrix / (norms + 1e-8)
    sim_matrix = normed @ normed.T

    clusters: Dict[int, List[Dict]] = {}
    assigned = [False] * len(articles)
    cluster_id = 0

    for i in range(len(articles)):
        if assigned[i]:
            continue
        cluster = [articles[i]]
        assigned[i] = True
        for j in range(i + 1, len(articles)):
            if not assigned[j] and sim_matrix[i][j] > sim_threshold:
                cluster.append(articles[j])
                assigned[j] = True
        clusters[cluster_id] = cluster
        cluster_id += 1

    return clusters
