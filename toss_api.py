"""토스증권 Open API 연동 (조회 전용).

주문 기능은 절대 쓰지 않는다. 계좌 조회와 보유 종목 조회만 사용한다.

    export TOSS_CLIENT_ID="..."
    export TOSS_CLIENT_SECRET="..."
    python3 toss_api.py --test

키 발급: 토스증권 WTS(tossinvest.com) 로그인 → 설정 → Open API
발급 즉시 사용 가능하지만, 같은 화면에서 "허용 IP" 등록이 필요하다.
등록 안 된 IP에서 호출하면 키가 맞아도 403이 난다.
"""

import argparse
import json
import os
import time
from pathlib import Path

import requests

HERE = Path(__file__).parent
TOKEN_CACHE = HERE / "cache" / "toss_token.json"

BASE = "https://openapi.tossinvest.com"


class TossAPIError(RuntimeError):
    pass


def _my_ip() -> str:
    try:
        return requests.get("https://ifconfig.me", timeout=5).text.strip()
    except Exception:
        return "확인 실패"


def get_access_token(client_id: str, client_secret: str, force: bool = False) -> str:
    """액세스 토큰. 만료 전까지 캐시해서 재사용한다 (매 요청마다 재발급하면 안 됨)."""
    TOKEN_CACHE.parent.mkdir(exist_ok=True)

    if not force and TOKEN_CACHE.exists():
        cached = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        if cached.get("expires_at", 0) > time.time() + 60:   # 60초 여유
            return cached["access_token"]

    r = requests.post(
        f"{BASE}/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )

    if r.status_code == 403:
        raise TossAPIError(
            f"403 Forbidden — IP 미등록일 가능성이 높습니다.\n"
            f"  현재 공인 IP: {_my_ip()}\n"
            f"  토스증권 WTS → 설정 → Open API → 허용 IP 관리에서 이 IP를 등록하세요."
        )
    if r.status_code != 200:
        raise TossAPIError(f"토큰 발급 실패 ({r.status_code}): {r.text[:300]}")

    body = r.json()
    token = body["access_token"]
    expires_in = body.get("expires_in", 3600)

    TOKEN_CACHE.write_text(json.dumps({
        "access_token": token,
        "expires_at": time.time() + expires_in,
    }), encoding="utf-8")

    return token


def _call(client_id: str, client_secret: str, method: str, path: str,
          account_seq: int = None, params: dict = None) -> dict:
    token = get_access_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}
    if account_seq is not None:
        headers["X-Tossinvest-Account"] = str(account_seq)

    r = requests.request(method, f"{BASE}{path}", headers=headers,
                        params=params, timeout=15)

    if r.status_code == 401:            # 토큰 만료 등 → 1회 재발급 후 재시도
        token = get_access_token(client_id, client_secret, force=True)
        headers["Authorization"] = f"Bearer {token}"
        r = requests.request(method, f"{BASE}{path}", headers=headers,
                            params=params, timeout=15)

    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 5))
        raise TossAPIError(f"요청 한도 초과. {wait}초 후 재시도하세요.")
    if r.status_code != 200:
        raise TossAPIError(f"{path} 실패 ({r.status_code}): {r.text[:300]}")

    return r.json()


def _unwrap_list(data) -> list:
    """API마다 배열을 감싸는 키가 다를 수 있어 여러 후보를 시도한다.

    실제로 계좌 API는 'result'를 썼다 (문서 추정과 다름). 홀딩스 등 다른
    API도 같은 실수를 반복하지 않도록 공통으로 처리한다.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("result", "items", "accounts", "data"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


def get_accounts_raw(client_id: str, client_secret: str) -> dict:
    """계좌 목록 원본 응답. 구조 확인용."""
    return _call(client_id, client_secret, "GET", "/api/v1/accounts")


def get_accounts(client_id: str, client_secret: str) -> list:
    """계좌 목록."""
    return _unwrap_list(get_accounts_raw(client_id, client_secret))


def get_holdings(client_id: str, client_secret: str, account_seq: int) -> dict:
    """보유 종목 + 요약. 국내/미국 주식만 포함, 옵션·채권은 제외. (원본 응답 그대로)"""
    return _call(client_id, client_secret, "GET", "/api/v1/holdings",
                account_seq=account_seq)


def _num(x, default: float = 0.0) -> float:
    """토스 API는 숫자를 전부 문자열로 준다 ('192100'). 안전하게 실수로 변환."""
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def get_holdings_result(client_id: str, client_secret: str, account_seq: int) -> dict:
    """보유 종목 + 요약을 담은 내부 객체.

    실제 응답은 {"result": {..요약.., "items": [...]}} 형태로 두 겹 감싸져 있다
    (문서의 겉모습과 다름 — 계좌 API의 'result' 사례와 같은 패턴).
    """
    raw = get_holdings(client_id, client_secret, account_seq)
    if isinstance(raw, dict):
        inner = raw.get("result")
        if isinstance(inner, dict) and "items" in inner:
            return inner
        if "items" in raw:
            return raw
    return {"items": []}


def get_holdings_items(client_id: str, client_secret: str, account_seq: int) -> list:
    """보유 종목 리스트만."""
    return get_holdings_result(client_id, client_secret, account_seq).get("items", [])


def fetch_portfolio(client_id: str = None, client_secret: str = None) -> dict:
    """대시보드에서 쓰는 진입점. 첫 번째 계좌의 요약+보유종목을 반환.

    반환값의 'items'는 종목 리스트, 나머지는 marketValue/profitLoss 같은
    계좌 전체 요약이다. 원화·달러 종목이 섞여 있을 수 있어 요약도
    통화별로 분리되어 온다 — 절대 두 통화 금액을 그냥 더하지 말 것.
    """
    client_id = client_id or os.environ.get("TOSS_CLIENT_ID", "").strip()
    client_secret = client_secret or os.environ.get("TOSS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise TossAPIError("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 환경변수가 없습니다.")

    accounts = get_accounts(client_id, client_secret)
    if not accounts:
        raise TossAPIError("연결된 계좌가 없습니다.")

    account_seq = accounts[0]["accountSeq"]
    return get_holdings_result(client_id, client_secret, account_seq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    cid = os.environ.get("TOSS_CLIENT_ID", "").strip()
    sec = os.environ.get("TOSS_CLIENT_SECRET", "").strip()
    if not cid or not sec:
        print("환경변수 없음:")
        print('  export TOSS_CLIENT_ID="..."')
        print('  export TOSS_CLIENT_SECRET="..."')
        return

    try:
        print("토큰 발급 중...")
        get_access_token(cid, sec)
        print("  성공\n")

        print("계좌 조회 중...")
        accounts = get_accounts(cid, sec)
        print(f"  {len(accounts)}개 계좌 발견")
        for a in accounts:
            print(f"    accountSeq={a.get('accountSeq')}")

        if not accounts:
            print("\n  0개로 나왔습니다. 원본 응답을 그대로 보여드립니다:")
            print(f"  {get_accounts_raw(cid, sec)}")
            return

        seq = accounts[0]["accountSeq"]
        print(f"\n보유 종목 조회 중 (계좌 {seq})...")
        result = get_holdings_result(cid, sec, seq)
        items = result.get("items", [])

        if not items:
            raw = get_holdings(cid, sec, seq)
            print("  0종목 보유 (전량 매도 상태이거나 신규 계좌)")
            if raw and raw != {"result": {"items": []}}:
                print(f"  원본 확인용: {raw}")
            return

        kr_value = _num(result.get("marketValue", {}).get("amount", {}).get("krw"))
        us_value = _num(result.get("marketValue", {}).get("amount", {}).get("usd"))
        overall_rate = _num(result.get("profitLoss", {}).get("rate"))

        print(f"  {len(items)}종목 보유")
        print(f"  원화 평가액 {kr_value:,.0f}원 / 달러 평가액 ${us_value:,.2f}")
        print(f"  전체 수익률 {overall_rate:+.2%}\n")

        for it in items:
            qty = _num(it.get("quantity"))
            rate = _num(it.get("profitLoss", {}).get("rate"))
            cur = it.get("currency", "")
            print(f"  {it['name']:20s} {qty:>10.4f} {cur}  수익률 {rate:+.2%}")

    except TossAPIError as e:
        print(f"\n오류: {e}")


if __name__ == "__main__":
    main()
