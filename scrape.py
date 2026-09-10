"""
Qoo10 재팬 랭킹 자동 추적 스크립트 (카테고리별 분리 저장판)

- config.json의 categories에 정의된 탭(総合/ビューティー/スキンケア/基礎化粧品 등)을
  실제로 클릭해서 이동한 뒤, 그 목록에서 상품을 찾습니다.
- 카테고리별로 rank_log_<slug>.csv / screenshots/<slug>/ 를 따로 저장합니다.
- screenshot_retention_days보다 오래된 스크린샷은 실행할 때마다 자동으로 지웁니다.
- 상품을 못 찾아도 "not_found" 상태로 기록을 남기고, 매번 스크린샷을 남깁니다.
"""
import json
import csv
import re
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
ALERT_LOG = BASE_DIR / "alerts.log"
DEBUG_HTML = BASE_DIR / "debug_latest.html"

MOBILE_VIEWPORT = {"width": 390, "height": 1000}
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
HEADER_OFFSET = 230


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def log_path_for(slug):
    return BASE_DIR / f"rank_log_{slug}.csv"


def shot_dir_for(slug):
    d = BASE_DIR / "screenshots" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def close_overlays(page):
    for text in ["閉じる", "닫기", "close", "Close", "×"]:
        try:
            btn = page.get_by_text(text, exact=False)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=1000)
                page.wait_for_timeout(400)
        except Exception:
            pass
    try:
        page.mouse.click(10, 10)
        page.wait_for_timeout(300)
    except Exception:
        pass


def navigate_category(page, category):
    """category의 url로 이동한 뒤, click_path에 있는 탭들을 순서대로 클릭합니다."""
    page.goto(category["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    close_overlays(page)
    for label in category.get("click_path", []):
        try:
            tab = page.get_by_text(label, exact=True)
            tab.first.click(timeout=5000)
            page.wait_for_timeout(1200)
        except Exception as e:
            print(f"  탭 '{label}' 클릭 실패: {e}")
    close_overlays(page)


def load_more(page, max_scrolls=15):
    last_count = -1
    for _ in range(max_scrolls):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(600)
        for text in ["更多", "もっと見る", "More", "もっと"]:
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
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)


def parse_ranking(page):
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
        el = None
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
            pass
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
    el = el_by_id.get(item_id)
    try:
        if el:
            el.scroll_into_view_if_needed(timeout=5000)
            page.evaluate(
                "(args) => { const el = args.el; const offset = args.offset;"
                " const r = el.getBoundingClientRect();"
                " window.scrollBy(0, r.top - offset); }",
                {"el": el, "offset": HEADER_OFFSET}
            )
        page.wait_for_timeout(1200)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        close_overlays(page)
        page.screenshot(path=str(shot_path), full_page=False)
        return True
    except Exception as e:
        print("스크린샷 저장 실패:", e)
        return False


def append_log(slug, rows):
    path = log_path_for(slug)
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp", "product_name", "item_id", "rank", "reviews", "matched_title", "url", "status"])
        for r in rows:
            w.writerow(r)


def last_found_rank(slug, identifier):
    path = log_path_for(slug)
    if not path.exists():
        return None
    last = None
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("item_id") == identifier or row.get("product_name") == identifier) and row.get("status", "found") == "found":
                last = row
    return last


def log_alert(msg):
    with open(ALERT_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    print("ALERT:", msg)


def cleanup_old_screenshots(slug, retention_days):
    d = shot_dir_for(slug)
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for f in d.glob("*.png"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            pass
    if removed:
        print(f"  [{slug}] {retention_days}일 지난 스크린샷 {removed}개 정리함")


def scrape_once(debug=True):
    config = load_config()
    threshold = config.get("alert_threshold", 5)
    retention_days = config.get("screenshot_retention_days", 14)
    categories_by_name = {c["name"]: c for c in config["categories"]}
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

        for product in config["products"]:
            target_id = extract_item_id(product.get("target_id") or product.get("target_url", ""))
            keyword = product.get("keyword", "")
            prod_name = product.get("name", target_id or keyword)
            track_categories = product.get("track_categories", list(categories_by_name.keys()))

            for cat_name in track_categories:
                category = categories_by_name.get(cat_name)
                if not category:
                    print(f"[{prod_name}] 알 수 없는 카테고리: {cat_name} (건너뜀)")
                    continue
                slug = category["slug"]
                print(f"\n[{prod_name}] 카테고리: {cat_name} ({slug})")

                navigate_category(page, category)
                load_more(page)

                items, el_by_id = parse_ranking(page)
                print(f"  총 {len(items)}개 상품을 읽었어요.")

                if debug:
                    DEBUG_HTML.write_text(page.content(), encoding="utf-8")

                matches = find_matches(items, target_id, keyword)
                row = None

                if not matches:
                    print(f"  상품을 찾지 못했어요.")
                    row = [ts, prod_name, target_id, "", "", "", category["url"], "not_found"]
                else:
                    m = matches[0]
                    print(f"  현재 {m['rank']}위 발견! - {m['title'][:40]}")

                    if product.get("screenshot", True):
                        shot_path = shot_dir_for(slug) / f"{m['item_id']}_{ts}.png"
                        capture_rank_area(page, el_by_id, m["item_id"], shot_path)

                    prev = last_found_rank(slug, target_id or prod_name)
                    if prev and prev.get("rank"):
                        delta = int(prev["rank"]) - m["rank"]
                        if abs(delta) >= threshold:
                            direction = "상승" if delta > 0 else "하락"
                            log_alert(f"[{cat_name}] {prod_name}: {abs(delta)}계단 {direction} ({prev['rank']}위 -> {m['rank']}위)")

                    row = [ts, prod_name, m["item_id"], m["rank"], m["reviews"] or "", m["title"], m["url"], "found"]

                append_log(slug, [row])
                cleanup_old_screenshots(slug, retention_days)

        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", default=True)
    args = parser.parse_args()
    scrape_once(debug=args.debug)
