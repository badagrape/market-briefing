"""투자자별 매매동향 (개인 / 외국인 / 기관).

실제 응답 구조 (확인함):
    {"result": {"nextUntil": "2026-08-13", "records": [
        {"date": "2026-08-28",
         "individual":  {"buyVolume","sellVolume","netBuyVolume"},
         "foreigner":   {...},
         "institution": {..., "breakdown": {"pensionFund", "trust", ...}},
         "otherCorporation": {...},
         "foreignerHolding": {"holdingQuantity","limitQuantity",...}}, ...]}}

주의:
- 숫자가 전부 문자열 (다른 토스 API와 동일)
- 단위는 '주식 수'(Volume)다. 금액이 아니다.
- 실시간이 아니다. 거래소가 장 마감 후 집계한 확정치이며
  updatedAt 을 보면 새벽에 갱신된다.

이 모듈은 숫자만 정확히 보여준다. "외국인이 사면 오른다" 같은 해석은
검증된 바 없어 코드에 넣지 않는다.
"""

import os

import pandas as pd
import requests

BASE = "https://openapi.tossinvest.com"

INVESTOR_LABELS = {
    "individual": "개인",
    "foreigner": "외국인",
    "institution": "기관",
    "otherCorporation": "기타법인",
}

INSTITUTION_LABELS = {
    "pensionFund": "연기금",
    "trust": "투신",
    "privateEquityFund": "사모",
    "financialInvestment": "금융투자",
    "insurance": "보험",
    "bank": "은행",
    "otherFinancialInstitution": "기타금융",
}


def _num(x, default=0.0) -> float:
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _creds(secret_getter=None) -> tuple:
    cid = (secret_getter("TOSS_CLIENT_ID") if secret_getter else None) \
        or os.environ.get("TOSS_CLIENT_ID", "")
    sec = (secret_getter("TOSS_CLIENT_SECRET") if secret_getter else None) \
        or os.environ.get("TOSS_CLIENT_SECRET", "")
    return cid.strip(), sec.strip()


def fetch_investor_trading(symbol: str, until: str = None,
                           secret_getter=None) -> dict:
    """종목별 투자자 매매동향 원본 조회. until 로 과거 페이지를 더 받는다."""
    import toss_api

    cid, sec = _creds(secret_getter)
    if not cid or not sec:
        raise RuntimeError("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 이 필요합니다.")

    params = {}
    if until:
        params["until"] = until

    def call(tok):
        return requests.get(
            f"{BASE}/api/v1/stocks/{symbol}/investor-trading",
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
            params=params, timeout=20)

    token = toss_api.get_access_token(cid, sec)
    r = call(token)
    if r.status_code == 401:
        token = toss_api.get_access_token(cid, sec, force=True)
        r = call(token)

    if r.status_code != 200:
        raise RuntimeError(f"매매동향 조회 실패 ({r.status_code}): {r.text[:200]}")

    return r.json().get("result", {})


def load_history(symbol: str, days: int = 20, secret_getter=None) -> pd.DataFrame:
    """며칠치 추이를 DataFrame으로. 한 번에 보름치쯤 오므로 필요시 페이징."""
    records, until, guard = [], None, 0

    while len(records) < days and guard < 4:
        result = fetch_investor_trading(symbol, until, secret_getter)
        page = result.get("records", [])
        if not page:
            break
        records.extend(page)
        until = result.get("nextUntil")
        if not until:
            break
        guard += 1

    if not records:
        return pd.DataFrame()

    rows = []
    for rec in records[:days]:
        row = {"날짜": rec.get("date")}
        for key, label in INVESTOR_LABELS.items():
            row[label] = _num(rec.get(key, {}).get("netBuyVolume"))

        inst = rec.get("institution", {}).get("breakdown", {}) or {}
        for key, label in INSTITUTION_LABELS.items():
            row[label] = _num(inst.get(key, {}).get("netBuyVolume"))

        fh = rec.get("foreignerHolding", {}) or {}
        held, limit = _num(fh.get("holdingQuantity")), _num(fh.get("limitQuantity"))
        row["외국인지분율"] = (held / limit * 100) if limit else None

        rows.append(row)

    df = pd.DataFrame(rows)
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df.sort_values("날짜").reset_index(drop=True)


def summarize(df: pd.DataFrame, days: int = 5) -> dict:
    """최근 N일 누적 순매수와 연속 매수/매도 일수."""
    if df.empty:
        return {}

    recent = df.tail(days)
    out = {}
    for label in INVESTOR_LABELS.values():
        if label not in df.columns:
            continue
        total = recent[label].sum()

        # 연속 일수 (부호가 같은 날이 며칠 이어졌나)
        streak, sign = 0, None
        for v in reversed(df[label].tolist()):
            s = 1 if v > 0 else (-1 if v < 0 else 0)
            if s == 0:
                break
            if sign is None:
                sign = s
            if s != sign:
                break
            streak += 1

        out[label] = {"누적": total, "연속": streak, "방향": sign}
    return out


def section_investor_trading(candidates: dict = None, key_prefix: str = "") -> None:
    """투자자별 매매동향 섹션."""
    import streamlit as st

    st.subheader("투자자별 매매동향")
    st.caption("개인·외국인·기관의 순매수 추이입니다. "
              "거래소가 장 마감 후 집계하는 확정치라 실시간은 아닙니다.")

    def _secret(k):
        try:
            if k in st.secrets:
                return st.secrets[k]
        except Exception:
            pass
        return None

    if not (_secret("TOSS_CLIENT_ID") or os.environ.get("TOSS_CLIENT_ID")):
        st.info("토스증권 API 키가 설정되면 매매동향이 표시됩니다.")
        return

    # --- 종목 선택
    c1, c2 = st.columns([3, 1])
    if candidates:
        opts = [f"{nm} ({tk})" for tk, nm in candidates.items()]
        picked = c1.selectbox("종목", opts + ["(직접 입력)"],
                             key=f"{key_prefix}inv_pick")
        if picked == "(직접 입력)":
            symbol = c1.text_input("종목코드", key=f"{key_prefix}inv_code")
        else:
            symbol = picked.rsplit(" (", 1)[1].rstrip(")")
    else:
        symbol = c1.text_input("종목코드", value="005930",
                              key=f"{key_prefix}inv_code")

    days = c2.selectbox("기간", [5, 10, 20], index=1, key=f"{key_prefix}inv_days")

    if not symbol:
        return

    try:
        df = _cached_history(symbol, days, _secret)
    except Exception as e:
        st.error(f"매매동향을 불러오지 못했습니다: {e}")
        return

    if df.empty:
        st.info("데이터가 없습니다.")
        return

    # --- 요약 (최근 5일 누적 + 연속 일수)
    summ = summarize(df, days=min(5, len(df)))
    cols = st.columns(4)
    for i, (label, s) in enumerate(summ.items()):
        arrow = "순매수" if s["방향"] == 1 else "순매도" if s["방향"] == -1 else ""
        streak_txt = f"{s['연속']}일 연속 {arrow}" if s["연속"] > 1 else None
        cols[i].metric(label, f"{s['누적']:+,.0f}주", streak_txt,
                      help="최근 5일 누적 순매수 수량")

    # --- 추이 차트
    main_cols = [c for c in ["개인", "외국인", "기관"] if c in df.columns]
    chart_df = df.set_index("날짜")[main_cols]
    st.bar_chart(chart_df, height=260)
    st.caption("막대가 0 위면 순매수, 아래면 순매도 (단위: 주식 수)")

    # --- 상세 표
    with st.expander("일자별 상세 · 기관 세부"):
        show = df.copy()
        show["날짜"] = show["날짜"].dt.strftime("%m/%d")
        num_cols = [c for c in show.columns if c not in ("날짜",)]
        st.dataframe(
            show.iloc[::-1], hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%,d")
                          for c in num_cols if c != "외국인지분율"},
        )
        st.caption("연기금·투신·사모 등은 기관 내 세부 주체입니다. "
                  "외국인지분율은 한도 대비 보유 비율(%)입니다.")

    st.caption("이 수치는 이미 체결된 거래의 집계입니다. "
              "특정 주체의 순매수가 이후 주가를 예측한다는 근거는 확립되어 있지 않습니다.")


def _cached_history(symbol: str, days: int, secret_getter):
    import streamlit as st

    @st.cache_data(ttl=1800)
    def _inner(sym, d):
        return load_history(sym, d, secret_getter)

    return _inner(symbol, days)
