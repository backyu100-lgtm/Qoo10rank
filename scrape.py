"""
Qoo10 재팬 랭킹 자동 추적 스크립트 (타겟 탐색형)

이전 방식(일단 최대한 다 불러온 뒤에 찾기)은 순위가 깊을 때
- 스크롤이 부족해서 못 찾거나
- 가상 스크롤 때문에 앞서 로딩된 상품이 DOM에서 사라지는 문제가 있었음.

이번 버전은 스크롤 한 번 할 때마다 "지금 화면에 타겟 상품이 있는지"를
바로바로 확인하고, 찾는 즉시 그 자리에서 멈춰서 순위/스크린샷을 확보함.
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
MAX_SCROLLS = 150
STABLE_ROUNDS_TO_STOP = 4


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
    page.goto(category["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    close_overlays(page)
    for label in category.get("click_path", []):
        clicked = False
        for exact in [False, True]:
            try:
                tab = page.get_by_text(label, exact=exact)
                if tab.count() > 0:
                    tab.first.scroll_into_view_if_needed(timeout=3000)
                    tab.first.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            # 마지막 수단: 텍스트가 정확히 일치하는 요소를 찾아서 JS로 강제 클릭
            try:
                page.evaluate(
                    """(label) => {
                        const els = Array.from(document.querySelectorAll('*'))
                            .filter(el => el.children.length === 0 && el.textContent.trim() === label);
                        if (els.length) { els[0].click(); return true; }
                        return false;
                    }""",
                    label
                )
                clicked = True
            except Exception as e:
                print(f"  탭 '{label}' 강제 클릭도 실패: {e}")
        if clicked:
            page.wait_for_timeout(1800)
        else:
            print(f"  탭 '{label}' 클릭 완전히 실패했어요 (이 카테고리는 건너뛰어질 수 있음)")
        close_overlays(page)
    close_overlays(page)


def read_current_anchors(page):
    anchors = page.query_selector_all('a[href*="/item/"], a[href*="goodscode="], a[href*="/g/"]')
    seen = set()
    out = []
    for a in anchors:
        href = a.get_attribute("href") or ""
        item_id = extract_item_id(href)
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        title = (a.get_attribute("title") or a.inner_text() or "").strip()
        out.append((item_id, title, href, a))
    return out


def get_container_info(a):
    rank, reviews, el = None, None, None
    try:
        container = a.evaluate_handle(
            "el => el.closest('li') || (el.parentElement && el.parentElement.parentElement)"
        )
        el = container.as_element()
        text = el.inner_text() if el else ""
        m = re.search(r"\(([\d,]+)\)", text)
        if m:
            reviews = m.group(1)
        for tok in text.split()[:3]:
            if tok.isdigit() and 1 <= int(tok) <= 300:
                rank = int(tok)
                break
    except Exception:
        pass
    return rank, reviews, el


def scroll_step(page):
    page.mouse.wheel(0, 3000)
    page.wait_for_timeout(900)
    for text in ["もっと見る", "More", "もっと", "更に読み込む"]:
        try:
            btn = page.get_by_text(text, exact=False)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=1000)
                page.wait_for_timeout(800)
        except Exception:
            pass


def find_target(page, target_id, keyword):
    last_count = -1
    stable_rounds = 0
    total_seen = 0

    for i in range(MAX_SCROLLS):
        anchors = read_current_anchors(page)
        total_seen = max(total_seen, len(anchors))
        for item_id, title, href, a in anchors:
            id_match = target_id and (item_id == target_id)
            kw_match = keyword and (keyword in title)
            if id_match or kw_match:
                rank, reviews, el = get_container_info(a)
                return {
                    "item_id": item_id, "title": title, "url": href,
                    "rank": rank, "reviews": reviews, "el": el or a
                }, total_seen

        count = len(anchors)
        if count == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_count = count
        if stable_rounds >= STABLE_ROUNDS_TO_STOP:
            break

        scroll_step(page)

    return None, total_seen


def capture_rank_area(page, el, shot_path):
    try:
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
                match, total_seen = find_target(page, target_id, keyword)

                if debug:
                    DEBUG_HTML.write_text(page.content(), encoding="utf-8")

                print(f"  최대 {total_seen}개까지 훑어봤어요.")

                if not match:
                    print("  상품을 찾지 못했어요.")
                    row = [ts, prod_name, target_id, "", "", "", category["url"], "not_found"]
                else:
                    print(f"  현재 {match['rank']}위 발견! - {match['title'][:40]}")

                    if product.get("screenshot", True):
                        shot_path = shot_dir_for(slug) / f"{match['item_id']}_{ts}.png"
                        capture_rank_area(page, match["el"], shot_path)

                    prev = last_found_rank(slug, target_id or prod_name)
                    if prev and prev.get("rank") and match["rank"]:
                        delta = int(prev["rank"]) - match["rank"]
                        if abs(delta) >= threshold:
                            direction = "상승" if delta > 0 else "하락"
                            log_alert(f"[{cat_name}] {prod_name}: {abs(delta)}계단 {direction} ({prev['rank']}위 -> {match['rank']}위)")

                    row = [ts, prod_name, match["item_id"], match["rank"] or "", match["reviews"] or "", match["title"], match["url"], "found"]

                append_log(slug, [row])
                cleanup_old_screenshots(slug, retention_days)

        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", default=True)
    args = parser.parse_args()
    scrape_once(debug=args.debug)
