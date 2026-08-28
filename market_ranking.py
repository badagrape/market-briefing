"""시가총액 순위표 (업종 포함).

토스증권 순위 화면과 비슷한 표를 만든다. 데이터는 FinanceDataReader
프로젝트가 GitHub에 매일 올려두는 KRX 스냅샷 두 개를 합쳐서 쓴다:
  - listing/krx  : 시세·시가총액·거래대금
  - listing/desc : 업종(Industry)·주요제품(Products)

토스의 "AI 요약", "토스증권 거래 비율"은 토스 자체 서비스 데이터라
공개 경로가 없어 가져올 수 없다.
"""

import re
from datetime import date, timedelta

import pandas as pd
import requests

BASE = ("https://raw.githubusercontent.com/FinanceData/"
        "fdr_krx_data_cache/refs/heads/master/data/listing")

EXCLUDE_NAME = re.compile(r"(?:스팩|기업인수목적|리츠$|인프라$|밸류$|ETN|ETF)")

# 통계청 표준산업분류는 너무 길다 ("자동차용 엔진 및 자동차 제조업").
# 토스처럼 한눈에 읽히게 줄인다. 위에서부터 먼저 맞는 규칙을 적용.
INDUSTRY_SHORT = [
    (r"반도체", "반도체"),
    (r"일차전지|이차전지", "2차전지"),
    (r"자동차용 엔진|자동차 제조", "자동차"),
    (r"자동차 신품 부품|자동차 부품", "자동차부품"),
    (r"항공기|우주선", "항공우주"),
    (r"선박 및 보트|선박", "조선"),
    (r"전동기|발전기|송전|배전", "전력장비"),
    (r"통신 및 방송 장비", "통신장비"),
    (r"전자부품", "전자부품"),
    (r"컴퓨터 프로그래밍|시스템 통합|소프트웨어", "소프트웨어"),
    (r"포털|인터넷", "인터넷"),
    (r"기초 의약물질|의약품 제조", "제약"),
    (r"의료용 기기", "의료기기"),
    (r"생물학적 제제", "바이오"),
    (r"은행", "은행"),
    (r"보험", "보험"),
    (r"금융 지원|증권", "증권"),
    (r"기타 금융업", "지주/금융"),
    (r"전기업|발전업", "전력"),
    (r"석유 정제|정유", "정유"),
    (r"기초 화학|화학물질", "화학"),
    (r"1차 철강|철강", "철강"),
    (r"비철금속", "비철금속"),
    (r"건물 건설|토목 건설|종합 건설", "건설"),
    (r"백화점|종합 소매|소매업", "유통"),
    (r"식료품|음료", "식음료"),
    (r"화장품", "화장품"),
    (r"의복|섬유", "의류/섬유"),
    (r"해상 운송|항공 운송|운송", "운송"),
    (r"창고|물류", "물류"),
    (r"부동산", "부동산"),
    (r"통신업|전기 통신", "통신"),
    (r"영화|방송|출판|게임 소프트웨어", "미디어/콘텐츠"),
    (r"기계 제조|기계 및 장비", "기계"),
    (r"전기장비|전기 장비", "전기장비"),
    (r"고무|플라스틱", "화학소재"),
    (r"도매|상품 중개", "도매/무역"),
]

# 표준산업분류가 실제 사업과 안 맞는 대형주는 직접 지정.
# (예: 삼성전자는 분류상 "통신 및 방송 장비 제조업"이지만 실질은 반도체)
INDUSTRY_OVERRIDE = {
    "005930": "반도체",      # 삼성전자
    "066570": "가전/전자",   # LG전자
    "034020": "발전설비",    # 두산에너빌리티
    "028260": "지주/건설",   # 삼성물산
}


def shorten_industry(industry, products=None, code=None) -> str:
    """긴 표준산업분류명을 짧게. 규칙에 없으면 주요제품으로 보완."""
    if code and code in INDUSTRY_OVERRIDE:
        return INDUSTRY_OVERRIDE[code]

    if isinstance(industry, str) and industry.strip():
        for pattern, short in INDUSTRY_SHORT:
            if re.search(pattern, industry):
                return short
        # 규칙에 없으면 꼬리를 떼고 첫 어절만 (잘림 방지)
        cleaned = re.sub(r"\s*(제조업|업)$", "", industry.split(";")[0])
        cleaned = cleaned.split(",")[0].split(" 및 ")[0].strip()
        if cleaned:
            return cleaned[:10]

    # 업종이 비었으면 주요제품에서 첫 단어라도
    if isinstance(products, str) and products.strip() and products.strip() != "-":
        return products.split(",")[0].split("(")[0].strip()[:10]

    return ""


def _fetch(path: str, lookback: int = 10):
    """GitHub 캐시에서 최근 영업일 CSV를 가져온다."""
    d = date.today() - timedelta(days=1)
    for _ in range(lookback):
        url = f"{BASE}/{path}/{d.isoformat()}.csv"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            df = pd.read_csv(pd.io.common.StringIO(r.text), dtype={"Code": str})
            return df, d
        d -= timedelta(days=1)
    raise RuntimeError(f"최근 {lookback}일 내 데이터를 찾지 못했습니다 ({path}).")


def load_ranking(top: int = 300, market: str = "ALL") -> tuple:
    """시가총액 상위 종목 + 업종. (DataFrame, 기준일) 반환."""
    krx, used_date = _fetch("krx")
    try:
        desc, _ = _fetch("desc")
        krx = krx.merge(desc[["Code", "Industry", "Products"]],
                        on="Code", how="left")
    except Exception:
        krx["Industry"] = None
        krx["Products"] = None

    if market != "ALL":
        krx = krx[krx["Market"] == market]

    krx = krx[~krx["Name"].astype(str).str.contains(EXCLUDE_NAME)]
    krx = krx.sort_values("Marcap", ascending=False).head(top).reset_index(drop=True)

    krx["업종"] = [
        shorten_industry(row.Industry, row.Products, row.Code)
        for row in krx.itertuples()
    ]

    # 우선주는 desc 데이터가 없다. 본주(코드 앞 5자리 동일)의 업종을 물려받는다.
    base_industry = {}
    for row in krx.itertuples():
        if row.업종 and not str(row.Name).endswith(("우", "우B", "우C")):
            base_industry.setdefault(str(row.Code)[:5], row.업종)

    krx["업종"] = [
        row.업종 or base_industry.get(str(row.Code)[:5], "")
        for row in krx.itertuples()
    ]

    krx["순위"] = range(1, len(krx) + 1)

    return krx, used_date


def format_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """화면에 보여줄 컬럼만 정리."""
    return pd.DataFrame({
        "순위": df["순위"],
        "종목명": df["Name"],
        "코드": df["Code"],
        "업종": df["업종"],
        "현재가": df["Close"],
        "등락률": df["ChagesRatio"],           # 원본이 이미 % 단위
        "거래대금(억)": (df["Amount"] / 1e8).round(0),
        "시가총액(조)": (df["Marcap"] / 1e12).round(1),
        "시장": df["Market"],
    })


def section_market_ranking(key_prefix: str = "", top: int = 300) -> None:
    """시가총액 순위표 섹션 (업종 포함)."""
    import streamlit as st
    import claude_buttons

    st.subheader("시가총액 순위")
    st.caption(f"코스피·코스닥 시가총액 상위 {top}종목. 업종별로 걸러볼 수 있습니다.")

    try:
        df, used_date = _cached_ranking(top)
    except Exception as e:
        st.error(f"순위 데이터를 불러오지 못했습니다: {e}")
        return

    disp = format_for_display(df)

    # --- 필터
    c1, c2 = st.columns([2, 1])
    industries = ["전체"] + sorted(
        [x for x in disp["업종"].unique() if x],
        key=lambda s: -(disp["업종"] == s).sum())
    picked = c1.selectbox("업종", industries, key=f"{key_prefix}rank_ind")
    market = c2.selectbox("시장", ["전체", "KOSPI", "KOSDAQ"],
                          key=f"{key_prefix}rank_mkt")

    view = disp
    if picked != "전체":
        view = view[view["업종"] == picked]
    if market != "전체":
        view = view[view["시장"].str.startswith(market)]

    st.caption(f"{len(view)}종목 · 기준일 {used_date}")

    st.dataframe(
        view[["순위", "종목명", "업종", "현재가", "등락률", "거래대금(억)", "시가총액(조)"]],
        hide_index=True, width="stretch", height=420,
        column_config={
            "순위": st.column_config.NumberColumn(width="small"),
            "현재가": st.column_config.NumberColumn(format="%,d"),
            "등락률": st.column_config.NumberColumn(format="%+.2f%%"),
            "거래대금(억)": st.column_config.NumberColumn(format="%,d"),
            "시가총액(조)": st.column_config.NumberColumn(format="%.1f"),
        },
    )

    # --- 표에서 고른 종목을 바로 분석
    if not view.empty:
        options = [f"{r.종목명} ({r.코드})" for r in view.head(50).itertuples()]
        sel = st.selectbox("이 중에서 분석할 종목", ["(선택 안 함)"] + options,
                          key=f"{key_prefix}rank_pick")
        if sel != "(선택 안 함)":
            nm = sel.rsplit(" (", 1)[0]
            tk = sel.rsplit(" (", 1)[1].rstrip(")")
            claude_buttons.render_skill_buttons(nm, tk,
                                                key_prefix=f"{key_prefix}rank_")

    st.caption("현재가·등락률은 전일 종가 기준입니다. 실시간 시세가 아닙니다.")


def _cached_ranking(top: int):
    import streamlit as st

    @st.cache_data(ttl=3600 * 6)
    def _inner(n):
        return load_ranking(n)

    return _inner(top)
