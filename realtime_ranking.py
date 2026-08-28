"""토스 실시간 랭킹.

GET /api/v1/rankings 한 번 호출로 최대 100종목을 받는다.
응답 구조 (실제 확인함):
    {"result": {"rankedAt": "...", "rankings": [
        {"rank": 1, "symbol": "000660", "currency": "KRW",
         "price": {"lastPrice": "1658000", "basePrice": "1730000",
                   "changeRate": "-0.0416"},
         "tradingVolume": "86171", "tradingAmount": "142809620000"}, ...]}}

주의:
- 숫자가 전부 문자열로 온다 (계좌 API와 동일)
- changeRate 는 소수 (-0.0416 = -4.16%)
- 종목명이 없어서 종목 목록으로 따로 붙여야 한다
"""

import os

import pandas as pd
import requests

BASE = "https://openapi.tossinvest.com"

RANKING_TYPES = {
    "MARKET_TRADING_AMOUNT": "거래대금",
    "MARKET_TRADING_VOLUME": "거래량",
    "TOP_GAINERS": "급상승",
    "TOP_LOSERS": "급하락",
    "TOSS_SECURITIES_TRADING_AMOUNT": "토스증권 거래대금",
    "TOSS_SECURITIES_TRADING_VOLUME": "토스증권 거래량",
}

# TOP_GAINERS / TOP_LOSERS 는 realtime 미지원 (400 에러)
NO_REALTIME = {"TOP_GAINERS", "TOP_LOSERS"}

DURATIONS = {
    "realtime": "실시간", "1d": "1일", "1w": "1주",
    "1mo": "1개월", "3mo": "3개월", "6mo": "6개월", "1y": "1년",
}


def _num(x, default=0.0) -> float:
    """토스 API는 숫자를 문자열로 준다."""
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fetch_rankings(rtype: str = "MARKET_TRADING_AMOUNT",
                   duration: str = "realtime",
                   market: str = "KR", count: int = 100,
                   exclude_caution: bool = True,
                   secret_getter=None) -> tuple:
    """랭킹 조회. (DataFrame, 집계시각) 반환."""
    import toss_api

    cid = (secret_getter("TOSS_CLIENT_ID") if secret_getter else None) \
        or os.environ.get("TOSS_CLIENT_ID", "")
    sec = (secret_getter("TOSS_CLIENT_SECRET") if secret_getter else None) \
        or os.environ.get("TOSS_CLIENT_SECRET", "")
    cid, sec = cid.strip(), sec.strip()

    if not cid or not sec:
        raise RuntimeError("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 이 필요합니다.")

    if rtype in NO_REALTIME and duration == "realtime":
        duration = "1d"     # 이 조합은 API가 400을 준다

    token = toss_api.get_access_token(cid, sec)
    r = requests.get(
        f"{BASE}/api/v1/rankings",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"type": rtype, "marketCountry": market,
                "duration": duration, "count": min(count, 100),
                "excludeInvestmentCaution": str(exclude_caution).lower()},
        timeout=20,
    )

    if r.status_code == 401:        # 토큰 만료 → 1회 재발급 후 재시도
        token = toss_api.get_access_token(cid, sec, force=True)
        r = requests.get(
            f"{BASE}/api/v1/rankings",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"type": rtype, "marketCountry": market,
                    "duration": duration, "count": min(count, 100),
                    "excludeInvestmentCaution": str(exclude_caution).lower()},
            timeout=20,
        )

    if r.status_code != 200:
        raise RuntimeError(f"랭킹 조회 실패 ({r.status_code}): {r.text[:200]}")

    result = r.json().get("result", {})
    items = result.get("rankings", [])
    ranked_at = result.get("rankedAt")

    if not items:
        return pd.DataFrame(), ranked_at

    rows = []
    for it in items:
        p = it.get("price", {})
        rows.append({
            "순위": it.get("rank"),
            "코드": it.get("symbol", ""),
            "현재가": _num(p.get("lastPrice")),
            "등락률": _num(p.get("changeRate")) * 100,      # 소수 → %
            "거래량": _num(it.get("tradingVolume")),
            "거래대금(억)": round(_num(it.get("tradingAmount")) / 1e8),
        })

    return pd.DataFrame(rows), ranked_at


def attach_names(df: pd.DataFrame, listing: pd.DataFrame = None,
                 industry_map: dict = None) -> pd.DataFrame:
    """종목명·업종을 붙인다. 랭킹 API는 symbol 만 주기 때문."""
    if df.empty:
        return df

    df = df.copy()
    if listing is not None and not listing.empty:
        name_map = dict(zip(listing["Code"], listing["Name"]))
        df["종목명"] = df["코드"].map(name_map).fillna(df["코드"])
    else:
        df["종목명"] = df["코드"]

    if industry_map:
        df["업종"] = df["코드"].map(industry_map).fillna("")
    else:
        df["업종"] = ""

    cols = ["순위", "종목명", "코드", "업종", "현재가", "등락률", "거래대금(억)"]
    return df[cols]


def section_realtime_ranking(key_prefix: str = "") -> None:
    """실시간 랭킹 섹션 (토스 앱의 그 탭들)."""
    import streamlit as st
    import claude_buttons

    st.subheader("실시간 랭킹")

    def _secret(k):
        try:
            if k in st.secrets:
                return st.secrets[k]
        except Exception:
            pass
        return None

    if not ((_secret("TOSS_CLIENT_ID") or os.environ.get("TOSS_CLIENT_ID"))
            and (_secret("TOSS_CLIENT_SECRET") or os.environ.get("TOSS_CLIENT_SECRET"))):
        st.info("토스증권 API 키가 설정되면 실시간 랭킹이 표시됩니다. "
                "(TOSS_CLIENT_ID / TOSS_CLIENT_SECRET)")
        return

    c1, c2 = st.columns([2, 1])
    label_to_type = {v: k for k, v in RANKING_TYPES.items()}
    picked_label = c1.selectbox("기준", list(RANKING_TYPES.values()),
                                key=f"{key_prefix}rt_type")
    rtype = label_to_type[picked_label]

    dur_options = [d for d in DURATIONS
                   if not (rtype in NO_REALTIME and d == "realtime")]
    picked_dur = c2.selectbox("기간", [DURATIONS[d] for d in dur_options],
                              key=f"{key_prefix}rt_dur")
    duration = [d for d in dur_options if DURATIONS[d] == picked_dur][0]

    try:
        df, ranked_at = _cached_rankings(rtype, duration)
    except Exception as e:
        st.error(f"랭킹을 불러오지 못했습니다: {e}")
        return

    if df.empty:
        st.info("이 조합은 랭킹이 집계되지 않았습니다. 다른 기준을 골라보세요.")
        return

    # 종목명·업종 붙이기.
    # 급상승 랭킹에는 소형주가 많아 시총 상위 목록만으로는 이름이 안 붙는다.
    # 전체 상장목록(stock_search)으로 이름을 먼저 채우고, 업종은 있는 만큼만.
    listing = None
    industry_map = {}
    try:
        import claude_buttons
        listing = claude_buttons._cached_listing()      # 전체 종목 (2,700여개)
    except Exception:
        pass
    try:
        import market_ranking
        base, _ = market_ranking._cached_ranking(500)
        industry_map = dict(zip(base["Code"], base["업종"]))
        if listing is None:
            listing = base[["Code", "Name"]]
    except Exception:
        pass

    df = attach_names(df, listing, industry_map)

    if ranked_at:
        st.caption(f"집계 시각: {ranked_at[:19].replace('T', ' ')}")

    st.dataframe(
        df, hide_index=True, width="stretch", height=420,
        column_config={
            "순위": st.column_config.NumberColumn(width="small"),
            "현재가": st.column_config.NumberColumn(format="%,d"),
            "등락률": st.column_config.NumberColumn(format="%+.2f%%"),
            "거래대금(억)": st.column_config.NumberColumn(format="%,d"),
        },
    )

    options = [f"{r.종목명} ({r.코드})" for r in df.head(50).itertuples()]
    sel = st.selectbox("이 중에서 분석할 종목", ["(선택 안 함)"] + options,
                       key=f"{key_prefix}rt_pick")
    if sel != "(선택 안 함)":
        nm = sel.rsplit(" (", 1)[0]
        tk = sel.rsplit(" (", 1)[1].rstrip(")")
        claude_buttons.render_skill_buttons(nm, tk, key_prefix=f"{key_prefix}rt_")

    st.caption("토스증권 Open API 기준. 최대 100위까지 제공됩니다.")


def _cached_rankings(rtype: str, duration: str):
    import streamlit as st

    ttl = 60 if duration == "realtime" else 600

    @st.cache_data(ttl=ttl)
    def _inner(t, d):
        return fetch_rankings(t, d)

    return _inner(rtype, duration)
