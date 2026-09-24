from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import MeterReading


SNAPSHOT_FIELDS = (
    "captured_at",
    "room",
    "building",
    "balance_yuan",
    "meter_reading_kwh",
    "subsidy_kwh",
    "subsidy_balance_yuan",
    "unit_price_yuan_per_kwh",
    "meter_address",
)


def _text(value: object | None) -> str:
    return "" if value is None else str(value)


class CsvStore:
    """将每次抓取的原始电表数据追加到一个 CSV 文件。"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.snapshots_path = data_dir / "history.csv"

    def save(self, reading: MeterReading) -> dict[str, str]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "captured_at": reading.captured_at.isoformat(timespec="seconds"),
            "room": reading.room,
            "building": reading.building,
            "balance_yuan": _text(reading.balance_yuan),
            "meter_reading_kwh": _text(reading.meter_reading_kwh),
            "subsidy_kwh": _text(reading.subsidy_kwh),
            "subsidy_balance_yuan": _text(reading.subsidy_balance_yuan),
            "unit_price_yuan_per_kwh": _text(reading.unit_price_yuan_per_kwh),
            "meter_address": _text(reading.meter_address),
        }
        self._ensure_schema()
        is_new = not self.snapshots_path.exists() or self.snapshots_path.stat().st_size == 0
        with self.snapshots_path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow(row)
        return row

    def _ensure_schema(self) -> None:
        if not self.snapshots_path.exists() or self.snapshots_path.stat().st_size == 0:
            return
        with self.snapshots_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            old_fields = reader.fieldnames or []
            if old_fields == list(SNAPSHOT_FIELDS):
                return
            if not set(old_fields).issubset(SNAPSHOT_FIELDS):
                raise ValueError("history.csv 包含未知字段，无法自动升级表头")
            rows = list(reader)

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8-sig", newline="", dir=self.data_dir,
                prefix="history-", suffix=".tmp", delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temp_path, self.snapshots_path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def latest(self, room: str | None = None) -> MeterReading:
        if not self.snapshots_path.exists():
            raise FileNotFoundError(f"历史数据不存在：{self.snapshots_path}")
        with self.snapshots_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if room is not None:
            rows = [row for row in rows if (row.get("room") or "").upper() == room.upper()]
        if not rows:
            raise ValueError(f"history.csv 中没有房间 {room} 的数据" if room else "history.csv 中没有可用于发送邮件的数据")
        row = rows[-1]
        try:
            return MeterReading(
                captured_at=datetime.fromisoformat(row["captured_at"]),
                room=row["room"],
                building=row["building"],
                balance_yuan=Decimal(row["balance_yuan"]),
                meter_reading_kwh=Decimal(row["meter_reading_kwh"]) if row.get("meter_reading_kwh") else None,
                subsidy_kwh=Decimal(row["subsidy_kwh"]) if row.get("subsidy_kwh") else None,
                subsidy_balance_yuan=(
                    Decimal(row["subsidy_balance_yuan"])
                    if row.get("subsidy_balance_yuan") else None
                ),
                unit_price_yuan_per_kwh=(
                    Decimal(row["unit_price_yuan_per_kwh"])
                    if row.get("unit_price_yuan_per_kwh") else None
                ),
                meter_address=row.get("meter_address") or None,
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ValueError("history.csv 最后一行格式无效") from exc
