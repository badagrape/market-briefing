"""거시지표 수집: 한국은행 ECOS + 세인트루이스 연준 FRED.

기사에서 숫자를 줍는 대신, 그 숫자를 만든 기관의 API에서 직접 받는다.
둘 다 무료, 둘 다 인증키 하나만 있으면 된다.

    export ECOS_API_KEY="..."
    export FRED_API_KEY="..."
    python3 indicators.py --test      # 값이 상식적인지 눈으로 확인
    python3 indicators.py             # data/indicators.json 저장

키 발급:
  ECOS: https://ecos.bok.or.kr → Open API → 회원가입 → 인증키 신청 (10분)
  FRED: https://fred.stlouisfed.org → My Account → API Keys (즉시 발급)
"""

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).parent
OUT = HERE / "data"


def load_config() -> dict:
    with open(HERE / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["context"]


# ---------------------------------------------------------------- ECOS

def fetch_ecos(key: str, stat_code: str, item_code: str, days_back: int = 14) -> dict:
    """최근 값 + 직전 값을 가져온다. 실패 시 예외를 던진다(호출부에서 처리)."""
    start = (date.today() - timedelta(days=days_back)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{key}"
        f"/json/kr/1/10/{stat_code}/D/{start}/{end}/{item_code}"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    if "StatisticSearch" not in data:
        err = data.get("RESULT", data)
        raise RuntimeError(f"ECOS 오류: {err}")

    rows = data["StatisticSearch"]["row"]
    if not rows:
        raise RuntimeError("ECOS: 최근 기간에 데이터 없음")

    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    return {
        "value": float(last["DATA_VALUE"]),
        "prev": float(prev["DATA_VALUE"]) if prev else None,
        "date": last["TIME"],
        "source": "ECOS",
    }


# ---------------------------------------------------------------- FRED

def fetch_fred(key: str, series_id: str, days_back: int = 21) -> dict:
    start = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": start,
        "sort_order": "desc",
        "limit": 10,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    if "observations" not in data:
        raise RuntimeError(f"FRED 오류: {data}")

    valid = [obs for obs in data["observations"] if obs["value"] != "."]
    if not valid:
        raise RuntimeError(f"FRED({series_id}): 최근 값이 전부 결측(.)")

    return {
        "value": float(valid[0]["value"]),
        "prev": float(valid[1]["value"]) if len(valid) >= 2 else None,
        "date": valid[0]["date"],
        "source": "FRED",
    }


def interpret(name: str, value: float, prev: float = None) -> str:
    """지표별 1줄 해석. 규칙 기반 — AI 호출 없이 즉시 반환.

    자료에서 강조한 원칙: 레벨보다 변화 방향이 더 중요하다.
    숫자 하나보다 "어느 쪽으로 움직이고 있는가"를 읽는다.
    """
    delta = (value - prev) if prev is not None else None

    if "기준금리" in name:
        if delta is None:
            return "한국은행 기준금리. 인상이면 대출·소비 부담 ↑, 인하면 경기부양 신호."
        if delta > 0:
            return f"기준금리 인상 (+{delta:.2f}%p) — 긴축 신호. 성장주·부채 많은 기업에 부담."
        elif delta < 0:
            return f"기준금리 인하 ({delta:.2f}%p) — 완화 신호. 유동성 공급, 위험자산 선호 ↑."
        return "기준금리 동결. 현 통화정책 방향 유지."

    if "달러인덱스" in name or "DTWEX" in name:
        if delta is None:
            return "달러인덱스(DXY). 달러 강세는 신흥국 자본 유출 압력, 원자재 가격 하락 요인."
        if delta > 0.5:
            return f"달러 강세 (+{delta:.2f}) — 신흥국(한국 포함) 자금 유출 압력. 원자재·금 가격 하락."
        elif delta < -0.5:
            return f"달러 약세 ({delta:.2f}) — 신흥국 자본 유입 여건. 원자재·금 가격 지지."
        return "달러인덱스 소폭 변동."

    if "환율" in name or "usd" in name.lower():
        if delta is None:
            return "원/달러 환율. 상승(원화 약세)은 수출기업 유리, 수입물가 상승 압력."
        if delta > 5:
            return f"원화 약세 (+{delta:.0f}원) — 수출기업(삼성·현대차 등) 단기 유리, 수입 원가 ↑."
        elif delta < -5:
            return f"원화 강세 ({delta:.0f}원) — 내수·수입 소비재 기업 유리, 수출기업 환헤지 부담."
        return "환율 소폭 변동. 큰 방향성 없음."

    if "10년물" in name or "DGS10" in name:
        if delta is None:
            return "미국 10년물 국채금리. 글로벌 자본 흐름의 기준. 상승 시 주식 밸류에이션 압박."
        if delta > 0.05:
            return f"금리 상승 (+{delta:.2f}%p) — 채권 매력 ↑, 고PER 성장주·리츠 밸류에이션 압박."
        elif delta < -0.05:
            return f"금리 하락 ({delta:.2f}%p) — 성장주·기술주 반등 여건. 달러 약세 동반 시 신흥국 유입."
        return "금리 소폭 변동. 방향성 관찰 중."

    if "VIX" in name:
        if value >= 30:
            return f"VIX {value:.1f} — 공포 구간. 시장 급변동 경계. 과거 반등 기회이기도 했음."
        elif value >= 20:
            return f"VIX {value:.1f} — 불안 구간. 변동성 확대 중. 방어주·현금 비중 점검."
        elif value <= 14:
            return f"VIX {value:.1f} — 과도한 낙관 경계. 시장 자기만족 구간, 갑작스러운 충격에 취약."
        return f"VIX {value:.1f} — 평온 구간 (15~20). 시장 불안 없음."

    if "WTI" in name or "유가" in name:
        if delta is None:
            return "WTI 국제유가. 에너지 비용·인플레이션·운송업 전반에 영향."
        if delta > 2:
            return f"유가 급등 (+${delta:.1f}) — 정유·화학 단기 수혜, 항공·운송·소비재 원가 ↑."
        elif delta < -2:
            return f"유가 급락 (${delta:.1f}) — 인플레이션 압력 완화, 항공·운송 비용 ↓."
        return "유가 소폭 변동."

    if "달러인덱스" in name or "DTWEX" in name:
        if delta is None:
            return "달러인덱스(DXY). 달러 강세는 신흥국 자본 유출 압력, 원자재 가격 하락 요인."
        if delta > 0.5:
            return f"달러 강세 (+{delta:.2f}) — 신흥국(한국 포함) 자금 유출 압력. 원자재 가격 하락."
        elif delta < -0.5:
            return f"달러 약세 ({delta:.2f}) — 신흥국 자본 유입 여건. 원자재·금 가격 지지."
        return "달러 소폭 변동."

    return ""  # 해석 없는 지표는 빈 문자열

# ---------------------------------------------------------------- 종합

def collect(cfg: dict, ecos_key: str, fred_key: str) -> dict:
    out = {}

    for key_name, spec in cfg["ecos"].items():
        try:
            r = fetch_ecos(ecos_key, spec["stat_code"], spec["item_code"])
            r["interp"] = interpret(spec["name"], r["value"], r.get("prev"))
            out[spec["name"]] = r
        except Exception as e:
            out[spec["name"]] = {"error": str(e)}
            print(f"  [실패] {spec['name']}: {e}")

    for spec in cfg["fred"]:
        try:
            r = fetch_fred(fred_key, spec["series"])
            r["interp"] = interpret(spec["name"], r["value"], r.get("prev"))
            out[spec["name"]] = r
        except Exception as e:
            out[spec["name"]] = {"error": str(e)}
            print(f"  [실패] {spec['name']}: {e}")

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="저장하지 않고 값만 출력")
    args = ap.parse_args()

    ecos_key = (os.environ.get("ECOS_API_KEY") or "").strip()
    fred_key = (os.environ.get("FRED_API_KEY") or "").strip()
    missing = [n for n, v in [("ECOS_API_KEY", ecos_key), ("FRED_API_KEY", fred_key)] if not v]
    if missing:
        print(f"환경변수 없음: {', '.join(missing)}")
        print('  export ECOS_API_KEY="..."')
        print('  export FRED_API_KEY="..."')
        return

    cfg = load_config()
    print("거시지표 수집 중...\n")
    result = collect(cfg, ecos_key, fred_key)

    print("\n결과:")
    ok = 0
    for name, v in result.items():
        if "error" in v:
            print(f"  {name:16s} 실패")
        else:
            print(f"  {name:16s} {v['value']:>12,.2f}  (기준일 {v['date']}, {v['source']})")
            ok += 1
    print(f"\n{ok}/{len(result)}개 성공")

    if args.test:
        print("\n--test: 저장하지 않음. 위 숫자가 상식적인 범위인지 확인하세요.")
        print("  (한국 기준금리 2~4%대, 美10년물 3~5%대, VIX 10~40대, WTI 60~100대)")
        return

    OUT.mkdir(exist_ok=True)
    target = OUT / "indicators.json"

    if ok == 0 and target.exists():
        print("\n전부 실패 — 기존 indicators.json 을 보존합니다 (덮어쓰지 않음).")
        return

    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {target}")


if __name__ == "__main__":
    main()
