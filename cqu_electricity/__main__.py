from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from loguru import logger

from .client import CquElectricityClient, CquError
from .chart import ChartError, draw_history_chart
from .config import ConfigError, Settings
from .mailer import EmailError, send_electricity_email, validate_email_settings
from .models import MeterReading
from .schedule import daily_trigger, email_trigger
from .storage import CsvStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="重庆大学宿舍电费监控")
    parser.add_argument(
        "command",
        choices=("once", "daemon", "plot", "email"),
        help="单次抓取、定时常驻、生成图表或按已有数据发送邮件",
    )
    parser.add_argument("--env-file", default=".env", help="环境变量文件，默认 .env")
    parser.add_argument("--output", help="图表输出路径，默认数据目录下的 history.png")
    return parser


def _configure_logging(level: str) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=sys.stderr.isatty(),
        backtrace=True,
        diagnose=False,
    )


def _job(settings: Settings) -> Callable[[], None]:
    store = CsvStore(settings.data_dir)

    def run() -> None:
        try:
            reading = CquElectricityClient(settings).fetch()
            store.save(reading)
            logger.info(
                "抓取成功：房间={} 余额={} 元 电表读数={} kWh",
                reading.room,
                f"{reading.total_balance_yuan(settings.electricity_price):.2f}",
                reading.meter_reading_kwh,
            )
        except Exception:
            logger.exception("电费抓取失败")
            return
        try:
            _warn_low_balance(settings, reading)
        except Exception:
            logger.exception("余额警告邮件发送失败")

    return run


def _email_job(settings: Settings) -> Callable[[], None]:
    def run() -> None:
        try:
            reading = CsvStore(settings.data_dir).latest()
            _deliver_email(settings, reading)
        except Exception:
            logger.exception("定时邮件发送失败")

    return run


def _deliver_email(settings: Settings, reading: MeterReading) -> None:
    chart_path = settings.data_dir / "history.png"
    draw_history_chart(
        settings.data_dir / "history.csv", chart_path, settings.room, settings.electricity_price
    )
    send_electricity_email(settings, reading, chart_path)
    logger.info("邮件已发送至：{}", ", ".join(settings.smtp_to))


def _warn_low_balance(settings: Settings, reading: MeterReading) -> None:
    if not settings.balance_warning_enabled:
        return
    balance = reading.total_balance_yuan(settings.electricity_price)
    if balance < settings.balance_warning_threshold:
        logger.warning("余额 {} 元低于阈值 {} 元，正在发送邮件", balance, settings.balance_warning_threshold)
        _deliver_email(settings, reading)


def main() -> int:
    args = _parser().parse_args()
    try:
        settings = Settings.from_env(args.env_file)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    _configure_logging(settings.log_level)

    if args.command == "plot":
        output_path = (
            settings.data_dir / "history.png"
            if args.output is None
            else Path(args.output)
        )
        try:
            result_path = draw_history_chart(
                settings.data_dir / "history.csv", output_path, settings.room, settings.electricity_price
            )
        except ChartError as exc:
            logger.error("制图失败：{}", exc)
            return 1
        logger.info("图表已生成：{}", result_path.resolve())
        return 0

    if args.command == "email" or (
        args.command == "daemon" and settings.email_enabled
    ) or (
        args.command in ("once", "daemon") and settings.balance_warning_enabled
    ):
        try:
            validate_email_settings(settings)
        except EmailError as exc:
            logger.error("邮件配置错误：{}", exc)
            return 2

    job = _job(settings)

    if args.command == "once":
        try:
            reading = CquElectricityClient(settings).fetch()
            CsvStore(settings.data_dir).save(reading)
        except CquError as exc:
            logger.error("抓取失败：{}", exc)
            return 1
        except Exception:
            logger.exception("抓取失败")
            return 1
        logger.info(
            "抓取成功：房间={} 余额={} 元 电表读数={} kWh",
            reading.room,
            f"{reading.total_balance_yuan(settings.electricity_price):.2f}",
            reading.meter_reading_kwh,
        )
        try:
            _warn_low_balance(settings, reading)
        except Exception:
            logger.exception("余额警告邮件发送失败")
            return 1
        return 0

    if args.command == "email":
        try:
            _deliver_email(settings, CsvStore(settings.data_dir).latest())
        except (ChartError, EmailError, FileNotFoundError, ValueError) as exc:
            logger.error("邮件发送失败：{}", exc)
            return 1
        except Exception:
            logger.exception("邮件发送失败")
            return 1
        return 0

    scheduler = BlockingScheduler(timezone=settings.timezone)
    scheduler.add_job(
        job,
        daily_trigger(settings.schedule_time, settings.timezone),
        id="capture",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    if settings.email_enabled:
        email_job = _email_job(settings)
        scheduler.add_job(
            email_job,
            email_trigger(settings.email_schedule, settings.timezone),
            id="email",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown(wait=False))
    logger.info(
        "后台监控已启动，每天抓取时间：{}（{}）",
        settings.schedule_time,
        settings.timezone.key,
    )
    if settings.email_enabled:
        logger.info(
            "邮件定时发送已启用，发送时间：{}",
            settings.email_schedule,
        )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("后台监控已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
