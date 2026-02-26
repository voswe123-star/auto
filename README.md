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
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (선택)
- `BUY_BUDGET_KRW`
- `CONSECUTIVE_DOWN_DAYS`
- `MAX_ORDERS_PER_RUN`
- `FORCE_BUY_QUANTITY_IF_OVER_BUDGET` (`0` 또는 1 이상의 정수)
- `DRY_RUN`
- `MAX_QUERY_PER_SEC` / `MAX_ORDER_PER_SEC`
- `MAX_RETRIES` / `RETRY_BACKOFF_SECONDS`

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
```

## 텔레그램 알림
- 파일 상단에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 입력하면 알림이 전송됩니다.
- 알림 이벤트:
  - 자동매매 시작
  - 매수 주문(또는 dry-run 주문예정)
  - 종목별 오류
  - 실행 종료 요약
- 둘 중 하나라도 비어있으면 텔레그램 알림은 자동으로 비활성화됩니다.

## 토큰 입력/자동발급 동작
- `ACCESS_TOKEN = ""` (기본): 실행 시 자동 발급해서 사용
- `ACCESS_TOKEN`에 값을 직접 넣으면: 입력 토큰을 우선 사용
- 자동 발급 시 `secretkey`/`appsecret`, JSON/Form 방식까지 순차 시도해 환경별 차이를 최대한 흡수합니다.
- 입력 토큰이 틀렸거나 만료되어 `401`이 발생하면: 자동으로 토큰 재발급 후 요청을 재시도
- 따라서 수동/자동 모두 지원하며, 실행 중 만료 상황도 자동 복구하도록 구성되어 있습니다.

## 주문/조회 제한 관련
- 제한 없이 연속 호출하면 계정/환경별로 `429`(too many requests), 주문거부, 조회 제한에 걸릴 수 있습니다.
- 현재 코드는 내부적으로 초당 호출 수를 제한하고(`query`, `order` 분리), 429/5xx 에러 시 재시도(backoff)합니다.
- 실제 허용치(모의/실서버, 계정등급, API 상품)에 맞춰 값을 보수적으로 조정해 운용하세요.

## 주의
- 엔드포인트(`/api/daily-candles`, `/api/quote`, `/api/order`) 및 필드명은 계정의 실제 키움 REST 명세에 맞춰 수정해야 합니다.
- 실거래 전 반드시 모의투자에서 충분히 검증하세요.


## 예산 초과 시 동작
- 기본값 `FORCE_BUY_QUANTITY_IF_OVER_BUDGET = 0`: 1주 가격이 예산보다 높으면 `예산 부족`으로 매수하지 않음
- `FORCE_BUY_QUANTITY_IF_OVER_BUDGET = N` (또는 `--force-buy-quantity-if-over-budget N`): 예산을 초과하더라도 `N`주를 매수
- 예: `N=3`이면 예산보다 1주 가격이 높아도 3주를 강제 매수
