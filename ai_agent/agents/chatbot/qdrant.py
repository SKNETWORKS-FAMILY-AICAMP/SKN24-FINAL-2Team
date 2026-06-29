"""
agents/chatbot/qdrant.py
Qdrant helpers: embedding, card fetch, semantic search, recommendations.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from qdrant_client import models
from qdrant_client.http.models import FieldCondition, Filter, MatchValue, MatchAny
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

EMBED_MODEL      = os.getenv("EMBED_MODEL", "jhgan/ko-sroberta-multitask")
RERANK_MODEL     = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")
SCORE_THRESHOLD  = float(os.getenv("SEARCH_SCORE_THRESHOLD", "0.76"))
TOP_K_RAG        = 5
TOP_K_RECOMMEND  = 3

# Fields to include in the card context sent to the LLM (#8)
_CARD_CONTEXT_FIELDS = {"title", "content", "card_type"}

logger.info("Loading embedding model: %s", EMBED_MODEL)
_model = SentenceTransformer(EMBED_MODEL)
logger.info("Embedding model loaded")

_reranker = None


def _get_reranker():
    """Lazy-load the cross-encoder reranker (#9)."""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            logger.info("Loading reranker model: %s", RERANK_MODEL)
            _reranker = CrossEncoder(RERANK_MODEL)
            logger.info("Reranker model loaded")
        except Exception as e:
            logger.warning("Reranker model failed to load (%s) — reranking disabled.", e)
            _reranker = False  # sentinel: do not retry
    return _reranker if _reranker is not False else None


def embed(text: str) -> List[float]:
    return _model.encode(text).tolist()


# ── Card context builder (#8: whitelist meaningful fields only) ───────────────

def _build_card_context(payload: Dict) -> str:
    return "\n".join(
        f"{k}: {v}"
        for k, v in payload.items()
        if k in _CARD_CONTEXT_FIELDS and isinstance(v, str) and v.strip()
    )


# ── Structured card_data projectors (full MySQL row, not the thin Qdrant payload) ──
# Each call site gets only the slice of card content it needs, instead of one
# flat blob — cheap calls (clarifying/eligibility questions) stay cheap, and
# only the main answer-generation call pays for the full core_content/perspectives.

def build_eligibility_card_context(card_data: Dict) -> str:
    """Title + summary highlights — for clarifying questions, eligibility checks,
    missing-info questions. Cheap: skips core_content and perspectives."""
    summary = card_data.get("summary") or {}
    lines = [f"title: {card_data.get('title', '')}"]

    if card_data.get("intro"):
        lines.append(f"intro: {card_data['intro']}")

    if summary.get("category"):
        lines.append(f"category: {summary['category']}")

    points = summary.get("summary_points") or []
    if points:
        lines.append("summary_points: " + " / ".join(points))

    if summary.get("youth_connection"):
        lines.append(f"youth_connection: {summary['youth_connection']}")

    policy_details = summary.get("policy_details") or {}
    if policy_details:
        detail_lines = []
        if policy_details.get("target"):
            detail_lines.append(f"target: {policy_details['target']}")
        if policy_details.get("content"):
            detail_lines.append(f"content: {policy_details['content']}")
        if policy_details.get("period"):
            detail_lines.append(f"period: {policy_details['period']}")
        if policy_details.get("method"):
            detail_lines.append(f"method: {policy_details['method']}")
        if policy_details.get("contact"):
            detail_lines.append(f"contact: {policy_details['contact']}")
        if detail_lines:
            lines.append("policy_details: " + " / ".join(detail_lines))

    return "\n".join(line for line in lines if line.strip())



def build_full_card_context(card_data: Dict) -> str:
    """Title + core_content + perspectives + debate_topic — for the main
    answer-generation call, where depth actually matters to the user."""
    lines = [f"title: {card_data.get('title', '')}"]
    if card_data.get("core_content"):
        lines.append(f"core_content: {card_data['core_content']}")
    perspectives = card_data.get("perspectives") or []
    if perspectives:
        persp_lines = "\n".join(
            f"- [{p.get('media', '')}] {p.get('stance', '')}" for p in perspectives
        )
        lines.append(f"perspectives:\n{persp_lines}")
    return "\n".join(line for line in lines if line.strip())


def build_recommend_reason_context(card_data: Dict) -> str:
    """Summary + debate_topic — mid slice for recommend-reason rationale,
    no need for the full core_content."""
    summary = card_data.get("summary") or {}
    lines = [f"title: {card_data.get('title', '')}"]
    points = summary.get("summary_points") or []
    if points:
        lines.append("summary_points: " + " / ".join(points))
    if card_data.get("debate_topic"):
        lines.append(f"debate_topic: {card_data['debate_topic']}")
    return "\n".join(line for line in lines if line.strip())


# ── Card fetch ────────────────────────────────────────────────────────────────

def fetch_card_by_id(card_id: str, collection_name: str = "policity_cards", qdrant_client=None) -> Dict:
    logger.info("[fetch_card_by_id] card_id='%s' collection='%s'", card_id, collection_name)
    results = qdrant_client.scroll(
        collection_name=collection_name,
        scroll_filter=Filter(must=[FieldCondition(key="card_id", match=MatchValue(value=int(card_id)))]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    points = results[0]
    return points[0].payload if points else {}


# ── Recommendations ───────────────────────────────────────────────────────────

def new_chat_card_recommendations(
    user_profile_text: str,
    qdrant_client: Optional[Any] = None,
    top_k: int = 3,
    seen_card_ids: Optional[list] = None,
) -> Dict[str, Any]:
    seen_ids = {str(cid) for cid in (seen_card_ids or [])}
    query_vector = embed(user_profile_text)

    hits = qdrant_client.search(
        collection_name="policity_cards",
        query_vector=query_vector,
        limit=top_k + len(seen_ids),
        with_payload=True,
    )
    hits = [h for h in hits if str(h.payload.get("card_id", h.id)) not in seen_ids][:top_k]

    # Fallback: pad to top_k using best-scoring cards (excluding seen cards)
    if len(hits) < top_k:
        already_chosen_ids = {str(h.payload.get("card_id", h.id)) for h in hits} | seen_ids
        fallback_hits = qdrant_client.search(
            collection_name="policity_cards",
            query_vector=query_vector,
            limit=max(top_k * 10, 50),
            with_payload=True,
        )
        for h in fallback_hits:
            if len(hits) >= top_k:
                break
            if str(h.payload.get("card_id", h.id)) not in already_chosen_ids:
                hits.append(h)
                already_chosen_ids.add(str(h.payload.get("card_id", h.id)))

    return {
        "recommendations": [
            {
                "card_id":  str(h.payload.get("card_id", h.id)),
                "title":    h.payload.get("title"),
                "content":  (h.payload.get("content") or "")[:200],  # snippet for LLM (#7)
                "score":    h.score,
            }
            for h in hits
        ]
    }

def chat_card_recommendations(
    user_profile_text: str,
    seen_card_ids: Optional[list[int]] = None,
    qdrant_client: Optional[Any] = None,
    top_k: int = 3,
) -> Dict[str, Any]:
    query_vector = embed(user_profile_text)

    # Fetch extra candidates so reranker has room to work (#9)
    candidates_k = top_k * 2

    search_filter = (
        Filter(
            must_not=[
                FieldCondition(key="card_id", match=MatchAny(any=seen_card_ids))
            ]
        )
        if seen_card_ids
        else None
    )
    hits = qdrant_client.search(
        collection_name="policity_cards",
        query_vector=query_vector,
        query_filter=search_filter,
        limit=candidates_k,
        with_payload=True,
    )

    # Rerank candidates using cross-encoder (#9)
    reranker = _get_reranker()
    if reranker and hits:
        pairs = [
            (user_profile_text, hit.payload.get("content", hit.payload.get("title", "")))
            for hit in hits
        ]
        scores = reranker.predict(pairs)
        hits = [h for _, h in sorted(zip(scores, hits), key=lambda x: x[0], reverse=True)]

    hits = hits[:top_k]

    # Fallback: pad to top_k using best-scoring cards (seen cards included)
    if len(hits) < top_k:
        already_chosen_ids = {str(h.payload.get("card_id", h.id)) for h in hits}
        fallback_hits = qdrant_client.search(
            collection_name="policity_cards",
            query_vector=query_vector,
            limit=max(top_k * 10, 50),
            with_payload=True,
        )
        if reranker and fallback_hits:
            pairs = [
                (user_profile_text, h.payload.get("content", h.payload.get("title", "")))
                for h in fallback_hits
            ]
            scores = reranker.predict(pairs)
            fallback_hits = [h for _, h in sorted(zip(scores, fallback_hits), key=lambda x: x[0], reverse=True)]
        for h in fallback_hits:
            if len(hits) >= top_k:
                break
            if str(h.payload.get("card_id", h.id)) not in already_chosen_ids:
                hits.append(h)
                already_chosen_ids.add(str(h.payload.get("card_id", h.id)))

    return {
        "recommendations": [
            {
                "card_id":  str(h.payload.get("card_id", h.id)),
                "title":    h.payload.get("title"),
                "content":  (h.payload.get("content") or "")[:200],  # snippet for LLM (#7)
                "score":    h.score,
            }
            for h in hits
        ]
    }
# ── Policy doc search ─────────────────────────────────────────────────────────

def _fetch_adjacent_chunks(
    data_id: int,
    chunk_index: int,
    chunk_total: int,
    qdrant_client,
) -> List[Tuple[int, str]]:
    """Return (chunk_index, chunk_text) for the chunks immediately before/after the given one (#6)."""
    indices = []
    if chunk_index > 0:
        indices.append(chunk_index - 1)
    if chunk_index + 1 < chunk_total:
        indices.append(chunk_index + 1)

    result = []
    for idx in indices:
        hits, _ = qdrant_client.scroll(
            collection_name="policity_docs",
            scroll_filter=Filter(must=[
                FieldCondition(key="data_id",     match=MatchValue(value=data_id)),
                FieldCondition(key="chunk_index", match=MatchValue(value=idx)),
            ]),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if hits:
            text = hits[0].payload.get("chunk_text", "").strip()
            if text:
                result.append((idx, text))
    return result


async def search_policy_docs(
    user_query: str,
    card_title: str = "",
    category_id: Optional[int] = None,
    top_k: int = 5,
    qdrant_client=None,
) -> str:
    # #1: Anchor embedding to the card topic so "tell me more" queries hit the right domain
    embed_query = f"{card_title}: {user_query}" if card_title else user_query
    query_vec = embed(embed_query)

    # #5: Filter by category when available
    search_filter = None
    if category_id is not None:
        search_filter = Filter(must=[
            FieldCondition(key="category_id", match=MatchValue(value=category_id))
        ])

    try:
        # Fetch extra candidates for dedup + reranking (#2, #9)
        raw_hits = qdrant_client.search(
            collection_name="policity_docs",
            query_vector=query_vec,
            query_filter=search_filter,
            score_threshold=SCORE_THRESHOLD,   # #3: drop irrelevant results
            limit=top_k * 3,
            with_payload=True,
        )

        logger.info(
            "[search_policy_docs] %d raw hits (threshold=%.2f) for query: '%s'",
            len(raw_hits), SCORE_THRESHOLD, user_query,
        )

        if not raw_hits:
            return ""

        # #2: Deduplicate — keep only the top-scoring chunk per article (data_id)
        seen_data_ids: set[int] = set()
        deduped = []
        for h in raw_hits:
            did = h.payload.get("data_id")
            if did not in seen_data_ids:
                seen_data_ids.add(did)
                deduped.append(h)
            if len(deduped) >= top_k * 2:
                break

        # #6: Expand each hit with adjacent chunks for richer context
        expanded: List[Dict] = []
        for h in deduped:
            payload      = h.payload or {}
            data_id      = payload.get("data_id")
            chunk_index  = payload.get("chunk_index", 0)
            chunk_total  = payload.get("chunk_total", 1)
            center_text  = payload.get("chunk_text", "").strip()

            if data_id is not None and chunk_total > 1:
                adjacent = _fetch_adjacent_chunks(data_id, chunk_index, chunk_total, qdrant_client)
                # Merge adjacent chunks in order around the center
                all_chunks = sorted(
                    [(chunk_index, center_text)] + adjacent,
                    key=lambda x: x[0],
                )
                merged_text = " ".join(t for _, t in all_chunks)
            else:
                merged_text = center_text

            expanded.append({
                "score":      h.score,
                "title":      payload.get("data_title", "").strip(),
                "text":       merged_text,
                "source_url": next(
                    (payload[k] for k in ("source_url", "url", "link", "href", "출처", "source")
                     if k in payload and payload[k]),
                    "",
                ),
            })

        # #9: Rerank expanded results with cross-encoder
        reranker = _get_reranker()
        if reranker and expanded:
            pairs  = [(user_query, doc["text"]) for doc in expanded]
            scores = reranker.predict(pairs)
            expanded = [doc for _, doc in sorted(zip(scores, expanded), key=lambda x: x[0], reverse=True)]

        # Take final top_k
        final = expanded[:top_k]

        lines = []
        for i, doc in enumerate(final, 1):
            parts = [f"[문서 {i}] score={doc['score']:.4f}"]
            if doc["title"]:
                parts.append(f"제목: {doc['title']}")
            if doc["text"]:
                parts.append(f"내용: {doc['text']}")
            if doc["source_url"]:
                parts.append(f"SOURCE_URL: {doc['source_url']}")
            line = "\n".join(parts)
            logger.info("[search_policy_docs] Hit %d: %s", i, line[:120])
            lines.append(line)

        return "\n\n".join(lines)

    except Exception as e:
        logger.warning("[search_policy_docs] Search failed, returning empty context. Error: %s", e)
        return ""
