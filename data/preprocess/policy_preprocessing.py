from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
import pandas as pd

# 경로 설정
POLICY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = POLICY_DIR.parents[1] if len(POLICY_DIR.parents) > 1 else POLICY_DIR

if (POLICY_DIR / "data").exists():
    DATA_DIR = POLICY_DIR / "data"
elif (POLICY_DIR.parent / "data").exists():
    DATA_DIR = POLICY_DIR.parent / "data"
else:
    DATA_DIR = POLICY_DIR / "data"

# 원본 및 출력 파일명 설정
RAW_JSON_PATH = DATA_DIR / "gov24정책_카테고리Top5_20260525_045101.json"
OUT_SINGLE_JSON_PATH = DATA_DIR / "all_policies_combined.json"
OUT_METADATA_CSV = DATA_DIR / "metadata_policies.csv"

# 디렉토리 자동 생성
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 제외 필드
EXCLUDE_FIELDS = [
    "등록일시", "조회수", "서비스ID", "서비스 ID", 
    "상세조회URL", "상세조회 URL", "소관기관코드"
]


def clean_updated_at_to_8digits(date_val: Any) -> str:
    """수정일시 데이터에서 앞 8자리 추출 (예: 20260511)"""
    if not date_val or pd.isna(date_val) or str(date_val).lower() == "nan":
        return "정보 없음"
    
    clean_str = re.sub(r'[^0-9]', '', str(date_val))
    if len(clean_str) >= 8:
        return clean_str[:8]
    return "정보 없음"


def clean_text_for_json(text: str) -> str:
    """결측치 처리 및 '||' 기호를 쉼표로 치환하는 정제 함수"""
    if text is None or pd.isna(text):
        return "정보 없음"
        
    text_str = str(text).strip()
    if not text_str or text_str.lower() == "nan" or text_str == "null":
        return "정보 없음"
    
    text_str = text_str.replace("||", ", ")
    text_str = text_str.replace('\\r', '').replace('\r', '')
    text_str = text_str.replace('\\n', '\n')
    
    lines = text_str.split('\n')
    cleaned_lines = []
    bullet_pattern = re.compile(r'^\s*([ㅇ○■●◆◇◈▶▷*]|-\s*|\u25e6)\s*')
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if bullet_pattern.match(stripped):
            processed_line = bullet_pattern.sub('- ', stripped)
            cleaned_lines.append(processed_line)
        else:
            cleaned_lines.append(stripped)
            
    return "\n".join(cleaned_lines) if cleaned_lines else "정보 없음"


def parse_application_dates(period_text: str) -> tuple[str, str]:
    """신청기한 텍스트에서 시작일과 종료일을 추출."""
    if not period_text or pd.isna(period_text) or str(period_text).lower() == "nan":
        return "정보 없음", "정보 없음"
    
    period_text = str(period_text).strip()
    
    ko_match = re.search(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일부터\s*(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일까지', period_text)
    if ko_match:
        year_start = ko_match.group(1)
        start_month = int(ko_match.group(2))
        start_day = int(ko_match.group(3))
        
        year_end = ko_match.group(4) if ko_match.group(4) else year_start
        end_month = int(ko_match.group(5))
        end_day = int(ko_match.group(6))
        
        return f"{year_start}-{start_month:02d}-{start_day:02d}", f"{year_end}-{end_month:02d}-{end_day:02d}"

    standard_match = re.search(r'(\d{4})[-./](\d{2})[-./](\d{2})\s*~\s*(\d{4})[-./](\d{2})[-./](\d{2})', period_text)
    if standard_match:
        return f"{standard_match.group(1)}-{standard_match.group(2)}-{standard_match.group(3)}", f"{standard_match.group(4)}-{standard_match.group(5)}-{standard_match.group(6)}"
        
    return period_text, period_text


def create_refined_policy_json(policy_data: Dict) -> Dict:
    """필드 제외 및 맞춤형 정제 딕셔너리 빌드"""
    refined_dict = {}
    for key, value in policy_data.items():
        clean_key = key.strip()
        if clean_key in EXCLUDE_FIELDS:
            continue
            
        if clean_key == "수정일시":
            refined_dict[clean_key] = value 
        else:
            refined_dict[clean_key] = clean_text_for_json(value)
            
    return refined_dict


def main():
    if not RAW_JSON_PATH.exists():
        print(f"❌ 원본 파일을 찾을 수 없습니다: {RAW_JSON_PATH}")
        return

    with open(RAW_JSON_PATH, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    combined_policies_json = []
    metadata_rows = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for category, policies in raw_data.items():
        print(f"📦 '{category}' 카테고리 데이터 처리 중...")
        
        for policy in policies:
            # 1. 개별 데이터 정제 및 통합 리스트에 추가
            refined_policy = create_refined_policy_json(policy)
            
            # 메타데이터와 매칭하기 쉽도록 서비스ID와 서비스명은 데이터 내부에 명시적으로 유지
            service_id = policy.get("서비스ID", "공통_ID")
            service_name = policy.get("서비스명", "무명_서비스")
            
            refined_policy["서비스ID"] = service_id
            refined_policy["카테고리"] = category
            
            combined_policies_json.append(refined_policy) # 리스트에 담기
            
            # 2. 신청시작일 / 신청종료일 분석
            start_date, end_date = parse_application_dates(policy.get("신청기한", ""))
            
            # 3. 메타데이터 생성 (file_path는 통합 JSON 파일 경로로 통일)
            metadata_rows.append({
                "data_id": service_id,
                "data_title": service_name,
                "category_id": 1,
                "file_path": OUT_SINGLE_JSON_PATH.name, # 🌟 단일 파일명 혹은 상대경로로 변경
                "source_url": policy.get("상세조회URL", "정보 없음"),
                "collected_at": today_str,
                "updated_at": clean_updated_at_to_8digits(policy.get("수정일시", "")),
                "department": policy.get("부서명", "정보 없음"),
                "apply_start_date": start_date,
                "apply_end_date": end_date
            })

    # json 파일 하나로 저장하기!!
    if combined_policies_json:
        with open(OUT_SINGLE_JSON_PATH, "w", encoding="utf-8") as f_out:
            json.dump(combined_policies_json, f_out, ensure_ascii=False, indent=2)
        print(f"✅ 모든 정책 데이터 통합 JSON 저장 완료: {OUT_SINGLE_JSON_PATH}")

    # 메타데이터 CSV 파일 저장
    if metadata_rows:
        columns_order = [
            "data_id", "data_title", "category_id", "file_path", "source_url", 
            "collected_at", "updated_at", "department", "apply_start_date", "apply_end_date"
        ]
        df_meta = pd.DataFrame(metadata_rows, columns=columns_order)
        df_meta.to_csv(OUT_METADATA_CSV, index=False, encoding="utf-8-sig")
        print(f"✅ 새 규격 반영 메타데이터 CSV 저장 완료: {OUT_METADATA_CSV}")


if __name__ == "__main__":
    main()