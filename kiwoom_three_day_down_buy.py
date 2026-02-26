#!/usr/bin/env python3
"""키움 REST API 기반 연속 하락 매수 전략 (모의투자 + 시총 상위 종목).

요청 반영:
- 키움 모의투자(기본) 기준 설정
- 한국 시총 상위 30개 종목 기본 감시
- 종목당 고정 예산 시장가 매수
- 조회/주문 초당 호출 제한 + 429/5xx 재시도(backoff)
- 연속 하락일 수(매수 조건)를 설정값/CLI로 조절 가능
"""

from __future__ import annotations

import argparse
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

# ==========================================================
# 사용자 입력값(반드시 실제 값으로 수정)
# ==========================================================
USE_MOCK_INVEST = True
MOCK_BASE_URL = "https://mockapi.kiwoom.com"
LIVE_BASE_URL = "https://api.kiwoom.com"
BASE_URL = MOCK_BASE_URL if USE_MOCK_INVEST else LIVE_BASE_URL


def _env_or_default(name: str, default: str) -> str:
    env_value = os.getenv(name)
    if env_value is None or not env_value.strip():
        return default
    return env_value


APP_KEY = _env_or_default("KIWOOM_APP_KEY", "YOUR_APP_KEY")
APP_SECRET = _env_or_default("KIWOOM_APP_SECRET", "YOUR_APP_SECRET")
ACCOUNT_NO = _env_or_default("KIWOOM_ACCOUNT_NO", "YOUR_ACCOUNT_NO")
PRODUCT_CODE = _env_or_default("KIWOOM_PRODUCT_CODE", "01")  # 계좌 상품코드(환경에 맞게 수정)
ACCESS_TOKEN = _env_or_default("KIWOOM_ACCESS_TOKEN", "")  # 직접 발급한 Access Token(선택). 비우면 자동 발급

TELEGRAM_BOT_TOKEN = ""  # 텔레그램 봇 토큰(선택)
TELEGRAM_CHAT_ID = ""  # 텔레그램 채팅 ID(선택)

BUY_BUDGET_KRW = 3_000_000  # 종목당 매수 금액
CONSECUTIVE_DOWN_DAYS = 3  # 연속 하락일 수(예: 3이면 기존 조건과 동일)
MAX_ORDERS_PER_RUN = 3  # 1회 실행 시 최대 주문 종목 수
DRY_RUN = False  # 실제 주문: False / 주문 없이 점검: True
FORCE_BUY_QUANTITY_IF_OVER_BUDGET = 0  # 0: 예산 초과시 미매수, N(>=1): 예산 초과시 N주 매수

# 호출 제한(계정 환경의 공식 한도보다 여유있게 보수적으로 설정 권장)
MAX_QUERY_PER_SEC = 4
MAX_ORDER_PER_SEC = 2
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 0.6

TOP30_SYMBOLS = [
    "005930", "000660", "373220", "207940", "005380", "012450", "035420", "329180", "012330", "086790",
    "105560", "055550", "000270", "068270", "042700", "138040", "028260", "139480", "035720", "034020",
    "032830", "003670", "015760", "066570", "003550", "096770", "017670", "009540", "018260", "010130",
]
# ==========================================================


@dataclass
class KiwoomConfig:
    base_url: str
    app_key: str
    app_secret: str
    account_no: str
    product_code: str


class SimpleRateLimiter:
    def __init__(self, max_calls_per_sec: int) -> None:
        if max_calls_per_sec <= 0:
            raise ValueError("max_calls_per_sec는 1 이상이어야 합니다")
        self.max_calls_per_sec = max_calls_per_sec
        self.call_times: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self.call_times and (now - self.call_times[0]) >= 1.0:
            self.call_times.popleft()

        if len(self.call_times) >= self.max_calls_per_sec:
            sleep_for = 1.0 - (now - self.call_times[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            while self.call_times and (now - self.call_times[0]) >= 1.0:
                self.call_times.popleft()

        self.call_times.append(time.monotonic())


class KiwoomRestClient:
    def __init__(
        self,
        config: KiwoomConfig,
        query_rate_limit: int,
        order_rate_limit: int,
        max_retries: int,
        retry_backoff_seconds: float,
    ) -> None:
        self.config = config
        self.session = requests.Session()
        self.access_token: str | None = None
        self.query_limiter = SimpleRateLimiter(query_rate_limit)
        self.order_limiter = SimpleRateLimiter(order_rate_limit)
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._has_refreshed_token = False

    def _request_with_retry(self, method: str, url: str, request_type: str, **kwargs: Any) -> requests.Response:
        limiter = self.order_limiter if request_type == "order" else self.query_limiter
        retryable_statuses = {429, 500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 2):
            limiter.wait()
            try:
                resp = self.session.request(method, url, timeout=10, **kwargs)
                if resp.status_code == 401:
                    return resp
                if resp.status_code in retryable_statuses and attempt <= self.max_retries:
                    wait_time = self.retry_backoff_seconds * attempt
                    print(
                        f"[WARN] {request_type} 호출 재시도 {attempt}/{self.max_retries} "
                        f"(status={resp.status_code}, {wait_time:.1f}s 대기)"
                    )
                    time.sleep(wait_time)
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_error = exc
                response = getattr(exc, "response", None)
                status = response.status_code if response is not None else None
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
                if attempt <= self.max_retries:
                    wait_time = self.retry_backoff_seconds * attempt
                    print(f"[WARN] {request_type} 호출 예외 재시도 {attempt}/{self.max_retries} ({wait_time:.1f}s 대기): {exc}")
                    time.sleep(wait_time)
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("요청 처리 중 알 수 없는 오류")

    def authenticate(self) -> None:
        url = f"{self.config.base_url}/oauth2/token"
        app_key = self.config.app_key.strip()
        app_secret = self.config.app_secret.strip()

        attempts = [
            {
                "label": "json(secretkey)",
                "kwargs": {
                    "headers": {"Content-Type": "application/json; charset=utf-8"},
                    "json": {
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "secretkey": app_secret,
                    }
                },
            },
            {
                "label": "json(appsecret)",
                "kwargs": {
                    "headers": {"Content-Type": "application/json; charset=utf-8"},
                    "json": {
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "appsecret": app_secret,
                    }
                },
            },
            {
                "label": "form(secretkey)",
                "kwargs": {
                    "data": {
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "secretkey": app_secret,
                    },
                    "headers": {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                },
            },
            {
                "label": "form(appsecret)",
                "kwargs": {
                    "data": {
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "appsecret": app_secret,
                    },
                    "headers": {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                },
            },
            {
                "label": "form(default-header, secretkey)",
                "kwargs": {
                    "data": {
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "secretkey": app_secret,
                    }
                },
            },
            {
                "label": "form(default-header, appsecret)",
                "kwargs": {
                    "data": {
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "appsecret": app_secret,
                    }
                },
            },
        ]

        last_data: dict[str, Any] = {}
        last_error: Exception | None = None
        for attempt in attempts:
            try:
                resp = self._request_with_retry("POST", url, request_type="query", **attempt["kwargs"])
                data = resp.json()
                token = data.get("access_token")
                if token:
                    self.access_token = token
                    self._has_refreshed_token = False
                    print(f"[INFO] 토큰 발급 성공 방식: {attempt['label']}")
                    return
                last_data = data
            except requests.RequestException as exc:
                last_error = exc
                print(f"[WARN] 토큰 발급 방식 실패({attempt['label']}): {exc}")
                continue

        error_hint = f" | 마지막 예외: {last_error}" if last_error else ""
        raise RuntimeError(
            "토큰 발급 실패: "
            f"{last_data} | 점검: APP_KEY/APP_SECRET 공백, 모의/실서버 URL, 키 활성 상태"
            f"{error_hint}"
        )

    def set_access_token(self, token: str) -> None:
        token = token.strip()
        if not token:
            raise ValueError("ACCESS_TOKEN 값이 비어 있습니다")
        self.access_token = token
        self._has_refreshed_token = False

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("authenticate() 먼저 호출 필요")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "Content-Type": "application/json; charset=utf-8",
        }

    def _safe_request(self, method: str, url: str, request_type: str, **kwargs: Any) -> requests.Response:
        """요청 중 인증 실패(401) 시 토큰 1회 재발급 후 자동 재시도."""
        resp = self._request_with_retry(method, url, request_type=request_type, **kwargs)
        if resp.status_code != 401:
            return resp

        if self._has_refreshed_token:
            resp.raise_for_status()
            return resp

        print("[WARN] 토큰 만료/무효(401) 감지 -> 자동 재발급 후 재시도")
        self.authenticate()
        self._has_refreshed_token = True

        retry_kwargs = dict(kwargs)
        headers = dict(retry_kwargs.get("headers", {}))
        headers.update(self._headers())
        retry_kwargs["headers"] = headers
        resp_retry = self._request_with_retry(method, url, request_type=request_type, **retry_kwargs)
        resp_retry.raise_for_status()
        return resp_retry

    def get_daily_closes(self, symbol: str, count: int) -> list[float]:
        url = f"{self.config.base_url}/api/daily-candles"
        params = {
            "symbol": symbol,
            "count": count,
            "date": datetime.now().strftime("%Y%m%d"),
        }
        resp = self._safe_request("GET", url, request_type="query", params=params, headers=self._headers())
        data = resp.json()

        rows = data.get("output") or data.get("candles") or []
        if len(rows) < count:
            raise RuntimeError(f"{symbol}: 일봉 데이터 부족 (요청={count}, 수신={len(rows)})")

        closes: list[float] = []
        for row in rows[:count]:
            close_raw = row.get("stck_clpr") or row.get("close") or row.get("종가")
            if close_raw is None:
                raise RuntimeError(f"{symbol}: 종가 필드 누락")
            closes.append(float(str(close_raw).replace(",", "")))

        return list(reversed(closes))

    def get_current_price(self, symbol: str) -> float:
        url = f"{self.config.base_url}/api/quote"
        params = {"symbol": symbol}
        resp = self._safe_request("GET", url, request_type="query", params=params, headers=self._headers())
        data = resp.json()
        quote = data.get("output") or data.get("quote") or {}
        price_raw = quote.get("stck_prpr") or quote.get("price") or quote.get("현재가")
        if price_raw is None:
            raise RuntimeError(f"{symbol}: 현재가 필드 누락")
        return float(str(price_raw).replace(",", ""))

    def place_market_buy(self, symbol: str, quantity: int) -> dict[str, Any]:
        if quantity <= 0:
            raise ValueError("quantity는 1 이상이어야 합니다")

        url = f"{self.config.base_url}/api/order"
        payload = {
            "account_no": self.config.account_no,
            "product_code": self.config.product_code,
            "symbol": symbol,
            "side": "buy",
            "order_type": "market",
            "quantity": quantity,
            "price": 0,
        }
        resp = self._safe_request("POST", url, request_type="order", json=payload, headers=self._headers())
        return resp.json()


def should_buy_consecutive_down(closes_old_to_new: list[float], current_price: float, down_days: int) -> bool:
    """연속 하락 조건.

    down_days=3이면:
    - D-3 > D-2 > D-1
    - 현재가 < D-1
    """
    if down_days < 2:
        raise ValueError("down_days는 2 이상이어야 합니다")
    if len(closes_old_to_new) < down_days:
        return False

    recent = closes_old_to_new[-down_days:]
    prior_down_ok = all(recent[i] > recent[i + 1] for i in range(len(recent) - 1))
    today_down_ok = current_price < recent[-1]
    return prior_down_ok and today_down_ok


def send_telegram_message(message: str) -> None:
    """텔레그램 알림 전송(토큰/채팅ID 미설정 시 스킵)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=5).raise_for_status()
    except requests.RequestException as exc:
        print(f"[WARN] 텔레그램 알림 전송 실패: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="키움 연속 하락 매수(모의투자 + 시총30)")
    parser.add_argument("--budget", type=int, default=BUY_BUDGET_KRW, help="종목당 매수 금액(원)")
    parser.add_argument("--down-days", type=int, default=CONSECUTIVE_DOWN_DAYS, help="연속 하락일 수(2 이상)")
    parser.add_argument("--max-orders", type=int, default=MAX_ORDERS_PER_RUN, help="한 번 실행시 최대 주문 수")
    parser.add_argument(
        "--force-buy-quantity-if-over-budget",
        type=int,
        default=FORCE_BUY_QUANTITY_IF_OVER_BUDGET,
        help="0: 예산 초과 시 미매수, N(>=1): 예산 초과 시 N주 매수",
    )
    parser.add_argument("--dry-run", action="store_true", default=DRY_RUN, help="주문 없이 조건만 체크")
    parser.add_argument("--query-per-sec", type=int, default=MAX_QUERY_PER_SEC, help="초당 조회 호출 제한")
    parser.add_argument("--order-per-sec", type=int, default=MAX_ORDER_PER_SEC, help="초당 주문 호출 제한")
    parser.add_argument("--retries", type=int, default=MAX_RETRIES, help="429/5xx 또는 네트워크 오류 재시도 횟수")
    parser.add_argument("--retry-backoff", type=float, default=RETRY_BACKOFF_SECONDS, help="재시도 백오프(초)")
    parser.add_argument("--symbols", nargs="*", default=TOP30_SYMBOLS, help="대상 종목코드들")
    return parser.parse_args()


def validate_user_settings() -> None:
    app_key = APP_KEY.strip()
    app_secret = APP_SECRET.strip()
    account_no = ACCOUNT_NO.strip()
    manual_access_token = ACCESS_TOKEN.strip()

    invalid_reasons: list[str] = []

    if not account_no or account_no == "YOUR_ACCOUNT_NO":
        invalid_reasons.append("- ACCOUNT_NO가 비어 있거나 기본 placeholder 입니다")

    if not manual_access_token:
        if not app_key or app_key == "YOUR_APP_KEY":
            invalid_reasons.append("- APP_KEY가 비어 있거나 기본 placeholder 입니다")
        if not app_secret or app_secret == "YOUR_APP_SECRET":
            invalid_reasons.append("- APP_SECRET이 비어 있거나 기본 placeholder 입니다")

    if invalid_reasons:
        detail = "\n".join(invalid_reasons)
        raise RuntimeError(
            "필수 설정값이 누락되었습니다.\n"
            f"{detail}\n"
            "해결 방법: 파일 상단 값 수정 또는 환경변수(KIWOOM_APP_KEY/KIWOOM_APP_SECRET/"
            "KIWOOM_ACCOUNT_NO/KIWOOM_ACCESS_TOKEN) 설정"
        )


def run_for_symbol(
    client: KiwoomRestClient,
    symbol: str,
    budget: int,
    dry_run: bool,
    down_days: int,
    force_buy_quantity_if_over_budget: int,
) -> bool:
    closes = client.get_daily_closes(symbol, count=down_days)
    current_price = client.get_current_price(symbol)

    print(f"[CHECK] {symbol} | 최근 {down_days}일 종가={closes[-down_days:]} | 현재가={current_price}")

    if not should_buy_consecutive_down(closes, current_price, down_days):
        print(f"[SKIP] {symbol} 조건 미충족")
        return False

    quantity = math.floor(budget / current_price)
    if quantity <= 0:
        if force_buy_quantity_if_over_budget >= 1:
            quantity = force_buy_quantity_if_over_budget
            print(f"[INFO] {symbol} 예산 초과지만 설정에 따라 {quantity}주 매수 진행")
        else:
            print(f"[SKIP] {symbol} 예산 부족")
            return False

    if dry_run:
        msg = f"[DRY-RUN] {symbol} 시장가 {quantity}주 매수 예정"
        print(msg)
        send_telegram_message(msg)
        return True

    result = client.place_market_buy(symbol, quantity)
    msg = f"[ORDER] {symbol} 주문 완료: {result}"
    print(msg)
    send_telegram_message(msg)
    return True


def main() -> int:
    validate_user_settings()
    args = parse_args()
    if args.down_days < 2:
        raise RuntimeError("--down-days는 2 이상으로 설정하세요.")
    if args.force_buy_quantity_if_over_budget < 0:
        raise RuntimeError("--force-buy-quantity-if-over-budget는 0 이상으로 설정하세요.")

    config = KiwoomConfig(
        base_url=BASE_URL,
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        account_no=ACCOUNT_NO,
        product_code=PRODUCT_CODE,
    )

    client = KiwoomRestClient(
        config,
        query_rate_limit=args.query_per_sec,
        order_rate_limit=args.order_per_sec,
        max_retries=args.retries,
        retry_backoff_seconds=args.retry_backoff,
    )
    if ACCESS_TOKEN.strip():
        client.set_access_token(ACCESS_TOKEN)
        print("[INFO] 수동 입력 ACCESS_TOKEN 사용")
    else:
        client.authenticate()
        print("[INFO] ACCESS_TOKEN 자동 발급 사용")

    print(f"[INFO] 모드: {'모의투자' if USE_MOCK_INVEST else '실투자'}")
    print(f"[INFO] 종목 수: {len(args.symbols)} | 종목당 예산: {args.budget:,}원")
    print(f"[INFO] 매수조건: 연속 하락 {args.down_days}일")
    print(f"[INFO] 예산 초과시 강제 매수 수량 설정: {args.force_buy_quantity_if_over_budget}")
    print(f"[INFO] 제한: 조회 {args.query_per_sec}/s, 주문 {args.order_per_sec}/s, 재시도 {args.retries}회")
    mode_text = "모의투자" if USE_MOCK_INVEST else "실투자"
    send_telegram_message(f"자동매매 시작 | 모드={mode_text} | 종목수={len(args.symbols)} | 예산={args.budget:,}원")

    ordered_count = 0
    for symbol in args.symbols:
        if ordered_count >= args.max_orders:
            print("[INFO] 최대 주문 수 도달 -> 종료")
            break

        try:
            bought = run_for_symbol(
                client,
                symbol,
                args.budget,
                args.dry_run,
                args.down_days,
                args.force_buy_quantity_if_over_budget,
            )
            if bought:
                ordered_count += 1
        except Exception as exc:  # noqa: BLE001
            err = f"[ERROR] {symbol}: {exc}"
            print(err)
            send_telegram_message(err)

    done_msg = f"[DONE] 주문(또는 주문예정) 종목 수: {ordered_count}"
    print(done_msg)
    send_telegram_message(done_msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
