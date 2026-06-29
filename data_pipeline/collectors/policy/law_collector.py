"""
법제처 국가법령정보 API 수집기
- gov24 top5 CSV에서 법령 + 자치법규 필드 파싱
- 법제처 API로 해당 조문 수집 (항내용 + 호내용 포함)
- data/laws/법령명_일련번호.json 저장 (Qdrant 입력용)
- data/metadata/laws_meta_날짜.csv 저장 (확인용 — 실패 항목 포함)

저장 구조:
  data/
  ├── laws/
  │   ├── 청년기본법_277289.json
  │   └── ...
  └── metadata/
      └── laws_meta_날짜.csv

각 JSON:
  {
    "법령명": "청년기본법",
    "법령일련번호": "277289",
    "법령구분": "법령",
    "시행일자": "20251001",
    "수집일시": "2026-06-05T02:00:00",
    "관련정책": [
      {"서비스ID": "...", "서비스명": "청년 문화예술패스", "조문": "제23조"}
    ],
    "조문": [
      {"조문번호": "23", "조문제목": "청년 문화활동 지원", "조문내용": "...전체본문..."}
    ]
  }

laws_meta.csv 컬럼:
  서비스ID, 서비스명, 법령구분, 법령명, 조문번호, 조문제목,
  법령일련번호, 시행일자, 수집일시, 수집여부, 실패사유
"""
import csv
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import DATA_DIR, LAWS_RAW_DIR, LAWS_PROCESSED_DIR, LAWS_META_DIR, POLICY_META_DIR, REQUEST_TIMEOUT

LAWS_DIR = LAWS_RAW_DIR  # 개별 JSON 저장 위치

try:
    import pandas as pd
except ImportError:
    raise ImportError("pandas 필요: pip install pandas")

logger = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────────────────────────
LAW_SEARCH_URL  = "https://www.law.go.kr/DRF/lawSearch.do"
LAW_SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
META_DIR        = LAWS_META_DIR
REQUEST_DELAY   = 0.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
    "Referer":    "https://www.law.go.kr/",
}


class LawCollector:

    def __init__(self, oc: str):
        if not oc:
            raise ValueError("LAW_API_OC 환경변수를 설정하세요.")
        self.oc = oc
        LAWS_RAW_DIR.mkdir(parents=True, exist_ok=True)
        LAWS_META_DIR.mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════════════════════════════════════
    # 퍼블릭 메서드
    # ══════════════════════════════════════════════════════════════════════

    def collect_from_top5(self, top5_csv_path: Path) -> int:
        """
        gov24 top5 CSV → 법령 + 자치법규 파싱 → 조문 수집 → JSON/CSV 저장
        반환: 저장된 파일 수
        """
        if not top5_csv_path.exists():
            logger.error(f"[Law] top5 파일 없음: {top5_csv_path}")
            return 0

        df = pd.read_csv(top5_csv_path, dtype=str).fillna("")
        logger.info(f"[Law] top5 로드: {len(df)}건 ({top5_csv_path.name})")

        # 전체 정책 기준 meta_rows (실패 포함)
        all_meta_rows = []

        # 법령이 없는 정책도 먼저 null 행으로 등록
        for _, row in df.iterrows():
            서비스ID = row.get("서비스ID", "").strip()
            서비스명 = row.get("서비스명", "").strip()
            법령_raw = row.get("법령", "").strip()
            조례_raw = row.get("자치법규", "").strip()

            if not 법령_raw and not 조례_raw:
                all_meta_rows.append(self._null_row(서비스ID, 서비스명, "", "", "법령없음"))

        # 법령 map: {법령명: {조문목록, 관련정책, 법령구분}}
        law_map: dict[str, dict] = {}

        for _, row in df.iterrows():
            서비스ID = row.get("서비스ID", "").strip()
            서비스명 = row.get("서비스명", "").strip()

            for raw, 구분 in [(row.get("법령", "").strip(), "법령"),
                              (row.get("자치법규", "").strip(), "자치법규")]:
                if not raw:
                    continue
                for law_name, jo_num in self._parse_law_field(raw):
                    if law_name not in law_map:
                        law_map[law_name] = {
                            "조문목록": [],
                            "관련정책": [],
                            "법령구분": 구분,
                        }
                    law_map[law_name]["조문목록"].append((jo_num, 서비스ID, 서비스명))
                    policy_entry = {
                        "서비스ID": 서비스ID,
                        "서비스명": 서비스명,
                        "조문": f"제{jo_num}조" if jo_num else "전체",
                    }
                    if policy_entry not in law_map[law_name]["관련정책"]:
                        law_map[law_name]["관련정책"].append(policy_entry)

        logger.info(f"[Law] 파싱된 법령/자치법규 수: {len(law_map)}개")

        saved = 0
        for law_name, info in law_map.items():
            result, rows = self._collect_one(law_name, info)
            if result:
                saved += 1
            all_meta_rows.extend(rows)
            time.sleep(REQUEST_DELAY)

        # CSV 저장 (확인용 — 실패 포함)
        self._save_meta_csv(all_meta_rows, top5_csv_path)

        # 개별 JSON 묶기 → law_grouped.json
        self._save_grouped()

        self._save_grouped()
        logger.info(f"[Law] 수집 완료: {saved}/{len(law_map)}개 저장")
        return saved

    def _save_grouped(self) -> None:
        """raw/ 개별 JSON → processed/law_grouped.json"""
        law_files = sorted(LAWS_RAW_DIR.glob("*.json"))
        if not law_files:
            logger.warning("[Law] grouped 저장 생략 — raw JSON 없음")
            return
        laws = []
        for fp in law_files:
            try:
                laws.append(json.loads(fp.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning(f"[Law] {fp.name} 읽기 실패: {e}")
        out_path = LAWS_PROCESSED_DIR / "law_grouped.json"
        out_path.write_text(json.dumps(laws, ensure_ascii=False, indent=2), encoding="utf-8")
        total_arts = sum(len(l.get("조문", [])) for l in laws)
        logger.info(f"[Law] law_grouped.json → {len(laws)}개 법령 / {total_arts}개 조문")

    def collect_and_map(self, top5_csv_path: Path) -> dict:
        """
        collect_from_top5와 동일하게 법령 수집하되,
        {서비스ID: [법령데이터, ...]} 형태로 반환.
        policy_pipeline에서 top5 JSON에 병합하는 용도.
        """
        if not top5_csv_path.exists():
            logger.error(f"[Law] top5 파일 없음: {top5_csv_path}")
            return {}

        df = pd.read_csv(top5_csv_path, dtype=str).fillna("")

        law_map: dict[str, dict] = {}
        for _, row in df.iterrows():
            서비스ID = row.get("서비스ID", "").strip()
            서비스명 = row.get("서비스명", "").strip()
            for raw, 구분 in [(row.get("법령", "").strip(), "법령"),
                              (row.get("자치법규", "").strip(), "자치법규")]:
                if not raw:
                    continue
                for law_name, jo_num in self._parse_law_field(raw):
                    if law_name not in law_map:
                        law_map[law_name] = {"조문목록": [], "관련정책": [], "법령구분": 구분}
                    law_map[law_name]["조문목록"].append((jo_num, 서비스ID, 서비스명))
                    entry = {"서비스ID": 서비스ID, "서비스명": 서비스명,
                             "조문": f"제{jo_num}조" if jo_num else "전체"}
                    if entry not in law_map[law_name]["관련정책"]:
                        law_map[law_name]["관련정책"].append(entry)

        # 서비스ID → 법령 리스트 매핑
        service_law_map: dict[str, list] = {}

        for law_name, info in law_map.items():
            구분 = info["법령구분"]
            관련정책 = info["관련정책"]
            jo_nums = [jo for jo, _, _ in info["조문목록"]]

            if 구분 == "법령":
                search_result = self._search_law(law_name)
                mst_key, name_key = "법령일련번호", "법령명한글"
            else:
                search_result = self._search_ordin(law_name)
                mst_key, name_key = "자치법규일련번호", "자치법규명"

            if not search_result:
                logger.warning(f"[Law/map] 검색 실패: '{law_name}'")
                time.sleep(REQUEST_DELAY)
                continue

            mst    = search_result.get(mst_key, "")
            법령명 = search_result.get(name_key, law_name)
            시행일 = search_result.get("시행일자", "")

            # 기존 저장 파일 있으면 재활용
            safe_name = 법령명.replace("/", "_").replace(" ", "_")
            save_path = LAWS_DIR / f"{safe_name}_{mst}.json"
            if save_path.exists():
                try:
                    data = json.loads(save_path.read_text(encoding="utf-8"))
                except Exception:
                    data = None
            else:
                time.sleep(REQUEST_DELAY)
                if 구분 == "법령":
                    articles = self._fetch_law_articles(mst, jo_nums)
                else:
                    articles = self._fetch_ordin_articles(mst, jo_nums)

                if not articles:
                    logger.warning(f"[Law/map] 조문 없음: '{법령명}'")
                    time.sleep(REQUEST_DELAY)
                    continue

                data = {
                    "법령명":       법령명,
                    "법령일련번호": mst,
                    "법령구분":     구분,
                    "시행일자":     시행일,
                    "수집일시":     datetime.now().isoformat(),
                    "관련정책":     관련정책,
                    "조문":         articles,
                }
                save_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info(f"[Law/map] 저장: {save_path.name}")

            if data:
                for p in 관련정책:
                    sid = p["서비스ID"]
                    if sid not in service_law_map:
                        service_law_map[sid] = []
                    # 중복 방지
                    if not any(l.get("법령일련번호") == mst for l in service_law_map[sid]):
                        law_entry = {
                            "법령명":       data["법령명"],
                            "법령일련번호": data["법령일련번호"],
                            "법령구분":     data["법령구분"],
                            "시행일자":     data["시행일자"],
                            "수집일시":     data["수집일시"],
                            "관련조문":     p["조문"],
                            "원본파일명":   save_path.name,
                            "조문수":       len(data.get("조문", [])),
                        }
                        service_law_map[sid].append(law_entry)

            time.sleep(REQUEST_DELAY)

        # 개별 JSON 묶기 → law_grouped.json
        self._save_grouped()

        logger.info(f"[Law/map] 서비스ID {len(service_law_map)}개에 법령 매핑 완료")
        return service_law_map

    # ══════════════════════════════════════════════════════════════════════
    # 파싱
    # ══════════════════════════════════════════════════════════════════════

    def _parse_law_field(self, raw: str) -> list[tuple[str, str]]:
        results = []
        for part in raw.split("||"):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(.+?)\(제(\d+)조(의\d+)?", part)
            if m:
                law_name = m.group(1).strip()
                jo_num   = m.group(2) + (m.group(3) or "")
            else:
                law_name = re.sub(r"\(.*?\)", "", part).strip()
                jo_num   = ""
            if law_name:
                results.append((law_name, jo_num))
        return results

    # ══════════════════════════════════════════════════════════════════════
    # 본문 조합 (항내용 + 호내용 포함)
    # ══════════════════════════════════════════════════════════════════════

    def _build_content(self, a: dict) -> str:
        parts = []
        본문 = a.get("조문내용", "").strip()
        if 본문:
            parts.append(본문)
        항list = a.get("항", [])
        if isinstance(항list, dict):
            항list = [항list]
        for 항 in 항list:
            항내용 = 항.get("항내용", "").strip()
            if 항내용:
                parts.append(항내용)
            호list = 항.get("호", [])
            if isinstance(호list, dict):
                호list = [호list]
            for 호 in 호list:
                호내용 = 호.get("호내용", "").strip()
                if 호내용:
                    parts.append(호내용)
        return " ".join(parts)

    # ══════════════════════════════════════════════════════════════════════
    # 법령 API
    # ══════════════════════════════════════════════════════════════════════

    def _search_law(self, law_name: str) -> Optional[dict]:
        try:
            resp = requests.get(
                LAW_SEARCH_URL,
                params={"OC": self.oc, "target": "law", "type": "JSON",
                        "query": law_name, "display": 5},
                headers=HEADERS, timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            laws = resp.json().get("LawSearch", {}).get("law")
            if not laws:
                return None
            if isinstance(laws, dict):
                laws = [laws]
            # 법령명 정확히 일치하는 것 우선
            for law in laws:
                if law.get("법령명한글", "").strip() == law_name.strip():
                    return law
            # 없으면 첫 번째 반환
            return laws[0]
        except Exception as e:
            logger.error(f"[Law] 검색 오류 '{law_name}': {e}")
            return None

    def _fetch_law_articles(self, mst: str, jo_nums: list[str]) -> list[dict]:
        try:
            resp = requests.get(
                LAW_SERVICE_URL,
                params={"OC": self.oc, "target": "law", "MST": mst, "type": "JSON"},
                headers=HEADERS, timeout=REQUEST_TIMEOUT,
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

        target_nums = set(jo_nums) - {""}
        if target_nums:
            def match(a):
                번호 = str(a.get("조문번호", "")).strip()
                가지 = str(a.get("조문가지번호", "")).strip()
                full = f"{번호}의{가지}" if 가지 and 가지 not in ("0", "") else 번호
                return full in target_nums
            articles = [a for a in articles if match(a)]

        return [
            {
                "조문번호": str(a.get("조문번호", "")) + (
                    "의" + str(a.get("조문가지번호", ""))
                    if str(a.get("조문가지번호", "")) not in ("", "0") else ""
                ),
                "조문제목": a.get("조문제목", ""),
                "조문내용": self._build_content(a),
            }
            for a in articles
        ]

    # ══════════════════════════════════════════════════════════════════════
    # 자치법규 API
    # ══════════════════════════════════════════════════════════════════════

    def _search_ordin(self, law_name: str) -> Optional[dict]:
        try:
            resp = requests.get(
                LAW_SEARCH_URL,
                params={"OC": self.oc, "target": "ordin", "type": "JSON",
                        "query": law_name, "display": 5},
                headers=HEADERS, timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            laws = resp.json().get("OrdinSearch", {}).get("law")
            if not laws:
                return None
            if isinstance(laws, dict):
                laws = [laws]
            # 법령명 정확히 일치하는 것 우선
            for law in laws:
                if law.get("자치법규명", "").strip() == law_name.strip():
                    return law
            # 없으면 첫 번째 반환
            return laws[0]
        except Exception as e:
            logger.error(f"[Ordin] 검색 오류 '{law_name}': {e}")
            return None

    def _fetch_ordin_articles(self, mst: str, jo_nums: list[str]) -> list[dict]:
        try:
            resp = requests.get(
                LAW_SERVICE_URL,
                params={"OC": self.oc, "target": "ordin", "MST": mst, "type": "JSON"},
                headers=HEADERS, timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            raw_list = resp.json().get("LawService", {}).get("조문", {}).get("조", [])
            if isinstance(raw_list, dict):
                raw_list = [raw_list]
        except Exception as e:
            logger.error(f"[Ordin] 본문 조회 오류 MST={mst}: {e}")
            return []

        articles = []
        for item in raw_list:
            번호_raw = item.get("조문번호", "")
            if isinstance(번호_raw, list):
                번호_raw = 번호_raw[0]
            번호 = str(int(번호_raw[:4])) if 번호_raw else ""
            articles.append({
                "조문번호": 번호,
                "조문제목": item.get("조제목", ""),
                "조문내용": item.get("조내용", ""),
            })

        target_nums = set(jo_nums) - {""}
        if target_nums:
            articles = [a for a in articles if a["조문번호"] in target_nums]

        return articles

    # ══════════════════════════════════════════════════════════════════════
    # 수집 + 저장
    # ══════════════════════════════════════════════════════════════════════

    def _collect_one(self, law_name: str, info: dict) -> tuple[bool, list]:
        jo_nums  = [jo for jo, _, _ in info["조문목록"]]
        관련정책 = info["관련정책"]
        구분     = info["법령구분"]

        if 구분 == "법령":
            search_result = self._search_law(law_name)
            mst_key  = "법령일련번호"
            name_key = "법령명한글"
        else:
            search_result = self._search_ordin(law_name)
            mst_key  = "자치법규일련번호"
            name_key = "자치법규명"

        if not search_result:
            logger.warning(f"[{구분}] 검색 실패: '{law_name}'")
            rows = [
                self._null_row(p["서비스ID"], p["서비스명"], 구분, law_name, "검색실패")
                for p in 관련정책
            ]
            return False, rows

        mst    = search_result.get(mst_key, "")
        법령명 = search_result.get(name_key, law_name)
        시행일 = search_result.get("시행일자", "")

        safe_name = 법령명.replace("/", "_").replace(" ", "_")
        save_path = LAWS_DIR / f"{safe_name}_{mst}.json"

        # 기존 파일 있으면 관련정책만 업데이트
        if save_path.exists():
            existing = json.loads(save_path.read_text(encoding="utf-8"))
            for p in 관련정책:
                if p not in existing.get("관련정책", []):
                    existing.setdefault("관련정책", []).append(p)
            existing["수집일시"] = datetime.now().isoformat()
            save_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[{구분}] 관련정책 업데이트: {save_path.name}")
            return True, self._make_meta_rows(existing, 구분)

        # 조문 수집
        time.sleep(REQUEST_DELAY)
        if 구분 == "법령":
            articles = self._fetch_law_articles(mst, jo_nums)
        else:
            articles = self._fetch_ordin_articles(mst, jo_nums)

        if not articles:
            logger.warning(f"[{구분}] 조문 없음: '{법령명}'")
            rows = [
                self._null_row(p["서비스ID"], p["서비스명"], 구분, 법령명, "조문없음(데이터오류)")
                for p in 관련정책
            ]
            return False, rows

        data = {
            "법령명":       법령명,
            "법령일련번호": mst,
            "법령구분":     구분,
            "시행일자":     시행일,
            "수집일시":     datetime.now().isoformat(),
            "관련정책":     관련정책,
            "조문":         articles,
        }
        save_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[{구분}] 저장 완료: {save_path.name} ({len(articles)}개 조문)")
        return True, self._make_meta_rows(data, 구분)

    # ══════════════════════════════════════════════════════════════════════
    # CSV 행 생성
    # ══════════════════════════════════════════════════════════════════════

    def _make_meta_rows(self, data: dict, 구분: str) -> list[dict]:
        """수집 성공 — 정책x조문 조합으로 행 생성"""
        rows = []
        for policy in data.get("관련정책", []):
            for article in data.get("조문", []):
                rows.append({
                    "서비스ID":     policy.get("서비스ID", ""),
                    "서비스명":     policy.get("서비스명", ""),
                    "법령구분":     구분,
                    "법령명":       data.get("법령명", ""),
                    "조문번호":     article.get("조문번호", ""),
                    "조문제목":     article.get("조문제목", ""),
                    "법령일련번호": data.get("법령일련번호", ""),
                    "시행일자":     data.get("시행일자", ""),
                    "수집일시":     data.get("수집일시", ""),
                    "수집여부":     "성공",
                    "실패사유":     "",
                })
        return rows

    def _null_row(self, 서비스ID: str, 서비스명: str, 구분: str,
                  법령명: str, 실패사유: str) -> dict:
        """수집 실패 — null 행 생성"""
        return {
            "서비스ID":     서비스ID,
            "서비스명":     서비스명,
            "법령구분":     구분,
            "법령명":       법령명,
            "조문번호":     "",
            "조문제목":     "",
            "법령일련번호": "",
            "시행일자":     "",
            "수집일시":     datetime.now().isoformat(),
            "수집여부":     "실패",
            "실패사유":     실패사유,
        }

    def _save_meta_csv(self, rows: list[dict], top5_csv_path: Path):
        if not rows:
            return
        date_str  = top5_csv_path.stem.replace("gov24_top5_", "")
        csv_path  = META_DIR / f"laws_meta_{date_str}.csv"
        fieldnames = ["서비스ID", "서비스명", "법령구분", "법령명",
                      "조문번호", "조문제목", "법령일련번호", "시행일자",
                      "수집일시", "수집여부", "실패사유"]
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"[Law] 확인용 CSV 저장: {csv_path.name} ({len(rows)}행)")


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

    top5_files = sorted((LAWS_META_DIR.parent.parent / "policies" / "metadata").glob("gov24_top5_*.csv"), reverse=True)
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