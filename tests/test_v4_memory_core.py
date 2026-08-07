"""B-lane V4 independent memory package tests."""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

from cortex_v4.memory import (
    DictStore,
    FileResolver,
    Hydrator,
    Pointer,
    TaskState,
    append_event,
    build_handoff,
    create_controller,
    create_log,
    deep_freeze,
    fold_decision,
    fold_failure,
    fold_follow_up,
    format_pointer,
    is_pointer,
    make_pointer,
    parse_pointer,
    read_events,
    render_stable,
    render_task,
    update_task_state,
    validate_handoff,
    validate_task,
)
from cortex_v4.memory.task_render import REQUIRED_TASK_KEYS


def test_pointer_make_parse_format():
    p = make_pointer("evidence", "E42", label="cite")
    assert str(p) == "evidence:E42"
    assert format_pointer(p) == "evidence:E42"
    assert parse_pointer("evidence:E42 extra note") == Pointer(namespace="evidence", key="E42")
    assert is_pointer("decision:D1")
    assert not is_pointer("nocolon")
    with pytest.raises(ValueError):
        parse_pointer("nocolon")
    with pytest.raises(ValueError):
        make_pointer("", "k")


def test_event_log_append_only_deep_freeze():
    log = create_log()
    e = append_event(log, {"kind": "obs", "payload": {"n": [1, 2]}})
    assert e.seq == 1
    assert e.payload["n"] == (1, 2)
    with pytest.raises(TypeError):
        e.payload["n"] = 9  # type: ignore[index]
    frozen = deep_freeze({"a": [{"b": 1}]})
    assert frozen["a"][0]["b"] == 1
    assert len(read_events(log)) == 1
    with pytest.raises(ValueError):
        log.append("")


def test_task_state_never_empty_goals():
    s = TaskState.create(goals=["g1"], constraints=["c"])
    s2 = update_task_state(s, add_goal=["g2"])
    assert s2.goals == ("g1", "g2")
    with pytest.raises(ValueError):
        update_task_state(s, goals=[])
    with pytest.raises(ValueError):
        TaskState.create(goals=[])
    text = render_stable(s2)
    assert "g1" in text and "never compacted" in text


def test_protected_spans_survive_compact():
    state = TaskState.create(goals=["PROTECTED-MARKER-991"])
    ctrl = create_controller(max_chars=180, task_state=state)
    ctrl.add_text("PROTECTED-MARKER-991 keep", protected=True)
    for i in range(15):
        ctrl.add_text(("noise " * 25) + str(i))
    ctrl.compact()
    ok, missing = ctrl.preservation_ok()
    assert ok and not missing
    assert "PROTECTED-MARKER-991" in ctrl.render()
    assert len(ctrl.offload_list()) >= 1


def test_compact_fail_closed_when_only_protected():
    ctrl = create_controller(max_chars=40, task_state=TaskState.create(goals=["G"]))
    ctrl.add_text("long protected body that exceeds budget on its own !!!", protected=True)
    ctrl.compact()
    ok, missing = ctrl.preservation_ok()
    assert ok
    assert "long protected" in ctrl.render()


def test_folder_kinds():
    ctrl = create_controller(max_chars=2000, task_state=TaskState.create(goals=["g"]))
    d = fold_decision(ctrl, key="D1", summary="ship")
    assert str(d.pointer) == "decision:D1"
    f = fold_failure(ctrl, key="F1", summary="broke")
    assert str(f.pointer) == "failure:F1"
    u = fold_follow_up(ctrl, key="U1", summary="later")
    assert str(u.pointer).startswith("follow_up:")
    with pytest.raises(ValueError):
        fold_decision(ctrl, key="", summary="x")


def test_hydrator_dict_and_file_escape():
    p = make_pointer("evidence", "E1")
    store = DictStore()
    store.put(p, "body")
    h = Hydrator(resolvers=[store])
    assert h.hydrate(p).value == "body"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "ok.txt").write_text("hi", encoding="utf-8")
        fr = FileResolver(root)
        h2 = Hydrator(resolvers=[fr])
        r = h2.hydrate(make_pointer("file", "ok.txt"))
        assert r.value == "hi"
        assert not h2.is_stale(r)
        (root / "ok.txt").write_text("changed", encoding="utf-8")
        assert h2.is_stale(r)
        with pytest.raises(PermissionError):
            fr.resolve(make_pointer("file", "../escape.txt"))


def test_validate_task_refuses_prompt_only():
    with pytest.raises(ValueError, match="prose-substitution"):
        validate_task({"prompt": "do the thing"})
    with pytest.raises(ValueError):
        validate_task({"task_id": "t"})
    task = {
        "task_id": "t1",
        "goals": ["g"],
        "constraints": [],
        "acceptance_criteria": ["a"],
        "methodology_ids": ["M32"],
    }
    text = render_task(task)
    assert "t1" in text and "M32" in text
    assert set(REQUIRED_TASK_KEYS) <= set(task)


def test_handoff_artifact():
    ho = build_handoff("memory", "B", "v4", acceptance_criteria=["tests"])
    validate_handoff(ho)
    assert ho["task"] == "memory"
    with pytest.raises(ValueError):
        validate_handoff({"task": "t"})


def test_no_forbidden_imports_in_memory_package():
    pkg = Path(__file__).resolve().parents[1] / "cortex_v4" / "memory"
    bad_prefixes = ("cortex_core", "cortex_v3")
    errors = []
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix in bad_prefixes:
                        if alias.name == prefix or alias.name.startswith(prefix + "."):
                            errors.append(f"{py.name}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                for prefix in bad_prefixes:
                    if node.module == prefix or node.module.startswith(prefix + "."):
                        errors.append(f"{py.name}:{node.module}")
                if node.module.startswith("cortex_v4.") and not node.module.startswith(
                    "cortex_v4.memory"
                ):
                    errors.append(f"{py.name}:import-host:{node.module}")
    assert not errors, errors
