"""메인 실행 스크립트.

    python run.py                # config.yaml 설정으로 실행
    python run.py --refresh      # 캐시 무시하고 데이터 새로 받기
    python run.py --top-n 3      # 설정 일부만 덮어쓰기
"""

import argparse
from pathlib import Path

import pandas as pd
import yaml

import data
from backtest import run_backtest, metrics, yearly_returns
from universe import TICKERS
import universe as _u

PIT_NAMES = {}


def name(t):
    return PIT_NAMES.get(t) or _u.NAMES.get(t, t)

HERE = Path(__file__).parent


def load_config(path=None) -> dict:
    with open(path or HERE / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fmt(m: dict) -> str:
    lines = []
    for k, v in m.items():
        lines.append(f"  {k:12s} {v:>8.1f}" if k == "기간(년)" else f"  {k:12s} {v:>8.2%}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="데이터 새로 받기")
    ap.add_argument("--top-n", type=int, help="보유 종목 수 덮어쓰기")
    ap.add_argument("--rebalance", help="ME 또는 QE")
    ap.add_argument("--no-market-filter", action="store_true")
    ap.add_argument("--pit", action="store_true", help="시점별 유니버스 사용")
    args = ap.parse_args()

    cfg = load_config()
    if args.top_n:
        cfg["portfolio"]["top_n"] = args.top_n
    if args.rebalance:
        cfg["portfolio"]["rebalance"] = args.rebalance
    if args.no_market_filter:
        cfg["market_filter"]["enabled"] = False

    start = cfg["period"]["start"]
    end = cfg["period"]["end"]

    # 유니버스 결정
    ucfg = cfg.get("universe", {"mode": "static"})
    universe_map, tickers = None, TICKERS
    if args.pit or ucfg.get("mode") == "pit":
        import universe_history as uh
        snaps = uh.load_cached(ucfg.get("top", 20), ucfg.get("market", "KOSPI"),
                               cfg["portfolio"]["rebalance"])
        if not snaps:
            print("시점별 유니버스 캐시가 없습니다. 먼저 실행하세요:")
            print(f"  python universe_history.py --start {start} "
                  f"--top {ucfg.get('top', 20)} --freq {cfg['portfolio']['rebalance']}")
            return
        universe_map = uh.to_frame(snaps)
        tickers = uh.all_tickers(snaps)
        PIT_NAMES.update(uh.load_names(ucfg.get("top", 20),
                                       ucfg.get("market", "KOSPI"),
                                       cfg["portfolio"]["rebalance"]))
        print(f"시점별 유니버스: {len(snaps)}개 시점 / 누적 {len(tickers)}종목")

    print("데이터 수집 중...")
    prices = data.fetch_prices(tickers, start, end, refresh=args.refresh)
    bench = data.fetch_benchmark(cfg["market_filter"]["benchmark"], start, end,
                                refresh=args.refresh)

    # 최소 lookback 만큼의 데이터가 있는 종목만
    need = cfg["momentum"]["lookback_days"]
    keep = [c for c in prices.columns if prices[c].notna().sum() > need]
    dropped = set(prices.columns) - set(keep)
    if dropped:
        print(f"데이터 부족으로 제외: {[name(t) for t in dropped]}")
    prices = prices[keep]

    print(f"\n종목 {len(prices.columns)}개 | {prices.index[0].date()} ~ {prices.index[-1].date()}")

    r = run_backtest(prices, bench, cfg, universe_map=universe_map)

    print("\n=== 전략 ===")
    print(fmt(metrics(r.nav)))
    bname = cfg["market_filter"]["benchmark"]
    print(f"\n=== 벤치마크 ({bname} 매수후보유) ===")
    print(fmt(metrics(r.benchmark)))

    print("\n=== 연도별 ===")
    yr = yearly_returns(r.nav, r.benchmark)
    print(yr.map(lambda v: f"{v:.1%}").to_string())

    # 저장
    outdir = HERE / cfg["output"]["dir"]
    outdir.mkdir(exist_ok=True)

    pd.DataFrame({"strategy": r.nav, "benchmark": r.benchmark}).to_csv(
        outdir / "nav.csv", encoding="utf-8-sig")

    log = pd.DataFrame(r.log)
    if not log.empty:
        log["종목명"] = log["holdings"].apply(
            lambda s: ", ".join(name(t) for t in s.split(",")) if s != "CASH" else "현금")
        log.to_csv(outdir / "rebalance_log.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame(r.trades, columns=["date", "ticker", "side", "value"]).to_csv(
        outdir / "trades.csv", index=False, encoding="utf-8-sig")

    print(f"\n결과 저장: {outdir}/")
    if not log.empty:
        print("\n=== 최근 리밸런싱 5회 ===")
        print(log[["exec_date", "종목명"]].tail().to_string(index=False))


if __name__ == "__main__":
    main()
