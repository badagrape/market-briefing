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


def section_indicators():
    st.subheader("거시지표")
    data, err = fetch_indicators_cached()
    if data is None:
        st.info(f"지표를 불러오지 못했습니다: {err}")
        return

    ok = {k: v for k, v in data.items() if "error" not in v}
    if ok:
        cols = st.columns(min(len(ok), 4))
        for i, (nm, v) in enumerate(ok.items()):
            cols[i % len(cols)].metric(nm, f"{v['value']:,.2f}",
                                       help=f"기준일 {v['date']} · {v['source']}")
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


def section_news():
    st.subheader("경제 뉴스")
    items, err = fetch_news_cached()
    if not items:
        st.info(f"뉴스를 불러오지 못했습니다: {err or '수집된 기사가 없습니다.'}")
        return

    for it in items[:15]:
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

    rows = [{"종목명": sig["names"].get(t, t), "종목코드": t, "모멘텀 점수": sig["scores"][t]} for t in sig["picks"]]
    df = pd.DataFrame(rows).sort_values("모멘텀 점수", ascending=False)
    st.dataframe(df.style.format({"모멘텀 점수": "{:+.1%}"}),
                hide_index=True, use_container_width=True)


def main():
    st.title("시장 브리핑")
    st.caption("가족과 공유하는 화면입니다. 개인 보유 종목이나 금액 정보는 포함되지 않습니다.")

    section_indicators()
    st.divider()
    section_strategy_picks()
    st.divider()
    section_news()

    st.divider()
    st.caption("이 화면은 참고용입니다. 투자 판단은 각자의 책임입니다.")


if __name__ == "__main__":
    main()

