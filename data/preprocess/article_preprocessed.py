import json
import os
import re
import csv
import random
import time
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI, APIError, RateLimitError, APIConnectionError
from kiwipiepy import Kiwi

# 0. OpenAI 클라이언트 초기화 (.env 파일에서 자동 로드)
from dotenv import load_dotenv
load_dotenv()

# 환경변수에서 API 키 읽기 (OPENAI_API_KEY)
client = OpenAI()  # .env 파일의 OPENAI_API_KEY를 자동으로 읽음

# 1. 환경 설정 및 경로 자동 매칭
today_str = datetime.now().strftime("%Y%m%d") 
today_csv_format = datetime.now().strftime("%Y-%m-%d")

dir_path = os.path.join("data")
input_file = os.path.join(dir_path, f"news_{today_str}.jsonl")

output_pretty_json = os.path.join(dir_path, f"news_{today_str}_pretty.json") 
output_inspection_txt = os.path.join(dir_path, "news_contents_inspection.txt")
output_metadata_csv = os.path.join(dir_path, "news_metadata.csv")
log_file = os.path.join(dir_path, f"preprocessing_{today_str}.log")

# 로깅 설정
os.makedirs(dir_path, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()  # 콘솔에도 출력
    ]
)

# Kiwi 초기화
kiwi = Kiwi()

# 전역 변수: API 사용 가능 여부
api_available = False


# 2. API 검증 함수
def validate_api_key():
    """프로그램 시작 시 API 키 유효성 검사"""
    try:
        logging.info("OpenAI API 키 검증 중...")
        test_response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "test"}],
            max_tokens=5,
            timeout=10
        )
        logging.info("✅ OpenAI API 키 검증 성공")
        print("✅ OpenAI API 키 검증 성공")
        return True
    except Exception as e:
        logging.error(f"❌ OpenAI API 키 오류: {str(e)}")
        print(f"❌ OpenAI API 키 오류: {str(e)}")
        print("⚠️ LLM 보정 없이 알고리즘 정제만 수행합니다.")
        return False

# 3. 핵심 알고리즘 및 전처리 함수들 (Kiwi 버전)
def clean_special_characters(text):
    """
    2번 조건: 기사에 꼭 필요한 특수문자 외에 의미 없는 웹 기호/이모지 덤프 제거
    """
    if not text:
        return ""
    
    # [💡 해결 1] HTML 엔티티 처리
    text = re.sub(r'&quot;', '"', text)    # &quot; → "
    text = re.sub(r'&amp;', '&', text)     # &amp; → &
    text = re.sub(r'&lt;', '', text)       # &lt; 삭제 (< 기호는 기사에 불필요)
    text = re.sub(r'&gt;', '', text)       # &gt; 삭제 (> 기호는 기사에 불필요)
    text = re.sub(r'&nbsp;', ' ', text)    # &nbsp; → 공백
    text = re.sub(r'&#39;', "'", text)     # &#39; → '
    text = re.sub(r'&#x27;', "'", text)    # &#x27; → '
    
    # 나머지 &로 시작하는 알 수 없는 엔티티 제거
    text = re.sub(r'&[a-zA-Z0-9#]+;', '', text)
    
    allowed_pattern = re.compile(r'[^가-힣a-zA-Z0-9\s\.\,\?\!\'\"\%\·\ㆍ\-\[\]\(\)\:\_▲◆■①②③④⑤]')
    return allowed_pattern.sub('', text)


def is_garbage_sentence_by_eomi_kiwi(line_text, pos_tags):
    """
    3번 조건 고도화: Kiwi 품사 태그 기준 문장 종결성 판별 엔진
    """
    line_strip = line_text.strip()
    total_tokens = len(pos_tags)
    if total_tokens == 0:
        return True

    last_token = pos_tags[-1]
    last_word = last_token.form
    last_tag = last_token.tag
    
    if last_word.endswith(('기', '게', '함', '음', '포토', '뉴스')) and not line_strip.endswith(('.', '?', '!')):
        return True
        
    if (last_tag.startswith('J') or last_tag == 'EC') and not line_strip.endswith(('.', '?', '!')):
        return True

    if last_tag == 'EF' and last_word.endswith(('세요', '시오', '시오.', '세요.', '자.', '자')):
        return True

    noun_count = sum(1 for t in pos_tags if t.tag.startswith('NN') or t.tag == 'NP')
    稳定_count = sum(1 for t in pos_tags if t.tag == 'VV') 
    adj_count = sum(1 for t in pos_tags if t.tag.startswith('VA') or t.tag.startswith('VC')) 
    josa_count = sum(1 for t in pos_tags if t.tag.startswith('J')) 
    eomi_count = sum(1 for t in pos_tags if t.tag.startswith('E')) 

    if (稳定_count + adj_count) > 0 and josa_count == 0 and noun_count > 1:
        return True

    grammatical_density = (josa_count + eomi_count) / total_tokens
    if grammatical_density < 0.10 and not line_strip.endswith(('다.', '요.')):
        return True

    return False

def process_content_via_algorithm(text):
    """
    1차 방어선: Kiwi 알고리즘 정제 + 대괄호 결합형 [ㅇㅇ신문 ㅇㅇㅇ 기자] 서두 노이즈 완벽 제거
    """
    if not text:
        return ""
        
    # 1. 공통 이메일 노이즈 선제거
    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '', text)
    
    lines = text.split('\n')
    valid_lines = []
    is_first_line = True # 맨 첫 줄(서두)인지 체크하기 위한 플래그
    
    for line in lines:
        line_strip = line.strip()
        
        # 품사 연산 진입 전 가벼운 규칙으로 1차 고속 패스
        if len(line_strip) < 14 and not line_strip.endswith(('다.', '요.', '오.')):
            if line_strip.startswith(('▲', '◆', '■', '①', '②', '③', '-')):
                valid_lines.append(line_strip)
            continue
            
        # 맨 첫 줄 서두의 기자명 / 언론사명 정규식 컷팅 구역
        if is_first_line and line_strip:
            # 대괄호/괄호 내부나 그 직후에 '기자'라는 단어가 포함된 문자열 제거
            line_strip = re.sub(r'^\[[^\]]*기자[^\]]*\]\s*', '', line_strip)
            line_strip = re.sub(r'^\([^)]*기자[^)]*\)\s*', '', line_strip)
            
            # 일반적인 대괄호 및 괄호 문자열 제거
            line_strip = re.sub(r'^\[[^\]]+\]\s*', '', line_strip)
            line_strip = re.sub(r'^\([^)]+\)\s*', '', line_strip)
            
            # 3문장 시작 지점에 노출되는 순수 기자명 조합 패턴 싹 비우기
            line_strip = re.sub(r'^[가-힣]{2,4}\s*기자\s*\(?[a-zA-Z0-9]*\)?\s*[:=]?\s*', '', line_strip)
            line_strip = re.sub(r'^[가-힣]{2,4}\s*기자\s*', '', line_strip)
            
            # 뉴스 일자나 언론사명이 기호와 함께 덤프된 잔여 서두 컷팅 ("OO뉴스 = ", "2026.05.26 = ")
            line_strip = re.sub(r'^[^=:\n]{2,15}[=:]\s*', '', line_strip)
            
            # 첫 줄 정돈이 끝났으므로 플래그 해제
            is_first_line = False
        # ----------------------------------------------------
            
        try:
            # Kiwi 분석기로 형태소 및 품사 분석 수행
            pos_tags = kiwi.tokenize(line_strip)
        except Exception:
            if line_strip:
                valid_lines.append(line_strip)
            continue
            
        # Kiwi 전용 종결어미 판별기 통과
        if is_garbage_sentence_by_eomi_kiwi(line_strip, pos_tags):
            continue
            
        if line_strip:
            valid_lines.append(line_strip)
        
    return '\n'.join(valid_lines).strip()

# 
def call_llm_final_correction(title_text, algo_text, max_retries=3):
    """
    최종 완결판 + 강화된 에러 처리 및 재시도 로직:
    1. 제목과 본문의 맥락을 비교하여 껍데기 기사 필터링
    2. 기사 2~3개가 강제로 이어 붙은 덤프는 1번째 진짜 기사만 남기고 뒤를 가차없이 잘라냄(Truncate)
    """
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system", 
                        "content": (
                            "너는 뉴스 데이터 전처리 파이프라인의 최종 '문맥 기반 노이즈 보정기 및 절단기'야.\n"
                            "제공되는 데이터는 '뉴스 제목'과 1차 정제를 거친 '뉴스 본문 후보'야.\n\n"
                            "초정밀 필터링 및 절단 지침\n"
                            "1. 크롤링 오류로 인해 제공된 '뉴스 제목'과 일치하는 진짜 기사 본문 뒤에, "
                            "완전히 다른 주제의 별개 기사 2~3개가 강제로 연달아 이어 붙어 있는 경우가 있어. "
                            "이 경우, 제공된 '뉴스 제목'과 일치하는 첫 번째 진짜 기사 본문만 완벽하게 남기고, "
                            "그 뒤에 이어지는 다른 기사 내용들은 가차 없이 전부 삭제(절단)해.\n"
                            "2. 만약 본문 후보 전체가 제공된 '뉴스 제목'과 전혀 상관없는 내용이거나, "
                            "서로 다른 사건들의 헤드라인/기사 제목만 나열된 덤프라면 아무것도 출력하지 말고 오직 빈 문자열(공백 없음)만 반환해.\n"
                            "3. 1번 지침에 따라 가짜 기사들을 잘라내고 남은 '첫 번째 진짜 기사 본문'의 정보, 단어, 어투는 "
                            "절대 임의로 요약하거나 수정하지 말고 원문 그대로 출력해.\n"
                            "4. 오직 정제된 최종 결과물만 출력하고 부연 설명은 절대 하지 마."
                        )
                    },
                    {
                        "role": "user", 
                        "content": f"[뉴스 제목]: {title_text}\n[본문 후보]:\n{algo_text}"
                    }
                ],
                temperature=0,  # 일관성 있고 정확한 절단을 위해 0 고정
                timeout=30  # 30초 타임아웃 설정
            )
            return response.choices[0].message.content.strip()
            
        except RateLimitError as e:
            # 대기 후 재시도
            wait_time = (attempt + 1) * 5  # 5초, 10초, 15초 대기 (병렬 처리 시 더 짧게)
            if attempt < max_retries - 1:
                time.sleep(wait_time)
                continue
            else:
                logging.error(f"❌ Rate Limit 초과. 알고리즘 결과 사용: {title_text[:30]}...")
                return algo_text
                
        except APIConnectionError as e:
            # 짧은 대기 후 재시도
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            else:
                logging.error(f"❌ 네트워크 연결 실패. 알고리즘 결과 사용: {title_text[:30]}...")
                return algo_text
                
        except APIError as e:
            # OpenAI API 서버 오류
            logging.error(f"❌ OpenAI API 오류: {str(e)[:100]}")
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            else:
                return algo_text
                
        except Exception as e:
            # 기타 예상치 못한 오류
            logging.error(f"❌ 예상치 못한 오류: {type(e).__name__} - {str(e)[:100]}")
            return algo_text
    
    # 모든 재시도 실패
    return algo_text


# 시간 단축을 위한 병렬처리 ()
def process_single_article(line_data):
    """
    단일 기사 처리 함수
    """
    line, line_num = line_data
    
    if not line.strip():
        return None
        
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logging.warning(f"⚠️ JSON 파싱 실패 (라인 {line_num})")
        return None
        
    # 제목 정제
    raw_title = data.get("title", "")
    clean_title = re.sub(r'&quot;', '"', raw_title)
    data["title"] = clean_title
    
    raw_content = data.get("content", "")
    
    # Kiwi 형태소 알고리즘 정제
    algo_content = process_content_via_algorithm(raw_content)
    
    # 특수문자 화이트리스트 필터링
    clean_sp_content = clean_special_characters(algo_content)
    
    # 알고리즘 단계에서 이미 본문이 없거나 너무 짧은 경우
    if not clean_sp_content or len(clean_sp_content) < 50:
        logging.info(f"⏭️ [알고리즘] 본문 정제 후 내용 부족: {clean_title[:40]}...")
        return None
    
    # LLM 최종 보정 (API 사용 가능할 때만)
    if api_available:
        final_content = call_llm_final_correction(clean_title, clean_sp_content)
        
        # LLM이 제목-본문 불일치로 빈 문자열 반환한 경우
        if not final_content:
            logging.info(f"⏭️ [LLM 필터링] 제목-본문 맥락 불일치: {clean_title[:40]}...")
            return None
        
        # LLM 보정 후에도 너무 짧은 경우
        if len(final_content) < 50:
            logging.info(f"⏭️ [LLM 보정] 처리 후 본문 부족: {clean_title[:40]}...")
            return None
    else:
        final_content = clean_sp_content
        
    data["content"] = final_content
    
    # 메타데이터 생성
    data_id = "".join([str(random.randint(0, 9)) for _ in range(10)])
    published_at_raw = data.get("published_at", "")
    published_date_only = published_at_raw[:10] if published_at_raw else "2026-05-15"
    
    metadata = [
        data_id,
        clean_title,
        1, 
        data.get("file_path", ""),
        data.get("url", ""),
        today_csv_format, 
        today_csv_format, 
        data.get("publisher"), 
        published_date_only 
    ]
    
    return (data, metadata)

# 메인 파이프라인 가동 (병렬 처리 버전)
if __name__ == "__main__":
    # API 키 검증
    api_available = validate_api_key()
    
    if not os.path.exists(input_file):
        logging.error(f"❌ [대기] 정제할 당일 뉴스 jsonl 파일이 없습니다.")
        logging.error(f"🔍 실패 경로: {os.path.abspath(input_file)}")
        print(f"❌ [대기] 정제할 당일 뉴스 jsonl 파일이 없습니다.\n🔍 실패 경로: {os.path.abspath(input_file)}")
    else:
        logging.info(f"🚀 [{today_str}] 파이프라인 시작!")
        print(f"🚀 [{today_str}] 파이프라인 시작!")
        
        # 파일 읽기
        with open(input_file, "r", encoding="utf-8", errors="ignore") as infile:
            lines = [(line, idx) for idx, line in enumerate(infile, 1)]
        
        total_lines = len(lines)
        logging.info(f"📄 총 {total_lines}개 라인 읽기 완료")
        print(f"📄 총 {total_lines}개 라인 읽기 완료")
        
        processed_articles = []
        metadata_rows = []
        
        # 병렬 처리 (동시에 10개씩 처리)
        max_workers = 10 if api_available else 1  # LLM 없으면 순차 처리
        
        logging.info(f"⚡ 병렬 처리 시작 (동시 작업 수: {max_workers})")
        print(f"⚡ 병렬 처리 시작 (동시 작업 수: {max_workers})")
        
        start_time = time.time()
        completed = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 모든 작업 제출
            future_to_line = {executor.submit(process_single_article, line_data): line_data 
                            for line_data in lines}
            
            # 완료되는 대로 결과 수집
            for future in as_completed(future_to_line):
                completed += 1
                
                # 진행 상황 표시 (매 20개마다)
                if completed % 20 == 0:
                    elapsed = time.time() - start_time
                    speed = completed / elapsed if elapsed > 0 else 0
                    remaining = (total_lines - completed) / speed if speed > 0 else 0
                    print(f"⏳ 진행: {completed}/{total_lines} ({completed*100//total_lines}%) | "
                          f"속도: {speed:.1f}개/초 | 예상 남은 시간: {remaining:.0f}초")
                
                try:
                    result = future.result()
                    if result:
                        data, metadata = result
                        processed_articles.append(data)
                        metadata_rows.append(metadata)
                except Exception as e:
                    logging.error(f"❌ 기사 처리 중 오류: {str(e)[:100]}")
        
        processed_count = len(processed_articles)
        elapsed_time = time.time() - start_time
        
        # 결과 저장
        with open(output_pretty_json, "w", encoding="utf-8") as json_out:
            json.dump(processed_articles, json_out, ensure_ascii=False, indent=2)
        logging.info(f"✅ JSON 파일 저장 완료: {output_pretty_json}")
            
        with open(output_inspection_txt, "w", encoding="utf-8") as txt_out:
            for idx, art in enumerate(processed_articles):
                txt_out.write(f"==================================================\n")
                txt_out.write(f"[{idx+1}] TITLE: {art.get('title')}\n")
                txt_out.write(f"==================================================\n")
                txt_out.write(f"{art.get('content')}\n\n")
        logging.info(f"✅ 검수 TXT 파일 저장 완료: {output_inspection_txt}")
                
        csv_headers = ['data_id', 'data_title', 'category_id', 'file_path', 'source_url', 'collected_at', 'updated_at', 'press', 'published_at']
        with open(output_metadata_csv, "w", encoding="utf-8", newline="") as csv_out:
            writer = csv.writer(csv_out)
            writer.writerow(csv_headers)
            writer.writerows(metadata_rows)
        logging.info(f"✅ 메타데이터 CSV 파일 저장 완료: {output_metadata_csv}")
            
        print("\n" + "="*50)
        logging.info(f"✨ 전처리 완료! (유효 기사: {processed_count}건)")
        print(f"✨ 전처리 완료! (유효 기사: {processed_count}건)")
        print(f"📊 처리 통계: 전체 {total_lines}건 → 유효 {processed_count}건 → 스킵 {total_lines - processed_count}건")
        print(f"⏱️ 총 소요 시간: {elapsed_time:.1f}초 ({elapsed_time/60:.1f}분)")
        print(f"⚡ 평균 속도: {processed_count/elapsed_time:.1f}개/초")
        
        if api_available:
            logging.info(f"🤖 LLM 보정 사용: {processed_count}건")
            print(f"🤖 LLM 보정 사용: {processed_count}건")
            print(f"스킵 사유는 로그 파일에서 확인 가능:")
            print(f"1. 본문 정제 후 내용 부족")
            print(f"2. 제목-본문 맥락 불일치")
            print(f"3. 처리 후 본문 부족")
        else:
            logging.info(f"⚠️ LLM 미사용: 알고리즘 정제만 수행")
            print(f"⚠️ LLM 미사용: 알고리즘 정제만 수행")
        
        logging.info(f"📝 로그 파일: {log_file}")
        print(f"📝 로그 파일: {log_file}")
        print("="*50)