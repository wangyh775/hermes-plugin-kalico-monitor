"""Read-only Moonraker status query for Hermes conversations."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

OBJECTS = {"print_stats": None, "webhooks": None, "extruder": None, "heater_bed": None, "virtual_sdcard": None}


def query_status(url: str, api_key: str = "", timeout: int = 6,
                 objects: dict | None = None) -> dict:
    url = url.rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Moonraker URL must be an HTTP(S) origin")
    payload = json.dumps({"objects": OBJECTS if objects is None else objects}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    request = urllib.request.Request(url + "/printer/objects/query", data=payload,
                                     headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        data = json.load(response)
    if "error" in data:
        raise RuntimeError("Moonraker object query returned an error")
    status = data.get("result", {}).get("status", {})
    if not isinstance(status, dict):
        raise ValueError("Unexpected Moonraker response")
    return status


def query_force_status(url: str, api_key: str = "", timeout: int = 6) -> dict:
    """Query the current filament_force plugin without affecting base status."""
    return query_status(url, api_key, timeout, objects={"filament_force": None})


THERMAL_PREFIXES = ("temperature_sensor ", "heater_generic ", "temperature_fan ")


def _available_objects(url: str, api_key: str, timeout: int = 4) -> list[str]:
    headers = {"X-Api-Key": api_key} if api_key else {}
    request = urllib.request.Request(url.rstrip("/") + "/printer/objects/list", headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        result = json.load(response)
    objects = result.get("result", {}).get("objects", [])
    return objects if isinstance(objects, list) else []


def chamber_object(url: str, api_key: str = "", configured: str = "") -> str:
    """Select only a thermal object; auto-detect a chamber-labelled sensor."""
    if configured:
        if not configured.startswith(THERMAL_PREFIXES):
            raise ValueError("Unsupported chamber thermal object")
        return configured
    try:
        options = _available_objects(url, api_key)
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    for obj in options:
        if (isinstance(obj, str) and obj.startswith(THERMAL_PREFIXES)
                and any(word in obj.lower().split(" ", 1)[1]
                        for word in ("chamber", "enclosure", "腔体"))):
            return obj
    return ""


def query_status_with_chamber(url: str, api_key: str = "", chamber: str = "",
                              include_chamber: bool = True) -> dict:
    """Preserve baseline status if the optional chamber sensor is unavailable."""
    if not include_chamber:
        return query_status(url, api_key)
    obj = chamber_object(url, api_key, chamber)
    if not obj:
        return query_status(url, api_key)
    try:
        data = query_status(url, api_key, objects={**OBJECTS, obj: None})
    except (OSError, ValueError, RuntimeError):
        return query_status(url, api_key)
    data["_kalico_chamber_object"] = obj
    return data
