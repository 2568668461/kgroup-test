"""Parameter merge rule — a pure function with NO database dependency.

Merge layers:
  L1  base_parameters           (task)
  L2  group_overrides           (group; applied unconditionally, "" IS a value)
  L3  ordered step overrides    ("" means "keep the previous effective value")

The effective parameters carry forward across steps ("sticky"), and every step
receives its own independent snapshot (deep copies, inputs never mutated).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


def resolve_step_parameters(
    base_parameters: Mapping[str, Any] | None,
    group_overrides: Mapping[str, Any] | None,
    ordered_step_overrides: Sequence[Mapping[str, Any] | None] | None,
) -> list[dict[str, Any]]:
    """Return one resolved-parameters dict per step, in step order.

    Rules (in order):
    1. Start from a deep copy of L1 base parameters.
    2. Apply every L2 group override unconditionally — including empty strings
       and brand-new keys ("" overwrites base, it does not fall back).
    3. Walk steps in order. For each L3 key:
       - value == ""  -> ignore, keep the previous effective value;
       - otherwise    -> overwrite (sticky: it stays for later steps).
    4. Append a deep copy snapshot of the current effective dict for the step.

    Inputs are never mutated; returned dicts share no state with inputs or
    with each other.
    """
    current: dict[str, Any] = deepcopy(dict(base_parameters)) if base_parameters else {}

    if group_overrides:
        for key, value in group_overrides.items():
            current[key] = deepcopy(value)

    snapshots: list[dict[str, Any]] = []
    for step_overrides in ordered_step_overrides or []:
        if step_overrides:
            for key, value in step_overrides.items():
                if value == "":  # noqa: E712 - deliberate: only the "" string falls back
                    continue
                current[key] = deepcopy(value)
        snapshots.append(deepcopy(current))
    return snapshots
