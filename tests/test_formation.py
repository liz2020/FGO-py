"""Pre-battle dialog regressions without loading the game/OCR model globals."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import re

import cv2
import numpy
import pytest


ROOT = Path(__file__).parent.parent / "FGO-py"


def load_definitions(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    nodes = tree.body
    if filename == "fgoDetect.py":
        nodes = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == "XDetectBase").body
    selected = [node for node in nodes if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), namespace)
    return namespace


@pytest.fixture
def detectors():
    return load_definitions("fgoDetect.py", {
        "isAutoFormationPrompt", "locateAutoFormationConfirmButton", "locateStorySkipConfirmButton",
    }, {"numpy": numpy, "cv2": cv2, "re": re})


@pytest.mark.parametrize("text", ["自动编成执行确认", "自动编队", "自動編成", "Auto Formation"])
def test_auto_formation_prompt_recognizes_labels(detectors, text):
    d = MagicMock()
    d.ocr.ocrArea.return_value = [text]
    assert detectors["isAutoFormationPrompt"](d)
    d._crop.assert_called_once_with((280, 35, 1000, 140))


def test_formation_screen_button_is_not_a_confirmation_prompt(detectors):
    d = MagicMock()
    d.ocr.ocrArea.return_value = ["队伍确认"]
    assert not detectors["isAutoFormationPrompt"](d)


@pytest.mark.parametrize("text", ["自动编成", "自动编队", "自動編成", "AUTO FORMATION"])
def test_auto_formation_targets_recognized_button_center(detectors, text):
    d = MagicMock()
    d.ocr.detect_and_ocr.return_value = [
        SimpleNamespace(text="不进行自动编成", box=numpy.array([[0, 0]] * 4)),
        SimpleNamespace(text=text, box=numpy.array([[148, 47], [270, 47], [270, 81], [148, 81]])),
    ]
    assert detectors["locateAutoFormationConfirmButton"](d) == (929, 604)


def test_auto_formation_does_not_click_negative_button(detectors):
    d = MagicMock()
    d.ocr.detect_and_ocr.return_value = [
        SimpleNamespace(text="不进行自动编成", box=numpy.array([[0, 0]] * 4)),
    ]
    assert detectors["locateAutoFormationConfirmButton"](d) is None


@pytest.mark.parametrize("text,expected", [
    ("是否进行自动编队？", None),
    ("是否跳过剧情？", (827, 562)),
    ("Skip this story?", (827, 562)),
    ("スキップしますか？", (827, 562)),
])
def test_blue_dialog_requires_story_skip_text(detectors, text, expected):
    d = MagicMock()
    # Both samples pass the existing blue-modal / light-button visual checks.
    d._crop.side_effect = [
        numpy.full((160, 560, 3), (220, 150, 100), dtype=numpy.uint8),
        numpy.full((90, 290, 3), (220, 190, 160), dtype=numpy.uint8),
    ]
    d.ocr.ocrArea.return_value = [text]
    assert detectors["locateStorySkipConfirmButton"](d) == expected


@pytest.fixture
def kernel():
    class ScriptStop(Exception):
        pass

    return load_definitions("fgoKernel.py", {"_useAutoFormation", "_advanceStory", "_startFormation"}, {
        "logger": MagicMock(), "fgoDevice": MagicMock(), "schedule": MagicMock(),
        "Detect": MagicMock(), "ScriptStop": ScriptStop,
        "_dismissPreBattlePopup": MagicMock(return_value=False),
        "_confirmBattleStart": MagicMock(),
    })


def test_open_auto_formation_prompt_is_confirmed_without_reopening(kernel):
    d = MagicMock()
    d.isAutoFormationPrompt.return_value = True
    d.locateAutoFormationConfirmButton.return_value = (929, 604)
    kernel["_useAutoFormation"](d)
    kernel["fgoDevice"].device.touch.assert_called_once_with((929, 604))
    kernel["fgoDevice"].device.perform.assert_not_called()
    kernel["schedule"].sleep.assert_called_once_with(1.5)


def test_auto_formation_opens_prompt_then_detects_confirmation(kernel):
    d, prompt = MagicMock(), MagicMock()
    d.isAutoFormationPrompt.return_value = False
    prompt.isAutoFormationPrompt.return_value = True
    prompt.locateAutoFormationConfirmButton.return_value = (929, 604)
    kernel["Detect"].return_value = prompt
    kernel["_useAutoFormation"](d)
    kernel["fgoDevice"].device.perform.assert_called_once_with("\xDE", (1000,))
    kernel["fgoDevice"].device.touch.assert_called_once_with((929, 604))


def test_missing_auto_formation_button_stops_without_blind_click(kernel):
    d = MagicMock()
    d.isAutoFormationPrompt.return_value = True
    d.locateAutoFormationConfirmButton.return_value = None
    with pytest.raises(kernel["ScriptStop"], match="confirmation button not found"):
        kernel["_useAutoFormation"](d)
    kernel["fgoDevice"].device.touch.assert_not_called()
    kernel["fgoDevice"].device.perform.assert_not_called()


def test_auto_formation_precedes_false_positive_story_skip(kernel):
    d = MagicMock()
    d.isAutoFormationPrompt.return_value = True
    d.locateAutoFormationConfirmButton.return_value = (929, 604)
    d.locateStorySkipConfirmButton.return_value = (827, 562)
    kernel["_advanceStory"](d)
    d.locateStorySkipConfirmButton.assert_not_called()
    kernel["fgoDevice"].device.touch.assert_called_once_with((929, 604))


def test_story_skip_still_uses_its_own_confirmation(kernel):
    d = MagicMock()
    d.isAutoFormationPrompt.return_value = False
    d.locateStorySkipConfirmButton.return_value = (827, 562)
    kernel["_advanceStory"](d)
    kernel["fgoDevice"].device.touch.assert_called_once_with((827, 562), 700)


def test_formation_confirms_auto_party_before_battle_start(kernel):
    d = MagicMock()
    d.isBattleFormationRestriction.return_value = False
    d.isAutoFormationPrompt.return_value = True
    d.locateAutoFormationConfirmButton.return_value = (929, 604)
    kernel["_startFormation"](d)
    kernel["fgoDevice"].device.touch.assert_called_once_with((929, 604))
    kernel["_confirmBattleStart"].assert_called_once_with()
