"""실전 신호 생성.

backtest.py 의 신호 함수를 그대로 재사용한다.
백테스트에서 검증한 로직과 실전 신호가 어긋나지 않게 하는 것이 핵심.

    python signal_now.py            # 오늘이 리밸런싱일이면 알림 전송
    python signal_now.py --force    # 날짜 무관하게 지금 신호 확인
    python signal_now.py --dry-run  # 전송 없이 화면 출력만
"""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import data
import notify
from backtest import momentum_score, trend_ok, market_ok
from run import load_config
from universe import TICKERS, name, SECTORS

HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"


# ------------------------------------------------------------ 리밸런싱일 판정

def is_rebalance_day(today: date, freq: str) -> bool:
    """오늘이 해당 주기의 마지막 '평일'인가.

    공휴일은 고려하지 않으므로, 기간 말이 연휴면 1~2일 일찍 울릴 수 있다.
    월간 모멘텀 전략에서 하루 차이는 치명적이지 않지만 감안할 것.
    """
    if today.weekday() >= 5:
        return False

    f = freq.upper()
    if f.startswith("W"):
        return today.weekday() == 4          # 금요일

    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)

    if f.startswith("Q"):
        return (d.month - 1) // 3 != (today.month - 1) // 3
    return d.month != today.month


def period_key(today: date, freq: str) -> str:
    """중복 전송 방지용 기간 식별자."""
    f = freq.upper()
    if f.startswith("W"):
        y, w, _ = today.isocalendar()
        return f"{y}-W{w:02d}"
    if f.startswith("Q"):
        return f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    return today.strftime("%Y-%m")


# ------------------------------------------------------------ 상태 저장

def load_state() -> dict:
    """보유 기록. trades.csv 가 있으면 그쪽이 우선(실제 체결 기준)."""
    state = {"holdings": [], "last_sent": None}
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))

    ledger = HERE / "trades.csv"
    if ledger.exists():
        try:
            import portfolio
            pos, _ = portfolio.positions(portfolio.load_trades())
            state["holdings"] = sorted(pos.keys())
            state["positions"] = pos
        except Exception as e:
            print(f"  (원장 읽기 실패, state.json 사용: {e})")
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


# ------------------------------------------------------------ 신호 계산

def compute_signal(cfg: dict) -> dict:
    m, sf, mf, pf = cfg["momentum"], cfg["stock_filter"], cfg["market_filter"], cfg["portfolio"]

    lookback = m["lookback_days"]
    start = (date.today() - timedelta(days=int(lookback * 2.2))).isoformat()

    prices = data.fetch_prices(TICKERS, start, refresh=True)
    bench = data.fetch_benchmark(mf["benchmark"], start, refresh=True)
    bench = bench.reindex(prices.index).ffill()

    asof = prices.index[-1]

    mom = momentum_score(prices, lookback, m["skip_days"]).loc[asof]
    ok = mom.notna()
    if sf["require_positive"]:
        ok &= mom > 0
    if sf["ma_enabled"]:
        ok &= trend_ok(prices, sf["ma_days"], sf["ma_slope_days"]).loc[asof]

    mkt_pass = True
    if mf["enabled"]:
        mkt_pass = bool(market_ok(bench, mf["ma_days"]).loc[asof])

    ranked = mom[ok].sort_values(ascending=False)
    picks = [] if not mkt_pass else list(ranked.head(pf["top_n"]).index)

    return {
        "asof": asof,
        "market_pass": mkt_pass,
        "bench_last": float(bench.loc[asof]),
        "bench_ma": float(bench.rolling(mf["ma_days"]).mean().loc[asof]),
        "picks": picks,
        "scores": mom,
        "ranked": ranked,
        "n_eligible": int(ok.sum()),
        "prices": prices.ffill().loc[asof],
        "slot_value": None,
    }


# ------------------------------------------------------------ 메시지

def build_message(sig: dict, prev: list, cfg: dict, state: dict = None) -> str:
    L = []
    freq = "월간" if cfg["portfolio"]["rebalance"].upper().startswith("M") else "분기"
    L.append(f"<b>{freq} 리밸런싱 신호</b>")
    L.append(f"기준일: {sig['asof'].date()}")
    L.append("")

    gap = sig["bench_last"] / sig["bench_ma"] - 1
    status = "통과" if sig["market_pass"] else "탈락 → 전량 현금"
    L.append(f"시장 필터: {status}")
    bname = cfg['market_filter']['benchmark']
    L.append(f"  {bname} {sig['bench_last']:,.0f} / {cfg['market_filter']['ma_days']}일선 대비 {gap:+.1%}")
    L.append("")

    if sig["picks"]:
        L.append(f"<b>목표 보유 ({len(sig['picks'])}종목, 동일비중)</b>")
        for i, t in enumerate(sig["picks"], 1):
            L.append(f"  {i}. {name(t)} ({SECTORS.get(t,'-')})  {sig['scores'][t]:+.1%}")
    else:
        L.append("<b>목표 보유: 없음 (현금)</b>")
    L.append("")

    cur = set(sig["picks"])
    old = set(prev)
    sells, buys, holds = old - cur, cur - old, cur & old

    pos = state.get("positions") if state else None
    px = sig.get("prices")

    L.append("<b>변경</b>")
    if sells:
        for t in sorted(sells):
            qty = pos.get(t, {}).get("qty") if pos else None
            L.append(f"  매도 {name(t)}" + (f"  {qty}주 전량" if qty else ""))
    else:
        L.append("  매도: 없음")

    if buys:
        for t in sorted(buys):
            line = f"  매수 {name(t)}"
            if px is not None and t in px and sig.get("slot_value"):
                shares = int(sig["slot_value"] // px[t])
                line += f"  약 {shares}주 (@{px[t]:,.0f})"
            L.append(line)
    else:
        L.append("  매수: 없음")

    L.append(f"  유지: {', '.join(name(t) for t in sorted(holds)) if holds else '없음'}")

    if not sells and not buys:
        L.append("")
        L.append("변경 없음 — 매매 불필요")

    if pos and px is not None:
        rows = [(t, p) for t, p in pos.items() if t in px]
        if rows:
            total_cost = sum(p["avg"] * p["qty"] for _, p in rows)
            total_val = sum(px[t] * p["qty"] for t, p in rows)
            pnl = total_val - total_cost
            L.append("")
            L.append("<b>현재 계좌</b>")
            L.append(f"  평가금액 {total_val:,.0f}원")
            L.append(f"  평가손익 {pnl:+,.0f}원 ({pnl/total_cost:+.1%})")

    L.append("")
    L.append(f"조건 통과 종목 수: {sig['n_eligible']}개 / {len(TICKERS)}개")
    L.append("")
    L.append("<i>참고용 신호입니다. 주문은 직접 확인 후 실행하세요.</i>")
    return "\n".join(L)


def build_watch_message(sig: dict, held: list, cfg: dict) -> str:
    """주간 현황 알림. 매매 지시가 아니라 상태 보고다.

    보유 종목을 바꾸지 않는다. 다음 리밸런싱까지 무슨 일이 일어나고 있는지만
    알려준다. 이걸 보고 중간에 매매하면 백테스트가 검증한 전략이 아니게 된다.
    """
    L = []
    L.append("<b>주간 현황</b>")
    L.append(f"기준일: {sig['asof'].date()}")
    L.append("")

    gap = sig["bench_last"] / sig["bench_ma"] - 1
    ma_days = cfg["market_filter"]["ma_days"]
    if sig["market_pass"]:
        bname = cfg['market_filter']['benchmark']
        L.append(f"시장: 정상 ({bname} {ma_days}일선 대비 {gap:+.1%})")
        if gap < 0.02:
            L.append("  ⚠ 이평선에 근접 — 다음 리밸런싱에서 현금 전환 가능")
    else:
        L.append(f"시장: 이평선 하회 ({gap:+.1%})")
        L.append("  다음 리밸런싱일에 전량 현금 신호가 나올 예정")
    L.append("")

    if held:
        L.append("<b>보유 종목 상태</b>")
        for t in held:
            score = sig["scores"].get(t)
            still_in = t in sig["picks"]
            mark = "유지" if still_in else "이탈 예상"
            s = f"{score:+.1%}" if pd.notna(score) else "-"
            L.append(f"  {name(t)}  {s}  [{mark}]")
    else:
        L.append("<b>보유: 없음 (현금)</b>")
    L.append("")

    incoming = [t for t in sig["picks"] if t not in held]
    if incoming:
        L.append("<b>진입 후보</b>")
        for t in incoming:
            L.append(f"  {name(t)}  {sig['scores'][t]:+.1%}")
        L.append("")

    L.append("<i>현황 보고입니다. 매매는 리밸런싱일에만 하세요.</i>")
    return "\n".join(L)


# ------------------------------------------------------------ 메인

def _plain(msg: str) -> str:
    for tag in ("<b>", "</b>", "<i>", "</i>"):
        msg = msg.replace(tag, "")
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="날짜 무관하게 실행")
    ap.add_argument("--dry-run", action="store_true", help="전송하지 않고 출력만")
    ap.add_argument("--watch", action="store_true",
                    help="주간 현황 알림 (보유 종목을 바꾸지 않음)")
    args = ap.parse_args()

    cfg = load_config()
    today = date.today()
    freq = cfg["portfolio"]["rebalance"]
    state = load_state()

    # ---------- 주간 현황 모드
    if args.watch:
        if not args.force and today.weekday() != 4:      # 금요일만
            print(f"{today}: 금요일이 아님. 종료.")
            return
        # 리밸런싱일과 겹치면 현황 알림은 생략 (중복 방지)
        if not args.force and is_rebalance_day(today, freq):
            print("오늘은 리밸런싱일. 현황 알림 생략.")
            return

        print("현황 계산 중...")
        sig = compute_signal(cfg)
        msg = build_watch_message(sig, state.get("holdings", []), cfg)

    # ---------- 리밸런싱 모드
    else:
        if not args.force and not is_rebalance_day(today, freq):
            print(f"{today}: 리밸런싱일이 아님. 종료.")
            return

        pkey = period_key(today, freq)
        if not args.force and state.get("last_sent") == pkey:
            print(f"{pkey} 신호는 이미 전송됨. 종료.")
            return

        print("신호 계산 중...")
        sig = compute_signal(cfg)

        # 계좌 총액에서 종목당 배분 금액 계산 (원장이 있을 때만)
        pos = state.get("positions")
        if pos and sig.get("prices") is not None:
            px = sig["prices"]
            val = sum(px[t] * p["qty"] for t, p in pos.items() if t in px)
            n = max(len(sig["picks"]), 1)
            if val > 0:
                sig["slot_value"] = val / n

        msg = build_message(sig, state.get("holdings", []), cfg, state)

    print("\n" + "-" * 50)
    print(_plain(msg))
    print("-" * 50 + "\n")

    if args.dry_run:
        print("dry-run: 전송하지 않음")
        return

    notify.send(msg)
    print("텔레그램 전송 완료")

    # 현황 모드와 --force 는 상태를 건드리지 않는다
    if not args.watch and not args.force:
        save_state({"holdings": sig["picks"],
                    "last_sent": period_key(today, freq),
                    "asof": str(sig["asof"].date())})
        print(f"상태 저장: {STATE_FILE.name}")


if __name__ == "__main__":
    main()
