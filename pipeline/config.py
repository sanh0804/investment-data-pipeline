import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "coinbase_exchange"
PRODUCT = "BTC-USD"
ENDPOINT = "https://api.exchange.coinbase.com/products/BTC-USD/candles"


@dataclass(frozen=True)
class Settings:
    start: date
    end: date
    connect_timeout: float
    read_timeout: float
    max_attempts: int
    backoff: float
    max_retry_delay: float


def number(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")  # Shell environment takes precedence.
    today = datetime.now(timezone.utc).date()
    start_text, end_text = os.getenv("START_DATE"), os.getenv("END_DATE")
    if bool(start_text) != bool(end_text):
        raise ValueError("Set both START_DATE and END_DATE, or neither")
    end = date.fromisoformat(end_text) if end_text else today
    start = date.fromisoformat(start_text) if start_text else end - timedelta(days=7)
    # 299 intervals leaves room for the endpoint's inclusive boundary candle.
    if not 1 <= (end - start).days <= 299 or end > today:
        raise ValueError("Use 1..299 completed UTC days; END_DATE must be <= today UTC")
    attempts = int(os.getenv("HTTP_MAX_ATTEMPTS", "3"))
    if not 1 <= attempts <= 5:
        raise ValueError("HTTP_MAX_ATTEMPTS must be 1..5 (including the first attempt)")
    return Settings(
        start, end,
        number("HTTP_CONNECT_TIMEOUT", 5, 0.1, 60),
        number("HTTP_READ_TIMEOUT", 20, 0.1, 60),
        attempts,
        number("HTTP_BACKOFF_SECONDS", 1, 0, 30),
        number("HTTP_MAX_RETRY_DELAY", 30, 0, 60),
    )
