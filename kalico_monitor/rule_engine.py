"""Generate read-only threshold events from subscribed Moonraker status.

This never issues printer commands. Rising-edge threshold alarms require a
continuous valid observation window; only a valid normal reading rearms the rule.
"""
from __future__ import annotations

import math
import time
from typing import Any

from .custom_config import EventRule, Reliability
from .events import Alert
from .storage import Outbox

META_KEY = "custom_event_rules_v1"


def numeric_field(status: dict, obj: str, dotted: str) -> float | None:
    value: Any = status.get(obj)
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def matched(value: float, condition: str, threshold: float) -> bool:
    return {
        "gt": value > threshold, "ge": value >= threshold,
        "lt": value < threshold, "le": value <= threshold,
    }[condition]


class RuleEngine:
    def __init__(self, store: Outbox, rules: tuple[EventRule, ...], reliability: Reliability | None = None):
        self.store = store
        self.rules = rules
        self.reliability = reliability or Reliability()
        previous = store.get_meta(META_KEY, {})
        self.tracker = previous if isinstance(previous, dict) else {}
        self.suspend()

    def suspend(self):
        changed = False
        for state in self.tracker.values():
            if isinstance(state, dict) and (state.get("since") is not None or state.get("last") is not None):
                state["since"] = None
                state["last"] = None
                state["last_observation"] = None
                changed = True
        if changed:
            self.store.set_meta(META_KEY, self.tracker)

    @property
    def objects(self) -> tuple[str, ...]:
        return tuple(sorted({rule.object for rule in self.rules}))

    def evaluate(self, status: dict, now: float | None = None,
                 *, connected: bool = True, generation: int = 0,
                 filename: str = "", field_seen: dict | None = None) -> int:
        now = time.monotonic() if now is None else now
        changed = False
        count = 0
        for rule in self.rules:
            previous = self.tracker.get(rule.name, {})
            previous = previous if isinstance(previous, dict) else {}
            state = dict(previous)
            signature = (rule.object, rule.field, rule.condition, rule.threshold,
                         rule.duration, rule.cooldown)
            if state.get("signature") != list(signature):
                # Configuration changes require a new continuous observation window.
                state = {"sequence":state.get("sequence", 0),
                         "signature":list(signature), "since":None,
                         "fired":False, "last":None}
            fresh = field_seen is None or 0 <= now - field_seen.get(
                (rule.object, rule.field.split(".")[0]), float("-inf")) <= self.reliability.field_max_age
            value = numeric_field(status, rule.object, rule.field) if connected and fresh else None
            active = value is not None and matched(value, rule.condition, rule.threshold)
            observed = now if field_seen is None else field_seen.get((rule.object, rule.field.split(".")[0]))
            state["quality"] = ("unavailable" if not connected or rule.object not in status else
                                "stale" if not fresh else "invalid" if value is None else "valid")
            since = state.get("since")
            if not active:
                state["since"] = None
                state["last_observation"] = None
                if value is not None:
                    state["fired"] = False
            else:
                last_observation = state.get("last_observation")
                if (not isinstance(since, (int, float)) or since > now
                        or (observed is not None and last_observation is not None
                            and observed - last_observation > self.reliability.observation_gap)):
                    state["since"] = observed if observed is not None else now
                state["last_observation"] = observed
                last = state.get("last")
                cooldown_ok = (not isinstance(last, (int, float))
                               or now - last >= rule.cooldown)
                if field_seen is not None and isinstance(state.get("last_wall"), (int, float)):
                    elapsed_wall = time.time() - state["last_wall"]
                    if elapsed_wall < 0:
                        state["last_wall"] = time.time()
                        elapsed_wall = 0
                    cooldown_ok = cooldown_ok and elapsed_wall >= rule.cooldown
                if (not state.get("fired")
                        and observed is not None and observed - state["since"] >= rule.duration
                        and cooldown_ok):
                    seq = int(state.get("sequence", 0)) + 1
                    state.update(fired=True, last=now, last_wall=time.time(), sequence=seq)
                    payload = {
                        "source": "moonraker", "object": rule.object,
                        "field": rule.field, "value": value,
                        "condition": rule.condition, "threshold": rule.threshold,
                        "duration_s": rule.duration,
                    }
                    alert = Alert(
                        kind=rule.name, generation=generation,
                        filename=filename,
                        detail=(f"{rule.object}.{rule.field} {rule.condition} "
                                f"{rule.threshold:g}; 当前值 {value:g}；"
                                f"持续 {rule.duration:g} s"),
                        severity=rule.severity, source_id=f"{rule.name}:{seq}",
                        payload=payload, trigger="threshold",
                    )
                    self.tracker[rule.name] = state
                    self.store.report_checkpoint(META_KEY, self.tracker, alert)
                    count += 1
            if state != previous:
                self.tracker[rule.name] = state
                changed = True
        if changed:
            self.store.set_meta(META_KEY, self.tracker)
        return count
