"""가격 데이터 수집 + 캐싱.

한 번 받은 데이터는 cache/ 에 저장해두고 재사용한다.
조건만 바꿔서 백테스트를 수십 번 돌릴 거라 이게 중요하다.
"""

from pathlib import Path
import pandas as pd

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.pkl"


def _load_cache(key: str):
    p = _cache_path(key)
    if p.exists():
        return pd.read_pickle(p)
    return None


def _save_cache(key: str, obj) -> None:
    obj.to_pickle(_cache_path(key))


def fetch_prices(tickers, start, end=None, refresh=False) -> pd.DataFrame:
    """종목별 수정종가를 date x ticker DataFrame으로 반환."""
    import FinanceDataReader as fdr

    key = f"prices_{start}_{end or 'today'}"
    if not refresh:
        cached = _load_cache(key)
        if cached is not None and set(tickers).issubset(cached.columns):
            return cached[list(tickers)]

    frames = {}
    for t in tickers:
        try:
            df = fdr.DataReader(t, start, end)
            if df.empty:
                print(f"  [skip] {t}: 데이터 없음")
                continue
            frames[t] = df["Close"]
            print(f"  [ok]   {t}: {len(df)}일")
        except Exception as e:
            print(f"  [fail] {t}: {e}")

    if not frames:
        raise RuntimeError("가져온 데이터가 없습니다. 네트워크나 티커를 확인하세요.")

    prices = pd.DataFrame(frames).sort_index()
    prices.index = pd.to_datetime(prices.index)
    _save_cache(key, prices)
    return prices


def fetch_benchmark(symbol, start, end=None, refresh=False) -> pd.Series:
    """지수 종가 Series.

    FinanceDataReader가 지수 데이터를 못 줄 때가 있어(빈 DataFrame),
    그럴 땐 pykrx(한국거래소 공식 데이터)로 재시도한다.
    """
    key = f"bench_{symbol}_{start}_{end or 'today'}"
    if not refresh:
        cached = _load_cache(key)
        if cached is not None:
            return cached

    s = None

    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(symbol, start, end)
        if not df.empty and "Close" in df.columns:
            s = df["Close"].sort_index()
    except Exception as e:
        print(f"  FinanceDataReader 조회 실패: {e}")

    if s is None or s.empty:
        print(f"  FinanceDataReader 실패 → pykrx로 재시도: {symbol}")
        try:
            from pykrx import stock

            idx_code = {"KS11": "1001", "KQ11": "2001"}.get(symbol, symbol)
            fmt = lambda d: pd.Timestamp(d).strftime("%Y%m%d")
            end_ = end or pd.Timestamp.today()
            df2 = stock.get_index_ohlcv_by_date(fmt(start), fmt(end_), idx_code,
                                                name_display=False)
            if df2.empty:
                raise RuntimeError("pykrx도 빈 데이터를 반환했습니다")
            s = df2["종가"].sort_index()
            s.index = pd.to_datetime(s.index)
        except Exception as e:
            raise RuntimeError(
                f"벤치마크({symbol}) 데이터를 가져오지 못했습니다. "
                f"FinanceDataReader와 pykrx 모두 실패: {e}"
            ) from e

    s.name = symbol
    _save_cache(key, s)
    return s
