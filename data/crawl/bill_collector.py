"""
열린국회정보 법안 데이터 수집기

실행:
  python bill_collector.py          # 전체 파이프라인
  python bill_collector.py --보완   # 제안이유 미수집분 보완만
  python bill_collector.py --청년   # 청년 필터링 + export만 재실행

변경 이력:
  - step2: 번호 범위 상수(ASSEMBLY_AGE / BILL_NO_START)를 상단에 명시적으로 분리
  - step3: 법안 하나씩 API 호출 → 페이지 단위 bulk 수집으로 변경
  - step4: 처리현황 업데이트 범위를 "신규 법안"으로 한정 (전체 루프 제거)
  - step5: rglob 반복 탐색 → file_path 컬럼 직접 참조
  - step6: 청년 필터링 완료 후 youth_bills.csv + filtered/ 폴더 자동 export (신규)
  - step7: 청년 법안 카테고리 분류 (일자리/주거/금융/복지/교육문화/참여) (신규)
  - raw_bills.csv: pdf_path, category 컬럼 추가
"""
import sys
import json
import time
import re
import shutil
import logging
import argparse
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

from config import (
    ASSEMBLY_API_KEY,
    OPENAI_API_KEY,
    DATA_DIR,
    LOG_DIR,
)
from openai import OpenAI

logger = logging.getLogger(__name__)

# ── GPT 청년 필터링 설정 ────────────────────────────────────────────────────
BATCH_SIZE = 50
GPT_MODEL  = "gpt-4o-mini"

# ── 설정 ───────────────────────────────────────────────────────────────────
BASE_URL  = "https://open.assembly.go.kr/portal/openapi"
PAL_BASE  = "https://pal.assembly.go.kr"
PAGE_SIZE = 100
MAX_RETRY = 3

# ── 국회 대수 설정 (새 대 시작 시 두 값 모두 수정) ──────────────────────
ASSEMBLY_AGE    = 22          # 현재 국회 대수
BILL_NO_START   = 2200001     # 해당 대 첫 번째 법안 번호

TODAY         = datetime.now().strftime("%Y-%m-%d")
RAW_BILLS_DIR = DATA_DIR / "raw" / "bills"          # 날짜 서브폴더는 save_raw_json 안에서 생성
RAW_DIR       = RAW_BILLS_DIR / TODAY
PDF_DIR       = DATA_DIR / "raw" / "bills_pdf"
META_DIR      = DATA_DIR / "metadata"
FILTERED_DIR  = DATA_DIR / "filtered" / "youth_bills"
LOG_FILE      = LOG_DIR / f"failure_bill_{TODAY}.jsonl"
CSV_RAW_BILLS = META_DIR / "raw_bills.csv"
CSV_YOUTH     = META_DIR / "youth_bills.csv"

for d in [RAW_DIR, PDF_DIR, META_DIR, LOG_DIR,
          FILTERED_DIR / "json", FILTERED_DIR / "pdf"]:
    d.mkdir(parents=True, exist_ok=True)

pal_session = requests.Session()
pal_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})


# ══════════════════════════════════════════════════════════════════════════
# 공통 함수
# ══════════════════════════════════════════════════════════════════════════

def api_get(endpoint: str, params: dict) -> dict:
    url  = f"{BASE_URL}/{endpoint}"
    base = {"KEY": ASSEMBLY_API_KEY, "Type": "json"}
    base.update(params)
    last_exc = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.get(url, params=base, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if "RESULT" in data:
                code = data["RESULT"].get("CODE", "")
                if code == "INFO-200":   # 결과 없음 (정상)
                    return {}
                raise ValueError(f"API 오류 [{code}]: {data['RESULT'].get('MESSAGE')}")
            return data
        except Exception as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"[{endpoint}] {MAX_RETRY}회 모두 실패: {last_exc}")


def parse_rows(data: dict, endpoint: str) -> list:
    wrapper = data.get(endpoint, [])
    return wrapper[1].get("row", []) if len(wrapper) >= 2 else []


def total_count(data: dict, endpoint: str) -> int:
    wrapper = data.get(endpoint, [])
    if not wrapper:
        return 0
    for item in wrapper[0].get("head", []):
        if "list_total_count" in item:
            return item["list_total_count"]
    return 0


def parse_date(value) -> str:
    if not value:
        return ""
    v = str(value).replace("-", "").strip()
    try:
        return datetime.strptime(v, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return str(value)


def save_raw_json(bill_no: str, api_row: dict) -> str:
    """JSON 저장 후 data/ 기준 상대 경로 반환"""
    payload   = {"collected_at": datetime.now().isoformat(), "bill_no": bill_no, "raw": api_row}
    file_path = RAW_DIR / f"{bill_no}.json"
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"raw/bills/{TODAY}/{bill_no}.json"


def write_failure_log(target: str, reason: str) -> None:
    entry = {"failed_at": datetime.now().isoformat(), "target": target, "reason": reason}
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_csv(path: Path, dtypes: dict = None) -> pd.DataFrame:
    return pd.read_csv(path, dtype=dtypes or {}) if path.exists() else pd.DataFrame()


def save_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def merge_and_save_csv(existing_path: Path, new_df: pd.DataFrame, dedup_col: str) -> pd.DataFrame:
    existing = load_csv(existing_path, {dedup_col: str})
    if new_df.empty:
        return existing
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=[dedup_col], keep="first")
    save_csv(combined, existing_path)
    logger.info("%s → 총 %d건 (신규 %d건)", existing_path.name, len(combined), len(new_df))
    return combined


def fetch_pal_page(bill_id: str) -> dict:
    url  = f"{PAL_BASE}/napal/search/lgsltpaSearch/view.do?lgsltPaId={bill_id}"
    resp = pal_session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    reason_text = ""
    desc_div = soup.select_one("div.item div.desc")
    if desc_div:
        for br in desc_div.find_all("br"):
            br.replace_with("\n")
        reason_text = desc_div.get_text(separator="\n", strip=True)
    if not reason_text:
        return {}

    book_id = pdf_url = hwp_url = ""
    for a in soup.select("a[href*='filegate']"):
        href = a["href"]
        m = re.search(
            r'bookId=([A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12})',
            href, re.IGNORECASE
        )
        if m:
            book_id = m.group(1)
        if href.startswith("https://likms"):
            if "type=1" in href and not pdf_url:
                pdf_url = href
            if "type=0" in href and not hwp_url:
                hwp_url = href

    return {"reason_text": reason_text, "book_id": book_id, "pdf_url": pdf_url, "hwp_url": hwp_url}


def download_pdf(pdf_url: str, bill_no: str) -> str | None:
    if not pdf_url:
        return None
    file_path = PDF_DIR / f"{bill_no}.pdf"
    if file_path.exists() and file_path.stat().st_size > 1000:
        return f"raw/bills_pdf/{bill_no}.pdf"
    try:
        resp = pal_session.get(pdf_url, timeout=30)
        if resp.status_code == 200 and len(resp.content) > 1000:
            file_path.write_bytes(resp.content)
            return f"raw/bills_pdf/{bill_no}.pdf"
    except Exception as e:
        logger.warning("[%s] PDF 다운로드 실패: %s", bill_no, e)
    return None


def _resolve_json_path(row: pd.Series) -> Path | None:
    """
    raw_bills.csv의 file_path 컬럼으로 JSON 경로 직접 해석.
    컬럼이 없거나 파일이 없으면 None 반환.
    """
    fp = str(row.get("file_path", "")).strip()
    if not fp:
        return None
    full = DATA_DIR / fp
    return full if full.exists() else None


# ══════════════════════════════════════════════════════════════════════════
# 파이프라인 단계
# ══════════════════════════════════════════════════════════════════════════

def step1_show_status():
    """현황 출력"""
    bills_df = load_csv(CSV_RAW_BILLS, {"bill_num": str})
    logger.info("현황 — raw_bills: %d건", len(bills_df))

    all_json   = list(RAW_BILLS_DIR.rglob("*.json"))
    has_reason = sum(1 for f in all_json if json.loads(f.read_text(encoding="utf-8")).get("reason_text"))
    no_pal     = sum(1 for f in all_json if json.loads(f.read_text(encoding="utf-8")).get("no_pal"))
    logger.info(
        "JSON — 총 %d개 / 제안이유있음 %d개 / 입법예고없음 %d개 / 미확인 %d개",
        len(all_json), has_reason, no_pal, len(all_json) - has_reason - no_pal,
    )
    logger.info("PDF  — %d개", len(list(PDF_DIR.glob("*.pdf"))))
    return bills_df


def step2_find_missing(bills_df: pd.DataFrame) -> tuple[list, int]:
    """
    신규 수집이 필요한 법안 번호 목록 반환.
    번호 범위: BILL_NO_START ~ API 최신 번호 (상단 상수로 관리).
    새 대 시작 시 ASSEMBLY_AGE / BILL_NO_START 두 값만 수정하면 됨.
    """
    if not ASSEMBLY_API_KEY:
        logger.error("ASSEMBLY_API_KEY 환경변수를 설정하세요.")
        return [], ASSEMBLY_AGE

    r = requests.get(
        f"{BASE_URL}/TVBPMBILL11",
        params={"KEY": ASSEMBLY_API_KEY, "Type": "json",
                "pIndex": 1, "pSize": 1, "AGE": ASSEMBLY_AGE},
        timeout=15,
    )
    r.raise_for_status()
    wrapper   = r.json().get("TVBPMBILL11", [])
    api_total = wrapper[0]["head"][0]["list_total_count"]
    latest_no = int(wrapper[1]["row"][0]["BILL_NO"])
    logger.info("API 전체: %d건 / 최신 번호: %d", api_total, latest_no)

    existing_nums = set(bills_df["bill_num"].astype(str).tolist()) if not bills_df.empty else set()
    all_slots     = set(str(i) for i in range(BILL_NO_START, latest_no + 1))
    to_check      = sorted(all_slots - existing_nums, key=int)

    logger.info("확인 필요: %d건 (전체 슬롯 %d개 - 기존 %d건)",
                len(to_check), len(all_slots), len(existing_nums))
    return to_check, ASSEMBLY_AGE


# ── 수정 2: 페이지 단위 bulk 수집 ────────────────────────────────────────
def step3_collect(to_check: list, current_age: int):
    """
    신규 법안을 페이지 단위로 bulk 수집.
    to_check 번호 집합에 있는 것만 처리 → 불필요한 API 호출 제거.
    """
    to_check_set  = set(str(n) for n in to_check)
    new_raw_bills = []
    stats         = {"inserted": 0, "real_missing": 0, "failed": 0}
    page          = 1

    logger.info("[수집] 페이지 단위 bulk 수집 시작 (대상 %d건)", len(to_check_set))

    while True:
        try:
            data = api_get("TVBPMBILL11", {"pIndex": page, "pSize": PAGE_SIZE, "AGE": current_age})
        except RuntimeError as exc:
            write_failure_log(f"collect_page_{page}", str(exc))
            break

        rows = parse_rows(data, "TVBPMBILL11")
        if not rows:
            break

        for row in rows:
            bill_no = str(row.get("BILL_NO", "")).strip()
            if bill_no not in to_check_set:
                continue   # 이미 수집한 법안 스킵

            bill_id   = row.get("BILL_ID", "")
            bill_name = (row.get("BILL_NAME") or "").strip()

            try:
                file_path = save_raw_json(bill_no, row)
            except Exception as exc:
                write_failure_log(bill_no, f"JSON 저장 실패: {exc}")
                stats["failed"] += 1
                continue

            pdf_path = None
            if bill_id:
                try:
                    pal = fetch_pal_page(bill_id)
                    json_file = RAW_DIR / f"{bill_no}.json"
                    saved     = json.loads(json_file.read_text(encoding="utf-8"))
                    if pal:
                        pdf_path = download_pdf(pal["pdf_url"], bill_no)
                        saved.update({**pal, "pdf_path": pdf_path})
                    else:
                        saved["no_pal"] = True
                    json_file.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as exc:
                    logger.warning("[%s] pal 실패: %s", bill_no, exc)
                time.sleep(0.5)

            new_raw_bills.append({
                **row,                                              # API 응답 전체
                "bill_num":       bill_no,                          # 정규화 키
                "proposed_at":    parse_date(row.get("PROPOSE_DT")), # 날짜 가공
                "process_status": row.get("PASS_GUBUN", ""),
                "is_youth_related": 0,
                "file_path":      file_path,
                "pdf_path":       pdf_path or "",
            })

            stats["inserted"] += 1
            logger.info("[수집] %s — %s", bill_no, bill_name[:40])
            time.sleep(0.3)

        collected_so_far = page * PAGE_SIZE
        logger.info("[수집] 페이지 %d 완료 (누적 검토 %d건 / 삽입 %d건)",
                    page, collected_so_far, stats["inserted"])

        if collected_so_far >= total_count(data, "TVBPMBILL11"):
            break
        page += 1

    logger.info("수집 완료 → 삽입:%d / 실패:%d", stats["inserted"], stats["failed"])
    return pd.DataFrame(new_raw_bills)


# ── 수정 3: 처리현황 업데이트 범위를 신규 법안으로 한정 ──────────────────
def step4_save_and_update(new_bills_df: pd.DataFrame, current_age: int):
    """
    raw_bills.csv 저장 + proposer_kind 보정 + 신규 법안 처리현황 최신화.
    기존에는 전체 API를 루프했지만, 신규 법안 번호만 조회하도록 변경.
    """
    final_bills = merge_and_save_csv(CSV_RAW_BILLS, new_bills_df, "bill_num")

    # proposer_kind 보정 (빈 값만)
    if not final_bills.empty and "proposer_kind" in final_bills.columns:
        mask = final_bills["proposer_kind"].isna() | (final_bills["proposer_kind"].astype(str).str.strip() == "")
        final_bills.loc[mask & (final_bills["proposer"].astype(str).str.strip() == "정부"), "proposer_kind"] = "정부"
        final_bills.loc[mask & (final_bills["proposer"].astype(str).str.strip() != "정부"), "proposer_kind"] = "의원"
        save_csv(final_bills, CSV_RAW_BILLS)

    if new_bills_df.empty:
        logger.info("[step4] 신규 법안 없음 — 처리현황 업데이트 생략")
        return final_bills

    # 신규 법안 번호만 처리현황 업데이트
    new_bill_nums = set(new_bills_df["bill_num"].astype(str).tolist())
    idx_map       = {str(r["bill_num"]): i for i, r in final_bills.iterrows()}
    updated       = 0
    page          = 1

    while True:
        try:
            data = api_get("nzpltgfqabtcpsmai", {"pIndex": page, "pSize": PAGE_SIZE, "AGE": current_age})
        except RuntimeError as exc:
            write_failure_log(f"done_page_{page}", str(exc))
            break

        rows = parse_rows(data, "nzpltgfqabtcpsmai")
        if not rows:
            break

        for row in rows:
            bill_no    = (row.get("BILL_NO") or "").strip()
            new_status = (row.get("PROC_RESULT") or "").strip()
            # 신규 법안이면서 처리결과가 있는 것만 업데이트
            if not new_status or bill_no not in new_bill_nums or bill_no not in idx_map:
                continue
            i = idx_map[bill_no]
            if final_bills.at[i, "process_status"] != new_status:
                final_bills.at[i, "process_status"] = new_status
                updated += 1

        if page * PAGE_SIZE >= total_count(data, "nzpltgfqabtcpsmai"):
            break
        page += 1

    if updated:
        save_csv(final_bills, CSV_RAW_BILLS)
    logger.info("처리현황 %d건 변경 / raw_bills %d행", updated, len(final_bills))
    return final_bills


# ══════════════════════════════════════════════════════════════════════════
# GPT 청년 법안 필터링
# ══════════════════════════════════════════════════════════════════════════

def _gpt_youth_bills_batch(rows: list[dict]) -> list[bool]:
    """GPT로 배치 단위 청년 관련 법안 분류"""
    client = OpenAI(api_key=OPENAI_API_KEY)

    system_prompt = (
        "당신은 한국 법안 데이터를 분류하는 전문가입니다.\n"
        "아래 JSON 배열의 각 항목이 청년(만 19세~34세)이 적용 대상에 포함되는 법안인지 판별하세요.\n"
        "true 기준:\n"
        "  - 제목 또는 제안이유에 청년, 청년기본, 청년고용, 청년주거, 청년창업, 사회초년생, 취업준비생 등이 명시된 경우\n"
        "  - 청년이 다른 계층과 함께 포함된 경우도 true\n"
        "  - 신혼부부, 1인가구, 무주택자 등 청년이 실질적으로 해당되는 대상도 true\n"
        "false 기준:\n"
        "  - 적용 대상이 아동, 청소년(만 18세 이하), 노인으로만 구성된 경우\n"
        "  - 청년이 적용 대상에 전혀 언급되지 않은 경우\n\n"
        "반드시 다음 형식의 JSON 객체만 반환하세요:\n"
        '{"results": [{"idx": 0, "is_youth": true}, {"idx": 1, "is_youth": false}, ...]}'
    )

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": json.dumps(rows, ensure_ascii=False)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw).get("results", [])
    except json.JSONDecodeError:
        logger.warning("[BillFilter] GPT 파싱 실패: %s", raw[:200])
        return [False] * len(rows)

    idx_map = {
        item["idx"]: item["is_youth"]
        for item in parsed
        if "idx" in item and "is_youth" in item
    }
    missing = [r["idx"] for r in rows if r["idx"] not in idx_map]
    if missing:
        logger.warning("[BillFilter] 누락 idx %s → False 처리", missing)

    return [idx_map.get(r["idx"], False) for r in rows]


# ── 수정 4: rglob 반복 탐색 제거 → file_path 컬럼 직접 참조 ─────────────
def step5_filter_youth() -> None:
    """
    raw_bills.csv 전체를 대상으로 GPT 청년 필터링 실행.
    is_youth_related == 0 인 것만 처리.
    JSON 경로는 file_path 컬럼으로 직접 참조 (rglob 제거).
    """
    if not OPENAI_API_KEY:
        logger.error("[BillFilter] OPENAI_API_KEY 없음 — 청년 필터링 건너뜀")
        return

    bills_df = load_csv(CSV_RAW_BILLS, {"bill_num": str})
    if bills_df.empty:
        logger.warning("[BillFilter] raw_bills.csv 없음")
        return

    if "is_youth_related" not in bills_df.columns:
        bills_df["is_youth_related"] = 0

    pending = bills_df[bills_df["is_youth_related"] == 0].copy()
    if pending.empty:
        logger.info("[BillFilter] 필터링할 신규 법안 없음")
        return

    logger.info("[BillFilter] GPT 청년 필터링 시작: %d건 (배치 %d개씩)", len(pending), BATCH_SIZE)

    youth_indices = []
    total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_no, start in enumerate(range(0, len(pending), BATCH_SIZE), 1):
        chunk = pending.iloc[start: start + BATCH_SIZE]
        rows  = []

        for i, (_, row) in enumerate(chunk.iterrows()):
            title  = str(row.get("BILL_NAME", ""))[:150]
            reason = ""
            jp     = _resolve_json_path(row)   # ← rglob 대신 직접 경로
            if jp:
                try:
                    data   = json.loads(jp.read_text(encoding="utf-8"))
                    reason = str(data.get("reason_text", ""))[:300]
                except Exception:
                    pass
            rows.append({"idx": i, "title": title, "reason": reason})

        try:
            flags = _gpt_youth_bills_batch(rows)
        except Exception as e:
            logger.warning("[BillFilter] 배치 %d 오류: %s → 모두 False", batch_no, e)
            flags = [False] * len(rows)

        if len(flags) != len(rows):
            flags = (flags + [False] * len(rows))[:len(rows)]

        for flag, (_, row) in zip(flags, chunk.iterrows()):
            if flag:
                youth_indices.append(row.name)

        logger.info("[BillFilter] 배치 %d/%d 완료 | 누적 청년 법안: %d건",
                    batch_no, total_batches, len(youth_indices))
        time.sleep(0.3)

    bills_df.loc[youth_indices, "is_youth_related"] = 1
    save_csv(bills_df, CSV_RAW_BILLS)

    total_youth = int(bills_df["is_youth_related"].sum())
    logger.info("[BillFilter] 완료 — 이번 신규 청년 법안: %d건 / 전체 누적: %d건",
                len(youth_indices), total_youth)


# ── 신규 step6: 청년 법안 export ─────────────────────────────────────────
def step6_export_youth() -> None:
    """
    raw_bills.csv에서 is_youth_related == 1 인 행을 뽑아:
      1. data/metadata/youth_bills.csv 저장
      2. data/filtered/youth_bills/json/  에 JSON 복사
      3. data/filtered/youth_bills/pdf/   에 PDF 복사 (있는 경우)

    매 실행마다 증분으로 동작 — 이미 복사된 파일은 스킵.
    """
    bills_df = load_csv(CSV_RAW_BILLS, {"bill_num": str})
    if bills_df.empty:
        logger.warning("[Export] raw_bills.csv 없음")
        return

    if "is_youth_related" not in bills_df.columns:
        logger.warning("[Export] is_youth_related 컬럼 없음 — 필터링 먼저 실행하세요")
        return

    youth_df = bills_df[bills_df["is_youth_related"] == 1].copy()
    if youth_df.empty:
        logger.info("[Export] 청년 법안 없음")
        return

    # 1. youth_bills.csv 저장
    save_csv(youth_df, CSV_YOUTH)
    logger.info("[Export] youth_bills.csv → %d건", len(youth_df))

    json_dst = FILTERED_DIR / "json"
    pdf_dst  = FILTERED_DIR / "pdf"
    stats    = {"json_copied": 0, "json_skip": 0, "json_missing": 0,
                "pdf_copied": 0, "pdf_skip": 0, "pdf_missing": 0}

    for _, row in youth_df.iterrows():
        bill_no = str(row.get("bill_num", "")).strip()

        # 2. JSON 복사
        src_json = _resolve_json_path(row)
        dst_json = json_dst / f"{bill_no}.json"
        if dst_json.exists():
            stats["json_skip"] += 1
        elif src_json and src_json.exists():
            shutil.copy2(src_json, dst_json)
            stats["json_copied"] += 1
        else:
            logger.debug("[Export] JSON 원본 없음: %s", bill_no)
            stats["json_missing"] += 1

        # 3. PDF 복사
        pdf_path_rel = str(row.get("pdf_path", "")).strip()
        dst_pdf      = pdf_dst / f"{bill_no}.pdf"
        if dst_pdf.exists():
            stats["pdf_skip"] += 1
        elif pdf_path_rel:
            src_pdf = DATA_DIR / pdf_path_rel
            if src_pdf.exists():
                shutil.copy2(src_pdf, dst_pdf)
                stats["pdf_copied"] += 1
            else:
                stats["pdf_missing"] += 1
        # pdf_path 자체가 없으면 카운트하지 않음 (원래 PDF 없는 법안)

    logger.info(
        "[Export] JSON — 복사:%d / 스킵:%d / 원본없음:%d",
        stats["json_copied"], stats["json_skip"], stats["json_missing"],
    )
    logger.info(
        "[Export] PDF  — 복사:%d / 스킵:%d / 원본없음:%d",
        stats["pdf_copied"], stats["pdf_skip"], stats["pdf_missing"],
    )


# ══════════════════════════════════════════════════════════════════════════
# 청년 법안 카테고리 분류
# ══════════════════════════════════════════════════════════════════════════

BILL_CATEGORIES = ["일자리", "주거", "금융", "복지", "교육문화", "참여", "기타"]


def _gpt_categorize_bills_batch(rows: list[dict]) -> list[str]:
    """
    GPT로 청년 법안 배치 단위 카테고리 분류.
    각 항목에 대해 BILL_CATEGORIES 중 하나를 반환.
    """
    client   = OpenAI(api_key=OPENAI_API_KEY)
    cats_str = " / ".join(BILL_CATEGORIES)

    system_prompt = (
        "당신은 한국 국회 법안 데이터를 분류하는 전문가입니다.\n"
        f"아래 JSON 배열의 각 법안을 다음 카테고리 중 하나로 분류하세요.\n"
        f"카테고리: {cats_str}\n\n"
        "카테고리 기준:\n"
        "  일자리:   청년고용, 취업지원, 직업훈련, 인턴십, 창업지원, 고용장려금, 실업급여\n"
        "  주거:     청년주택, 전세자금, 월세지원, 주거급여, 임대주택, 부동산\n"
        "  금융:     대출, 저축, 금리우대, 신용지원, 장학금(생활비), 채무\n"
        "  복지:     의료, 심리상담, 생활지원, 긴급복지, 사회서비스, 돌봄\n"
        "  교육문화: 교육비지원, 자격증, 문화·여가, 도서관, 예술, 학습\n"
        "  참여:     봉사활동, 청년위원회, 정책참여, 해외교류, 리더십\n"
        "  기타:     위 카테고리에 해당하지 않는 경우\n\n"
        "반드시 다음 형식의 JSON 객체만 반환하세요:\n"
        '{"results": [{"idx": 0, "category": "일자리"}, {"idx": 1, "category": "주거"}, ...]}'
    )

    response = OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": json.dumps(rows, ensure_ascii=False)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw).get("results", [])
    except json.JSONDecodeError:
        logger.warning("[Categorize] GPT 파싱 실패: %s", raw[:200])
        return ["기타"] * len(rows)

    idx_map = {
        item["idx"]: item.get("category", "기타")
        for item in parsed
        if "idx" in item
    }
    missing = [r["idx"] for r in rows if r["idx"] not in idx_map]
    if missing:
        logger.warning("[Categorize] 누락 idx %s → 기타 처리", missing)

    return [idx_map.get(r["idx"], "기타") for r in rows]


def step7_categorize_youth() -> None:
    """
    youth_bills.csv (is_youth_related == 1) 중 category가 없는 것만 GPT로 분류.
    raw_bills.csv와 youth_bills.csv 양쪽 모두 category 컬럼 갱신.
    증분 동작 — 이미 분류된 법안은 스킵.
    """
    if not OPENAI_API_KEY:
        logger.error("[Categorize] OPENAI_API_KEY 없음 — 카테고리 분류 건너뜀")
        return

    bills_df = load_csv(CSV_RAW_BILLS, {"bill_num": str})
    if bills_df.empty:
        logger.warning("[Categorize] raw_bills.csv 없음")
        return

    if "category" not in bills_df.columns:
        bills_df["category"] = ""

    # 청년 법안 중 아직 카테고리 없는 것만
    pending = bills_df[
        (bills_df["is_youth_related"] == 1) &
        (bills_df["category"].isna() | (bills_df["category"].astype(str).str.strip() == ""))
    ].copy()

    if pending.empty:
        logger.info("[Categorize] 분류할 신규 청년 법안 없음")
        return

    logger.info("[Categorize] GPT 카테고리 분류 시작: %d건 (배치 %d개씩)", len(pending), BATCH_SIZE)

    total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE
    result_map    = {}   # row.name → category

    for batch_no, start in enumerate(range(0, len(pending), BATCH_SIZE), 1):
        chunk = pending.iloc[start: start + BATCH_SIZE]
        rows  = []

        for i, (_, row) in enumerate(chunk.iterrows()):
            title  = str(row.get("BILL_NAME", ""))[:150]
            reason = ""
            jp     = _resolve_json_path(row)
            if jp:
                try:
                    data   = json.loads(jp.read_text(encoding="utf-8"))
                    reason = str(data.get("reason_text", ""))[:300]
                except Exception:
                    pass
            rows.append({"idx": i, "title": title, "reason": reason})

        try:
            categories = _gpt_categorize_bills_batch(rows)
        except Exception as e:
            logger.warning("[Categorize] 배치 %d 오류: %s → 모두 기타", batch_no, e)
            categories = ["기타"] * len(rows)

        if len(categories) != len(rows):
            categories = (categories + ["기타"] * len(rows))[:len(rows)]

        for cat, (_, row) in zip(categories, chunk.iterrows()):
            result_map[row.name] = cat

        logger.info("[Categorize] 배치 %d/%d 완료", batch_no, total_batches)
        time.sleep(0.3)

    # raw_bills.csv 갱신
    for idx, cat in result_map.items():
        bills_df.at[idx, "category"] = cat
    save_csv(bills_df, CSV_RAW_BILLS)

    # youth_bills.csv도 갱신
    if CSV_YOUTH.exists():
        youth_df = load_csv(CSV_YOUTH, {"bill_num": str})
        if "category" not in youth_df.columns:
            youth_df["category"] = ""
        bill_to_cat = {str(bills_df.at[idx, "bill_num"]): cat for idx, cat in result_map.items()}
        youth_df["category"] = youth_df.apply(
            lambda r: bill_to_cat.get(str(r["bill_num"]), r.get("category", "")),
            axis=1,
        )
        save_csv(youth_df, CSV_YOUTH)

    # 카테고리별 건수 로그
    cat_counts = bills_df[bills_df["is_youth_related"] == 1]["category"].value_counts()
    logger.info("[Categorize] 완료 — 이번 분류: %d건", len(result_map))
    for cat, cnt in cat_counts.items():
        logger.info("    %s: %d건", cat, cnt)


# ══════════════════════════════════════════════════════════════════════════
# 보완 모드
# ══════════════════════════════════════════════════════════════════════════

def step_supplement_pal():
    """기존 법안 중 제안이유 미수집분 보완"""
    all_json_files = sorted(RAW_BILLS_DIR.rglob("*.json"))
    stats = {"done": 0, "skip_done": 0, "skip_no_pal": 0, "fail": 0}

    for jf in all_json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        if data.get("reason_text"):
            stats["skip_done"] += 1
            continue
        if data.get("no_pal"):
            stats["skip_no_pal"] += 1
            continue

        bill_no = data["bill_no"]
        bill_id = data.get("raw", {}).get("BILL_ID", "")
        if not bill_id:
            stats["fail"] += 1
            continue

        try:
            pal = fetch_pal_page(bill_id)
        except Exception as exc:
            write_failure_log(bill_no, f"pal: {exc}")
            stats["fail"] += 1
            time.sleep(1)
            continue

        if not pal:
            data["no_pal"] = True
            jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            stats["skip_no_pal"] += 1
            time.sleep(0.3)
            continue

        pdf_path = download_pdf(pal["pdf_url"], bill_no)
        data.update({**pal, "pdf_path": pdf_path})
        jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        # raw_bills.csv pdf_path 컬럼도 동기화
        bills_df = load_csv(CSV_RAW_BILLS, {"bill_num": str})
        if not bills_df.empty and "pdf_path" in bills_df.columns:
            mask = bills_df["bill_num"].astype(str) == str(bill_no)
            if mask.any() and pdf_path:
                bills_df.loc[mask, "pdf_path"] = pdf_path
                save_csv(bills_df, CSV_RAW_BILLS)

        stats["done"] += 1
        logger.info("[PAL] %s — %d자 | PDF: %s", bill_no, len(pal["reason_text"]), "✅" if pdf_path else "❌")
        time.sleep(0.5)

    logger.info("보완 완료:%d / 이미수집:%d / 입법예고없음:%d / 실패:%d",
                stats["done"], stats["skip_done"], stats["skip_no_pal"], stats["fail"])


# ══════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════

def run():
    start = datetime.now()
    logger.info("=== 법안 수집 시작: %s ===", start.isoformat())

    bills_df            = step1_show_status()
    to_check, curr_age  = step2_find_missing(bills_df)

    if to_check:
        new_bills_df = step3_collect(to_check, curr_age)
        step4_save_and_update(new_bills_df, curr_age)
    else:
        logger.info("누락 법안 없음")

    step5_filter_youth()      # is_youth_related 갱신
    step6_export_youth()      # youth_bills.csv + filtered/ 폴더 export
    step7_categorize_youth()  # 청년 법안 카테고리 분류

    logger.info("=== 법안 수집 완료 (%.1f초) ===", (datetime.now() - start).total_seconds())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="열린국회정보 법안 수집기")
    parser.add_argument("--보완", action="store_true", dest="supplement",
                        help="제안이유 미수집분 보완만 실행")
    parser.add_argument("--청년", action="store_true", dest="youth_only",
                        help="청년 필터링 + export + 카테고리 분류만 재실행")
    args = parser.parse_args()

    if args.supplement:
        step_supplement_pal()
    elif args.youth_only:
        step5_filter_youth()
        step6_export_youth()
        step7_categorize_youth()
    else:
        run()