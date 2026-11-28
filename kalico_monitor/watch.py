"""State transition detector adapted from TakeshiTGAL/klipper-print-watch.

Original work: Copyright (c) 2026 TakeshiTGAL, MIT license.
See LICENSE and NOTICE.md. No network I/O occurs in this module.
"""
from __future__ import annotations

import math
from typing import Any

STOPPED = {"shutdown", "error"}


def as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


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
        "total_duration": as_number(stats.get("total_duration")),
        "job_identity": status.get("_monitor_job", {}).get("identity"),
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
        "transition_counts": {},
        "file_position": sample["file_position"],
        "progress": sample["progress"],
        "progress_changed_at": now,
        "total_duration": sample.get("total_duration"),
        "job_identity": sample.get("job_identity"),
        "stall_open": False,
    }


def compare(previous: dict | None, sample: dict, now: float, stall_minutes: int = 0, *, allow_stall: bool = True) -> tuple[dict, list[dict]]:
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

    old_duration, duration = previous.get("total_duration"), sample.get("total_duration")
    reset = (old_print in ("printing", "paused") and old_duration is not None
             and duration is not None and old_duration - duration > 2
             and sample.get("file_position") is not None
             and previous.get("file_position") is not None
             and sample["file_position"] < previous["file_position"])
    identity = sample.get("job_identity")
    old_identity = previous.get("job_identity")
    identity_changed = bool(identity and old_identity and identity != old_identity)
    new_job = new_print == "printing" and (reset or identity_changed or
        old_print in ("", "standby", "complete", "error", "cancelled")
        or (bool(sample["filename"]) and sample["filename"] != old_filename)
    )
    if new_job:
        generation += 1
    notified = set(previous.get("notified", []))
    if new_job:
        notified = set()
    transition_counts = dict(previous.get("transition_counts") or {})
    if new_job:
        transition_counts = {}

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

    def transition(kind: str, detail: str) -> None:
        # Pause/resume can happen repeatedly in one print. A generation-only
        # dedupe key loses the second pause; persist a per-kind sequence.
        sequence = int(transition_counts.get(kind, 0)) + 1
        transition_counts[kind] = sequence
        events.append({"kind": kind, "generation": generation, "detail": detail,
                       "sequence": sequence})

    if new_job and not stopped:
        once("started", f"Print started: {sample['filename'] or '(unknown)'}")
    if new_print != old_print:
        if new_print == "complete":
            once("complete", f"Print completed: {sample['filename'] or '(unknown)'}")
        elif new_print == "error" and not stopped:
            once("error", sample["message"] or "Print entered error state")
        elif new_print == "paused" and old_print == "printing" and not stopped:
            transition("paused", f"Print paused: {sample['filename'] or '(unknown)'}")
        elif new_print == "printing" and old_print == "paused" and not stopped:
            transition("resumed", f"Print resumed: {sample['filename'] or '(unknown)'}")
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
        if moved or new_job or changed_at > now:
            changed_at = now
            stall_open = False
        elif allow_stall and stall_minutes > 0 and not stall_open and now - changed_at >= 60 * stall_minutes:
            stall_open = True
            transition("stalled", f"File position has not advanced for {stall_minutes} minutes")
    else:
        changed_at = now
        stall_open = False

    state.update({
        "version": 1, "filename": sample["filename"], "state": new_print,
        "klippy_state": new_klippy, "generation": generation,
        "notified": sorted(notified), "transition_counts": transition_counts,
        "file_position": sample["file_position"],
        "progress": sample["progress"], "progress_changed_at": changed_at,
        "total_duration": sample.get("total_duration"),
        "job_identity": identity if identity else (old_identity if not new_job else None),
        "stall_open": stall_open,
    })
    return state, events
