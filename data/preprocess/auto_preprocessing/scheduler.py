import logging
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler

# 전처리 파이프라인 함수 가져오기 (main_pipeline.py)
from main_pipeline import run_total_preprocessing_pipeline

# 만약 팀원이 만든 수집(크롤러) 모듈이 있다면 이와 같이 import 합니다.
# from scrapers import run_daily_collectors 

# 1. 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler("automation_daily.log", encoding="utf-8"),
        logging.StreamHandler() # 터미널 창에도 출력
    ]
)

# 2. 새벽 2시에 실행될 통합 태스크 정의
def daily_automation_job():
    logging.info("=========================================")
    logging.info("새벽 2시: 정치 데이터 자동화 파이프라인 가동 시작")
    logging.info("=========================================")
    
    try:
        # [Step 1] 데이터 수집 단계 (팀원들이 구현한 크롤러/API 호출 함수)
        logging.info("1단계: 뉴스/법안/정책 데이터 수집 시작...")
        # run_daily_collectors() 
        logging.info("1단계: 데이터 수집 완료.")
        
        # [Step 2] 데이터 전처리 및 누적 적재 단계 (본인이 구현한 로직)
        logging.info("2단계: 데이터 전처리 및 중복 필터링 파이프라인 가동...")
        run_total_preprocessing_pipeline()
        logging.info("2단계: 전처리 및 서비스 데이터셋 적재 완료.")
        
        logging.info("오늘 자 배치 자동화가 성공적으로 종료되었습니다.")
        
    except Exception as e:
        # 새벽에 찌그러진 PDF나 API 에러로 터지면 알림 로그를 남깁니다.
        logging.critical(f"새벽 자동화 배치 중 치명적 에러 발생: {e}", exc_info=True)

# 3. 스케줄러 가동 구문
if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="Asia/Seoul") # 한국 시간대 설정
    
    # 매일 새벽 2시 0분 0초에 daily_automation_job 함수를 실행하도록 등록
    scheduler.add_job(
        daily_automation_job, 
        trigger='cron', 
        hour=2, 
        minute=0, 
        second=0,
        id='daily_political_data_job'
    )
    
    logging.info("APScheduler 서버 프로세스가 가동되었습니다. 매일 새벽 2시에 알람이 울립니다...")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("스케줄러 프로세스가 사용자에 의해 종료되었습니다.")