import os
import json
import time
from pathlib import Path
from datetime import datetime
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_POLICY_MD_DIR = BASE_DIR / "policy_md"  # [추가] 정책 마크다운 저장 폴더
OUTPUT_CLEANED_CSV = BASE_DIR / "youth_policies_cleaned.csv"
OUTPUT_METADATA_CSV = BASE_DIR / "youth_policies_metadata.csv"

OUTPUT_POLICY_MD_DIR.mkdir(parents=True, exist_ok=True)

YOUTH_RELATED_COLUMN_CANDIDATES = ["is_youth_related", "youth_related", "청년관련여부", "청년 관련 여부"]

def filter_youth_related(df: pd.DataFrame) -> pd.DataFrame:
    found_col = next((c for c in YOUTH_RELATED_COLUMN_CANDIDATES if c in df.columns), None)
    if not found_col: return df.copy()
    
    def check_val(v):
        if pd.isna(v): return False
        s = str(v).strip().lower()
        return s in ["1", "true", "y", "yes", "관련"] or "청년" in s

    return df[df[found_col].apply(check_val)].copy()

def clean_policy_data(df: pd.DataFrame) -> pd.DataFrame:
    df_out = df.copy()
    if "서비스명" in df_out.columns:
        df_out["서비스명"] = df_out["서비스명"].fillna("제목 없음").astype(str).str.strip()
    if "서비스소개" in df_out.columns:
        df_out["서비스소개"] = df_out["서비스소개"].fillna("").astype(str).str.strip()
    return df_out

# =========================================================
# 자동화 실행 진입 함수
# =========================================================
def run_policy_pipeline(target_file: Path):
    if not target_file.exists():
        print(f"[-] 정책 소스 파일이 존재하지 않습니다: {target_file}")
        return

    print(f"[+] 정책 파이프라인 자동정제 및 마크다운 변환 가동: {target_file.name}")
    df_raw = pd.read_csv(target_file)
    
    df_youth = filter_youth_related(df_raw)
    df_cleaned = clean_policy_data(df_youth)
    
    collected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metadata_list = []

    for idx, row in df_cleaned.iterrows():
        srv_id = row.get("서비스ID", row.get("서비스id", f"policy_{idx}_{int(time.time())}"))
        title = row.get("서비스명", "제목 없음")
        content = row.get("서비스소개", "내용 없음")
        url = row.get("상세조회URL", row.get("상세조회url", "https://www.gov.kr"))
        dept = row.get("소관기관명", row.get("소관기관", "정부24"))

        # 1. [자동화] 정책 데이터의 마크다운 자동 생성
        md_path = OUTPUT_POLICY_MD_DIR / f"{srv_id}.md"
        with open(md_path, "w", encoding="utf-8") as md_f:
            md_f.write(f"# [정책] {title}\n\n")
            md_f.write(f"- 소관 부처/기관: {dept}\n")
            md_f.write(f"- 지원 대상 및 신청기한: {row.get('신청시작일','')} ~ {row.get('신청종료일','')}\n")
            md_f.write(f"- 원본 URL: {url}\n\n")
            md_f.write(f"## 정책 상세 내용\n{content}\n")

        metadata_list.append({
            "data_id": str(srv_id),
            "data_title": str(title),
            "category_id": "policy",
            "file_path": str(md_path.relative_to(BASE_DIR)),
            "source_url": str(url),
            "collected_at": collected_at,
            "updated_at": collected_at,
            "department": str(dept),
            "apply_start_date": str(row.get("신청시작일", "")),
            "apply_end_date": str(row.get("신청종료일", "")),
            "is_youth_related": 1
        })

    if metadata_list:
        df_new_metadata = pd.DataFrame(metadata_list)
        
        # 메타데이터 마스터 CSV 누적 업데이트
        if OUTPUT_METADATA_CSV.exists():
            final_df = pd.concat([pd.read_csv(OUTPUT_METADATA_CSV), df_new_metadata], ignore_index=True)
        else:
            final_df = df_new_metadata
            
        final_df.to_csv(OUTPUT_METADATA_CSV, index=False, encoding="utf-8-sig")
        print(f"[+] 정책 마크다운 생성 및 메타데이터 누적 완료: {len(metadata_list)}건 누적 추가 완료.")