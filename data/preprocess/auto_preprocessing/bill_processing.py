import os
import re
import json
import unicodedata
from pathlib import Path
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_MD_FOLDER = BASE_DIR / "processed_mds_youth"
OUTPUT_METADATA_YOUTH_CSV = BASE_DIR / "metadata_youth_only.csv"
YOUTH_CACHE_JSON = BASE_DIR / "youth_filter_cache.json"

OUTPUT_MD_FOLDER.mkdir(parents=True, exist_ok=True)


# [추가] 할루시네이션 방어용 텍스트 정교화 및 조항 슬라이싱 함수
def clean_law_text_with_llm(client: OpenAI, raw_text: str) -> str:
    """깨진 PDF 텍스트의 본래 문맥과 단어를 유지하며 개행/띄어쓰기만 마크다운(# 제X조)으로 정정"""
    prompt = f"""
    당신은 대한민국 국회 입법 전문 교정가입니다. 
    다음 문서는 PDF 파싱 과정에서 무작위 줄바꿈 및 띄어쓰기 파괴 노이즈가 발생한 법안 전문입니다.
    법률 용어의 의미를 절대로 왜곡하거나 누락하지 말고, 오직 문맥상 가독성과 LLM의 의미 추출 효율을 높이기 위한 정형화 교정만 수행하여 마크다운 형태로 변환하세요.
    각 조항은 반드시 단락을 분리하여 '# 제X조(제목)' 양식으로 통일해 주세요.

    [원문]
    {raw_text[:6000]}
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )
    return response.choices[0].message.content

def split_bill_into_articles(cleaned_markdown: str) -> list[str]:
    """글자 수가 아닌 조항 단위 분할로 RAG 컨텍스트 왜곡 방지"""
    articles = re.split(r'(?=#+ 제\d+조|제\d+조)', cleaned_markdown)
    return [art.strip() for art in articles if art.strip()]

def extract_text_from_pdf(pdf_path: Path) -> str:
    if not fitz:
        return ""
    doc = fitz.open(pdf_path)
    text_list = []
    for page in doc:
        t = page.get_text()
        if t:
            text_list.append(t)
    return unicodedata.normalize("NFC", "\n".join(text_list))

def check_youth_relation_with_llm(client, text, bill_num, cache) -> int:
    if bill_num in cache:
        return cache[bill_num]
    snippet = text[:3000]
    prompt = f"다음 법안이 2030 청년(고용, 주거, 교육, 복지 등) 정책과 직접적인 연관이 있으면 '1', 전혀 없으면 '0'만 반환하세요.\n\n법안 텍스트 샘플:\n{snippet}"
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=5
        )
        ans = res.choices[0].message.content.strip()
        val = 1 if "1" in ans else 0
        cache[bill_num] = val
        return val
    except Exception:
        return 0


# 메인 배치 연동 함수
def run_bill_pipeline(target_folder: Path):
    if not target_folder.exists():
        print(f"[-] 법안 PDF 소스 폴더가 존재하지 않습니다: {target_folder}")
        return

    load_dotenv()
    client = OpenAI()
    
    youth_cache = {}
    if YOUTH_CACHE_JSON.exists():
        try:
            youth_cache = json.loads(YOUTH_CACHE_JSON.read_text(encoding="utf-8"))
        except Exception: pass

    # 기존 마스터 정보 수집 (중복 검사용)
    existing_ids = set()
    if OUTPUT_METADATA_YOUTH_CSV.exists():
        try:
            existing_ids = set(pd.read_csv(OUTPUT_METADATA_YOUTH_CSV)['data_id'].astype(str).tolist())
        except Exception: pass

    metadata_records = []
    pdf_files = list(target_folder.glob("*.pdf"))

    for pdf_path in pdf_files:
        bill_num = pdf_path.stem
        
        # 파일 단위의 고유 청크 존재 체크 (이미 한 번 가공된 법안이라면 전체 스킵)
        if f"{bill_num}_1" in existing_ids:
            continue

        raw_text = extract_text_from_pdf(pdf_path)
        if not raw_text.strip():
            continue

        is_youth = check_youth_relation_with_llm(client, raw_text, bill_num, youth_cache)
        
        if is_youth == 1:
            # LLM 가독성 교정 패싱 
            cleaned_md = clean_law_text_with_llm(client, raw_text)
            # 조항별 슬라이싱 청킹
            articles = split_bill_into_articles(cleaned_md)

            for i, article_content in enumerate(articles):
                chunk_id = f"{bill_num}_{i+1}"
                md_path = OUTPUT_MD_FOLDER / f"{chunk_id}.md"
                md_path.write_text(article_content, encoding="utf-8")

                metadata_records.append({
                    "data_id": chunk_id,
                    "data_title": f"{bill_num} 법안 - {i+1}조항",
                    "category_id": "bill",
                    "file_path": str(md_path.relative_to(BASE_DIR)),
                    "source_url": "https://likms.assembly.go.kr/bill/main.do",
                    "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "department": "국회",
                    "bill_num": bill_num,
                    "proposer": "의원발의",
                    "process_status": "접수",
                    "proposed_at": datetime.now().strftime("%Y-%m-%d"),
                    "is_youth_related": 1
                })

    # 캐시 실시간 업데이트 보존
    YOUTH_CACHE_JSON.write_text(json.dumps(youth_cache, ensure_ascii=False, indent=2), encoding="utf-8")

    if metadata_records:
        new_df = pd.DataFrame(metadata_records)
        columns = ["data_id", "data_title", "category_id", "file_path", "source_url", "collected_at", "updated_at", "department", "bill_num", "proposer", "process_status", "proposed_at", "is_youth_related"]
        new_df = new_df.reindex(columns=columns)

        if OUTPUT_METADATA_YOUTH_CSV.exists():
            final_df = pd.concat([pd.read_csv(OUTPUT_METADATA_YOUTH_CSV), new_df], ignore_index=True)
        else:
            final_df = new_df

        final_df.to_csv(OUTPUT_METADATA_YOUTH_CSV, index=False, encoding="utf-8-sig")
        print(f"[+] 청년 법안 조항 분할 마스터 CSV 누적 성공: {len(metadata_records)}개 조항 추가 완료.")