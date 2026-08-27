"""경제 뉴스 헤드라인 수집: RSS (크롤링 아님).

제목·링크·발행시각·출처까지만 가져온다. 본문은 절대 수집하지 않는다 —
본문 전문을 가져오는 건 언론사 저작물을 복제하는 것이라 저작권 문제가 된다.
필요하면 링크를 눌러 언론사 페이지에서 읽는 구조로 둔다.

    python3 news.py --test    # 피드별 생사와 건수만 확인, 저장 안 함
    python3 news.py           # data/news.json 저장
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import yaml

HERE = Path(__file__).parent
OUT = HERE / "data"


def load_config() -> dict:
    with open(HERE / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["context"]


def _repair_xml(raw: str) -> str:
    """깨진 XML 실체(entity)를 복구.

    일부 언론사 피드는 &nbsp; 같은 HTML 실체를 XML에 그대로 써서
    'undefined entity' 파싱 오류를 낸다. XML이 기본으로 아는 실체는
    &amp; &lt; &gt; &quot; &apos; 다섯 개뿐이므로 나머지를 숫자 참조로 바꾼다.
    """
    import html as _html
    import re as _re

    def sub(m):
        ent = m.group(1)
        if ent in ("amp", "lt", "gt", "quot", "apos") or ent.startswith("#"):
            return m.group(0)
        ch = _html.unescape(f"&{ent};")
        if ch == f"&{ent};":          # 정체불명 → 통째로 escape
            return f"&amp;{ent};"
        return f"&#{ord(ch[0])};"

    return _re.sub(r"&([A-Za-z][A-Za-z0-9]*|#\d+|#x[0-9A-Fa-f]+);", sub, raw)


def fetch_feed(name: str, url: str) -> tuple:
    """(entries, 상태메시지) 반환. entries는 실패 시 빈 리스트."""
    feed = feedparser.parse(url)

    # 파싱 실패 시: 직접 받아서 XML을 복구한 뒤 재시도
    if getattr(feed, "bozo", 0) and not feed.entries:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8", errors="replace")
            feed = feedparser.parse(_repair_xml(raw))
            if feed.entries:
                print(f"    (XML 복구 후 파싱 성공)")
        except Exception as e:
            return [], f"파싱 실패: {e}"

    if not feed.entries:
        if getattr(feed, "bozo", 0):
            return [], f"파싱 실패 (bozo): {getattr(feed, 'bozo_exception', '알 수 없음')}"
        return [], "항목 0건 (죽은 피드이거나 주소 변경)"
    if not feed.entries:
        return [], "항목 0건 (죽은 피드이거나 주소 변경)"

    rows = []
    for e in feed.entries:
        pub = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if not pub:
            continue
        rows.append({
            "title": e.title.strip(),
            "link": e.link,
            "published": datetime(*pub[:6], tzinfo=timezone.utc).isoformat(),
            "source": name,
            "summary": getattr(e, "summary", "")[:150],
        })

    if not rows:
        return [], "항목은 있으나 발행시각 파싱 불가 (형식이 표준과 다를 수 있음)"
    return rows, f"{len(rows)}건"


def collect(cfg: dict) -> list:
    all_rows = []
    for feed in cfg["rss_feeds"]:
        rows, status = fetch_feed(feed["name"], feed["url"])
        print(f"  {feed['name']:14s} {status}")
        all_rows.extend(rows)

    # 링크 기준 중복 제거
    seen = set()
    deduped = []
    for r in all_rows:
        if r["link"] not in seen:
            seen.add(r["link"])
            deduped.append(r)

    # 최근 N시간만
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg["news_lookback_hours"])
    recent = [r for r in deduped if datetime.fromisoformat(r["published"]) >= cutoff]

    recent.sort(key=lambda r: r["published"], reverse=True)
    return recent[: cfg["news_max_items"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="피드 상태만 확인, 저장 안 함")
    args = ap.parse_args()

    cfg = load_config()
    print("RSS 피드 확인 중...\n")
    items = collect(cfg)

    print(f"\n최근 {cfg['news_lookback_hours']}시간 내 {len(items)}건 (중복 제거·개수 제한 후)")
    for it in items[:5]:
        print(f"  [{it['source']}] {it['title']}")
    if len(items) > 5:
        print(f"  ... 외 {len(items) - 5}건")

    if args.test:
        print("\n--test: 저장하지 않음.")
        print("피드 중 '항목 0건'이나 '파싱 실패'가 있으면 config.yaml의 URL을 교체하세요.")
        return

    OUT.mkdir(exist_ok=True)
    target = OUT / "news.json"

    # 전부 실패해서 0건이면 기존 파일을 지우지 않는다.
    # 자동 실행 중 일시적 네트워크 장애로 멀쩡한 데이터를 날리는 사고 방지.
    if not items and target.exists():
        print("\n수집 0건 — 기존 news.json 을 보존합니다 (덮어쓰지 않음).")
        raise SystemExit(1)

    target.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {target}")


if __name__ == "__main__":
    main()
