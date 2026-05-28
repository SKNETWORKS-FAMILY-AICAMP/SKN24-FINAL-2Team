from __future__ import annotations

import os
import re
import json
from pathlib import Path
from datetime import datetime
import pandas as pd

# =========================================================
# 경로 설정
# =========================================================
ARTICLE_DIR = Path(__file__).resolve().parent
DATA_DIR = ARTICLE_DIR / "data"
OUT_MD_DIR = DATA_DIR / "news_md"  # 마크다운 파일들이 자동 저장될 폴더
OUT_METADATA_CSV = DATA_DIR / "metadata_youth.csv"

OUT_MD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 정규식 패턴 규칙
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"(https?://[^\s]+|www\.[^\s]+)")
CLEAN_PATTERNS = [
    re.compile(r"\[[^\]]*\]"), re.compile(r"\([^\)]*\)"),
    re.compile(r"▲.*$", re.MULTILINE), re.compile(r"기자\s*$", re.MULTILINE),
    re.compile(r"재배포\s*금지.*$", re.MULTILINE), re.compile(r"무단\s*전재.*$", re.MULTILINE)
]

def preprocess_text(text: str) -> str:
    if not text:
        return ""
    text = EMAIL_RE.sub("", text)
    text = URL_RE.sub("", text)
    for pattern in CLEAN_PATTERNS:
        text = pattern.sub("", text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return "\n\n".join(lines)

# =========================================================
# 자동화 실행 진입 함수
# =========================================================
def run_news_pipeline(target_file: Path):
    if not target_file.exists():
        print(f"[-] 뉴스 소스 파일이 존재하지 않습니다: {target_file}")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    processed_rows = []

    # 중복 방지용 기존 ID 로드
    existing_ids = set()
    if OUT_METADATA_CSV.exists():
        try:
            existing_df = pd.read_csv(OUT_METADATA_CSV)
            existing_ids = set(existing_df['data_id'].astype(str).tolist())
        except Exception: pass

    with open(target_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            
            data_id = str(item.get("id", "")).strip()
            if not data_id or data_id in existing_ids:
                continue

            title = item.get("title", "").strip()
            content = item.get("content", "")
            source_url = item.get("url", "")
            press = item.get("press", "")
            published_at = item.get("published_at", "")

            # 1. 본문 정제
            cleaned_content = preprocess_text(content)
            
            # 2. [자동화] 마크다운 문서 자동 생성 및 저장
            out_md_path = OUT_MD_DIR / f"{data_id}.md"
            with open(out_md_path, "w", encoding="utf-8") as md_f:
                md_f.write(f"# {title}\n\n")
                md_f.write(f"- 매체: {press}\n- 발행일: {published_at}\n- URL: {source_url}\n\n")
                md_f.write("## 본문 내용\n")
                md_f.write(cleaned_content)

            processed_rows.append({
                "data_id": data_id,
                "data_title": title,
                "category_id": "article",
                "file_path": str(out_md_path.relative_to(ARTICLE_DIR)),
                "source_url": source_url,
                "collected_at": now,
                "updated_at": published_at if published_at else now,
                "press": press,
                "published_at": published_at,
                "is_youth_related": 1
            })

    # 3. CSV 누적 업데이트
    if processed_rows:
        new_df = pd.DataFrame(processed_rows)
        if OUT_METADATA_CSV.exists():
            final_df = pd.concat([pd.read_csv(OUT_METADATA_CSV), new_df], ignore_index=True)
        else:
            final_df = new_df
        
        final_df.to_csv(OUT_METADATA_CSV, index=False, encoding="utf-8-sig")
        print(f"[+] 뉴스 마크다운 변환 및 메타데이터 누적 완료: {len(processed_rows)}건 추가.")