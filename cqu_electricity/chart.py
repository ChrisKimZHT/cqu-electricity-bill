from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import DEFAULT_ELECTRICITY_PRICE, total_balance_yuan


class ChartError(RuntimeError):
    """历史数据不足或格式错误。"""


@dataclass(frozen=True, slots=True)
class DailyPoint:
    day: date
    usage_kwh: float
    balance_yuan: float


def _decimal(raw: str, field: str, line_number: int) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ChartError(f"history.csv 第 {line_number} 行的 {field} 无效：{raw!r}") from exc


def load_daily_points(
    history_path: Path,
    room: str,
    days: int = 14,
    electricity_price: Decimal = DEFAULT_ELECTRICITY_PRICE,
) -> list[DailyPoint]:
    if not history_path.exists():
        raise ChartError(f"找不到历史数据文件：{history_path}")

    latest_by_day: dict[date, tuple[datetime, Decimal, Decimal]] = {}
    with history_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"captured_at", "room", "balance_yuan", "meter_reading_kwh"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ChartError(f"history.csv 缺少字段：{', '.join(sorted(missing))}")

        for line_number, row in enumerate(reader, start=2):
            if row["room"].strip().upper() != room.upper():
                continue
            try:
                captured_at = datetime.fromisoformat(row["captured_at"])
            except ValueError as exc:
                raise ChartError(
                    f"history.csv 第 {line_number} 行 captured_at 无效：{row['captured_at']!r}"
                ) from exc
            balance = _decimal(row["balance_yuan"], "balance_yuan", line_number)
            subsidy = (
                _decimal(row["subsidy_kwh"], "subsidy_kwh", line_number)
                if row.get("subsidy_kwh") else None
            )
            balance = total_balance_yuan(balance, subsidy, electricity_price)
            meter = _decimal(row["meter_reading_kwh"], "meter_reading_kwh", line_number)
            current = latest_by_day.get(captured_at.date())
            if current is None or captured_at > current[0]:
                latest_by_day[captured_at.date()] = (captured_at, meter, balance)

    if not latest_by_day:
        raise ChartError(f"history.csv 中没有房间 {room} 的数据")

    end_day = max(latest_by_day)
    start_day = end_day - timedelta(days=days - 1)
    points: list[DailyPoint] = []
    for offset in range(days):
        current_day = start_day + timedelta(days=offset)
        current = latest_by_day.get(current_day)
        previous = latest_by_day.get(current_day - timedelta(days=1))
        balance_value = float(current[2]) if current else math.nan
        usage_value = math.nan
        if current and previous:
            usage = current[1] - previous[1]
            if usage >= 0:
                usage_value = float(usage)
        points.append(DailyPoint(current_day, usage_value, balance_value))
    return points


def draw_history_chart(
    history_path: Path,
    output_path: Path,
    room: str,
    electricity_price: Decimal = DEFAULT_ELECTRICITY_PRICE,
) -> Path:
    matplotlib_config = output_path.parent / ".matplotlib"
    matplotlib_config.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_config.resolve()))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    points = load_daily_points(history_path, room, days=14, electricity_price=electricity_price)
    labels = [point.day.strftime("%m-%d") for point in points]
    positions = list(range(len(points)))

    font_path = Path(__file__).resolve().parent.parent / "SourceHanSansCN-Bold.ttf"
    font_manager.fontManager.addfont(str(font_path))
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["text.color"] = "black"
    plt.rcParams["axes.labelcolor"] = "black"
    plt.rcParams["axes.titlecolor"] = "black"
    plt.rcParams["xtick.color"] = "black"
    plt.rcParams["ytick.color"] = "black"

    figure, usage_axis = plt.subplots(figsize=(9, 6), dpi=150)
    bars = usage_axis.bar(
        positions,
        [point.usage_kwh for point in points],
        width=0.62,
        color="#1f77b4",
        label="每日用电量",
        zorder=2,
    )
    usage_axis.set_xlabel("日期")
    usage_axis.set_ylabel("每日用电量（度）", color="black")
    usage_axis.tick_params(axis="both", colors="black")
    usage_axis.grid(axis="y", linestyle="--", alpha=0.25, zorder=1)

    balance_axis = usage_axis.twinx()
    usage_axis.yaxis.tick_right()
    usage_axis.yaxis.set_label_position("right")
    balance_axis.yaxis.tick_left()
    balance_axis.yaxis.set_label_position("left")
    line = balance_axis.plot(
        positions,
        [point.balance_yuan for point in points],
        color="#ff7f0e",
        linewidth=2.2,
        marker="o",
        markersize=5,
        label="余额",
        zorder=3,
    )[0]
    balance_axis.set_ylabel("余额（元）", color="black")
    balance_axis.tick_params(axis="y", colors="black")

    usage_axis.set_xlim(-0.7, len(points) - 0.3)
    usage_axis.set_xticks(positions, labels, rotation=35, ha="right")
    if not any(not math.isnan(point.usage_kwh) for point in points):
        usage_axis.set_ylim(0, 1)
        usage_axis.text(
            0.5,
            0.5,
            "暂无连续两日采样，无法计算每日用电量",
            transform=usage_axis.transAxes,
            ha="center",
            va="center",
            color="black",
        )

    usage_axis.set_title(f"{room} 最近14天用电情况")
    usage_axis.legend([bars, line], ["每日用电量", "余额"], loc="upper left")
    figure.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path)
    plt.close(figure)
    return output_path
