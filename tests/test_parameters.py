"""Unit tests for the pure parameter merge function — no database needed."""

from __future__ import annotations

from copy import deepcopy

from app.domain.parameters import resolve_step_parameters


def test_three_layer_override():
    # L1 -> L2 -> L3, each layer overwrites
    result = resolve_step_parameters(
        {"region": "us", "timeout": 30},
        {"region": "cn"},
        [{"region": "eu"}],
    )
    assert result == [{"region": "eu", "timeout": 30}]


def test_group_empty_string_overrides_base():
    # "" from L2 is a REAL value, it must overwrite L1
    result = resolve_step_parameters(
        {"region": "us"},
        {"region": ""},
        [{}],
    )
    assert result == [{"region": ""}]


def test_step_empty_string_keeps_previous_effective_value():
    # "" from L3 means "keep the current effective value"
    result = resolve_step_parameters(
        {"region": "us"},
        {"region": "cn"},
        [{"region": ""}],
    )
    assert result == [{"region": "cn"}]


def test_step_empty_string_falls_back_to_group_value_not_base():
    # after L2 overwrote base, an L3 "" keeps the L2 value
    result = resolve_step_parameters(
        {"timeout": 1},
        {"timeout": 2},
        [{"timeout": 3}, {"timeout": ""}],
    )
    assert result == [{"timeout": 3}, {"timeout": 3}]


def test_sticky_propagation_across_steps():
    # a value set at step 1 persists at step 2 unless overridden again
    result = resolve_step_parameters(
        {"mode": "slow"},
        None,
        [{"mode": "fast"}, {}, {}],
    )
    assert result == [
        {"mode": "fast"},
        {"mode": "fast"},
        {"mode": "fast"},
    ]


def test_later_step_can_override_again():
    result = resolve_step_parameters(
        None,
        None,
        [{"mode": "fast"}, {"mode": "turbo"}],
    )
    assert result == [{"mode": "fast"}, {"mode": "turbo"}]


def test_new_keys_from_group_and_step():
    result = resolve_step_parameters(
        {"a": 1},
        {"b": 2},
        [{"c": 3}, {"b": ""}],
    )
    # b="" at step 2 is ignored, keeps the group value
    assert result == [
        {"a": 1, "b": 2, "c": 3},
        {"a": 1, "b": 2, "c": 3},
    ]


def test_mixed_json_types_not_coerced():
    # numbers, booleans, None and strings must stay distinct
    result = resolve_step_parameters(
        {"n": 0, "b": False, "s": "", "z": None},
        {"n": 1, "b": True},
        [{"n": 0, "b": False, "s": "x", "z": None}],
    )
    assert result == [{"n": 0, "b": False, "s": "x", "z": None}]


def test_empty_string_only_falls_back_for_exact_empty_string():
    # 0, False, None are NOT treated as "empty"
    result = resolve_step_parameters(
        {"v": "initial"},
        None,
        [{"v": 0}, {"v": False}, {"v": None}],
    )
    assert result == [{"v": 0}, {"v": False}, {"v": None}]


def test_empty_base_and_overrides():
    assert resolve_step_parameters(None, None, None) == []
    assert resolve_step_parameters({}, {}, [{}]) == [{}]


def test_empty_step_list():
    assert resolve_step_parameters({"a": 1}, {"b": 2}, []) == []


def test_inputs_are_not_mutated():
    base = {"region": "us", "nested": {"k": "v"}}
    group = {"region": "cn", "extra": [1, 2]}
    steps = [{"region": "eu"}]
    base_orig, group_orig, steps_orig = deepcopy(base), deepcopy(group), deepcopy(steps)

    resolve_step_parameters(base, group, steps)

    assert base == base_orig
    assert group == group_orig
    assert steps == steps_orig


def test_snapshots_are_independent_objects():
    # mutating one snapshot must not affect others or inputs
    base = {"nested": {"k": "v"}}
    result = resolve_step_parameters(base, None, [{}, {}])
    result[0]["nested"]["k"] = "mutated"
    result[0]["new"] = 1
    assert result[1]["nested"]["k"] == "v"
    assert "new" not in result[1]
    assert base["nested"]["k"] == "v"


def test_step_count_and_order_stable():
    steps = [{}, {"a": 1}, {}, {"b": 2}, {}]
    result = resolve_step_parameters({"x": 0}, None, steps)
    assert len(result) == 5
    assert result[1] == {"x": 0, "a": 1}
    assert result[3] == {"x": 0, "a": 1, "b": 2}


def test_step_none_overrides_treated_as_empty():
    # a step may carry None instead of a dict
    result = resolve_step_parameters({"a": 1}, {"b": 2}, [None, {"c": 3}])
    assert result == [{"a": 1, "b": 2}, {"a": 1, "b": 2, "c": 3}]
