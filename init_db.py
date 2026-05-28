# 파일 위치: team2_final/init_db.py (최종 간결본)
import sys
import os
from sqlalchemy import create_engine

current_dir = os.path.abspath(os.path.dirname(__file__))
sys.path.append(current_dir)
sys.path.append(os.path.join(current_dir, 'pipeline'))

from pipeline.config import DB_URL
from pipeline.db.rdb import init_tables  # 👈 rdb.py에 선언된 순정 초기화 함수 호출

def main():
    engine = create_engine(DB_URL)
    init_tables(engine)  # 소문자(articles, cards, card_tabs) 규칙대로 깔끔하게 생성
    print("✨ POLICITY 소문자 표준 테이블 세팅 완료!")

if __name__ == "__main__":
    main()