"""텔레그램 알림 전송."""

import os
import urllib.parse
import urllib.request
import json


class NotifyError(RuntimeError):
    pass


def send(text: str, token: str = None, chat_id: str = None) -> None:
    token = token or os.environ.get("TELEGRAM_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise NotifyError(
            "TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 환경변수가 없습니다.\n"
            "로컬 테스트: export TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=...\n"
            "GitHub: 저장소 Settings > Secrets and variables > Actions 에 등록"
        )

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(url, data=payload, timeout=20) as r:
            body = json.loads(r.read())
    except Exception as e:
        raise NotifyError(f"텔레그램 전송 실패: {e}") from e

    if not body.get("ok"):
        raise NotifyError(f"텔레그램 API 오류: {body}")


def test():
    """세팅 확인용. python notify.py 로 실행."""
    send("연결 테스트 성공. 이 메시지가 보이면 세팅 완료입니다.")
    print("전송 완료. 텔레그램을 확인하세요.")


if __name__ == "__main__":
    test()
