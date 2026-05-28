"""
법제처 국가법령정보 API 수집기
- gov24 top5 CSV에서 법령 필드 파싱
- 법제처 API로 해당 조문만 수집
- data/laws/법령명_일련번호.json 으로 저장

저장 구조:
  data/
  └── laws/
      ├── 청년기본법_277289.json
      ├── 고용보험법_12345.json
      └── ...

각 JSON:
  {
    "법령명": "청년기본법",
    "법령일련번호": "277289",
    "수집일시": "2026-05-26T02:00:00",
    "관련정책": [
      {"서비스ID": "149200005007", "서비스명": "국민취업지원제도", "조문": "제17조"}
    ],
    "조문": [
      {"조문번호": "17", "조문제목": "청년 일자리 지원", "조문내용": "..."}
    ]
  }
"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR, REQUEST_TIMEOUT

try:
    import pandas as pd
except ImportError:
    raise ImportError("pandas 필요: pip install pandas")

logger = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────────────────────────
LAW_SEARCH_URL  = "http://www.law.go.kr/DRF/lawSearch.do"
LAW_SERVICE_URL = "http://www.law.go.kr/DRF/lawService.do"
LAWS_DIR        = DATA_DIR / "laws"
REQUEST_DELAY   = 0.5   # 법제처 API 요청 간격 (초)


class LawCollector:

    def __init__(self, oc: str):
        """
        oc: 법제처 open.law.go.kr 가입 이메일 @ 앞부분
            예) test@gmail.com → oc="test"
        """
        if not oc:
            raise ValueError("LAW_API_OC 환경변수를 설정하세요.")
        self.oc = oc
        LAWS_DIR.mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════════════════════════════════════
    # 퍼블릭 메서드
    # ══════════════════════════════════════════════════════════════════════

    def collect_from_top5(self, top5_csv_path: Path) -> int:
        """
        gov24 top5 CSV → 법령 파싱 → 조문 수집 → JSON 저장
        반환: 저장된 법령 파일 수
        """
        if not top5_csv_path.exists():
            logger.error(f"[Law] top5 파일 없음: {top5_csv_path}")
            return 0

        df = pd.read_csv(top5_csv_path, dtype=str).fillna("")
        logger.info(f"[Law] top5 로드: {len(df)}건 ({top5_csv_path.name})")

        # 정책별 법령 파싱
        # {법령명: {조문번호: [관련정책 dict]}} 형태로 수집
        law_map: dict[str, dict] = {}   # 법령명 → {mst, 조문목록, 관련정책}

        for _, row in df.iterrows():
            서비스ID  = row.get("서비스ID", "").strip()
            서비스명  = row.get("서비스명", "").strip()
            법령_raw  = row.get("법령", "").strip()

            if not 법령_raw:
                continue

            parsed = self._parse_law_field(법령_raw)
            for law_name, jo_num in parsed:
                if law_name not in law_map:
                    law_map[law_name] = {
                        "조문목록": [],     # [(jo_num, 서비스ID, 서비스명)]
                        "관련정책": [],
                    }
                law_map[law_name]["조문목록"].append((jo_num, 서비스ID, 서비스명))
                # 관련정책 중복 없이 추가
                policy_entry = {"서비스ID": 서비스ID, "서비스명": 서비스명, "조문": f"제{jo_num}조" if jo_num else "전체"}
                if policy_entry not in law_map[law_name]["관련정책"]:
                    law_map[law_name]["관련정책"].append(policy_entry)

        logger.info(f"[Law] 파싱된 법령 수: {len(law_map)}개")

        saved = 0
        for law_name, info in law_map.items():
            result = self._collect_one_law(law_name, info)
            if result:
                saved += 1
            time.sleep(REQUEST_DELAY)

        logger.info(f"[Law] 수집 완료: {saved}/{len(law_map)}개 저장")
        return saved

    # ══════════════════════════════════════════════════════════════════════
    # 법령명 파싱
    # ══════════════════════════════════════════════════════════════════════

    def _parse_law_field(self, raw: str) -> list[tuple[str, str]]:
        """
        '청년기본법(제17조)||청년고용촉진 특별법(제1조)' 형태 파싱
        → [('청년기본법', '17'), ('청년고용촉진 특별법', '1')]

        조문이 없으면 jo_num = '' (전체 수집)
        """
        results = []
        parts   = raw.split("||")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(.+?)\(제(\d+(?:의\d+)?)조.*?\)$", part)
            if m:
                law_name = m.group(1).strip()
                jo_num   = m.group(2).strip()
            else:
                # 조문 표기 없는 경우 (법령명만)
                law_name = re.sub(r"\(.*?\)", "", part).strip()
                jo_num   = ""
            if law_name:
                results.append((law_name, jo_num))
        return results

    # ══════════════════════════════════════════════════════════════════════
    # 법제처 API 호출
    # ══════════════════════════════════════════════════════════════════════

    def _search_law(self, law_name: str) -> Optional[dict]:
        """법령명으로 검색 → 첫 번째 결과 반환"""
        try:
            resp = requests.get(
                LAW_SEARCH_URL,
                params={"OC": self.oc, "target": "law", "type": "JSON",
                        "query": law_name, "display": 1},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            law = resp.json().get("LawSearch", {}).get("law")
            if not law:
                logger.warning(f"[Law] 검색 결과 없음: '{law_name}'")
                return None
            return law if isinstance(law, dict) else law[0]
        except Exception as e:
            logger.error(f"[Law] 검색 오류 '{law_name}': {e}")
            return None

    def _fetch_articles(self, mst: str, jo_nums: list[str]) -> list[dict]:
        """
        법령일련번호로 본문 조회 → 필요한 조문만 필터링해서 반환
        jo_nums가 비어있으면 전체 조문 반환
        """
        try:
            resp = requests.get(
                LAW_SERVICE_URL,
                params={"OC": self.oc, "target": "law", "MST": mst, "type": "JSON"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            articles = resp.json().get("법령", {}).get("조문", {}).get("조문단위", [])
            if not articles:
                return []
            if isinstance(articles, dict):
                articles = [articles]
        except Exception as e:
            logger.error(f"[Law] 본문 조회 오류 MST={mst}: {e}")
            return []

        # 특정 조문만 필터링
        target_nums = set(jo_nums) - {""}
        if target_nums:
            articles = [
                a for a in articles
                if str(a.get("조문번호", "")) in target_nums
            ]

        return [
            {
                "조문번호": a.get("조문번호", ""),
                "조문제목": a.get("조문제목", ""),
                "조문내용": a.get("조문내용", ""),
            }
            for a in articles
        ]

    # ══════════════════════════════════════════════════════════════════════
    # 수집 + 저장
    # ══════════════════════════════════════════════════════════════════════

    def _collect_one_law(self, law_name: str, info: dict) -> bool:
        """법령 하나 수집 → JSON 저장. 이미 파일 있으면 관련정책만 업데이트"""
        jo_nums     = [jo for jo, _, _ in info["조문목록"]]
        관련정책    = info["관련정책"]

        # 검색
        search_result = self._search_law(law_name)
        if not search_result:
            return False

        mst      = search_result.get("법령일련번호", "")
        법령명   = search_result.get("법령명한글", law_name)
        시행일자 = search_result.get("시행일자", "")

        # 기존 파일 있으면 관련정책만 업데이트
        save_path = LAWS_DIR / f"{법령명}_{mst}.json"
        if save_path.exists():
            existing = json.loads(save_path.read_text(encoding="utf-8"))
            existing_policies = existing.get("관련정책", [])
            for p in 관련정책:
                if p not in existing_policies:
                    existing_policies.append(p)
            existing["관련정책"] = existing_policies
            existing["수집일시"] = datetime.now().isoformat()
            save_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[Law] 기존 파일 관련정책 업데이트: {save_path.name}")
            return True

        # 본문 조회
        time.sleep(REQUEST_DELAY)
        articles = self._fetch_articles(mst, jo_nums)
        if not articles:
            logger.warning(f"[Law] 조문 없음: '{법령명}'")
            return False

        # 저장
        data = {
            "법령명":       법령명,
            "법령일련번호": mst,
            "시행일자":     시행일자,
            "수집일시":     datetime.now().isoformat(),
            "관련정책":     관련정책,
            "조문":         articles,
        }
        save_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[Law] 저장 완료: {save_path.name} ({len(articles)}개 조문)")
        return True


# ══════════════════════════════════════════════════════════════════════════
# 단독 실행
# ══════════════════════════════════════════════════════════════════════════

def run():
    import os
    from dotenv import load_dotenv
    load_dotenv()

    oc = os.getenv("LAW_API_OC", "")
    if not oc:
        logger.error("LAW_API_OC 환경변수를 설정하세요.")
        return

    # 가장 최신 top5 CSV 사용
    meta_dir  = DATA_DIR / "metadata"
    top5_files = sorted(meta_dir.glob("gov24_top5_*.csv"), reverse=True)
    if not top5_files:
        logger.error("[Law] top5 CSV 파일 없음. gov24 수집 먼저 실행하세요.")
        return

    top5_csv = top5_files[0]
    logger.info(f"[Law] top5 파일: {top5_csv.name}")

    collector = LawCollector(oc=oc)
    collector.collect_from_top5(top5_csv)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    run()