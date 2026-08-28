"""가족 공유용 대시보드.

보유 종목·평가금액·토스 API 관련 코드가 이 파일에는 한 줄도 없다.
다루는 데이터는 거시지표, 뉴스, 그리고 '지금 전략이 추천하는 종목'뿐이며
마지막 것도 실제 보유 여부와 무관하게 계산되는 신호다.

로컬 실행:
    streamlit run family_dashboard.py

클라우드 배포 시에는 로컬 파일(data/*.json)을 읽지 않고 이 화면이 열릴 때마다
직접 API를 호출한다 — 그래야 내 맥이 꺼져 있어도 최신 데이터가 뜬다.
ECOS_API_KEY / FRED_API_KEY 는 st.secrets 에서 읽는다 (없으면 환경변수로 대체,
로컬 테스트용).
"""

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))

st.set_page_config(page_title="시장 브리핑", page_icon="📰", layout="wide")


def _secret_or_env(key: str) -> str:
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, "")


# ---------------------------------------------------------------- 거시지표

@st.cache_data(ttl=3600)   # 1시간 캐시 — 매 방문마다 API를 두드리지 않도록
def fetch_indicators_cached():
    import indicators
    ecos_key = _secret_or_env("ECOS_API_KEY")
    fred_key = _secret_or_env("FRED_API_KEY")
    if not ecos_key or not fred_key:
        return None, "ECOS/FRED 키가 설정되지 않았습니다."
    try:
        cfg = indicators.load_config()
        return indicators.collect(cfg, ecos_key, fred_key), None
    except Exception as e:
        return None, str(e)


def section_indicators(ncols: int = 3, compact: bool = False):
    st.subheader("거시지표")
    data, err = fetch_indicators_cached()
    if data is None:
        st.info(f"지표를 불러오지 못했습니다: {err}")
        return

    ok = {k: v for k, v in data.items() if "error" not in v}
    if not ok:
        st.info("수집된 지표가 없습니다.")
        return

    cols = st.columns(min(len(ok), ncols))
    for i, (nm, v) in enumerate(ok.items()):
        cur, prev = v["value"], v.get("prev")
        delta = cur - prev if prev is not None else None
        delta_str = f"{delta:+.2f}" if delta is not None else None

        cols[i % len(cols)].metric(
            nm, f"{cur:,.2f}", delta_str,
            help=f"기준일 {v['date']} · {v['source']}"
        )

    # 좁은 칸에서는 해석을 접어둔다 (안 그러면 세로로 너무 길어짐)
    interps = [(nm, v.get("interp", "")) for nm, v in ok.items() if v.get("interp")]
    if interps:
        if compact:
            with st.expander("지표 해석 보기"):
                for nm, txt in interps:
                    st.caption(f"**{nm}** — {txt}")
        else:
            st.divider()
            for nm, txt in interps:
                st.caption(f"**{nm}** — {txt}")

    st.caption(f"조회: {datetime.now(KST):%m/%d %H:%M} (1시간 캐시)")


# ---------------------------------------------------------------- 뉴스

@st.cache_data(ttl=1800)   # 30분 캐시
def fetch_news_cached():
    import news
    try:
        cfg = news.load_config()
        return news.collect(cfg), None
    except Exception as e:
        return None, str(e)


def section_news(limit: int = 15):
    st.subheader("경제 뉴스")
    items, err = fetch_news_cached()
    if not items:
        st.info(f"뉴스를 불러오지 못했습니다: {err or '수집된 기사가 없습니다.'}")
        return

    for it in items[:limit]:
        pub = datetime.fromisoformat(it["published"]).astimezone(KST)
        st.markdown(
            f"**[{it['title']}]({it['link']})**  \n"
            f"<span style='color:gray;font-size:0.85em'>{it['source']} · {pub:%m/%d %H:%M}</span>",
            unsafe_allow_html=True,
        )
    st.caption(f"조회: {datetime.now(KST):%m/%d %H:%M} (30분 캐시) · "
              "제목·링크만 표시합니다. 본문은 링크를 눌러 언론사에서 읽어주세요.")


# ---------------------------------------------------------------- 전략 신호

@st.cache_data(ttl=1800)
def fetch_signal_cached():
    import signal_now
    from run import load_config
    from universe import name as uname
    try:
        cfg = load_config()
        sig = signal_now.compute_signal(cfg)
        return {
            "asof": str(sig["asof"].date()),
            "market_pass": sig["market_pass"],
            "picks": sig["picks"],
            "scores": {t: float(sig["scores"][t]) for t in sig["picks"]},
            "names": {t: uname(t) for t in sig["picks"]},
        }, None
    except Exception as e:
        return None, str(e)


def section_strategy_picks():
    st.subheader("지금 전략이 주목하는 종목")
    st.caption("모멘텀 전략이 오늘 기준으로 계산한 관심 종목입니다. "
              "실제 누군가의 보유 종목과는 무관한, 순수한 시장 신호입니다.")

    sig, err = fetch_signal_cached()
    if sig is None:
        st.info(f"신호 계산을 아직 못했습니다: {err}")
        return

    st.caption(f"기준일: {sig['asof']}")

    if not sig["market_pass"]:
        st.warning("시장 필터 탈락 — 전략상 지금은 현금 보유 구간입니다.")
        return

    if not sig["picks"]:
        st.write("조건을 만족하는 종목이 없습니다.")
        return

    import claude_buttons
    st.caption("종목명 옆 버튼을 클릭하면 Claude Desktop에서 바로 분석이 시작됩니다 "
              "(Claude Desktop 앱 필요, 무료).")

    rows = sorted(sig["picks"], key=lambda t: sig["scores"][t], reverse=True)
    for t in rows:
        nm = sig["names"].get(t, t)
        claude_buttons.render_pick_row(nm, t, sig["scores"][t], key_prefix="fam_")


def section_realtime_ranking():
    import realtime_ranking
    realtime_ranking.section_realtime_ranking(key_prefix="fam_")


def section_market_ranking():
    import market_ranking
    market_ranking.section_market_ranking(key_prefix="fam_", top=300)


def section_stock_search():
    import claude_buttons
    claude_buttons.section_stock_search(key_prefix="fam_")


def _render_stock_analysis():
    """OpenAI 기반 종목 분석 — 현재 비활성화 (계정에 크레딧 없음).

    나중에 OpenAI 계정에 결제수단/크레딧을 등록하면 main()에서
    이 함수 호출 줄의 주석만 풀면 다시 켜진다. analysis_ui.py 등은
    그대로 남아 있어 코드를 다시 만들 필요 없다.
    """
    try:
        import analysis_ui
    except ImportError as e:
        st.caption(f"종목 분석 모듈을 불러오지 못했습니다: {e}")
        return

    candidates = {}
    sig, _ = fetch_signal_cached()
    if sig and sig.get("picks"):
        candidates = {t: sig["names"].get(t, t) for t in sig["picks"]}

    analysis_ui.section_stock_analysis(candidates, key_prefix="fam_")


def section_investor_trading():
    import investor_trading
    cands = {}
    try:
        import signal_now
        from universe import name as uname
        from run import load_config
        sig = signal_now.compute_signal(load_config())
        cands = {t: uname(t) for t in sig.get("picks", [])}
    except Exception:
        pass
    investor_trading.section_investor_trading(cands, key_prefix="fam_")


def main():
    st.title("시장 브리핑")
    st.caption("가족과 공유하는 화면입니다. 개인 보유 종목이나 금액 정보는 포함되지 않습니다.")

    # 상단: 거시지표(왼쪽) | 경제뉴스(오른쪽)
    left, right = st.columns([1, 1], gap="large")
    with left:
        section_indicators(ncols=2, compact=True)
    with right:
        section_news(limit=8)

    st.divider()
    section_market_ranking()

    st.divider()
    section_strategy_picks()
    st.divider()
    section_realtime_ranking()
    st.divider()
    section_investor_trading()
    st.divider()
    section_stock_search()
    # OpenAI 크레딧 등록 전까지 비활성화. 다시 쓰려면 아래 줄 주석 해제.
    # st.divider()
    # _render_stock_analysis()

    st.divider()
    st.caption("이 화면은 참고용입니다. 투자 판단은 각자의 책임입니다.")


if __name__ == "__main__":
    main()

