"""
Qoo10 재팬 랭킹 자동 추적 스크립트

- config.json에 등록한 상품의 현재 순위를 rank_log.csv에 기록
- 상품을 찾으면, 그 상품이 보이는 위치로 스크롤해서 모바일 화면 크기로
  "그 주변만" 캡처 (전체 페이지를 다 찍지 않음 -> 길이도 짧고, 로딩도 다 된 상태)
- 순위가 급하게 오르내리면 alerts.log에 남김
- 상품을 못 찾은 경우에도 rank_log.csv에 "not_found" 상태로 반드시 기록을 남김
- debug_latest.html에 마지막으로 읽은 페이지 원본을 덮어써서 저장
"""
import json
import csv
import re
import argparse
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "rank_log.csv"
SHOT_DIR = BASE_DIR / "screenshots"
ALERT_LOG = BASE_DIR / "alerts.log"
DEBUG_HTML = BASE_DIR / "debug_latest.html"

# 챌린저스처럼 모바일 화면 크기로 캡처
MOBILE_VIEWPORT = {"width": 390, "height": 844}
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def extract_item_id(url_or_str):
    if not url_or_str:
        return ""
    m = re.search(r'(?:goodscode=|/g/|/item/[^/]+/|goods_no=)(\d+)', url_or_str)
    if m:
        return m.group(1)
    m_num = re.search(r'\b\d{7,12}\b', url_or_str)
    if m_num:
        return m_num.group(0)
    return url_or_str.strip()


def load_more(page, max_scrolls=15):
    """스크롤하면서 하위 순위 상품까지 최대한 로딩시킵니다."""
    last_count = -1
    for _ in range(max_scrolls):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(600)
        for text in ["더보기", "もっと見る", "More", "もっと"]:
            try:
                btn = page.get_by_text(text, exact=False)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=1000)
                    page.wait_for_timeout(600)
            except Exception:
                pass
        count = len(page.query_selector_all('a[href*="/item/"], a[href*="goodscode="], a[href*="/g/"]'))
        if count == last_count:
            break
        last_count = count
    # 캡처 전에 맨 위로 되돌려둔다 (특정 상품 위치로 다시 스크롤할 것이므로)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)


def parse_ranking(page):
    """
    순위 목록을 추출하면서, 나중에 스크린샷 위치를 잡을 때 쓸 수 있도록
    각 item_id에 해당하는 앵커 엘리먼트도 함께 보관합니다.
    """
    anchors = page.query_selector_all('a[href*="/item/"], a[href*="goodscode="], a[href*="/g/"]')
    seen_ids = set()
    items = []
    el_by_id = {}
    rank = 0

    for a in anchors:
        href = a.get_attribute("href") or ""
        item_id = extract_item_id(href)
        if not item_id or item_id in seen_ids:
            continue

        title = (a.get_attribute("title") or a.inner_text() or "").strip()
        seen_ids.add(item_id)
        rank += 1

        review_count = None
        try:
            container = a.evaluate_handle(
                "el => el.closest('li') || (el.parentElement && el.parentElement.parentElement)"
            )
            el = container.as_element()
            text = el.inner_text() if el else ""
            m = re.search(r"\(([\d,]+)\)", text)
            if m:
                review_count = m.group(1)
        except Exception:
            el = None

        items.append({"rank": rank, "item_id": item_id, "title": title, "url": href, "reviews": review_count})
        el_by_id[item_id] = el if el else a
    return items, el_by_id


def find_matches(items, target_id, keyword):
    matches = []
    for it in items:
        id_match = target_id and (it["item_id"] == target_id)
        kw_match = keyword and (keyword in it["title"])
        if id_match or kw_match:
            matches.append(it)
    return matches


def capture_rank_area(page, el_by_id, item_id, shot_path):
    """
    상품이 보이는 위치로 스크롤해서, 모바일 화면 하나 분량만 캡처합니다
    (전체 페이지가 아니라 그 상품 근처만).
    """
    el = el_by_id.get(item_id)
    try:
        if el:
            el.scroll_into_view_if_needed(timeout=5000)
            # 화면 정중앙 즈음에 오도록 살짝 더 스크롤 보정
            page.evaluate(
                "(el) => { const r = el.getBoundingClientRect();"
                " window.scrollBy(0, r.top - window.innerHeight/2 + r.height/2); }",
                el
            )
        page.wait_for_timeout(1200)  # 주변 이미지 로딩 대기
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        page.screenshot(path=str(shot_path), full_page=False)
        return True
    except Exception as e:
        print("스크린샷 저장 실패:", e)
        return False


def append_log(rows):
    is_new = not LOG_PATH.exists()
    with open(LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "product_name", "item_id", "rank", "reviews", "matched_title", "url", "status"])
        for r in rows:
            w.writerow(r)


def last_found_rank(identifier):
    if not LOG_PATH.exists():
        return None
    last = None
    with open(LOG_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("item_id") == identifier or row.get("product_name") == identifier) and row.get("status", "found") == "found":
                last = row
    return last


def log_alert(msg):
    with open(ALERT_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    print("ALERT:", msg)


def scrape_once(debug=True):
    config = load_config()
    threshold = config.get("alert_threshold", 5)
    SHOT_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=MOBILE_UA,
            viewport=MOBILE_VIEWPORT,
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            locale="ja-JP",
        )
        page = context.new_page()
        rows_to_log = []

        for product in config["products"]:
            url = product.get("category_url", config.get("default_category_url"))
            target_id = extract_item_id(product.get("target_id") or product.get("target_url", ""))
            keyword = product.get("keyword", "")
            prod_name = product.get("name", target_id or keyword)

            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            load_more(page)

            items, el_by_id = parse_ranking(page)
            print(f"[{prod_name}] 페이지에서 총 {len(items)}개 상품을 읽었어요.")

            if debug:
                DEBUG_HTML.write_text(page.content(), encoding="utf-8")

            matches = find_matches(items, target_id, keyword)

            if not matches:
                print(f"[{prod_name}] (ID: {target_id} / 키워드: {keyword}) 랭킹 페이지에서 상품을 찾지 못했어요.")
                if product.get("screenshot", True):
                    # 못 찾았을 때는 대신 페이지 맨 위 화면이라도 남겨서 상태를 확인할 수 있게 함
                    shot_path = SHOT_DIR / f"{target_id or 'unknown'}_{ts}_notfound.png"
                    try:
                        page.screenshot(path=str(shot_path), full_page=False)
                    except Exception as e:
                        print("스크린샷 저장 실패:", e)
                rows_to_log.append([ts, prod_name, target_id, "", "", "", url, "not_found"])
                continue

            m = matches[0]
            print(f"[{prod_name}] 현재 {m['rank']}위 발견! - {m['title'][:40]}")

            if product.get("screenshot", True):
                shot_path = SHOT_DIR / f"{m['item_id']}_{ts}.png"
                capture_rank_area(page, el_by_id, m["item_id"], shot_path)

            prev = last_found_rank(target_id or prod_name)
            if prev and prev.get("rank"):
                delta = int(prev["rank"]) - m["rank"]
                if abs(delta) >= threshold:
                    direction = "상승" if delta > 0 else "하락"
                    log_alert(f"{prod_name}: {abs(delta)}계단 {direction} ({prev['rank']}위 -> {m['rank']}위)")

            canonical_url = f"https://www.qoo10.jp/g/{m['item_id']}"
            rows_to_log.append([ts, prod_name, m["item_id"], m["rank"], m["reviews"] or "", m["title"], canonical_url, "found"])

        append_log(rows_to_log)
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", default=True)
    args = parser.parse_args()
    scrape_once(debug=args.debug)
