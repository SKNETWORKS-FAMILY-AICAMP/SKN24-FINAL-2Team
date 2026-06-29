"""
공공데이터포털 gov24 정책 목록 수집기
- API: https://api.odcloud.kr/api/gov24/v3/serviceDetail
- 역할: 정책 수집 → GPT 청년 필터링 → 카테고리 분류+Top5 → 네이버 검색 쿼리 반환

증분 수집:
  - 첫 실행: 전체 수집 + GPT 필터링
             → gov24_youth.csv (청년 정책)
             → gov24_seen.csv (전체 수집 서비스ID)
  - 이후 실행: gov24_seen.csv 기준 신규만 GPT 필터링 → 기존 CSV에 추가
"""
import csv
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests
from openai import OpenAI

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import GOV24_API_KEY, OPENAI_API_KEY, DATA_DIR, POLICY_RAW_DIR, POLICY_META_DIR, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

BASE_URL   = "https://api.odcloud.kr/api/gov24/v3/serviceDetail"
PER_PAGE   = 100
BATCH_SIZE = 50

CATEGORIES = ["일자리", "교육", "주거", "금융", "생활복지", "문화"]
TOP_N      = 10


class Gov24Collector:

    def __init__(self):
        if not GOV24_API_KEY:
            logger.error("GOV24_API_KEY 환경변수를 설정하세요.")
        if not OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY 환경변수를 설정하세요.")
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)

    # ══════════════════════════════════════════════════════════════════════
    # 핵심 퍼블릭 메서드
    # ══════════════════════════════════════════════════════════════════════

    def collect_and_process(
        self,
        gov24_keyword: Optional[str] = None,
        save: bool = False,
        db_conn=None,
    ) -> tuple[Dict[str, List[dict]], List[str]]:
        """
        gov24 증분 수집 → GPT 청년 필터링 → 카테고리 Top5 + 정책명 쿼리 반환.
        gov24_seen.csv 기준으로 신규 정책만 GPT 처리.
        """
        youth_csv = POLICY_META_DIR / "gov24_youth.csv"
        seen_csv  = POLICY_META_DIR / "gov24_seen.csv"
        youth_csv.parent.mkdir(parents=True, exist_ok=True)

        # ── 기존 청년 정책 CSV 로드 ────────────────────────────────────────
        if youth_csv.exists():
            existing_youth_df = pd.read_csv(youth_csv, dtype={"서비스ID": str})
            logger.info(f"[Gov24] 기존 청년 정책: {len(existing_youth_df)}건")
        else:
            existing_youth_df = pd.DataFrame()
            logger.info("[Gov24] 기존 청년 정책 없음")

        # ── 전체 수집 서비스ID CSV 로드 ────────────────────────────────────
        # if seen_csv.exists():
        #     seen_df  = pd.read_csv(seen_csv, dtype={"서비스ID": str})
        #     seen_ids = set(seen_df["서비스ID"].dropna().tolist())
        #     logger.info(f"[Gov24] 기존 수집 전체: {len(seen_ids)}건")
        # else:
        #     seen_df  = pd.DataFrame()
        #     seen_ids = set()
        #     logger.info("[Gov24] 기존 수집 내역 없음 — 전체 수집")

        from storage import rds as _rds
        seen_ids = _rds.load_seen_policy_ids(db_conn) if db_conn else set()
        logger.info(f"[Gov24] 기존 수집 전체: {len(seen_ids)}건")

        # ── gov24 전체 목록 조회 ───────────────────────────────────────────
        all_items = self._get_all_pages(keyword=gov24_keyword)
        if not all_items:
            logger.error("[Gov24] 수집된 정책 없음")
            return {c: [] for c in CATEGORIES}, [], {}

        # ── 신규 정책만 추출 (전체 서비스ID 기준) ─────────────────────────
        new_items = [
            item for item in all_items
            if str(item.get("서비스ID", "")) not in seen_ids
        ]
        logger.info(f"[Gov24] 전체 {len(all_items)}건 / 신규 {len(new_items)}건")

        # ── 신규 정책만 GPT 청년 필터링 ───────────────────────────────────
        new_youth_items = []
        if new_items:
            new_youth_items = self._filter_youth(new_items)
            logger.info(f"[Gov24] 신규 청년 정책 {len(new_youth_items)}건")

            # 청년 정책 CSV 업데이트
            if new_youth_items:
                new_youth_df   = pd.DataFrame(new_youth_items)
                combined_youth = pd.concat([existing_youth_df, new_youth_df], ignore_index=True)
                combined_youth.to_csv(youth_csv, index=False, encoding="utf-8-sig")
                logger.info(f"[Gov24] gov24_youth.csv 저장 → 총 {len(combined_youth)}건")

            # 전체 수집 서비스ID CSV 업데이트 (청년 여부 관계없이 전부)
            # new_seen_df   = pd.DataFrame([{"서비스ID": str(item.get("서비스ID", ""))} for item in new_items])
            # combined_seen = pd.concat([seen_df, new_seen_df], ignore_index=True)
            # combined_seen.to_csv(seen_csv, index=False, encoding="utf-8-sig")
            # logger.info(f"[Gov24] gov24_seen.csv 저장 → 총 {len(combined_seen)}건")
            new_service_ids = [str(item.get("서비스ID", "")) for item in new_items if item.get("서비스ID")]
            if db_conn:
                _rds.upsert_seen_policies(db_conn, new_service_ids)
            logger.info(f"[Gov24] POLICY_SEEN 저장 → {len(new_service_ids)}건 추가")
        else:
            logger.info("[Gov24] 신규 정책 없음 — 기존 데이터 사용")

        # ── 카테고리 분류 + Top5 선정 ─────────────────────────────────────
        # 최초 실행: new_items = 전체 / 이후 실행: new_items = 신규만
        # 신규 없으면 오늘 수집 생략
        if not new_items:
            logger.info("[Gov24] 신규 정책 없음 — 오늘 네이버 검색 생략")
            return {c: [] for c in CATEGORIES}, [], {}

        # 신규 중 청년 정책이 없으면 생략
        if not new_youth_items:
            logger.info("[Gov24] 신규 청년 정책 없음 — 오늘 네이버 검색 생략")
            return {c: [] for c in CATEGORIES}, [], {}

        logger.info(f"[Gov24] 신규 청년 정책 {len(new_youth_items)}건 → 카테고리 분류 시작")
        categorized = self._categorize_and_top5(new_youth_items)

        if save:
            self._save_categorized(categorized)

        # ── 네이버 검색용 쿼리: 신규 카테고리 Top5만 ─────────────────────
        queries = []
        query_to_category = {}
        for cat, items in categorized.items():
            for item in items:
                name = item.get("서비스명", "").strip()
                if name and name not in queries:
                    queries.append(name)
                    query_to_category[name] = cat

        return categorized, queries, query_to_category

    # ══════════════════════════════════════════════════════════════════════
    # GPT 1단계: 청년 필터링
    # ══════════════════════════════════════════════════════════════════════

    def _filter_youth(self, items: List[dict]) -> List[dict]:
        """GPT(gpt-4o-mini)로 청년 관련 정책만 필터링"""
        logger.info(f"[Gov24] GPT 청년 필터링 ({len(items)}건, 배치 {BATCH_SIZE}개씩)")

        youth_items   = []
        total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

        for batch_no, start in enumerate(range(0, len(items), BATCH_SIZE), 1):
            chunk = items[start: start + BATCH_SIZE]
            rows  = [
                {
                    "idx":        i,
                    "서비스명":       str(row.get("서비스명", ""))[:100],
                    "서비스목적": str(row.get("서비스목적", ""))[:200],
                    "지원대상":       str(row.get("지원대상", ""))[:300],
                }
                for i, row in enumerate(chunk)
            ]

            try:
                flags = self._gpt_youth_batch(rows)
            except Exception as e:
                logger.warning(f"[Gov24] 배치 {batch_no} 오류: {e} → 모두 False")
                flags = [False] * len(rows)

            for flag, item in zip(flags, chunk):
                if flag:
                    youth_items.append(item)

            logger.info(
                f"[Gov24] 청년 필터 배치 {batch_no}/{total_batches} 완료 "
                f"| 누적: {len(youth_items)}건"
            )
            time.sleep(0.3)

        return youth_items

    def _gpt_youth_batch(self, rows: List[dict]) -> List[bool]:
        """GPT: 청년 여부 분류"""
        system_prompt = (
            "당신은 공공서비스 데이터를 분류하는 전문가입니다.\n"
            "아래 JSON 배열의 각 항목이 청년(만 19세~34세)이 지원 대상에 포함되는 서비스인지 판별하세요.\n"
            "true 기준:\n"
            "  - 지원대상에 청년, 청년층, 만 19~34세, 대학생, 취업준비생, 사회초년생이 명시된 경우\n"
            "  - 청년이 다른 계층과 함께 포함된 경우도 true\n"
            "  - 신혼부부, 1인가구, 무주택자 등 청년이 실질적으로 해당되는 대상도 true\n"
            "false 기준:\n"
            "  - 지원대상이 아동, 청소년(만 18세 이하), 노인으로만 구성된 경우\n"
            "  - 청년이 지원대상에 전혀 언급되지 않은 경우\n\n"
            "반드시 아래 형식의 JSON 배열만 반환하세요. 설명 없이 배열만:\n"
            '[{"idx": 0, "is_youth": true}, {"idx": 1, "is_youth": false}, ...]'
        )
        return self._gpt_call_bool(system_prompt, rows, key="is_youth")

    # ══════════════════════════════════════════════════════════════════════
    # GPT 2단계: 카테고리 분류 + Top5
    # ══════════════════════════════════════════════════════════════════════

    def _categorize_and_top5(self, items: List[dict]) -> Dict[str, List[dict]]:
        """청년 정책 전체를 카테고리 분류 → 카테고리별 Top5 선정. 기타는 버림."""
        logger.info(f"[Gov24] GPT 카테고리 분류 ({len(items)}건, 배치 {BATCH_SIZE}개씩)")

        annotated     = []
        total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

        for batch_no, start in enumerate(range(0, len(items), BATCH_SIZE), 1):
            chunk = items[start: start + BATCH_SIZE]
            rows  = [
                {
                    "idx":        i,
                    "서비스명":       str(row.get("서비스명", ""))[:100],
                    "서비스목적": str(row.get("서비스목적", ""))[:200],
                    "지원대상":       str(row.get("지원대상", ""))[:300],
                }
                for i, row in enumerate(chunk)
            ]

            try:
                results = self._gpt_categorize_batch(rows)
            except Exception as e:
                logger.warning(f"[Gov24] 카테고리 배치 {batch_no} 오류: {e} → 기타 처리")
                results = [{"idx": r["idx"], "category": "기타", "importance": 0} for r in rows]

            result_map = {r["idx"]: r for r in results}
            for i, item in enumerate(chunk):
                info = result_map.get(i, {"category": "기타", "importance": 0})
                annotated.append({
                    **item,
                    "_category":   info.get("category", "기타"),
                    "_importance": info.get("importance", 0),
                })

            logger.info(f"[Gov24] 카테고리 배치 {batch_no}/{total_batches} 완료")
            time.sleep(0.3)

        categorized: Dict[str, List[dict]] = {c: [] for c in CATEGORIES}
        for cat in CATEGORIES:
            cat_items = [a for a in annotated if a["_category"] == cat]
            top5      = sorted(cat_items, key=lambda x: x["_importance"], reverse=True)[:TOP_N]
            categorized[cat] = [
                {k: v for k, v in item.items() if not k.startswith("_")}
                for item in top5
            ]
            logger.info(f"[Gov24] {cat}: 전체 {len(cat_items)}건 → Top{TOP_N} 선정")

        return categorized

    def _gpt_categorize_batch(self, rows: List[dict]) -> List[dict]:
        """GPT: 카테고리 분류 + 중요도 점수"""
        cats_str = " / ".join(CATEGORIES) + " / 기타"
        system_prompt = (
            "당신은 공공서비스 정책을 분류하는 전문가입니다.\n"
            f"아래 JSON 배열의 각 항목을 다음 카테고리 중 하나로 분류하고, "
            f"해당 정책이 얼마나 중요한 정책인지 1~10점으로 평가하세요.\n"
            f"카테고리: {cats_str}\n\n"
            "카테고리 기준:\n"
            "  일자리:   취업지원, 직업훈련, 인턴십, 창업지원, 고용장려금\n"
            "  교육:     교육비지원, 자격증, 학습지원, 장학금\n"
            "  주거:     청년주택, 전세자금, 월세지원, 주거급여, 임대주택\n"
            "  금융:     대출, 저축, 금리우대, 신용지원, 생활비 지원\n"
            "  생활복지: 의료, 심리상담, 생활지원, 긴급복지, 사회서비스\n"
            "  문화:     문화·여가, 도서관, 예술, 스포츠, 관광\n"
            "  기타:     위 카테고리에 해당하지 않는 경우\n\n"
            "중요도 평가 기준:\n"
            "  10점: 수혜 대상이 넓고 지원 규모가 크며 실생활에 직접적인 영향\n"
            "  7~9점: 특정 상황에 있는 사람에게 매우 유용한 정책\n"
            "  4~6점: 일부에게 도움이 되지만 범위가 제한적인 정책\n"
            "  1~3점: 수혜 대상이 매우 좁거나 지원 규모가 작은 정책\n\n"
            "반드시 아래 형식의 JSON 배열만 반환하세요. 설명 없이 배열만:\n"
            '[{"idx": 0, "category": "일자리", "importance": 8}, ...]'
        )

        response = self.openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": json.dumps(rows, ensure_ascii=False)},
            ],
            temperature=0,
        )

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json|```", "", raw).strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match  = re.search(r"\[.*\]", raw, re.DOTALL)
            parsed = json.loads(match.group()) if match else []

        if not isinstance(parsed, list):
            return [{"idx": r["idx"], "category": "기타", "importance": 0} for r in rows]

        return parsed

    # ══════════════════════════════════════════════════════════════════════
    # GPT 공통 헬퍼
    # ══════════════════════════════════════════════════════════════════════

    def _gpt_call_bool(self, system_prompt: str, rows: List[dict], key: str) -> List[bool]:
        response = self.openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": json.dumps(rows, ensure_ascii=False)},
            ],
            temperature=0,
        )

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json|```", "", raw).strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                except Exception:
                    logger.warning(f"[Gov24] GPT 파싱 실패: {raw[:100]}")
                    return [False] * len(rows)
            else:
                logger.warning(f"[Gov24] GPT 배열 추출 실패: {raw[:100]}")
                return [False] * len(rows)

        if not isinstance(parsed, list):
            return [False] * len(rows)

        idx_map = {item["idx"]: item[key] for item in parsed}
        return [bool(idx_map.get(r["idx"], False)) for r in rows]

    # ══════════════════════════════════════════════════════════════════════
    # gov24 API 호출
    # ══════════════════════════════════════════════════════════════════════

    def collect_all(self, keyword: Optional[str] = None, save: bool = False) -> List[dict]:
        """GPT 없이 gov24 전체 데이터만 수집."""
        items = self._get_all_pages(keyword=keyword)
        if save and items:
            self._save(items, tag=keyword or "전체")
        return items

    def _get_all_pages(self, keyword: Optional[str] = None) -> List[dict]:
        first = self._fetch_page(page=1, keyword=keyword)
        if not first:
            return []

        total       = first.get("totalCount", 0)
        all_items   = first.get("data", [])
        total_pages = (total + PER_PAGE - 1) // PER_PAGE
        logger.info(f"[Gov24] 총 {total}건 / {total_pages}페이지")

        for page in range(2, total_pages + 1):
            logger.info(f"[Gov24] 페이지 {page}/{total_pages} 수집 중...")
            result = self._fetch_page(page=page, keyword=keyword)
            if result:
                all_items.extend(result.get("data", []))
            time.sleep(0.3)

        logger.info(f"[Gov24] 수집 완료: {len(all_items)}건")
        return all_items

    def _fetch_page(self, page: int, keyword: Optional[str] = None) -> Optional[dict]:
        params = {
            "serviceKey": GOV24_API_KEY.strip(),
            "page":       page,
            "perPage":    PER_PAGE,
            "returnType": "JSON",
        }
        if keyword:
            params["cnd_nm"] = keyword

        try:
            resp = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"[Gov24] 페이지 {page} 요청 실패: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════════
    # 저장 유틸
    # ══════════════════════════════════════════════════════════════════════

    def _save(self, items: List[dict], tag: str = "") -> None:
        POLICY_RAW_DIR.mkdir(parents=True, exist_ok=True)
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag_str = f"_{tag}" if tag else ""

        json_path = POLICY_RAW_DIR / f"gov24정책{tag_str}_{ts}.json"
        csv_path  = POLICY_RAW_DIR / f"gov24정책{tag_str}_{ts}.csv"

        json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[Gov24] JSON 저장 → {json_path.name} ({len(items)}건)")

        if items:
            fieldnames = list(items[0].keys())
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(items)
            logger.info(f"[Gov24] CSV 저장 → {csv_path.name} ({len(items)}건)")

    def _save_categorized(self, categorized: Dict[str, List[dict]]) -> None:
        """카테고리 Top5를 저장
        - data/policies/raw/gov24_top5_날짜.json  : 카테고리별 통합 JSON (1파일)
        - data/policies/metadata/gov24_top5_날짜.csv : 확인용 CSV
        """
        POLICY_RAW_DIR.mkdir(parents=True, exist_ok=True)
        POLICY_META_DIR.mkdir(parents=True, exist_ok=True)

        today     = datetime.now().strftime("%Y-%m-%d")
        json_path = POLICY_RAW_DIR  / f"gov24_top5_{today}.json"
        csv_path     = POLICY_META_DIR / f"gov24_top5_{today}.csv"

        # 저장 시 제외할 필드
        EXCLUDE_FIELDS = {"rank", "중요도점수"}

        # JSON: 카테고리별로 묶어서 1파일 저장 (원본 필드만)
        output = {}
        rows   = []
        for cat, items in categorized.items():
            cleaned = [{k: v for k, v in item.items() if k not in EXCLUDE_FIELDS} for item in items]
            output[cat] = cleaned
            for item in cleaned:
                rows.append({"category": cat, **item})

        json_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # CSV: 확인용
        if rows:
            pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")

        total = sum(len(v) for v in categorized.values())
        logger.info(f"[Gov24] 카테고리 Top5 저장 → {json_path.name} / {csv_path.name} (총 {total}건)")
        for cat, items in categorized.items():
            logger.info(f"  [{cat}] {len(items)}개")
            for item in items:
                logger.info(f"    - {item.get('서비스명', '')}")