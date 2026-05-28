from __future__ import annotations

import json
import random
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Set, List, Tuple, Any
import pandas as pd
import pdfplumber

# 경로 설정
BILL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BILL_DIR.parents[1] if len(BILL_DIR.parents) > 1 else BILL_DIR

# 스크립트와 같은 위치에 있는 data 폴더 지정
DATA_DIR = BILL_DIR / "data"
PDF_DIR = DATA_DIR / "bills_pdf_raw"
JSON_DIR = DATA_DIR / "bills_json_raw"
OUT_MD_DIR = DATA_DIR / "bills_markdown"
OUT_METADATA_CSV = DATA_DIR / "metadata_bills.csv"
PREVIEW_MD = DATA_DIR / "preview_sample.md"  # 미리보기 파일

# 디버그 모드 (상세한 로그 출력)
DEBUG_MODE = True

DATA_DIR.mkdir(parents=True, exist_ok=True)
PDF_DIR.mkdir(parents=True, exist_ok=True)
JSON_DIR.mkdir(parents=True, exist_ok=True)
OUT_MD_DIR.mkdir(parents=True, exist_ok=True)


def generate_data_id(existing_ids: Set[int]) -> int:
    """기존 ID와 중복되지 않는 랜덤 데이터 ID 생성 (10자리 정수)"""
    while True:
        new_id = random.randint(1000000000, 9999999999)
        if new_id not in existing_ids:
            existing_ids.add(new_id)
            return new_id


def to_relative_path(path: Path) -> str:
    """프로젝트 루트 기준 상대 경로 반환"""
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def clean_date(date_str: str) -> str:
    """날짜 형식 정리"""
    if not date_str or date_str == "null":
        return ""
    return str(date_str).strip()


def sanitize_filename(filename: str) -> str:
    """파일명에 사용할 수 없는 특수문자 제거"""
    return re.sub(r'[\/:*?"<>|]', '_', filename)


def format_date(date_str: str) -> str:
    """날짜 형식 변환: 2024. 6. 10. → 2024년 6월 10일 / 2024-05-31 -> 2024년 5월 31일"""
    if not date_str or not date_str.strip() or date_str == "null":
        return date_str
    
    date_str = date_str.strip()
    
    if '-' in date_str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return f"{dt.year}년 {dt.month}월 {dt.day}일"
        except ValueError:
            pass

    match = re.match(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*', date_str)
    if match:
        year, month, day = match.groups()
        return f"{year}년 {month}월 {day}일"
    
    match = re.match(r'(\d{4})\.\s*(\d{1,2})\.\s*\.\s*', date_str)
    if match:
        year, month = match.groups()
        return f"{year}년 {month}월"
    
    match = re.match(r'(\d{4})\.\s*(\d{1,2})\.\s*', date_str)
    if match:
        year, month = match.groups()
        return f"{year}년 {month}월"
    
    return date_str


def clean_bill_name(bill_name: str) -> str:
    """법률명 정리: 대표발의 괄호는 빼고, (일부개정법률안) 같은 법안 종류 괄호는 유지"""
    bill_name = re.sub(r'\([^)]*대표발의[^)]*\)', '', bill_name)
    bill_name = re.sub(r'\([^)]*의원\s+발의[^)]*\)', '', bill_name)
    bill_name = re.sub(r'\s+', ' ', bill_name)
    return bill_name.strip()


def parse_proposers(proposer_text: str) -> str:
    """발의자 이름을 콤마로 구분하여 추출"""
    if not proposer_text or proposer_text == "정부":
        return "정부"
        
    proposer_text = re.sub(r'의원.*$', '', proposer_text)
    proposer_text = re.sub(r'\([^)]*\)', '', proposer_text)
    proposer_text = proposer_text.replace("대표발의자", "").replace("대표발의", "")
    
    proposer_text = re.sub(r'[·,\s]+', ' ', proposer_text)
    names = re.findall(r'[가-힣]{2,4}', proposer_text)
    
    exclude = ['주요내용', '주오내용', '제안이유', '제안', '이유', '내용', 
               '법률', '개정', '일부', '부칙', '현행', '개정안', '발의',
               '연월일', '대표', '등', '의원', '제출자', '제출']
    
    filtered_names = []
    for name in names:
        if name not in exclude and not any(kw in name for kw in exclude):
            if name not in filtered_names:
                filtered_names.append(name)
    
    return ", ".join(filtered_names)


def load_json_metadata(bill_num: str) -> Dict:
    """JSON 파일에서 메타데이터 로드"""
    json_path = JSON_DIR / f"{bill_num}.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"   ⚠️ JSON 로드 실패 ({bill_num}.json): {e}")
    return {}


def extract_process_status(json_data: Dict) -> str:
    """JSON 데이터에서 process_status 추출"""
    if not json_data:
        return "정보없음"
    
    raw = json_data.get("raw", {})
    
    proc_result = raw.get("PROC_RESULT")
    if proc_result and str(proc_result).strip().lower() != "null":
        return str(proc_result).strip()
    
    cmt_result = raw.get("CMT_PROC_RESULT_CD")
    if cmt_result and str(cmt_result).strip().lower() != "null":
        return str(cmt_result).strip()
    
    law_result = raw.get("LAW_PROC_RESULT_CD")
    if law_result and str(law_result).strip().lower() != "null":
        return str(law_result).strip()
    
    return "정보없음"


def extract_pdf_text(pdf_path: Path) -> str:
    """PDF에서 텍스트 추출"""
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print(f"❌ PDF 읽기 에러 ({pdf_path.name}): {e}")
    return text


def parse_comparison_table_from_pdf(pdf_path: Path) -> str:
    """🌟 [최적화] PDF에서 신ㆍ구조문대비표를 안전하게 추출"""
    if DEBUG_MODE:
        print(f"\n   [신ㆍ구조문대비표 마크다운 생성 프로세스 가동]")
        
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            has_table_page = False
            
            # 1. 페이지를 돌며 텍스트 취합 및 표 구조 존재 유무 선점검
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"
                    if '신' in page_text and '구' in page_text:
                        has_table_page = True
            
            # 2. 텍스트 기반 정밀 매핑 시도
            if full_text.strip():
                table_md = parse_comparison_table_from_text(full_text)
                if table_md.strip():
                    return table_md
            
            # 3. 텍스트 파싱 실패 시, 구조적 표(Table Grid) 추출 폴백
            if has_table_page:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if not page_text or '신' not in page_text or '구' not in page_text:
                        continue
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            if table and len(table) >= 2:
                                return format_comparison_table_to_markdown(table)
                                
    except Exception as e:
        if DEBUG_MODE:
            print(f"   ⚠️ 대비표 파싱 중 예외 발생: {e}")
        
    if DEBUG_MODE:
        print(f"   ❌ [경고] {pdf_path.name}에서 신ㆍ구조문대비표 추출에 실패했습니다.")
    return ""


def parse_comparison_table_from_text(text: str) -> str:
    """🌟 [버그 수정] 중복 코드를 제거하고 정밀 복원 분기 로직만 남겨서 정상 작동하도록 수정"""
    if DEBUG_MODE:
        print(f"   [2단 레이아웃 문맥 기반 정밀 매핑 시작]")
        
    pattern = r'신\s*[ㆍ·ㆍ· /]*\s*구\s*조\s*문\s*대\s*[비조]\s*표'
    match = re.search(pattern, text)
    table_text = text[match.end():] if match else text
    
    if "부 칙" in table_text:
        table_text = table_text.split("부 칙")[0]
        
    raw_lines = table_text.split('\n')
    
    # 1. 신설 제정안 스타일 사전 검사
    is_creation_bill = False
    sample_text = "".join(raw_lines[:30])
    if "기본법안" in sample_text or "제안" in sample_text or "주요" in sample_text:
        if len(re.findall(r'제\s*\d+\s*조의\s*\d+|제\s*\d+\s*조', sample_text)) < 5:
            is_creation_bill = True

    if is_creation_bill:
        md_lines = ["", "# 신ㆍ구조문대비표 (신설안)", ""]
        for line in raw_lines:
            line = line.strip()
            if not line or len(line) < 2 or re.match(r'^\s*-\s*\d+\s*-\s*$', line):
                continue
            cleaned_line = re.sub(r'\s+', ' ', line)
            md_lines.append(cleaned_line)
        return "\n\n".join(md_lines)

    # 2. 일부개정안 정밀 문맥 복원 로직 (기존 데드코드 영역을 살리고 결합함)
    md_lines = ["", "# 신ㆍ구조문대비표", "", "| 현행 | 개정안 |", "|------|--------|"]
    noise_words = ['현 행', '개 정 안', '현행', '개정안', '의 안', '번 호']
    
    for line in raw_lines:
        line = line.strip()
        if not line or len(line) < 2 or any(nw in line for nw in noise_words):
            continue
            
        if re.match(r'^\s*-\s*\d+\s*-\s*$', line) or '법률 제' in line or '제 호' in line:
            continue
            
        if re.match(r'^[-\s.·━━━━━━──—━]+$', line):
            continue

        left_side, right_side = "", ""
        article_matches = list(re.finditer(r'제\s*\d+\s*조', line))
        
        if len(article_matches) >= 2:
            split_idx = article_matches[1].start()
            left_side = line[:split_idx].strip()
            right_side = line[split_idx:].strip()
        else:
            if "------" in line:
                parts = line.split("------")
                non_empty_parts = [p.strip() for p in parts if p.strip()]
                if len(non_empty_parts) >= 2:
                    left_side = non_empty_parts[0]
                    right_side = " ".join(non_empty_parts[1:])
                else:
                    cleaned_core = line.replace("------", "").strip()
                    if any(kw in cleaned_core for kw in ['신설', '개정']):
                        left_side = "------"
                        right_side = cleaned_core
                    else:
                        left_side = cleaned_core
                        right_side = "------"
            else:
                space_split = re.split(r'\s{2,}', line)
                if len(space_split) >= 2:
                    left_side = space_split[0].strip()
                    right_side = " ".join(space_split[1:]).strip()
                else:
                    left_side = line.strip()
                    right_side = ""

        # 더미 기호 및 하이픈 정리
        left_side = re.sub(r'^-+$|^\.+$', '', left_side).strip()
        right_side = re.sub(r'^-+$|^\.+$', '', right_side).strip()
        left_side = re.sub(r'-{3,}', '------', left_side)
        right_side = re.sub(r'-{3,}', '------', right_side)

        left_side = left_side.replace('|', '\\|')
        right_side = right_side.replace('|', '\\|')
        
        if left_side or right_side:
            if left_side and not right_side:
                if any(kw in left_side for kw in ['주어야', '일)', '회-', '개정']):
                    right_side = left_side
                    left_side = ""
            
            if not left_side: left_side = "------"
            if not right_side: right_side = "------"
            md_lines.append(f"| {left_side} | {right_side} |")
            
    return "\n".join(md_lines)


def format_comparison_table_to_markdown(table: List[List]) -> str:
    """표 데이터를 마크다운 표 형태로 변환"""
    md_lines = ["", "# 신구문 대조표", "", "| 현행 | 개정안 |", "|------|--------|"]
    data_rows = table[1:] if len(table) > 1 else table
    
    for row in data_rows:
        if not row or len(row) < 2:
            continue
        
        current_cell = str(row[0]) if row[0] else ""
        amendment_cell = str(row[1]) if row[1] else ""
        
        if not current_cell.strip() and not amendment_cell.strip():
            continue
        
        strikethrough_patterns = ['---', '━━', '──', '—', '━']
        for pattern in strikethrough_patterns:
            if pattern in current_cell:
                current_cell = current_cell.split(pattern)[0].strip()
            if pattern in amendment_cell:
                amendment_cell = amendment_cell.split(pattern)[0].strip()
        
        current_cell = current_cell.replace('\n', '<br>').replace('|', '\\|')
        amendment_cell = amendment_cell.replace('\n', '<br>').replace('|', '\\|')
        
        md_lines.append(f"| {current_cell} | {amendment_cell} |")
        
    return "\n".join(md_lines) + "\n" if len(md_lines) > 5 else ""


def clean_pdf_text(text: str) -> str:
    """[노이즈 컷팅] '페이지 번호'와 '법률 제 호 공란'을 완벽하게 삭제합니다."""
    text = re.sub(r'[①-⑳]', lambda m: f"{ord(m.group(0)) - ord('①') + 1})", text)
    
    text = re.sub(r'^\s*-\s*\d+\s*-\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\s*-\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*-\s*\d+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\s*$', '', text, flags=re.MULTILINE)
    
    text = re.sub(r'법률\s+제\s*호', '', text)
    text = re.sub(r'법률\s+제\s+호', '', text)
    text = re.sub(r'제\s+호', '', text)
    
    clean_pattern = r'신\s*[ㆍ·ㆍ· /]*\s*구\s*조\s*문\s*대\s*[비조]\s*표.*$'
    text = re.sub(clean_pattern, '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def parse_bill_content_improved(text: str, json_data: Dict, is_pending: bool = False) -> Dict[str, str]:
    """본문 핵심 정보 파싱 추출기"""
    result = {
        "bill_name": "",
        "bill_num": json_data.get("bill_no", ""),
        "date_label": "제출연월일" if is_pending else "발의연월일",
        "date_value": "",
        "proposer_label": "제출자" if is_pending else "발의자",
        "proposer_value": "",
        "reason_content": "",
        "main_content": "",
        "body_content": "",
        "addendum": "",
        "comparison_table": ""
    }
    
    if json_data.get("raw", {}).get("BILL_NAME"):
        result["bill_name"] = clean_bill_name(json_data["raw"]["BILL_NAME"])
    else:
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if lines: result["bill_name"] = clean_bill_name(lines[0])
            
    if not result["bill_num"]:
        match = re.search(r'의안\s*번호\s*(\d+)', text)
        if match: result["bill_num"] = match.group(1)

    propose_dt = json_data.get("raw", {}).get("PROPOSE_DT", "")
    if propose_dt and propose_dt != "null":
        result["date_value"] = format_date(propose_dt)
    else:
        date_match = re.search(r'(\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.)', text)
        if date_match: result["date_value"] = format_date(date_match.group(1))

    if is_pending:
        result["proposer_value"] = "정부"
    else:
        raw_proposer = json_data.get("raw", {}).get("PUBL_PROPOSER", "")
        rst_proposer = json_data.get("raw", {}).get("RST_PROPOSER", "")
        if rst_proposer and rst_proposer != "null":
            combined = rst_proposer + ", " + raw_proposer if raw_proposer and raw_proposer != "null" else rst_proposer
            result["proposer_value"] = parse_proposers(combined)
        else:
            json_prop = json_data.get("raw", {}).get("PROPOSER", "")
            result["proposer_value"] = parse_proposers(json_prop if json_prop else text)

    json_reason = json_data.get("reason_text", "")
    if json_reason:
        reason_match = re.search(r'제안이유\n(.*?)(?=주요내용|$)', json_reason, re.DOTALL)
        main_match = re.search(r'주요내용\n(.*)$', json_reason, re.DOTALL)
        if reason_match: result["reason_content"] = reason_match.group(1).strip()
        if main_match: result["main_content"] = main_match.group(1).strip()

    if not result["reason_content"] or not result["main_content"]:
        intro_match = re.search(r'제안\s*이유.*?(?=(?:제\s*1장|법률\s*제|부\s*칙|$))', text, re.DOTALL | re.IGNORECASE)
        if intro_match:
            full_intro = intro_match.group(0).strip()
            split_patterns = [r'\n\s*가\s*\.', r'\n\s*①', r'\n\s*1\s*\.']
            split_idx = -1
            
            for p in split_patterns:
                idx_match = re.search(p, full_intro)
                if idx_match:
                    split_idx = idx_match.start()
                    break
            
            if split_idx != -1:
                raw_reason = full_intro[:split_idx].strip()
                raw_main = full_intro[split_idx:].strip()
                raw_reason = re.sub(r'^제안\s*이유\s*(및)?\s*(주요\s*내용)?\s*(및)?', '', raw_reason, flags=re.IGNORECASE).strip()
                raw_reason = re.sub(r'^\s*및\s*', '', raw_reason).strip()
                result["reason_content"] = raw_reason
                result["main_content"] = raw_main
            else:
                cleaned_intro = re.sub(r'^제안\s*이유\s*(및)?\s*(주요\s*내용)?\s*(및)?', '', full_intro, flags=re.IGNORECASE).strip()
                result["reason_content"] = cleaned_intro

    body_match = re.search(r'((?:제\s*1장|법률\s+제).*?)(?=부\s*칙|$)', text, re.DOTALL)
    if body_match:
        cleaned_body = body_match.group(1).strip()
        if result["bill_name"] in cleaned_body[:100]:
            cleaned_body = cleaned_body.replace(result["bill_name"], "", 1).strip()
        result["body_content"] = cleaned_body

    addendum_match = re.search(r'부\s*칙\s*(.*)$', text, re.DOTALL)
    if addendum_match:
        result["addendum"] = addendum_match.group(1).strip()

    return result


def save_to_markdown(data: Dict, out_path: Path) -> str:
    """마크다운 조립 영구 저장 및 반환"""
    md_content = []
    
    md_content.append(f"# 법률명\n{data['bill_name']}\n")
    if data.get("bill_num"):
        md_content.append(f"# 의안번호\n{data['bill_num']}\n")
    md_content.append(f"# {data['date_label']}\n{data['date_value']}\n")
    md_content.append(f"# {data['proposer_label']}\n{data['proposer_value']}\n")
    
    if data.get("reason_content"):
        md_content.append(f"# 제안 이유\n{data['reason_content']}\n")
    if data.get("main_content"):
        md_content.append(f"# 주요 내용\n{data['main_content']}\n")
    if data.get("body_content"):
        md_content.append(f"# 본문\n{data['body_content']}\n")
        
    if data.get("addendum"):
        clean_addendum = data['addendum'].split("신ㆍ구조문대비표")[0].strip()
        md_content.append(f"# 부칙\n{clean_addendum}\n")
        
    if data.get("comparison_table") and data["comparison_table"].strip():
        md_content.append(data["comparison_table"].strip() + "\n")
        
    full_markdown_text = "\n".join(md_content)
    
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_markdown_text)
        
    return full_markdown_text


def create_metadata_row(pdf_path: Path, md_filepath: Path, data_id: int, parsed_data: Dict[str, str], json_data: Dict = None) -> Dict:
    raw = json_data.get("raw", {}) if json_data else {}
    cmt_present_dt = raw.get("CMT_PRESENT_DT", "")
    updated_at = cmt_present_dt if cmt_present_dt and cmt_present_dt != "null" else datetime.now().strftime("%Y-%m-%d")

    return {
        "data_id": data_id,
        "data_title": raw.get("BILL_NAME", parsed_data.get("bill_name", "제목 없음")),
        "category_id": 1,
        "file_path": to_relative_path(md_filepath),
        "source_url": raw.get("DETAIL_LINK", ""),
        "collected_at": datetime.now().strftime("%Y-%m-%d"),
        "updated_at": updated_at,
        "bill_num": raw.get("BILL_NO", parsed_data.get("bill_num", "")),
        "proposer": raw.get("PROPOSER", parsed_data.get("proposer_value", "")),
        "process_status": extract_process_status(json_data),
        "proposed_at": raw.get("PROPOSE_DT", "")
    }


def save_csv_safely(df: pd.DataFrame, output_path: Path) -> Path:
    """CSV 파일 안전하게 저장"""
    try:
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        return output_path
    except PermissionError:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback_path = output_path.with_name(f"{output_path.stem}_{timestamp}{output_path.suffix}")
        print(f"[경고] CSV 파일이 열려있어 대체 저장합니다: {fallback_path}")
        df.to_csv(fallback_path, index=False, encoding="utf-8-sig")
        return fallback_path


def process_bill_pdfs():
    """메인 프로세스 실행 엔진"""
    print("\n" + "=" * 80)
    print(" 📄 법안 PDF 데이터 연동 및 정제 가동 (요구사항 반영 완료)")
    print("=" * 80 + "\n")
    
    if not PDF_DIR.exists():
        print(f"❌ [에러] PDF 폴더 없음: {PDF_DIR}")
        return
    
    pdf_files = list(PDF_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"⚠️ [알림] 처리 대상 .pdf 파일이 없습니다.")
        return
    
    existing_df = pd.DataFrame()
    existing_ids = set()
    if OUT_METADATA_CSV.exists():
        try:
            existing_df = pd.read_csv(OUT_METADATA_CSV)
            if "data_id" in existing_df.columns:
                existing_ids = set(existing_df["data_id"].dropna().astype(int))
        except Exception:
            pass
            
    new_metadata_rows = []
    preview_content = []
    
    for idx, pdf_file in enumerate(pdf_files, 1):
        try:
            print(f"[{idx:03d}/{len(pdf_files)}] 전처리 중: {pdf_file.name}")
            bill_num = pdf_file.stem.replace('_pending', '')
            is_pending = '_pending' in pdf_file.name
            
            json_data = load_json_metadata(bill_num)
            raw_text = extract_pdf_text(pdf_file)
            if not raw_text:
                print(f"   ❌ {pdf_file.name} 추출 실패")
                continue
                
            # 1. 1차 노이즈 제거 및 클리닝
            cleaned_text = clean_pdf_text(raw_text)
            
            # 2. 신설 제정안인지 1차 필터링 검사
            is_creation_bill = False
            if "기본법안" in cleaned_text[:1200] and "제안이유" in cleaned_text[:1200]:
                if len(re.findall(r'제\s*\d+\s*조', cleaned_text[:1200])) < 5:
                    is_creation_bill = True
            
            # 3. 신구조문대비표 엔진 가동 (안전하게 감싸진 함수 호출)
            if is_creation_bill:
                if DEBUG_MODE:
                    print("   💡 [안내] 신설 제정 법안 레이아웃 탐지 - 표 구조 생략")
                comparison_table = ""
            else:
                comparison_table = parse_comparison_table_from_pdf(pdf_file)
            
            # 4. 세부 데이터 구조화
            parsed_data = parse_bill_content_improved(cleaned_text, json_data, is_pending)
            parsed_data["comparison_table"] = comparison_table
            
            md_filename = f"{bill_num}.md"
            md_filepath = OUT_MD_DIR / md_filename
            
            # 5. 마크다운 저장
            markdown_content = save_to_markdown(parsed_data, md_filepath)
                
            data_id = generate_data_id(existing_ids)
            metadata_row = create_metadata_row(pdf_file, md_filepath, data_id, parsed_data, json_data)
            new_metadata_rows.append(metadata_row)
            
            if len(preview_content) < 3:
                preview_content.append(f"\n\n---\n\n# 📄 {pdf_file.name}\n\n{markdown_content}")
                
            print(f"   ✓ 성공: {md_filename}")
        except Exception as e:
            print(f"   ❌ 에러 ({pdf_file.name}): {e}")
            continue
            
    if preview_content:
        with open(PREVIEW_MD, "w", encoding="utf-8") as f:
            f.write("# 🔍 법안 마크다운 변환 미리보기 (샘플)\n" + "\n".join(preview_content))
            
    if new_metadata_rows:
        new_df = pd.DataFrame(new_metadata_rows)
        columns = [
            "data_id", "data_title", "category_id", "file_path", "source_url",
            "collected_at", "updated_at", "bill_num", "proposer", "process_status",
            "proposed_at"
        ]
        new_df = new_df.reindex(columns=columns, fill_value="")
        
        if not existing_df.empty:
            current_bill_nums = [str(row["bill_num"]) for row in new_metadata_rows]
            existing_df = existing_df[~existing_df["bill_num"].astype(str).isin(current_bill_nums)]
            final_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            final_df = new_df
            
        saved_csv_path = save_csv_safely(final_df, OUT_METADATA_CSV)
        print(f"\n🎉 전체 완료! CSV 갱신 성공: {saved_csv_path}")


if __name__ == "__main__":
    process_bill_pdfs()