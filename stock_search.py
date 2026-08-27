"""전체 상장종목 검색.

전략 유니버스(20개 대장주)에 없는 종목도 찾을 수 있게 한다.
build_universe.py 와 같은 GitHub 캐시 방식을 쓴다 — KRX 사이트를 직접
긁지 않아서 안정적이다. 최근 몇 달치만 있으므로 "오늘 기준 종목 목록"
용도로만 쓰고, 과거 시점 조회에는 쓰지 않는다.
"""

import re
from datetime import date, timedelta

import pandas as pd
import requests

CACHE_REPO = ("https://raw.githubusercontent.com/FinanceData/"
              "fdr_krx_data_cache/refs/heads/master/data/listing/krx")

# 검색에서 제외 — 스팩·ETF·ETN·리츠는 "기업"이 아니라 분석 스킬과 안 맞음.
# 우선주는 제외하지 않는다 — 실제 회사라 검색 대상으로 유효함.
EXCLUDE_NAME = re.compile(r"(?:스팩|기업인수목적|리츠$|인프라$|밸류$|ETN|ETF)")


def _fetch_snapshot(as_of: date, lookback_days: int = 10):
    """가장 최근 영업일의 전종목 스냅샷. (DataFrame, 실제사용일) 반환."""
    d = as_of
    for _ in range(lookback_days):
        url = f"{CACHE_REPO}/{d.isoformat()}.csv"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            df = pd.read_csv(pd.io.common.StringIO(r.text), dtype={"Code": str})
            return df, d
        d -= timedelta(days=1)
    raise RuntimeError(f"최근 {lookback_days}일 내 종목 목록을 찾지 못했습니다.")


def load_listing(market: str = "ALL") -> pd.DataFrame:
    """전 종목 목록. 컬럼: Code, Name, Market, Marcap."""
    df, _ = _fetch_snapshot(date.today() - timedelta(days=1))
    if market != "ALL":
        df = df[df["Market"] == market]
    df = df[~df["Name"].astype(str).str.contains(EXCLUDE_NAME)]
    return df[["Code", "Name", "Market", "Marcap"]].reset_index(drop=True)


def search(query: str, listing: pd.DataFrame, limit: int = 15) -> pd.DataFrame:
    """종목명 또는 코드로 검색. 시가총액 큰 순으로 정렬."""
    query = query.strip()
    if not query:
        return listing.iloc[0:0]

    if query.isdigit():
        hit = listing[listing["Code"].str.startswith(query)]
    else:
        hit = listing[listing["Name"].str.contains(query, case=False, na=False)]

    return hit.sort_values("Marcap", ascending=False).head(limit)
