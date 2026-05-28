# main_pipeline.py
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd

from article_preprocessing import run_news_pipeline
from bill_preprocessing import run_bill_pipeline
from policy_preprocessing import run_policy_pipeline

# 1. 전체 파이프라인
def run_total_preprocessing_pipeline():
    """
    새벽 2시 스케줄러가 수집 종료 후 최종적으로 호출할 전처리 마스터 함수
    """
    today_str = datetime.now().strftime("%Y%m%d")
    logging.info(f"--- {today_str} 당일 신규 데이터 전처리 파이프라인 가동 ---")
    
    # 경로 명확히 정해지면 그 때 수정 필요 (아직은 시행 단계 아님!!)
    today_news_file = Path(f"./raw_article/news_{today_str}.jsonl")
    today_bill_folder = Path(f"./raw_pdf/{today_str}/") # 오늘 다운로드된 PDF 폴더
    today_policy_file = Path(f"./youth_policies_gov24_{today_str}.csv")

    # 2. 법안 함수 호출
    logging.info("법안(Bill) 전처리 시작...")

    try:
        # 기존 코드에 '오늘의 폴더 경로'만 파라미터로 넘겨줌
        # 내부적으로 중복 체크 후 기존 CSV에 Append 수행 (메타데이터)
        run_bill_pipeline(target_folder=today_bill_folder)
        logging.info("법안 전처리 완료.")
    except Exception as e:
        logging.error(f"법안 전처리 중 오류 발생: {e}")

  
    # 3.  정책 함수 호출
    logging.info("정책(Policy) 전처리 시작...")
    try:
        # 오늘 들어온 신규 정책 CSV를 정제하여 마스터 CSV에 누적 적재
        run_policy_pipeline(target_file=today_policy_file)
        logging.info("정책 전처리 완료.")
    except Exception as e:
        logging.error(f"정책 전처리 중 오류 발생: {e}")

    # 4. 기사 함수 호출
    logging.info("뉴스 기사(Article) 전처리 시작...")
    try:
        # 오늘 날짜 타겟 뉴스 파일 패스를 넘겨 정규식 청소 진행 후 누적 적재
        run_news_pipeline(target_file=today_news_file)
        logging.info("뉴스 기사 전처리 완료.")
    except Exception as e:
        logging.error(f"뉴스 기사 전처리 중 오류 발생: {e}")

    logging.info(f"--- {today_str} 모든 도메인 데이터 누적 전처리 종료 ---")

# 5. 메타데이터 업데이트
def save_metadata_incremental(new_metadata_df, output_csv_path):
    if output_csv_path.exists():
        existing_df = pd.read_csv(output_csv_path)          # 기존 메타데이터
        final_df = pd.concat([existing_df, new_metadata_df], ignore_index=True)     # 새로운 메타데이터 추가
    else:
        final_df = new_metadata_df
        
    # 3. 최종 통합본을 저장
    final_df.to_csv(output_csv_path, index=False, encoding="utf-8-sig")

if __name__ == "__main__":
    # 로컬에서 혼자 테스트할 때 직접 실행 가능하도록 설정
    logging.basicConfig(level=logging.INFO)
    run_total_preprocessing_pipeline()
    save_metadata_incremental()