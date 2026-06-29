"""
정책 JSON → 정제된 JSON 변환 파서
policy_preprocessing.py 로직 흡수
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 제외 필드 (메타데이터용이라 본문에 불필요)
_EXCLUDE_FIELDS = {
    "등록일시", "조회수", "서비스ID", "서비스 ID",
    "상세조회URL", "상세조회 URL", "소관기관코드",
}

# gov24 카테고리 → category_id 매핑
_CATEGORY_MAP = {
    "일자리": 1,
    "교육":   2,
    "주거":   3,
    "금융":   4,
    "생활복지": 5,
    "복지":   5,
    "문화":   6,
}


# ══════════════════════════════════════════════════════════════════════════
# 텍스트 정제
# ══════════════════════════════════════════════════════════════════════════

def _clean_text(text) -> str:
    """결측치 처리 + || 치환 + 불릿 정규화"""
    if text is None:
        return "정보 없음"
    text_str = str(text).strip()
    if not text_str or text_str.lower() in ("nan", "null", ""):
        return "정보 없음"

    text_str = text_str.replace("||", ", ")
    text_str = text_str.replace('\\r', '').replace('\r', '')
    text_str = text_str.replace('\\n', '\n')

    bullet = re.compile(r'^\s*([ㅇ○■●◆◇◈▶▷*]|-\s*|\u25e6)\s*')
    lines = []
    for line in text_str.split('\n'):
        s = line.strip()
        if not s:
            continue
        lines.append(bullet.sub('- ', s) if bullet.match(s) else s)

    return "\n".join(lines) if lines else "정보 없음"


def _parse_dates(period_text: str) -> Tuple[Optional[str], Optional[str]]:
    """신청기한 텍스트 → (시작일, 종료일) YYYY-MM-DD 형식"""
    if not period_text or str(period_text).lower() in ("nan", "정보 없음"):
        return None, None

    text = str(period_text).strip()

    # 한국어 형식: 2024년 3월 1일부터 ~ 2024년 12월 31일까지
    m = re.search(
        r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일부터'
        r'\s*(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일까지',
        text
    )
    if m:
        y1, mo1, d1 = m.group(1), int(m.group(2)), int(m.group(3))
        y2 = m.group(4) or y1
        mo2, d2 = int(m.group(5)), int(m.group(6))
        return f"{y1}-{mo1:02d}-{d1:02d}", f"{y2}-{mo2:02d}-{d2:02d}"

    # 표준 형식: 2024-03-01 ~ 2024-12-31
    m = re.search(
        r'(\d{4})[-./](\d{2})[-./](\d{2})\s*~\s*(\d{4})[-./](\d{2})[-./](\d{2})',
        text
    )
    if m:
        return (
            f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
            f"{m.group(4)}-{m.group(5)}-{m.group(6)}",
        )

    return None, None


def _clean_updated_at(date_val) -> Optional[str]:
    """수정일시 → YYYY-MM-DD 형식"""
    if not date_val or str(date_val).lower() in ("nan", ""):
        return None
    digits = re.sub(r'[^0-9]', '', str(date_val))
    if len(digits) >= 8:
        d = digits[:8]
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None


# ══════════════════════════════════════════════════════════════════════════
# 퍼블릭 인터페이스
# ══════════════════════════════════════════════════════════════════════════

def parse_policy(raw: Dict, category: str = "") -> Dict:
    """
    단일 정책 데이터 정제

    Args:
        raw:      gov24 수집 원본 dict
        category: gov24 카테고리명 (일자리/교육/주거 등)

    Returns:
        {
            "service_id":       str,
            "title":            str,
            "content":          str,   # S3 저장 + 임베딩용
            "category_id":      int,
            "department":       str,
            "apply_period":     str | None,  # 신청기한 텍스트 그대로
            "source_url":       str,
            "updated_at":       str | None,
        }
    """
    service_id   = raw.get("서비스ID", "")
    service_name = raw.get("서비스명", "정보 없음")
    source_url   = raw.get("온라인신청사이트URL", "") or ""

    # 본문용 정제 dict (제외 필드 제거)
    refined = {}
    for key, value in raw.items():
        clean_key = key.strip()
        if clean_key in _EXCLUDE_FIELDS:
            continue
        refined[clean_key] = _clean_text(value)

    # 카테고리 추가
    refined["카테고리"] = category

    # JSON 문자열로 직렬화 (임베딩용 content)
    content = json.dumps(refined, ensure_ascii=False, indent=2)

    # 신청기한 텍스트 그대로 저장
    apply_period = _clean_text(raw.get("신청기한", ""))
    if apply_period == "정보 없음":
        apply_period = None

    # 수정일시
    updated_at = _clean_updated_at(raw.get("수정일시", ""))

    # category_id 결정
    category_id = _CATEGORY_MAP.get(category, 5)  # 기본값: 생활복지

    return {
        "service_id":       service_id,
        "title":            service_name,
        "content":          content,
        "category_id":      category_id,
        "department":       _clean_text(raw.get("소관기관명", "")),
        "apply_period":     apply_period,
        "source_url":       source_url,
        "updated_at":       updated_at,
    }


def parse_policies_from_json(json_path: Path) -> List[Dict]:
    """
    gov24 수집 JSON → 정제된 정책 리스트

    JSON 구조:
        {"카테고리명": [정책1, 정책2, ...], ...}
    """
    if not json_path.exists():
        logger.error(f"[PolicyParser] 파일 없음: {json_path}")
        return []

    with json_path.open(encoding="utf-8") as f:
        raw_data = json.load(f)

    results = []
    for category, policies in raw_data.items():
        for policy in policies:
            try:
                parsed = parse_policy(policy, category)
                results.append(parsed)
            except Exception as e:
                logger.warning(f"[PolicyParser] 파싱 실패 ({policy.get('서비스명', '?')}): {e}")

    logger.info(f"[PolicyParser] {len(results)}건 정제 완료")
    return results


# ══════════════════════════════════════════════════════════════════════════
# CSV 저장 / 로드
# ══════════════════════════════════════════════════════════════════════════

import csv

POLICY_CSV_COLUMNS = [
    "service_id", "title", "category_id", "department",
    "apply_period",
    "source_url", "updated_at", "s3_path", "collected_at",
]


def save_policies_to_csv(policies: List[Dict], csv_path: Path) -> None:
    """parse_policy() 결과 리스트 → CSV 저장"""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    rows = []
    for policy in policies:
        rows.append({
            "service_id":       policy.get("service_id", ""),
            "title":            policy.get("title", ""),
            "category_id":      policy.get("category_id", ""),
            "department":       policy.get("department", ""),
            "apply_period":     policy.get("apply_period") or "",
            "source_url":       policy.get("source_url", ""),
            "updated_at":       policy.get("updated_at") or "",
            "s3_path":          "",       # S3 업로드 후 채움
            "collected_at":     today,
        })

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=POLICY_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"[PolicyParser] CSV 저장: {csv_path} ({len(rows)}건)")


def load_policies_from_csv(csv_path: Path) -> List[Dict]:
    """CSV → dict 리스트 (RDS 적재용)"""
    if not csv_path.exists():
        logger.error(f"[PolicyParser] CSV 없음: {csv_path}")
        return []

    rows = []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    logger.info(f"[PolicyParser] CSV 로드: {csv_path} ({len(rows)}건)")
    return rows