"""
debate_agent/hate_vector.py
벡터 유사도 기반 혐오표현 탐지

흐름:
  init_hate_collection()  → Qdrant "hate_speech" 컬렉션에 예시 문장 임베딩 저장
  vector_hate_filter()    → 입력 문장 임베딩 후 유사도 검색 → threshold 이상이면 차단

임베딩: jhgan/ko-sroberta-multitask (768d)
임계값: 기본 0.70 (조정 가능)

카테고리별 예시 문장:
  political_slur  : 정치인 멸칭·비하 발언
  group_stigma    : 진영·지지층 낙인
  regional_slur   : 지역감정 조장
  threat          : 위협·배제·폭력
  context_hate    : 맥락 의존형 (수박·홍어 등을 정치 문맥에서 사용)
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

HATE_COLLECTION = "hate_speech"
EMBED_MODEL = "jhgan/ko-sroberta-multitask"
EMBED_DIM = 768

_st_model: SentenceTransformer | None = None


def _get_st_model() -> SentenceTransformer:
    global _st_model
    if _st_model is None:
        _st_model = SentenceTransformer(EMBED_MODEL)
    return _st_model

# ════════════════════════════════════════════════════════════════════════════
# 혐오표현 예시 문장 (카테고리별)
# ════════════════════════════════════════════════════════════════════════════

HATE_EXAMPLES: List[Dict] = [
    # ── 정치인 멸칭 ──────────────────────────────────────────────────────
    {"text": "찢재명은 나라를 망치는 범죄자야",           "category": "political_slur"},
    {"text": "굥이 대통령이라니 나라 꼴이 말이 아니다",   "category": "political_slur"},
    {"text": "닭그네 때문에 나라가 이 꼴 났지",           "category": "political_slur"},
    {"text": "문재앙이 경제를 다 망쳐놨어",               "category": "political_slur"},
    {"text": "쥐박이 시절이 더 나빴어",                   "category": "political_slur"},
    {"text": "놈현이 왜 영웅이냐고",                      "category": "political_slur"},
    {"text": "한가발은 정치 수준이 바닥이야",             "category": "political_slur"},
    {"text": "윤틀러가 독재를 하고 있어",                 "category": "political_slur"},
    {"text": "이 정치인은 완전 쥐명 스타일이네",          "category": "political_slur"},
    {"text": "굥 지지자들 제정신이냐",                    "category": "political_slur"},

    # ── 집단·진영 비하 ───────────────────────────────────────────────────
    {"text": "틀딱들이 나라를 망쳐놨어",                  "category": "group_stigma"},
    {"text": "빨갱이들은 대화가 안 돼",                   "category": "group_stigma"},
    {"text": "개딸들은 무조건 편 드는 맹목적 지지자야",   "category": "group_stigma"},
    {"text": "좌빨들이 언론을 장악했어",                  "category": "group_stigma"},
    {"text": "국짐 지지자들은 생각이 없어",               "category": "group_stigma"},
    {"text": "2찍들이 나라를 망쳤다",                     "category": "group_stigma"},
    {"text": "종북 세력이 청와대에 들어갔어",             "category": "group_stigma"},
    {"text": "대깨문들은 팩트도 안 봐",                   "category": "group_stigma"},
    {"text": "좌좀들이 유튜브를 도배하고 있어",           "category": "group_stigma"},
    {"text": "토착왜구 정당이 집권하고 있어",             "category": "group_stigma"},
    {"text": "민주당충들이 국회를 장악했어",              "category": "group_stigma"},

    # ── 지역 비하 ────────────────────────────────────────────────────────
    {"text": "홍어들은 어디서든 문제야",                  "category": "regional_slur"},
    {"text": "전라도 사람들은 믿으면 안 돼",              "category": "regional_slur"},
    {"text": "과메기들이 정치를 독점하고 있어",           "category": "regional_slur"},
    {"text": "경상도가 나라를 망쳤어",                    "category": "regional_slur"},
    {"text": "절라도 놈들은 다 빨갱이야",                 "category": "regional_slur"},
    {"text": "흉노 지역 출신이라 저런 거야",              "category": "regional_slur"},
    {"text": "호남 사람들은 정치적으로 편향돼 있어",      "category": "regional_slur"},
    {"text": "통구이 지역 출신들이 저러니",               "category": "regional_slur"},

    # ── 위협·배제·폭력 ───────────────────────────────────────────────────
    {"text": "저런 정치인은 척결해야 해",                 "category": "threat"},
    {"text": "빨갱이들 다 잡아다 격리해야 해",            "category": "threat"},
    {"text": "저 의원은 때려죽여야 마땅해",               "category": "threat"},
    {"text": "이 세력들을 박멸해야 나라가 산다",          "category": "threat"},
    {"text": "단두대에 보내야 할 놈들이야",               "category": "threat"},
    {"text": "저런 놈들은 씨를 말려야 해",                "category": "threat"},
    {"text": "탄핵 주도 세력 다 처단해야 해",             "category": "threat"},
    {"text": "저 정치인 국회에서 끌어내려야 해",          "category": "threat"},

    # ── 맥락 의존형 ──────────────────────────────────────────────────────
    {"text": "저 의원은 수박이야, 겉만 민주당이지",       "category": "context_hate"},
    {"text": "그분이 이 사건의 몸통이라는 건 다 알잖아",  "category": "context_hate"},
    {"text": "탄핵을 당장 해야 해, 무조건 구속 보내",     "category": "context_hate"},
    {"text": "호남 사람들은 무조건 민주당만 찍잖아",      "category": "context_hate"},
    {"text": "저 지역 출신 정치인은 다 부패했어",         "category": "context_hate"},
    {"text": "노인들은 보수 표만 찍어서 나라를 망쳐",     "category": "context_hate"},
    {"text": "젊은 세대는 다 좌파가 됐어",               "category": "context_hate"},
    {"text": "여성 정치인들이 젠더 이슈만 들고 나온다",   "category": "context_hate"},
]

# ════════════════════════════════════════════════════════════════════════════
# 임베딩 헬퍼
# ════════════════════════════════════════════════════════════════════════════

def _embed(texts: List[str]) -> List[List[float]]:
    """ko-sroberta-multitask으로 배치 임베딩 (768d)"""
    model = _get_st_model()
    return model.encode(texts, convert_to_numpy=True).tolist()


# ════════════════════════════════════════════════════════════════════════════
# 컬렉션 초기화
# ════════════════════════════════════════════════════════════════════════════

def _load_examples(dataset_path: Optional[str] = None) -> List[Dict]:
    """
    혐오표현 예시 문장 로드.
    dataset_path 가 있으면 JSON 파일에서 로드 (hate_dataset.json),
    없으면 내장 HATE_EXAMPLES 사용.
    """
    import json
    if dataset_path:
        p = Path(dataset_path)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"데이터셋 로드: {len(data):,}건 ({dataset_path})")
            return data
        else:
            logger.warning(f"데이터셋 파일 없음: {dataset_path} — 내장 예시 사용")
    return HATE_EXAMPLES


def init_hate_collection(
    qdrant_client,
    openai_client=None,  # 하위 호환용 (미사용)
    force_reinit: bool = False,
    dataset_path: Optional[str] = None,
    batch_size: int = 100,
) -> None:
    """
    Qdrant에 "hate_speech" 컬렉션을 생성하고 예시 문장을 upsert.

    Parameters
    ----------
    qdrant_client  : QdrantClient (local file or server)
    force_reinit   : True이면 기존 컬렉션 삭제 후 재생성
    dataset_path   : hate_dataset.json 경로 (None이면 내장 예시 사용)
    batch_size     : 임베딩 배치 크기
    """
    from qdrant_client import models as qmodels

    existing = [c.name for c in qdrant_client.get_collections().collections]

    if HATE_COLLECTION in existing:
        if not force_reinit:
            logger.info(f"'{HATE_COLLECTION}' 컬렉션이 이미 존재합니다. 건너뜁니다.")
            print(f"[hate_vector] '{HATE_COLLECTION}' 컬렉션이 이미 존재합니다. (force_reinit=True 로 재생성)")
            return
        qdrant_client.delete_collection(HATE_COLLECTION)
        logger.info(f"'{HATE_COLLECTION}' 컬렉션 삭제 후 재생성")

    qdrant_client.create_collection(
        collection_name=HATE_COLLECTION,
        vectors_config=qmodels.VectorParams(
            size=EMBED_DIM,
            distance=qmodels.Distance.COSINE,
        ),
    )

    examples = _load_examples(dataset_path)
    total = len(examples)
    print(f"[hate_vector] 임베딩 시작: {total:,}건 (배치 {batch_size})")

    # 배치 단위로 임베딩 + upsert
    for start in range(0, total, batch_size):
        batch = examples[start:start + batch_size]
        texts = [ex["text"] for ex in batch]
        vectors = _embed(texts)

        points = [
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={"text": ex["text"], "category": ex["category"]},
            )
            for ex, vec in zip(batch, vectors)
        ]
        qdrant_client.upsert(collection_name=HATE_COLLECTION, points=points)

        done = min(start + batch_size, total)
        if done % 500 == 0 or done == total:
            print(f"  {done:,}/{total:,} 완료 ({done/total*100:.0f}%)")

    print(f"[hate_vector] '{HATE_COLLECTION}' 컬렉션 초기화 완료 ({total:,}건)")


# ════════════════════════════════════════════════════════════════════════════
# 벡터 유사도 필터
# ════════════════════════════════════════════════════════════════════════════

def vector_hate_filter(
    text: str,
    qdrant_client,
    openai_client=None,  # 하위 호환용 (미사용)
    threshold: float = 0.70,
    top_k: int = 3,
) -> Dict:
    """
    입력 문장을 임베딩해 hate_speech 컬렉션과 유사도 비교.
    threshold 이상이면 차단.

    Parameters
    ----------
    text           : 검사할 사용자 입력
    qdrant_client  : QdrantClient
    threshold      : 차단 임계값 (기본 0.82, 높을수록 엄격)
    top_k          : 검색 결과 수

    Returns
    -------
    {
        "passed"        : bool,
        "violation_type": str,
        "matched"       : str,   # 가장 유사한 예시 문장
        "score"         : float, # 유사도 점수
        "message"       : str,
    }
    """
    try:
        # 컬렉션 존재 확인
        existing = [c.name for c in qdrant_client.get_collections().collections]
        if HATE_COLLECTION not in existing:
            logger.warning("hate_speech 컬렉션 없음 — 벡터 필터 건너뜀")
            return {"passed": True, "violation_type": "none",
                    "matched": "", "score": 0.0, "message": ""}

        # 입력 임베딩
        vec = _embed([text])[0]

        # 유사도 검색 (qdrant-client 버전별 API 호환)
        try:
            # 최신 버전 API
            from qdrant_client.models import QueryRequest
            response = qdrant_client.query_points(
                collection_name=HATE_COLLECTION,
                query=vec,
                limit=top_k,
                with_payload=True,
            )
            results = response.points
        except Exception:
            # 구버전 fallback
            results = qdrant_client.search(
                collection_name=HATE_COLLECTION,
                query_vector=vec,
                limit=top_k,
                with_payload=True,
            )

        if not results:
            return {"passed": True, "violation_type": "none",
                    "matched": "", "score": 0.0, "message": ""}

        top = results[0]
        score = top.score
        category = top.payload.get("category", "hate")
        matched_text = top.payload.get("text", "")

        if score >= threshold:
            category_msg = {
                "political_slur": "특정 정치인을 비하하는 표현",
                "group_stigma":   "특정 집단을 낙인찍는 표현",
                "regional_slur":  "지역을 비하하는 표현",
                "threat":         "위협적이거나 폭력적인 표현",
                "context_hate":   "맥락상 혐오로 해석될 수 있는 표현",
            }.get(category, "부적절한 표현")

            return {
                "passed": False,
                "violation_type": category,
                "matched": matched_text,
                "score": round(score, 4),
                "message": (
                    f"{category_msg}이 감지되었습니다 (유사도 {score:.0%}). "
                    "토론 주제와 관련된 정책 논거를 사용해주세요."
                ),
            }

        return {
            "passed": True,
            "violation_type": "none",
            "matched": "",
            "score": round(score, 4),
            "message": "",
        }

    except Exception as e:
        logger.warning(f"vector_hate_filter 오류: {e}")
        return {"passed": True, "violation_type": "none",
                "matched": "", "score": 0.0, "message": ""}
