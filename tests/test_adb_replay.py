"""Covers adb_replay's selector matching (the core of why replay survives
minor UI changes instead of a raw coordinate replay) and its drift
detection: a recorded selector that no longer resolves, or a final judgment
that disagrees with the recording, must escalate to full re-authoring
rather than being silently trusted -- that's the whole point of not just
blindly replaying taps forever."""

import json
import time
from pathlib import Path

from runner import adb_client, adb_replay, ollama_client
from runner.adb_task import AdbInitTask, AdbWorkTask
from runner.ui_dump import parse_elements

BTN_XML = """<?xml version="1.0"?>
<hierarchy>
  <node text="" resource-id="" class="android.widget.FrameLayout" clickable="false" bounds="[0,0][1080,1920]">
    <node text="Go" resource-id="com.example:id/go_button" class="android.widget.Button" clickable="true" bounds="[0,0][200,100]"/>
  </node>
</hierarchy>"""

RESULT_XML = """<?xml version="1.0"?>
<hierarchy>
  <node text="" resource-id="" class="android.widget.FrameLayout" clickable="false" bounds="[0,0][1080,1920]">
    <node text="Done!" resource-id="com.example:id/status" class="android.widget.TextView" clickable="false" bounds="[0,0][400,100]"/>
  </node>
</hierarchy>"""

NO_BUTTON_XML = """<?xml version="1.0"?>
<hierarchy>
  <node text="" resource-id="" class="android.widget.FrameLayout" clickable="false" bounds="[0,0][1080,1920]">
    <node text="Other" resource-id="com.example:id/other" class="android.widget.TextView" clickable="false" bounds="[0,0][400,100]"/>
  </node>
</hierarchy>"""

ICON_ONLY_XML = """<?xml version="1.0"?>
<hierarchy>
  <node text="" resource-id="" class="android.widget.ImageButton" clickable="true" bounds="[0,0][100,100]"/>
</hierarchy>"""

TEXT_ONLY_XML = """<?xml version="1.0"?>
<hierarchy>
  <node text="" resource-id="" class="android.widget.FrameLayout" clickable="false" bounds="[0,0][1080,1920]">
    <node text="Done!" resource-id="" class="android.widget.TextView" clickable="false" bounds="[0,0][400,100]"/>
  </node>
</hierarchy>"""


def _elements(xml):
    return parse_elements(xml)


def _task(**kwargs) -> AdbWorkTask:
    defaults = dict(id="task-001", title="t", goal="g", acceptance_criteria=["c"])
    defaults.update(kwargs)
    return AdbWorkTask(**defaults)


def _init() -> AdbInitTask:
    return AdbInitTask(batch_id="b", default_package="com.example")


# --- resolve_selector / build_selector ----------------------------------

def test_resolve_selector_matches_by_resource_id():
    el = adb_replay.resolve_selector(_elements(BTN_XML), {"resource_id": "com.example:id/go_button"})
    assert el is not None and el.resource_id == "com.example:id/go_button"


def test_resolve_selector_missing_returns_none():
    assert adb_replay.resolve_selector(_elements(NO_BUTTON_XML), {"resource_id": "com.example:id/go_button"}) is None


def test_resolve_selector_falls_back_to_text_and_class_when_no_resource_id():
    el = adb_replay.resolve_selector(_elements(TEXT_ONLY_XML), {"text": "Done!", "class_name": "TextView"})
    assert el is not None and el.text == "Done!"


def test_build_selector_prefers_resource_id():
    el = _elements(BTN_XML)[0]
    assert adb_replay.build_selector(el) == {"resource_id": "com.example:id/go_button"}


def test_build_selector_falls_back_to_text_when_no_resource_id():
    el = _elements(TEXT_ONLY_XML)[0]
    assert adb_replay.build_selector(el) == {"text": "Done!", "class_name": "TextView"}


def test_build_selector_none_when_element_has_neither_resource_id_nor_text():
    el = _elements(ICON_ONLY_XML)[0]
    assert adb_replay.build_selector(el) is None


# --- capture_step ---------------------------------------------------------

def test_capture_step_tap_returns_selector_and_bare_action():
    elements = _elements(BTN_XML)
    captured = adb_replay.capture_step({"action": "tap", "index": elements[0].index}, elements)
    assert captured == {"selector": {"resource_id": "com.example:id/go_button"}, "action": {"action": "tap"}}


def test_capture_step_tap_returns_none_when_unrecordable():
    elements = _elements(ICON_ONLY_XML)
    assert adb_replay.capture_step({"action": "tap", "index": elements[0].index}, elements) is None


def test_capture_step_non_tap_actions_need_no_selector():
    assert adb_replay.capture_step({"action": "swipe", "direction": "up"}, []) == \
        {"selector": None, "action": {"action": "swipe", "direction": "up"}}
    assert adb_replay.capture_step({"action": "key", "name": "back"}, []) == \
        {"selector": None, "action": {"action": "key", "name": "back"}}
    assert adb_replay.capture_step({"action": "wait", "seconds": 1.5}, []) == \
        {"selector": None, "action": {"action": "wait", "seconds": 1.5}}
    assert adb_replay.capture_step({"action": "input_text", "text": "hi"}, []) == \
        {"selector": None, "action": {"action": "input_text", "text": "hi"}}


def test_capture_step_done_is_not_recorded_as_a_step():
    assert adb_replay.capture_step({"action": "done", "result": "pass"}, []) is None


# --- recording round trip / staleness -------------------------------------

def test_write_and_load_recording_round_trip(tmp_path: Path):
    task = _task()
    steps = [{"selector": {"resource_id": "x"}, "action": {"action": "tap"}}]
    adb_replay.write_recording(tmp_path, task, steps, "pass", "looked fine")

    loaded = adb_replay.load_recording(tmp_path, task)
    assert loaded is not None
    assert loaded["steps"] == steps
    assert loaded["expected_result"] == "pass"


def test_load_recording_returns_none_when_goal_text_changed(tmp_path: Path):
    adb_replay.write_recording(tmp_path, _task(), [], "pass", "")
    assert adb_replay.load_recording(tmp_path, _task(goal="a different goal")) is None


def test_load_recording_returns_none_when_missing(tmp_path: Path):
    assert adb_replay.load_recording(tmp_path, _task()) is None


# --- replay_adb_task: the escalation triggers ------------------------------

def _fake_dump_ui(xml_sequence):
    responses = iter(xml_sequence)
    return lambda serial=None: next(responses)


def _fake_judgment(result, reason=""):
    def fake(prompt, model, host, think=False, format=None, options=None):
        return ollama_client.GenerateResult(text=json.dumps({"result": result, "reason": reason}),
                                             prompt_eval_count=1, eval_count=1)
    return fake


def _patch_launch(monkeypatch):
    monkeypatch.setattr(adb_client, "force_stop", lambda package, serial=None: None)
    monkeypatch.setattr(adb_client, "start_app", lambda package, activity=None, serial=None: None)
    monkeypatch.setattr(time, "sleep", lambda s: None)


def test_replay_executes_recorded_steps_and_confirms_matching_verdict(monkeypatch):
    _patch_launch(monkeypatch)
    monkeypatch.setattr(adb_client, "tap", lambda x, y, serial=None: None)
    monkeypatch.setattr(adb_client, "dump_ui", _fake_dump_ui([BTN_XML, RESULT_XML]))
    monkeypatch.setattr(ollama_client, "generate", _fake_judgment("pass", "looks right"))

    recording = {
        "steps": [{"selector": {"resource_id": "com.example:id/go_button"}, "action": {"action": "tap"}}],
        "expected_result": "pass",
        "expected_reason": "it worked",
    }
    entry = adb_replay.replay_adb_task(_task(), _init(), recording, model="fake", host="http://x",
                                        serial=None, dry_run=False)

    assert entry["status"] == "pass"
    assert entry["replayed"] is True
    assert [s["action"]["action"] for s in entry["steps"]] == ["tap"]


def test_replay_escalates_when_a_recorded_selector_goes_missing(monkeypatch):
    _patch_launch(monkeypatch)
    monkeypatch.setattr(adb_client, "dump_ui", _fake_dump_ui([NO_BUTTON_XML]))

    recording = {
        "steps": [{"selector": {"resource_id": "com.example:id/go_button"}, "action": {"action": "tap"}}],
        "expected_result": "pass",
        "expected_reason": "",
    }
    entry = adb_replay.replay_adb_task(_task(), _init(), recording, model="fake", host="http://x",
                                        serial=None, dry_run=False)

    assert entry["status"] == "drift"
    assert entry["drift"] == "selector_missing"
    assert entry["replayed"] is False


def test_replay_escalates_when_final_judgment_disagrees_with_recording(monkeypatch):
    _patch_launch(monkeypatch)
    monkeypatch.setattr(adb_client, "dump_ui", _fake_dump_ui([RESULT_XML]))
    monkeypatch.setattr(ollama_client, "generate", _fake_judgment("fail", "broke"))

    recording = {"steps": [], "expected_result": "pass", "expected_reason": ""}
    entry = adb_replay.replay_adb_task(_task(), _init(), recording, model="fake", host="http://x",
                                        serial=None, dry_run=False)

    assert entry["status"] == "drift"
    assert entry["drift"] == "verdict_changed"
    assert entry["previous_result"] == "pass"
    assert entry["new_result"] == "fail"
