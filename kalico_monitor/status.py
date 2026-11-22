"""Read-only Moonraker status query for Hermes conversations."""

import json
import urllib.parse
import urllib.request

OBJECTS = {"print_stats": None, "webhooks": None, "extruder": None, "heater_bed": None, "virtual_sdcard": None}


def query_status(url: str, api_key: str = "", timeout: int = 6) -> dict:
    url = url.rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Moonraker URL must be an HTTP(S) origin")
    payload = json.dumps({"objects": OBJECTS}).encode("utf-8")
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
