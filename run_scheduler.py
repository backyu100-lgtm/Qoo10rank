"""
컴퓨터가 켜져 있는 동안, 지정한 간격마다 scrape_once()를 반복 실행합니다.
터미널에서 이 스크립트를 실행한 채로 두면 자동으로 체크인이 쌓여요.
컴퓨터가 꺼지거나 스크립트가 종료되면 멈춰요 (완전 자동화는 GitHub Actions 사용).
"""
import time
from datetime import datetime
from scrape import scrape_once

INTERVAL_MINUTES = 30  # 15분 이상을 권장해요 (너무 잦으면 차단 위험)

if __name__ == "__main__":
    print(f"{INTERVAL_MINUTES}분 간격으로 추적을 시작합니다. 종료하려면 Ctrl+C")
    while True:
        print(f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 체크인 실행 ===")
        try:
            scrape_once()
        except Exception as e:
            print("에러 발생:", e)
        time.sleep(INTERVAL_MINUTES * 60)
