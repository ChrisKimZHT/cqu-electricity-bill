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
    usage_estimated: bool = False
    balance_estimated: bool = False
    usage_available: bool = True
    usage_from_balance: bool = False


@dataclass(frozen=True, slots=True)
class _Snapshot:
    captured_at: datetime
    meter_reading_kwh: Decimal | None
    balance_yuan: Decimal
    subsidy_balance_yuan: Decimal | None
    unit_price_yuan_per_kwh: Decimal | None


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

    latest_by_day: dict[date, _Snapshot] = {}
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
            subsidy_balance = (
                _decimal(row["subsidy_balance_yuan"], "subsidy_balance_yuan", line_number)
                if row.get("subsidy_balance_yuan") else None
            )
            balance = total_balance_yuan(balance, subsidy, electricity_price, subsidy_balance)
            meter = (
                _decimal(row["meter_reading_kwh"], "meter_reading_kwh", line_number)
                if row.get("meter_reading_kwh") else None
            )
            unit_price = (
                _decimal(row["unit_price_yuan_per_kwh"], "unit_price_yuan_per_kwh", line_number)
                if row.get("unit_price_yuan_per_kwh") else None
            )
            if unit_price is not None and (not unit_price.is_finite() or unit_price <= 0):
                raise ChartError(f"history.csv 第 {line_number} 行的 unit_price_yuan_per_kwh 必须大于 0")
            current = latest_by_day.get(captured_at.date())
            if current is None or captured_at > current.captured_at:
                latest_by_day[captured_at.date()] = _Snapshot(
                    captured_at, meter, balance, subsidy_balance, unit_price
                )

    if not latest_by_day:
        raise ChartError(f"history.csv 中没有房间 {room} 的数据")

    end_day = max(latest_by_day)
    start_day = end_day - timedelta(days=days - 1)
    usage_by_day: dict[date, tuple[float, bool]] = {}
    balance_by_day: dict[date, tuple[float, bool]] = {}
    for offset in range(days):
        current_day = start_day + timedelta(days=offset)
        current = latest_by_day.get(current_day)
        previous = latest_by_day.get(current_day - timedelta(days=1))
        if current:
            balance_by_day[current_day] = (float(current.balance_yuan), False)
        if current and previous:
            if current.meter_reading_kwh is not None and previous.meter_reading_kwh is not None:
                usage = current.meter_reading_kwh - previous.meter_reading_kwh
                if usage >= 0:
                    usage_by_day[current_day] = (float(usage), False)
            elif (
                current.subsidy_balance_yuan is not None
                and previous.subsidy_balance_yuan is not None
                and current.unit_price_yuan_per_kwh is not None
            ):
                spent = previous.balance_yuan - current.balance_yuan
                if spent >= 0:
                    usage_by_day[current_day] = (
                        float(spent / current.unit_price_yuan_per_kwh), True
                    )

    sampled_days = sorted(day for day in latest_by_day if start_day <= day <= end_day)
    for first_day, last_day in zip(sampled_days, sampled_days[1:]):
        gap_days = (last_day - first_day).days
        if gap_days == 1:
            continue
        first_meter = latest_by_day[first_day].meter_reading_kwh
        last_meter = latest_by_day[last_day].meter_reading_kwh
        if first_meter is not None and last_meter is not None and last_meter >= first_meter:
            average_usage = float((last_meter - first_meter) / gap_days)
            for offset in range(1, gap_days + 1):
                usage_by_day[first_day + timedelta(days=offset)] = (average_usage, True)

        first_balance = latest_by_day[first_day].balance_yuan
        last_balance = latest_by_day[last_day].balance_yuan
        for offset in range(1, gap_days):
            missing_day = first_day + timedelta(days=offset)
            interpolated = first_balance + (last_balance - first_balance) * offset / gap_days
            balance_by_day[missing_day] = (float(interpolated), True)

    points: list[DailyPoint] = []
    samples = [snapshot for day, snapshot in latest_by_day.items() if start_day <= day <= end_day]
    usage_from_balance = any(
        snapshot.subsidy_balance_yuan is not None
        and snapshot.unit_price_yuan_per_kwh is not None
        for snapshot in samples
    )
    usage_available = usage_from_balance or any(
        snapshot.meter_reading_kwh is not None for snapshot in samples
    )
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        usage, usage_estimated = usage_by_day.get(day, (math.nan, False))
        balance, balance_estimated = balance_by_day.get(day, (math.nan, False))
        points.append(DailyPoint(
            day, usage, balance, usage_estimated, balance_estimated,
            usage_available, usage_from_balance,
        ))
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
    from matplotlib.lines import Line2D

    points = load_daily_points(history_path, room, days=14, electricity_price=electricity_price)
    labels = [point.day.strftime("%m-%d") for point in points]
    positions = list(range(len(points)))

    font_path = Path(__file__).resolve().parent / "fonts" / "SourceHanSansCN-Normal.ttf"
    font_manager.fontManager.addfont(str(font_path))
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams["font.weight"] = "normal"
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["text.color"] = "black"
    plt.rcParams["axes.labelcolor"] = "black"
    plt.rcParams["axes.titlecolor"] = "black"
    plt.rcParams["xtick.color"] = "black"
    plt.rcParams["ytick.color"] = "black"

    if not points[0].usage_available:
        figure, axis = plt.subplots(figsize=(9, 6), dpi=150)
        balances = [point.balance_yuan for point in points]
        axis.plot(positions, balances, color="#666666", linewidth=2.2)
        axis.plot(
            positions,
            [point.balance_yuan if not point.balance_estimated else math.nan for point in points],
            color="#ff7f0e", linewidth=2.2, marker="o", markersize=5, label="余额",
        )
        if any(point.balance_estimated for point in points):
            axis.plot(
                positions,
                [point.balance_yuan if point.balance_estimated else math.nan for point in points],
                color="#666666", linestyle="None", marker="o", markersize=5, label="估算值",
            )
        axis.set_xlim(-0.7, len(points) - 0.3)
        axis.set_xticks(positions, labels, rotation=35, ha="right")
        axis.set_xlabel("日期")
        axis.set_ylabel("余额（元）")
        axis.set_title(f"{room} 最近14天电费余额")
        axis.grid(axis="y", linestyle="--", alpha=0.25)
        axis.legend(loc="upper left")
        figure.tight_layout()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output_path)
        plt.close(figure)
        return output_path

    figure, usage_axis = plt.subplots(figsize=(9, 6), dpi=150)
    bars = usage_axis.bar(
        positions,
        [point.usage_kwh for point in points],
        width=0.62,
        color=[
            "#b0b0b0" if points[0].usage_from_balance or point.usage_estimated else "#1f77b4"
            for point in points
        ],
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
    balances = [point.balance_yuan for point in points]
    balance_axis.plot(
        positions,
        balances,
        color="#666666",
        linewidth=2.2,
        zorder=3,
    )
    line = balance_axis.plot(
        positions,
        [point.balance_yuan if not point.balance_estimated else math.nan for point in points],
        color="#ff7f0e",
        linewidth=2.2,
        marker="o",
        markersize=5,
        label="余额",
        zorder=3,
    )[0]
    balance_axis.plot(
        positions,
        [point.balance_yuan if point.balance_estimated else math.nan for point in points],
        color="#666666",
        linestyle="None",
        marker="o",
        markersize=5,
        zorder=4,
    )
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

    title = f"{room} 最近14天用电情况"
    if points[0].usage_from_balance:
        title += "（按余额估算）"
    usage_axis.set_title(title)
    usage_label = "每日用电量（余额估算）" if points[0].usage_from_balance else "每日用电量"
    legend_handles = [bars, line]
    legend_labels = [usage_label, "余额"]
    if any(point.balance_estimated for point in points) or (
        not points[0].usage_from_balance and any(point.usage_estimated for point in points)
    ):
        legend_handles.append(Line2D([], [], color="#666666", marker="o"))
        legend_labels.append("估算值")
    usage_axis.legend(legend_handles, legend_labels, loc="upper left")
    figure.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path)
    plt.close(figure)
    return output_path
