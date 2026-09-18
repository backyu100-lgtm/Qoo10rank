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


def click_tab(page, label):
    """
    label이 '포함된' 텍스트를 가진 요소들 중, 화면에 보이면서 글자 수가 가장 짧은
    것을 클릭합니다 (진짜 탭 라벨일수록 텍스트가 짧고, 잘못 걸리는 큰 래퍼
    요소일수록 텍스트가 김 -> 짧은 것 우선으로 오탐을 줄임).
    메인 페이지와 iframe을 모두 뒤집니다.
    """
    js = """(label) => {
        const all = Array.from(document.querySelectorAll('*'));
        let candidates = all.filter(el => el.textContent && el.textContent.includes(label));
        candidates = candidates.filter(el => el.offsetWidth > 0 && el.offsetHeight > 0);
        candidates.sort((a, b) => a.textContent.trim().length - b.textContent.trim().length);
        const target = candidates[0];
        if (target) {
            target.scrollIntoView({block: 'center'});
            target.click();
            return {clicked: true, total: candidates.length, text: target.textContent.trim().slice(0, 30)};
        }
        return {clicked: false, total: candidates.length, text: ''};
    }"""
    print(f"  (페이지 내 프레임 수: {len(page.frames)})")
    for idx, frame in enumerate(page.frames):
        try:
            result = frame.evaluate(js, label)
        except Exception:
            continue
        if result["total"] > 0:
            tag = "메인" if idx == 0 else f"iframe#{idx}"
            print(f"  탭 '{label}' [{tag}] 후보 {result['total']}개, 선택된 텍스트: \"{result['text']}\" -> 클릭: {result['clicked']}")
            if result["clicked"]:
                return True
    print(f"  탭 '{label}' 어떤 프레임에서도 후보를 찾지 못했어요")
    return False


def navigate_category(page, category):
    page.goto(category["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    close_overlays(page)
    # 탭 영역이 화면에 실제로 노출되도록 살짝 스크롤 (지연 마운트 대비)
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(600)
    for label in category.get("click_path", []):
        clicked = click_tab(page, label)
        if not clicked:
            print(f"  탭 '{label}' 클릭 완전히 실패했어요 (이 카테고리는 건너뛰어질 수 있음)")
        page.wait_for_timeout(1800)
        close_overlays(page)
    close_overlays(page)


def read_current_anchors(page):
    out = []
    for frame in page.frames:
        try:
            anchors = frame.query_selector_all('a[href*="/item/"], a[href*="goodscode="], a[href*="/g/"]')
        except Exception:
            continue
        seen = set()
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


def wait_images_loaded(page, timeout_ms=6000):
    """현재 화면에 보이는 이미지들이 실제로 다 로드될 때까지 기다립니다 (빈 회색 박스 방지)."""
    try:
        page.wait_for_function(
            """() => {
                const imgs = Array.from(document.querySelectorAll('img'));
                const visible = imgs.filter(i => {
                    const r = i.getBoundingClientRect();
                    return r.top < window.innerHeight && r.bottom > 0 && r.width > 0;
                });
                if (visible.length === 0) return true;
                return visible.every(i => i.complete && i.naturalWidth > 0);
            }""",
            timeout=timeout_ms
        )
    except Exception:
        pass  # 시간 내에 다 못 끝나도 일단 진행 (완전히 멈추게 하지 않음)


def capture_rank_area(page, el, shot_path):
    try:
        el.scroll_into_view_if_needed(timeout=5000)
        page.evaluate(
            "(args) => { const el = args.el; const offset = args.offset;"
            " const r = el.getBoundingClientRect();"
            " window.scrollBy(0, r.top - offset); }",
            {"el": el, "offset": HEADER_OFFSET}
        )
        page.wait_for_timeout(1000)
        # 살짝 더 스크롤했다가 되돌아오면서 지연 로딩 트리거를 한 번 더 자극
        page.mouse.wheel(0, 200)
        page.wait_for_timeout(300)
        page.mouse.wheel(0, -200)
        page.wait_for_timeout(500)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        wait_images_loaded(page)
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
