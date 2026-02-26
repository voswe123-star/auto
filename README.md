# auto

키움 REST API를 이용한 자동매매 예제 코드입니다.

## 현재 구성
- `kiwoom_three_day_down_buy.py` 단일 파일
- 기본값: **키움 모의투자 모드**
- 기본 대상: **한국 시총 상위 30개 종목**
- 전략: **설정 가능한 연속 하락일 수** + 당일 현재가 하락이면 매수
- 안정장치: 조회/주문 초당 호출 제한 + 429/5xx 재시도(백오프)

## 매수 조건 (설정 가능)
- `--down-days N` 또는 파일 상단 `CONSECUTIVE_DOWN_DAYS = N`으로 설정
- 조건:
  - 최근 `N`거래일 종가가 연속 하락 (`D-(N) > ... > D-1`)
  - 현재가 < D-1 종가

예) `N=3`이면 기존과 동일하게 3연속 하락 조건입니다.

## 실행 전 필수 수정
`kiwoom_three_day_down_buy.py` 상단 설정값을 수정하세요.
- `USE_MOCK_INVEST`
- `MOCK_BASE_URL` / `LIVE_BASE_URL`
- `APP_KEY`
- `APP_SECRET`
- `ACCOUNT_NO`
- `PRODUCT_CODE`
- `ACCESS_TOKEN` (선택, 비우면 자동 발급)
- 또는 동일 항목을 환경변수(`KIWOOM_APP_KEY`, `KIWOOM_APP_SECRET`, `KIWOOM_ACCOUNT_NO`, `KIWOOM_PRODUCT_CODE`, `KIWOOM_ACCESS_TOKEN`)로 설정
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (선택)
- `BUY_BUDGET_KRW`
- `CONSECUTIVE_DOWN_DAYS`
- `MAX_ORDERS_PER_RUN`
- `FORCE_BUY_QUANTITY_IF_OVER_BUDGET` (`0` 또는 1 이상의 정수)
- `DRY_RUN`
- `BUY_EXECUTION_TIME` (기본 `15:10`)
- `MAX_QUERY_PER_SEC` / `MAX_ORDER_PER_SEC`
- `MAX_RETRIES` / `RETRY_BACKOFF_SECONDS`
- `KRX_HOLIDAY_DATES` (휴장일 수동 캘린더), 필요 시 `KIWOOM_KRX_HOLIDAYS` 환경변수로 추가

## 실행
```bash
python3 kiwoom_three_day_down_buy.py
```

옵션으로 덮어쓰기 가능
```bash
python3 kiwoom_three_day_down_buy.py --budget 3000000 --down-days 4 --max-orders 3 --dry-run
python3 kiwoom_three_day_down_buy.py --force-buy-quantity-if-over-budget 3
python3 kiwoom_three_day_down_buy.py --symbols 005930 000660 005380 --down-days 5
python3 kiwoom_three_day_down_buy.py --query-per-sec 3 --order-per-sec 1 --retries 4 --retry-backoff 0.8
python3 kiwoom_three_day_down_buy.py --buy-time 15:10
python3 kiwoom_three_day_down_buy.py --skip-balance-check
```


## 매수 실행 시각 대기
- 기본값은 `BUY_EXECUTION_TIME = "15:10"` 입니다.
- `--buy-time HH:MM`으로 원하는 시각으로 변경할 수 있습니다.
- 프로그램 시작 시 토큰 인증(수동 토큰 설정 또는 자동 발급)을 먼저 완료한 뒤, 해당 시각까지 대기 후 매수 로직을 시작합니다.
- 해당 시각을 이미 지난 뒤 실행하면 **다음 날 같은 시각까지 대기**합니다.


## 주문 전 계좌 잔고 조회
- 기본값으로 주문 직전에 계좌 주문가능금액(예수금/매수가능금액)을 조회합니다.
- 조회된 주문가능금액이 예상 주문금액(`현재가 x 수량`)보다 작으면 해당 종목 매수는 스킵합니다.
- 환경별 잔고 API/필드 차이를 고려해 `/api/account/balance`와 `/v1/trading/balance`를 순차 시도합니다.
- 테스트 용도로만 `--skip-balance-check`로 비활성화할 수 있습니다.


## 한국거래소 휴장일 체크
- 실행 시 매수 처리 전에 휴장일을 확인합니다.
- 휴장일 판단 기준:
  - 주말(토/일)
  - `KRX_HOLIDAY_DATES`에 등록된 날짜
  - 환경변수 `KIWOOM_KRX_HOLIDAYS`(콤마 구분 `YYYY-MM-DD`)로 추가한 날짜
- 휴장일이면 종목 조회/주문 없이 **"휴장일로 매매 스킵"** 리포트만 텔레그램 1회 전송 후 종료합니다.

## 텔레그램 알림
- 파일 상단에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 입력하면 알림이 전송됩니다.
- 현재 동작: 실행 종료 시점에 **1회만** 종합 리포트를 전송합니다.
- 리포트 형식 예시:
  - `[15:10:50]`
  - `📅 날짜: 2026-02-25 통합 리포트`
  - `⚙️ [전략 및 시간 설정 상태]`
  - `💸 매수금액: 3,000,000원`
  - `📉 하락일수: 3일`
  - `⏰ 매수시간: 15:10`
  - `[조건 충족 종목]`
  - `✅ [조건충족] : 삼성전자(005930)`
  - `[매수 결과]`
  - `🔵 [매수성공] : 삼성전자(005930) : 5주`
- 종목별 결과는 조건충족/성공/드라이런/스킵/오류를 모두 취합해서 마지막 1건으로 발송됩니다.
- 둘 중 하나라도 비어있으면 텔레그램 알림은 자동으로 비활성화됩니다.

## 토큰 입력/자동발급 동작
- `ACCESS_TOKEN = ""` (기본): 실행 시 자동 발급해서 사용
- `ACCESS_TOKEN`에 값을 직접 넣으면: 입력 토큰을 우선 사용
- 실행 로그에 현재 사용 토큰이 출력됩니다(보안상 콘솔/로그 공유에 주의).
- 자동 발급 시 `secretkey`/`appsecret`, JSON/Form 방식까지 순차 시도하고 `access_token`/`token` 응답 키를 모두 지원합니다.
- 입력 토큰이 틀렸거나 만료되어 `401`이 발생하면: 자동으로 토큰 재발급 후 요청을 재시도
- 따라서 수동/자동 모두 지원하며, 실행 중 만료 상황도 자동 복구하도록 구성되어 있습니다.

## 주문/조회 제한 관련
- 제한 없이 연속 호출하면 계정/환경별로 `429`(too many requests), 주문거부, 조회 제한에 걸릴 수 있습니다.
- 현재 코드는 내부적으로 초당 호출 수를 제한하고(`query`, `order` 분리), 429/5xx 에러 시 재시도(backoff)합니다.
- 실제 허용치(모의/실서버, 계정등급, API 상품)에 맞춰 값을 보수적으로 조정해 운용하세요.

## 주의
- 조회/주문 엔드포인트는 후보를 순차 시도합니다 (`/api/*` 우선, 실패 시 `/v1/*` fallback). 환경별 명세가 다르면 코드 상수 후보를 조정하세요.
- 일봉(과거 데이터) 조회는 정규장 외 시간에도 보통 가능하므로, 장중 여부보다 엔드포인트/파라미터 불일치가 500의 더 흔한 원인입니다.
- 실거래 전 반드시 모의투자에서 충분히 검증하세요.


## 예산 초과 시 동작
- 기본값 `FORCE_BUY_QUANTITY_IF_OVER_BUDGET = 0`: 1주 가격이 예산보다 높으면 `예산 부족`으로 매수하지 않음
- `FORCE_BUY_QUANTITY_IF_OVER_BUDGET = N` (또는 `--force-buy-quantity-if-over-budget N`): 예산을 초과하더라도 `N`주를 매수
- 예: `N=3`이면 예산보다 1주 가격이 높아도 3주를 강제 매수
