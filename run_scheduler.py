"""
컴퓨터가 켜져 있는 동안, 지정한 간격마다 scrape_once()를 반복 실행합니다.
"""
import time
from datetime import datetime
from scrape import scrape_once

INTERVAL_MINUTES = 60

if __name__ == "__main__":
    print(f"{INTERVAL_MINUTES}분 간격으로 추적을 시작합니다. 종료하려면 Ctrl+C")
    while True:
        print(f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 체크인 실행 ===")
        try:
            scrape_once()
        except Exception as e:
            print("에러 발생:", e)
        time.sleep(INTERVAL_MINUTES * 60)
