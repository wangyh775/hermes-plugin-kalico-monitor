"""State transition detector adapted from TakeshiTGAL/klipper-print-watch.

Original work: Copyright (c) 2026 TakeshiTGAL, MIT license.
See LICENSE and NOTICE.md. No network I/O occurs in this module.
"""
from __future__ import annotations

from typing import Any

STOPPED = {"shutdown", "error"}
TERMINAL = {"complete", "error", "cancelled"}


def as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def sample_from_status(status: dict) -> dict:
    stats = status.get("print_stats", {})
    virtual = status.get("virtual_sdcard", {})
    hooks = status.get("webhooks", {})
    stats = stats if isinstance(stats, dict) else {}
    virtual = virtual if isinstance(virtual, dict) else {}
    hooks = hooks if isinstance(hooks, dict) else {}
    return {
        "filename": stats.get("filename") if isinstance(stats.get("filename"), str) else "",
        "state": stats.get("state") if isinstance(stats.get("state"), str) else "",
        "message": stats.get("message") if isinstance(stats.get("message"), str) else "",
        "klippy_state": hooks.get("state") if isinstance(hooks.get("state"), str) else "",
        "klippy_message": hooks.get("state_message") if isinstance(hooks.get("state_message"), str) else "",
        "file_position": as_number(virtual.get("file_position")),
        "progress": as_number(virtual.get("progress")),
    }


def initial_state(sample: dict, now: float) -> dict:
    """Initial baseline. Shutdown is still reported by compare()."""
    return {
        "version": 1,
        "filename": sample["filename"],
        "state": sample["state"],
        "klippy_state": sample["klippy_state"],
        "generation": 1 if sample["state"] in ("printing", "paused") else 0,
        "stops": 0,
        "notified": [],
        "file_position": sample["file_position"],
        "progress": sample["progress"],
        "progress_changed_at": now,
        "stall_open": False,
    }


def compare(previous: dict | None, sample: dict, now: float, stall_minutes: int = 0) -> tuple[dict, list[dict]]:
    """Return updated persistent state plus edge-triggered events.

    Preserves job generation across resume (paused -> printing); dedupes
    terminal events per generation. A shutdown wins over print-error.
    """
    if previous is None:
        state = initial_state(sample, now)
        if sample["klippy_state"] in STOPPED:
            state["stops"] = 1
            return state, [{"kind": "klippy_shutdown", "generation": 1,
                            "detail": sample["klippy_message"] or "Kalico stopped at monitor startup"}]
        return state, []

    state = dict(previous)
    events = []
    generation = int(previous.get("generation", 0))
    old_print = previous.get("state", "")
    new_print = sample["state"]
    old_filename = previous.get("filename", "")
    old_klippy = previous.get("klippy_state", "")
    new_klippy = sample["klippy_state"]
    stopped = new_klippy in STOPPED

    new_job = new_print == "printing" and (
        old_print in ("", "standby", "complete", "error", "cancelled")
        or (bool(sample["filename"]) and sample["filename"] != old_filename)
    )
    if new_job:
        generation += 1
    notified = set(previous.get("notified", []))
    if new_job:
        notified = set()

    if stopped and old_klippy not in STOPPED:
        stops = int(previous.get("stops", 0)) + 1
        state["stops"] = stops
        events.append({"kind": "klippy_shutdown", "generation": stops,
                       "detail": sample["klippy_message"] or "Klippy entered shutdown/error state"})

    def once(kind: str, detail: str) -> None:
        key = f"{generation}:{kind}"
        if key not in notified:
            notified.add(key)
            events.append({"kind": kind, "generation": generation, "detail": detail})

    if new_print != old_print:
        if new_print == "complete":
            once("complete", f"Print completed: {sample['filename'] or '(unknown)'}")
        elif new_print == "error" and not stopped:
            once("error", sample["message"] or "Print entered error state")
        elif new_print == "paused" and old_print == "printing" and not stopped:
            once("paused", f"Print paused: {sample['filename'] or '(unknown)'}")
        elif new_print == "cancelled":
            once("cancelled", f"Print cancelled: {sample['filename'] or '(unknown)'}")

    changed_at = float(previous.get("progress_changed_at") or now)
    stall_open = bool(previous.get("stall_open", False))
    moved = (
        sample["filename"] != old_filename
        or sample["file_position"] != previous.get("file_position")
        or sample["progress"] != previous.get("progress")
    )
    if new_print == "printing" and sample["file_position"] is not None and not stopped:
        if moved or new_job:
            changed_at = now
            stall_open = False
        elif stall_minutes > 0 and not stall_open and now - changed_at >= 60 * stall_minutes:
            stall_open = True
            once("stalled", f"File position has not advanced for {stall_minutes} minutes")
    else:
        changed_at = now
        stall_open = False

    state.update({
        "version": 1, "filename": sample["filename"], "state": new_print,
        "klippy_state": new_klippy, "generation": generation,
        "notified": sorted(notified), "file_position": sample["file_position"],
        "progress": sample["progress"], "progress_changed_at": changed_at,
        "stall_open": stall_open,
    })
    return state, events
