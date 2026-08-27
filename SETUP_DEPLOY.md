# 가족용 대시보드 인터넷 배포 가이드

## 왜 별도 폴더인가

이 배포용 파일 세트에는 `toss_api.py`, `portfolio.py`, `trades.csv` 관련 코드가
**하나도 없다.** 계좌·보유종목·금액을 다루는 파일 자체가 존재하지 않으므로,
저장소를 공개(Public)로 해도 개인 자산 정보가 노출될 방법이 없다.

**이 폴더는 기존 프로젝트 폴더와 절대 합치지 마세요.** 별도로 관리해야
"이 저장소엔 원래부터 민감한 게 안 들어갈 구조"가 유지된다.

## 포함된 파일 (10개 + 설정 2개)

```
family_dashboard.py   ← 화면 (진입점)
signal_now.py          ← 모멘텀 신호 계산
notify.py              ← signal_now.py가 import하지만 실제로 호출 안 함
backtest.py            ← 모멘텀/추세 계산 함수
data.py                ← 시세 조회
universe.py             ← 대장주 종목 목록 (종목코드·이름만, 개인정보 없음)
run.py                  ← config 로더
indicators.py           ← 거시지표 수집
news.py                 ← 뉴스 수집
config.yaml             ← 설정값 (통계표코드 등, 실제 키는 없음 — 확인됨)
requirements.txt
.gitignore
```

## 1. GitHub 저장소 만들기

1. github.com → New repository
2. 이름 예: `market-briefing` (뭐든 상관없음)
3. **Public** 선택 — 무료 Streamlit Cloud는 공개 저장소만 지원한다
4. 이 폴더의 내용을 그대로 push:

```bash
cd family_deploy
git init
git add .
git commit -m "가족용 시장 브리핑"
git branch -M main
git remote add origin https://github.com/사용자명/market-briefing.git
git push -u origin main
```

## 2. Streamlit Community Cloud 배포

1. share.streamlit.io 접속 → GitHub 계정으로 로그인
2. **New app** → 방금 만든 저장소 선택
3. Main file path: `family_dashboard.py`
4. **Deploy** 클릭 — 1~2분이면 끝

## 3. API 키 등록 (Secrets)

배포 화면 → **App settings → Secrets**에 이렇게 입력:

```toml
ECOS_API_KEY = "발급받은_ECOS_키"
FRED_API_KEY = "발급받은_FRED_키"
```

**이 키는 저장소(GitHub)가 아니라 Streamlit Cloud 서버에만 저장된다.**
공개 저장소를 봐도 키는 보이지 않는다.

## 4. 링크 공유

배포가 끝나면 `https://사용자명-market-briefing.streamlit.app` 같은 주소가 생긴다.
이 링크를 가족에게 카카오톡 등으로 보내면 각자 브라우저로 바로 열어볼 수 있다.
로그인도, 앱 설치도 필요 없다.

## 데이터는 어떻게 갱신되나

로컬 launchd와 별개로, **이 클라우드 앱은 화면이 열릴 때마다 직접 API를 호출**한다
(거시지표 1시간 캐시, 뉴스 30분 캐시, 신호 30분 캐시). 네 맥이 꺼져 있어도
문제없이 최신 데이터를 보여준다 — 완전히 독립적으로 돈다.

## 무료 티어의 한계

- 앱이 며칠간 아무도 안 열면 "잠자기" 상태가 되고, 다음 접속 시 30초 정도
  깨어나는 시간이 걸릴 수 있다. 정상 동작이다.
- 커스텀 도메인은 무료 티어에서 지원 안 함 (`.streamlit.app` 주소 고정)
- 코드가 담긴 저장소는 공개이므로, 앞으로도 이 폴더에는 개인정보·비밀키를
  절대 추가하지 않는다는 원칙을 지킬 것

## 업데이트하려면

로직을 고치고 싶으면 로컬에서 수정 후:

```bash
git add .
git commit -m "수정 내용"
git push
```

Streamlit Cloud가 push를 감지해 몇 분 안에 자동으로 재배포한다.
