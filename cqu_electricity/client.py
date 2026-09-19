from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any
from urllib.parse import urlparse

import requests

from .config import Settings
from .models import MeterReading

class CquError(RuntimeError):
    """抓取流程出现可说明的错误。"""


class ParseError(CquError):
    pass


def _number(value: Any, label: str) -> Decimal:
    match = re.search(r"-?\d+(?:\.\d+)?", unescape(str(value)).replace(",", ""))
    if not match:
        raise ParseError(f"响应中缺少{label}")
    try:
        return Decimal(match.group())
    except InvalidOperation as exc:
        raise ParseError(f"无法解析{label}: {value!r}") from exc


class CquElectricityClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 14; Pixel 7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0 Mobile Safari/537.36"
                ),
            }
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.settings.request_timeout)
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def fetch(self) -> MeterReading:
        self._set_charge_token(self.settings.synjones_auth)
        public = urlparse(self.settings.electricity_url)
        query_url = f"{public.scheme}://{public.netloc}/charge/feeitem/getThirdData"
        result, building_name = self._query_room(query_url)
        response_map = result.get("map") or {}
        combined = {
            **(response_map.get("data") or {}),
            **(response_map.get("showData") or {}),
            "room": self.settings.room,
            "building": building_name,
        }
        return self._extract_json(combined)

    def _query_room(self, query_url: str) -> tuple[dict[str, Any], str]:
        base = {"feeitemid": self.settings.fee_item_id}
        initial = self._request(
            "POST", query_url, data={**base, "type": "select", "level": 0}
        ).json()
        initial_map = initial.get("map") or {}
        levels = initial_map.get("total") or []
        buildings = initial_map.get("data") or []
        if initial.get("code") != 200 or len(levels) < 2 or not buildings:
            raise CquError(f"无法加载楼栋列表：{initial.get('msg') or initial}")

        building_code = str(levels[0].get("code") or "building")
        room_code = str(levels[-1].get("code") or "room")
        building_candidates = self._building_candidates(buildings)
        if not building_candidates:
            wanted = self.settings.building or self.settings.room
            raise CquError(f"楼栋列表中找不到 {wanted!r}")

        matched_room: dict[str, Any] | None = None
        matched_building: dict[str, Any] | None = None
        for building in building_candidates:
            rooms_result = self._request(
                "POST",
                query_url,
                data={
                    **base,
                    "type": "select",
                    "level": 1,
                    building_code: building["value"],
                },
            ).json()
            rooms = (rooms_result.get("map") or {}).get("data") or []
            matched_room = next(
                (
                    room
                    for room in rooms
                    if str(room.get("name", "")).strip().upper() == self.settings.room
                    or str(room.get("value", "")).strip().upper() == self.settings.room
                ),
                None,
            )
            if matched_room:
                matched_building = building
                break

        if not matched_room or not matched_building:
            building_text = self.settings.building or "自动识别的宿舍楼"
            raise CquError(f"{building_text} 中找不到房间 {self.settings.room}")

        final = self._request(
            "POST",
            query_url,
            data={
                **base,
                "type": "IEC",
                "level": len(levels),
                building_code: matched_building["value"],
                room_code: matched_room["value"],
            },
        ).json()
        final_map = final.get("map") or {}
        if final.get("code") != 200 or not final_map.get("showData"):
            raise CquError(f"电费查询失败：{final.get('msg') or final}")
        return final, str(matched_building.get("name") or matched_building["value"])

    def _building_candidates(self, buildings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.settings.building:
            wanted = self.settings.building.strip().lower()
            return [
                item
                for item in buildings
                if wanted == str(item.get("name", "")).strip().lower()
                or wanted == str(item.get("value", "")).strip().lower()
            ]

        # 虎溪房号首字母对应园区，首个数字对应楼号；优先查询最可能楼栋。
        garden_by_prefix = {"A": "梅园", "B": "竹园", "C": "松园", "D": "兰园"}
        garden = garden_by_prefix.get(self.settings.room[:1])
        number_match = re.search(r"\d", self.settings.room)
        number = number_match.group() if number_match else ""
        preferred = [
            item
            for item in buildings
            if garden
            and garden in str(item.get("name", ""))
            and number in str(item.get("name", ""))
        ]
        # 未识别时只遍历看起来像宿舍的选项，避免查询商户和教学楼。
        fallback = [
            item
            for item in buildings
            if item not in preferred
            and any(word in str(item.get("name", "")) for word in ("园", "公寓", "宿舍"))
        ]
        return preferred + fallback

    def _set_charge_token(self, token: str) -> None:
        public = urlparse(self.settings.electricity_url)
        authorization = f"bearer {token.removeprefix('bearer ').removeprefix('Bearer ')}"
        self.session.headers["synjones-auth"] = authorization
        self.session.headers["Authorization"] = "Basic Y2hhcmdlOmNoYXJnZV9zZWNyZXQ="
        # 网页端也把令牌写入 /charge 路径 Cookie；一并设置以兼容不同版本。
        self.session.cookies.set("synjones-auth", authorization, domain=public.hostname, path="/charge")

    def _extract_json(self, data: Any) -> MeterReading:
        candidates: list[dict[str, Any]] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                candidates.append(value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data)
        room_key_names = ("room", "roomname", "fjmc", "accountname")
        target = next(
            (
                item
                for item in candidates
                if any(str(item.get(k, "")).upper() == self.settings.room for k in room_key_names)
            ),
            candidates[0] if candidates else {},
        )

        # 第三方水电接口常用 [{name/label: "剩余金额", value: "84.57"}]。
        labelled: dict[str, Any] = {}
        for item in candidates:
            label = item.get("name") or item.get("label") or item.get("title") or item.get("text")
            value = item.get("value") if "value" in item else item.get("data")
            if label not in (None, "") and value not in (None, ""):
                labelled[str(label).strip().lower()] = value

        all_values: dict[str, Any] = {}
        for item in candidates:
            for key, value in item.items():
                if not isinstance(value, (dict, list)) and value not in (None, ""):
                    all_values.setdefault(str(key).strip().lower(), value)

        def pick(*names: str) -> Any:
            lowered = {str(k).strip().lower(): v for k, v in target.items()}
            for name in names:
                normalized = name.lower()
                if normalized in lowered and lowered[normalized] not in (None, ""):
                    return lowered[normalized]
                if normalized in all_values:
                    return all_values[normalized]
                if normalized in labelled:
                    return labelled[normalized]
            raise ParseError(f"接口 JSON 缺少字段：{'/'.join(names)}")

        return MeterReading(
            captured_at=datetime.now(self.settings.timezone),
            room=self.settings.room,
            building=str(
                target.get("building")
                or target.get("buildingname")
                or all_values.get("building")
                or all_values.get("buildingname")
                or self.settings.building
                or ""
            ),
            balance_yuan=_number(
                pick(
                    "balance",
                    "money",
                    "surplusmoney",
                    "remainingamount",
                    "剩余金额",
                    "余额",
                ),
                "余额",
            ),
            meter_reading_kwh=_number(
                pick(
                    "meterreading",
                    "reading",
                    "electricreading",
                    "usedegree",
                    "quantity",
                    "电表读数",
                    "电量读数",
                ),
                "电表读数",
            ),
            subsidy_kwh=self._optional_number(
                {**all_values, **labelled, **target},
                "subsidy",
                "electricallowance",
                "surplusdegree",
                "电剩余补助（度）",
                "电剩余补助",
            ),
            meter_address=str(
                target.get("meteraddress")
                or all_values.get("meteraddress")
                or all_values.get("电表地址")
                or labelled.get("电表地址")
                or ""
            )
            or None,
        )

    @staticmethod
    def _optional_number(data: dict[str, Any], *names: str) -> Decimal | None:
        lowered = {str(k).lower(): v for k, v in data.items()}
        for name in names:
            value = lowered.get(name.lower())
            if value not in (None, ""):
                return _number(value, name)
        return None
