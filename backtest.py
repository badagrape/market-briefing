"""모멘텀 백테스트 엔진.

설계 원칙
---------
1. 신호는 리밸런싱일 '종가까지'의 데이터만 사용한다.
2. 매매는 그 다음 거래일 종가에 체결된다고 가정한다. (미래참조 방지)
3. 비용은 매매 금액에 비례해 실제로 차감한다.
"""

from dataclasses import dataclass, field
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 신호 계산

def momentum_score(prices: pd.DataFrame, lookback: int, skip: int) -> pd.DataFrame:
    """12-1 모멘텀. P[t-skip] / P[t-lookback] - 1"""
    if skip >= lookback:
        raise ValueError("skip_days는 lookback_days보다 작아야 합니다")
    return prices.shift(skip) / prices.shift(lookback) - 1.0


def trend_ok(prices: pd.DataFrame, ma_days: int, slope_days: int) -> pd.DataFrame:
    """현재가가 이평선 위 + 이평선이 우상향이면 True"""
    ma = prices.rolling(ma_days, min_periods=ma_days).mean()
    ok = prices > ma
    if slope_days > 0:
        ok = ok & (ma > ma.shift(slope_days))
    return ok.fillna(False)


def market_ok(bench: pd.Series, ma_days: int) -> pd.Series:
    ma = bench.rolling(ma_days, min_periods=ma_days).mean()
    return (bench > ma).fillna(False)


# ---------------------------------------------------------------- 리밸런싱 일정

def rebalance_dates(index: pd.DatetimeIndex, freq: str) -> list:
    """각 기간의 마지막 거래일 목록."""
    s = pd.Series(index, index=index)
    return list(s.groupby(index.to_period(freq[0])).last().values)


# ---------------------------------------------------------------- 결과 컨테이너

@dataclass
class Result:
    nav: pd.Series
    benchmark: pd.Series
    weights: pd.DataFrame
    trades: list = field(default_factory=list)
    log: list = field(default_factory=list)

    @property
    def returns(self) -> pd.Series:
        return self.nav.pct_change().fillna(0.0)


# ---------------------------------------------------------------- 엔진

def universe_at(universe_map: dict, d) -> set:
    """d 시점에 유효한 유니버스. 가장 최근 스냅샷(<= d)을 사용."""
    if not universe_map:
        return None
    keys = [k for k in universe_map if k <= d]
    if not keys:
        return set()
    return universe_map[max(keys)]


def run_backtest(prices: pd.DataFrame, bench: pd.Series, cfg: dict,
                 universe_map: dict = None) -> Result:
    prices = prices.sort_index()
    bench = bench.sort_index().reindex(prices.index).ffill()

    m = cfg["momentum"]
    sf = cfg["stock_filter"]
    mf = cfg["market_filter"]
    pf = cfg["portfolio"]
    cs = cfg["costs"]

    buy_rate = (cs["buy_bps"] + cs["slippage_bps"]) / 10000.0
    sell_rate = (cs["sell_bps"] + cs["slippage_bps"]) / 10000.0

    # --- 신호 (원본 가격으로 계산 → 미상장 구간은 NaN으로 자동 제외)
    mom = momentum_score(prices, m["lookback_days"], m["skip_days"])
    eligible = mom.notna()
    if sf["require_positive"]:
        eligible &= mom > 0
    if sf["ma_enabled"]:
        eligible &= trend_ok(prices, sf["ma_days"], sf["ma_slope_days"])

    mkt = market_ok(bench, mf["ma_days"]) if mf["enabled"] else pd.Series(True, index=prices.index)

    # --- 상장폐지: 종목별 마지막 유효 거래일
    last_valid = {c: prices[c].last_valid_index() for c in prices.columns}

    # --- 매매 계획: 신호일 -> 체결일
    idx = prices.index
    pos_of = {d: i for i, d in enumerate(idx)}
    plan = {}
    for d in rebalance_dates(idx, pf["rebalance"]):
        i = pos_of[d]
        if i + 1 >= len(idx):
            continue                      # 체결일이 없음
        exec_date = idx[i + 1]
        if not bool(mkt.loc[d]):
            plan[exec_date] = ([], d)     # 시장 필터 탈락 → 전량 현금
            continue

        cand = mom.loc[d][eligible.loc[d]].dropna()

        # 시점별 유니버스로 후보 제한
        uni = universe_at(universe_map, d)
        if uni is not None:
            cand = cand[[t for t in cand.index if t in uni]]

        # 이미 상장폐지된 종목 제외
        cand = cand[[t for t in cand.index
                     if last_valid[t] is not None and last_valid[t] >= exec_date]]

        picks = list(cand.sort_values(ascending=False).head(pf["top_n"]).index)
        plan[exec_date] = (picks, d)

    # --- 시뮬레이션
    px_all = prices.ffill()
    cash, shares = 1.0, {}
    nav_rows, w_rows, trades, log = [], [], [], []

    for d in idx:
        px = px_all.loc[d]

        # 상장폐지 강제 청산 (마지막 거래일에 전량 매도)
        for t in list(shares):
            lv = last_valid[t]
            if lv is not None and d == lv:
                val = shares[t] * px[t]
                cash += val * (1 - sell_rate)
                trades.append((d, t, "DELIST", val))
                del shares[t]

        pv = cash + sum(sh * px[t] for t, sh in shares.items())

        if d in plan:
            picks, sig_date = plan[d]
            n_slots = max(len(picks), pf["min_hold_names"])
            tgt_val = pv / n_slots if n_slots > 0 else 0.0

            # 1) 매도 먼저 (현금 확보)
            for t in list(shares):
                target = tgt_val if t in picks else 0.0
                cur = shares[t] * px[t]
                if cur - target > 1e-12:
                    sell_val = cur - target
                    cash += sell_val * (1 - sell_rate)
                    shares[t] -= sell_val / px[t]
                    trades.append((d, t, "SELL", sell_val))
                    if shares[t] <= 1e-12:
                        del shares[t]

            # 2) 매수
            for t in picks:
                cur = shares.get(t, 0.0) * px[t]
                if tgt_val - cur > 1e-12:
                    want = tgt_val - cur
                    afford = cash / (1 + buy_rate)
                    buy_val = min(want, max(afford, 0.0))
                    if buy_val <= 1e-12:
                        continue
                    cash -= buy_val * (1 + buy_rate)
                    shares[t] = shares.get(t, 0.0) + buy_val / px[t]
                    trades.append((d, t, "BUY", buy_val))

            pv = cash + sum(sh * px[t] for t, sh in shares.items())
            log.append({
                "signal_date": sig_date, "exec_date": d,
                "holdings": ",".join(picks) if picks else "CASH",
                "n": len(picks), "nav": pv,
            })

        nav_rows.append(pv)
        w_rows.append({t: sh * px[t] / pv for t, sh in shares.items()} if pv > 0 else {})

    nav = pd.Series(nav_rows, index=idx, name="strategy")
    weights = pd.DataFrame(w_rows, index=idx).fillna(0.0)
    bench_nav = (bench / bench.iloc[0]).rename("benchmark")

    return Result(nav=nav, benchmark=bench_nav, weights=weights,
                  trades=trades, log=log)


# ---------------------------------------------------------------- 성과 지표

def _mdd(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1).min())


def metrics(nav: pd.Series, periods_per_year: int = 252) -> dict:
    nav = nav.dropna()
    if len(nav) < 2:
        return {}
    ret = nav.pct_change().dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    vol = ret.std() * np.sqrt(periods_per_year)
    mdd = _mdd(nav)
    monthly = nav.resample("ME").last().pct_change().dropna()
    return {
        "총수익률": nav.iloc[-1] / nav.iloc[0] - 1,
        "CAGR": cagr,
        "변동성": vol,
        "샤프(rf=0)": cagr / vol if vol > 0 else np.nan,
        "MDD": mdd,
        "칼마": cagr / abs(mdd) if mdd < 0 else np.nan,
        "월간승률": (monthly > 0).mean(),
        "기간(년)": years,
    }


def yearly_returns(nav: pd.Series, bench: pd.Series) -> pd.DataFrame:
    a = nav.resample("YE").last().pct_change()
    a.iloc[0] = nav.resample("YE").last().iloc[0] / nav.iloc[0] - 1
    b = bench.resample("YE").last().pct_change()
    b.iloc[0] = bench.resample("YE").last().iloc[0] / bench.iloc[0] - 1
    out = pd.DataFrame({"전략": a, "벤치마크": b})
    out.index = out.index.year
    out["초과"] = out["전략"] - out["벤치마크"]
    return out
