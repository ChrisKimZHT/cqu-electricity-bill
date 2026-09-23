from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
import os
import re

from .models import DEFAULT_ELECTRICITY_PRICE


class ConfigError(ValueError):
    """环境变量缺失或格式不正确。"""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"缺少环境变量 {name}，请参考 .env.example 配置 .env")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，当前值为 {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} 必须大于 0")
    return value


def _electricity_price() -> Decimal:
    raw = os.getenv("ELECTRICITY_PRICE", str(DEFAULT_ELECTRICITY_PRICE)).strip()
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigError("ELECTRICITY_PRICE 必须是大于 0 的电价（元/度）") from exc
    if not value.is_finite() or value <= 0:
        raise ConfigError("ELECTRICITY_PRICE 必须是大于 0 的电价（元/度）")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} 必须是 true 或 false，当前值为 {raw!r}")


def _balance_warning_threshold() -> Decimal:
    raw = os.getenv("BALANCE_WARNING_THRESHOLD", "10").strip()
    message = "BALANCE_WARNING_THRESHOLD 必须是大于或等于 0 的金额（元）"
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigError(message) from exc
    if not value.is_finite() or value < 0:
        raise ConfigError(message)
    return value


def _schedule_time(raw: str, name: str) -> str:
    value = raw.strip()
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ConfigError(f"{name} 必须使用 HH:MM 格式（00:00～23:59），当前值为 {raw!r}")
    return value


def _email_schedule(raw: str) -> str:
    parts = raw.strip().split("@")
    if len(parts) != 2:
        raise ConfigError("EMAIL_SCHEDULE 必须使用 HH:MM@星期 格式，例如 08:00@1,3,5")
    time = _schedule_time(parts[0], "EMAIL_SCHEDULE 的时间")
    weekdays = [day.strip() for day in parts[1].split(",")]
    if any(day not in "01234567" or len(day) != 1 for day in weekdays):
        raise ConfigError("EMAIL_SCHEDULE 的星期必须是逗号分隔的 0～7（0、7 均为周日）")
    # 统一周日的两种写法，并去除重复星期。
    days = dict.fromkeys("0" if day == "7" else day for day in weekdays)
    return f"{time}@{','.join(days)}"


@dataclass(frozen=True, slots=True)
class Settings:
    room: str
    building: str | None
    schedule_time: str
    timezone: ZoneInfo
    data_dir: Path
    request_timeout: int
    log_level: str
    email_enabled: bool
    email_schedule: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_to: tuple[str, ...]
    smtp_use_ssl: bool
    smtp_starttls: bool
    smtp_timeout: int
    email_subject_prefix: str
    electricity_url: str
    fee_item_id: str
    synjones_auth: str
    electricity_price: Decimal = DEFAULT_ELECTRICITY_PRICE
    balance_warning_enabled: bool = False
    balance_warning_threshold: Decimal = Decimal("10")

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "Settings":
        load_dotenv(env_file, override=False)
        for old, new, example in (
            ("SCHEDULE_CRON", "SCHEDULE_TIME", "00:00"),
            ("EMAIL_SCHEDULE_CRON", "EMAIL_SCHEDULE", "08:00@0,1,2,3,4,5,6"),
        ):
            if old in os.environ and new not in os.environ:
                raise ConfigError(f"{old} 已停用，请改用 {new}，例如 {new}={example}")
        timezone_name = os.getenv("TIMEZONE", "Asia/Shanghai").strip()
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"未知时区 TIMEZONE={timezone_name!r}") from exc

        return cls(
            room=_required("CQU_ROOM").upper(),
            building=os.getenv("CQU_BUILDING", "").strip() or None,
            schedule_time=_schedule_time(
                os.getenv("SCHEDULE_TIME", "00:00"),
                "SCHEDULE_TIME",
            ),
            timezone=timezone,
            data_dir=(
                Path("/data")
                if _boolean("DOCKER_MODE")
                else Path(os.getenv("DATA_DIR", ".")).expanduser()
            ),
            request_timeout=_positive_int("REQUEST_TIMEOUT", 20),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            email_enabled=_boolean("EMAIL_ENABLED", False),
            email_schedule=_email_schedule(
                os.getenv("EMAIL_SCHEDULE", "08:00@0,1,2,3,4,5,6"),
            ),
            smtp_host=os.getenv("SMTP_HOST", "").strip(),
            smtp_port=_positive_int("SMTP_PORT", 465),
            smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", "").strip(),
            smtp_to=tuple(
                address.strip()
                for address in os.getenv("SMTP_TO", "").split(",")
                if address.strip()
            ),
            smtp_use_ssl=_boolean("SMTP_USE_SSL", True),
            smtp_starttls=_boolean("SMTP_STARTTLS", False),
            smtp_timeout=_positive_int("SMTP_TIMEOUT", 20),
            email_subject_prefix=os.getenv(
                "EMAIL_SUBJECT_PREFIX", "电费监控"
            ).strip(),
            electricity_url=os.getenv(
                "ELECTRICITY_URL",
                "http://payment.cqu.edu.cn/charge-app/#/pays?id=448",
            ).strip(),
            fee_item_id=os.getenv("FEE_ITEM_ID", "448").strip(),
            synjones_auth=_required("SYNJONES_AUTH"),
            electricity_price=_electricity_price(),
            balance_warning_enabled=_boolean("BALANCE_WARNING_ENABLED", False),
            balance_warning_threshold=_balance_warning_threshold(),
        )
