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
    """최근 값 하나를 가져온다. 실패 시 예외를 던진다(호출부에서 처리)."""
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
        # ECOS는 에러도 200으로 주고 본문에 메시지를 담는다
        err = data.get("RESULT", data)
        raise RuntimeError(f"ECOS 오류: {err}")

    rows = data["StatisticSearch"]["row"]
    if not rows:
        raise RuntimeError("ECOS: 최근 기간에 데이터 없음")

    last = rows[-1]
    return {
        "value": float(last["DATA_VALUE"]),
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

    for obs in data["observations"]:      # 최신순 → 숫자인 첫 값 사용
        if obs["value"] != ".":
            return {"value": float(obs["value"]), "date": obs["date"], "source": "FRED"}

    raise RuntimeError(f"FRED({series_id}): 최근 값이 전부 결측(.)")


# ---------------------------------------------------------------- 종합

def collect(cfg: dict, ecos_key: str, fred_key: str) -> dict:
    out = {}

    for key_name, spec in cfg["ecos"].items():
        try:
            out[spec["name"]] = fetch_ecos(ecos_key, spec["stat_code"], spec["item_code"])
        except Exception as e:
            out[spec["name"]] = {"error": str(e)}
            print(f"  [실패] {spec['name']}: {e}")

    for spec in cfg["fred"]:
        try:
            out[spec["name"]] = fetch_fred(fred_key, spec["series"])
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
