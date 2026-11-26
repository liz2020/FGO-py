"""Pre-battle dialog regressions without loading the game/OCR model globals."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call
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
        "isBattleFormationRestriction", "locateFormationRestrictionCloseButton",
        "locateFormationDecideButton",
        "locateFriendRequestEndButton",
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

    namespace = load_definitions("fgoKernel.py", {"_useAutoFormation", "_advanceStory", "_startFormation"}, {
        "logger": MagicMock(), "fgoDevice": MagicMock(), "schedule": MagicMock(),
        "Detect": MagicMock(), "ScriptStop": ScriptStop,
        "_dismissPreBattlePopup": MagicMock(return_value=False),
        "_confirmBattleStart": MagicMock(),
        "_resolveFormationRestriction": MagicMock(),
        "_dismissFriendRequest": MagicMock(return_value=False),
        "_clearFormationRestrictions": MagicMock(side_effect=lambda d: d),
    })
    namespace["Detect"].return_value.isAutoFormationPrompt.return_value = False
    namespace["Detect"].return_value.locateAutoFormationConfirmButton.return_value = None
    return namespace


def test_open_auto_formation_prompt_is_confirmed_without_reopening(kernel):
    d = MagicMock()
    d.isAutoFormationPrompt.return_value = True
    d.locateAutoFormationConfirmButton.return_value = (929, 604)
    kernel["Detect"].return_value.locateFormationDecideButton.return_value = (1168, 668)
    kernel["_useAutoFormation"](d)
    assert kernel["fgoDevice"].device.touch.call_args_list == [
        call((929, 604)),
        call((1168, 668), 1000),
    ]
    kernel["fgoDevice"].device.perform.assert_not_called()
    kernel["schedule"].sleep.assert_called_once_with(1.5)


def test_auto_formation_opens_prompt_then_detects_confirmation(kernel):
    d, prompt = MagicMock(), MagicMock()
    d.isAutoFormationPrompt.return_value = False
    d.locateAutoFormationConfirmButton.return_value = None
    prompt.isAutoFormationPrompt.return_value = True
    prompt.locateAutoFormationConfirmButton.return_value = (929, 604)
    decided = MagicMock()
    decided.isAutoFormationPrompt.return_value = False
    decided.locateAutoFormationConfirmButton.return_value = None
    decided.locateFormationDecideButton.return_value = (1168, 668)
    kernel["Detect"].side_effect = [prompt, decided]
    kernel["_useAutoFormation"](d)
    kernel["fgoDevice"].device.perform.assert_called_once_with("\xDE", (1000,))
    assert kernel["fgoDevice"].device.touch.call_args_list == [
        call((929, 604)),
        call((1168, 668), 1000),
    ]


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
    d.isBattleFormationRestriction.return_value = False
    d.isAutoFormationPrompt.return_value = True
    d.locateAutoFormationConfirmButton.return_value = (929, 604)
    d.locateStorySkipConfirmButton.return_value = (827, 562)
    kernel["Detect"].return_value.locateFormationDecideButton.return_value = (1168, 668)
    kernel["_advanceStory"](d)
    d.locateStorySkipConfirmButton.assert_not_called()
    assert kernel["fgoDevice"].device.touch.call_args_list == [
        call((929, 604)),
        call((1168, 668), 1000),
    ]


def test_story_skip_still_uses_its_own_confirmation(kernel):
    d = MagicMock()
    d.isBattleFormationRestriction.return_value = False
    d.isAutoFormationPrompt.return_value = False
    d.locateStorySkipConfirmButton.return_value = (827, 562)
    kernel["_advanceStory"](d)
    kernel["fgoDevice"].device.touch.assert_called_once_with((827, 562), 700)


def test_formation_confirms_auto_party_before_battle_start(kernel):
    d = MagicMock()
    d.isBattleFormationRestriction.return_value = False
    d.isAutoFormationPrompt.return_value = True
    d.locateAutoFormationConfirmButton.return_value = (929, 604)
    kernel["Detect"].return_value.locateFormationDecideButton.return_value = (1168, 668)
    actions = MagicMock()
    actions.attach_mock(kernel["fgoDevice"].device.touch, "touch")
    actions.attach_mock(kernel["_confirmBattleStart"], "start")
    kernel["_startFormation"](d)
    assert actions.mock_calls == [
        call.touch((929, 604)),
        call.touch((1168, 668), 1000),
        call.start(),
    ]


@pytest.mark.parametrize("text", [
    "编成条件", "编 成 条 件", "編成条件", "編成條件",
    "编制限制", "編成制限", "Formation Conditions", "Formation Restrictions",
])
def test_formation_condition_dialog_is_a_restriction(detectors, text):
    d = MagicMock()
    d.ocr.ocrArea.return_value = [text, "要进行本关卡"]
    assert detectors["isBattleFormationRestriction"](d)


@pytest.mark.parametrize("text", ["队伍确认", "自动编成执行确认", "SKIP STORY"])
def test_unrelated_dialog_is_not_a_formation_restriction(detectors, text):
    d = MagicMock()
    d.ocr.ocrArea.return_value = [text]
    assert not detectors["isBattleFormationRestriction"](d)


def test_formation_restriction_close_uses_recognized_label(detectors):
    d = MagicMock()
    d.ocr.detect_and_ocr.return_value = [
        SimpleNamespace(text="关闭", box=numpy.array([[151, 84], [227, 84], [227, 116], [151, 116]])),
    ]
    assert detectors["locateFormationRestrictionCloseButton"](d) == (639, 600)


@pytest.mark.parametrize("text", ["决定", "決定", "CONFIRM"])
def test_formation_decide_targets_recognized_label(detectors, text):
    d = MagicMock()
    d.ocr.detect_and_ocr.return_value = [
        SimpleNamespace(text=text, box=numpy.array([[68, 28], [148, 28], [148, 68], [68, 68]])),
    ]
    assert detectors["locateFormationDecideButton"](d) == (1168, 668)


def test_friend_request_end_requires_request_heading(detectors):
    d = MagicMock()
    d.ocr.ocrArea.return_value = ["是否向提供支援的玩家", "发送好友申请？"]
    d.ocr.detect_and_ocr.return_value = [
        SimpleNamespace(text="结束", box=numpy.array([[200, 40], [260, 40], [260, 80], [200, 80]])),
        SimpleNamespace(text="申请好友", box=numpy.array([[0, 0]] * 4)),
    ]
    assert detectors["locateFriendRequestEndButton"](d) == (330, 620)
    d.ocr.ocrArea.return_value = ["战斗结束"]
    assert detectors["locateFriendRequestEndButton"](d) is None


@pytest.mark.parametrize("skip_story", [True, False])
def test_post_battle_ends_friend_request_even_without_story_skip(skip_story):
    namespace = load_definitions("fgoKernel.py", {
        "skipStoryAfterBattle", "_dismissFriendRequest",
    }, {
        "time": MagicMock(), "schedule": MagicMock(), "Detect": MagicMock(),
        "fgoDevice": MagicMock(), "logger": MagicMock(), "_advanceStory": MagicMock(),
        "_dismissServantUpdate": MagicMock(return_value=False),
    })
    namespace["time"].time.return_value = 0
    request, done = MagicMock(), MagicMock()
    for method in ("isMainInterface", "isInterludeList", "isBattleContinue", "isBattleFormation",
                   "isChooseFriend", "isTurnBegin"):
        getattr(request, method).return_value = False
    request.locateFriendRequestEndButton.return_value = (330, 620)
    done.isMainInterface.return_value = True
    namespace["Detect"].side_effect = [request, done]
    assert namespace["skipStoryAfterBattle"](skip_story=skip_story)
    namespace["fgoDevice"].device.touch.assert_called_once_with((330, 620), 700)
    namespace["fgoDevice"].device.perform.assert_not_called()
    namespace["_advanceStory"].assert_not_called()


def test_friend_request_retains_legacy_template_fallback():
    namespace = load_definitions("fgoKernel.py", {"_dismissFriendRequest"}, {
        "fgoDevice": MagicMock(), "logger": MagicMock(),
    })
    d = MagicMock()
    d.locateFriendRequestEndButton.return_value = None
    d.isAddFriend.return_value = True
    assert namespace["_dismissFriendRequest"](d)
    namespace["fgoDevice"].device.perform.assert_called_once_with("X", (300,))


def test_missing_decide_button_does_not_start_battle(kernel):
    d = MagicMock()
    d.isBattleFormationRestriction.return_value = False
    d.isAutoFormationPrompt.return_value = True
    d.locateAutoFormationConfirmButton.return_value = (929, 604)
    kernel["Detect"].return_value.locateFormationDecideButton.return_value = None
    with pytest.raises(kernel["ScriptStop"], match="decide button not found"):
        kernel["_startFormation"](d)
    kernel["fgoDevice"].device.touch.assert_called_once_with((929, 604))
    kernel["_confirmBattleStart"].assert_not_called()


@pytest.mark.parametrize("entry", ["_startFormation", "_advanceStory"])
def test_restriction_is_resolved_before_generic_popup_dismissal(kernel, entry):
    d = MagicMock()
    d.isBattleFormationRestriction.return_value = True
    kernel["_dismissPreBattlePopup"].return_value = True
    kernel[entry](d)
    kernel["_resolveFormationRestriction"].assert_called_once_with()
    kernel["_confirmBattleStart"].assert_called_once_with()
    kernel["_dismissPreBattlePopup"].assert_not_called()


@pytest.mark.parametrize("skip_story", [True, False])
def test_restriction_without_visible_battle_button_routes_to_formation(skip_story):
    namespace = load_definitions("fgoKernel.py", {"skipStoryToBattle"}, {
        "time": MagicMock(), "schedule": MagicMock(), "Detect": MagicMock(),
        "_startFormation": MagicMock(),
        "_dismissServantUpdate": MagicMock(return_value=False),
    })
    namespace["time"].time.return_value = 0
    restriction, battle = MagicMock(), MagicMock()
    restriction.locateInterludeStart.return_value = None
    battle.locateInterludeStart.return_value = None
    restriction.isTurnBegin.return_value = False
    restriction.isBattleFinished.return_value = False
    restriction.isBattleDefeated.return_value = False
    restriction.isChooseFriend.return_value = False
    restriction.isMainInterface.return_value = False
    restriction.isInterludeList.return_value = False
    restriction.isBattleContinue.return_value = False
    restriction.isBattleFormation.return_value = False
    restriction.isBattleFormationRestriction.return_value = True
    battle.isTurnBegin.return_value = True
    namespace["Detect"].side_effect = [restriction, battle]
    assert namespace["skipStoryToBattle"](skip_story=skip_story) == "battle"
    namespace["_startFormation"].assert_called_once_with(restriction)


@pytest.mark.parametrize("close", [(639, 600), None])
def test_formation_repair_requires_close_button_before_auto_party(close):
    class ScriptStop(Exception):
        pass

    namespace = load_definitions("fgoKernel.py", {"_fixFormationRestriction", "_clearFormationRestrictions"}, {
        "logger": MagicMock(), "Detect": MagicMock(), "fgoDevice": MagicMock(),
        "_useAutoFormation": MagicMock(), "ScriptStop": ScriptStop, "schedule": MagicMock(),
    })
    restriction, cleared = MagicMock(), MagicMock()
    restriction.isBattleFormationRestriction.return_value = True
    restriction.locateFormationRestrictionCloseButton.return_value = close
    cleared.isBattleFormationRestriction.return_value = False
    namespace["Detect"].side_effect = [restriction, cleared]
    if close:
        namespace["_fixFormationRestriction"]()
        namespace["fgoDevice"].device.touch.assert_called_once_with(close, 700)
        namespace["_useAutoFormation"].assert_called_once_with(cleared)
    else:
        with pytest.raises(ScriptStop, match="close button not found"):
            namespace["_fixFormationRestriction"]()
        namespace["fgoDevice"].device.touch.assert_not_called()
        namespace["_useAutoFormation"].assert_not_called()


@pytest.fixture
def stacked_formation():
    class ScriptStop(Exception):
        pass

    return load_definitions("fgoKernel.py", {
        "_clearFormationRestrictions", "_useAutoFormation", "_fixFormationRestriction",
    }, {
        "logger": MagicMock(), "Detect": MagicMock(), "fgoDevice": MagicMock(),
        "schedule": MagicMock(), "ScriptStop": ScriptStop,
    })


def formation_screen(*, restriction=False, prompt=False, close=(639, 600)):
    d = MagicMock()
    d.isBattleFormationRestriction.return_value = restriction
    d.locateFormationRestrictionCloseButton.return_value = close
    d.isAutoFormationPrompt.return_value = prompt
    d.locateAutoFormationConfirmButton.return_value = (929, 604) if prompt else None
    d.locateFormationDecideButton.return_value = (1168, 668)
    return d


def test_stacked_conditions_are_all_closed_before_auto_formation(stacked_formation):
    first, second = formation_screen(restriction=True), formation_screen(restriction=True)
    prompt, decided = formation_screen(prompt=True), formation_screen()
    stacked_formation["Detect"].side_effect = [first, second, prompt, decided]
    stacked_formation["_fixFormationRestriction"]()
    assert stacked_formation["fgoDevice"].device.touch.call_args_list == [
        call((639, 600), 700), call((639, 600), 700),
        call((929, 604)), call((1168, 668), 1000),
    ]
    stacked_formation["fgoDevice"].device.perform.assert_not_called()


def test_conditions_after_auto_formation_are_closed_before_decide(stacked_formation):
    prompt = formation_screen(prompt=True)
    condition, decided = formation_screen(restriction=True), formation_screen()
    stacked_formation["Detect"].side_effect = [condition, decided]
    stacked_formation["_useAutoFormation"](prompt)
    assert stacked_formation["fgoDevice"].device.touch.call_args_list == [
        call((929, 604)), call((639, 600), 700), call((1168, 668), 1000),
    ]


def test_auto_formation_waits_for_confirm_button_without_reopening(stacked_formation):
    opening = formation_screen(prompt=True)
    opening.locateAutoFormationConfirmButton.return_value = None
    prompt, decided = formation_screen(prompt=True), formation_screen()
    stacked_formation["Detect"].side_effect = [opening, prompt, decided]
    stacked_formation["_useAutoFormation"](formation_screen())
    stacked_formation["fgoDevice"].device.perform.assert_called_once_with("\xDE", (1000,))
    assert stacked_formation["fgoDevice"].device.touch.call_args_list == [
        call((929, 604)), call((1168, 668), 1000),
    ]


def test_auto_formation_accepts_button_while_heading_ocr_is_missing(stacked_formation):
    prompt, decided = formation_screen(), formation_screen()
    prompt.locateAutoFormationConfirmButton.return_value = (929, 604)
    stacked_formation["Detect"].return_value = decided
    stacked_formation["_useAutoFormation"](prompt)
    stacked_formation["fgoDevice"].device.perform.assert_not_called()
    assert stacked_formation["fgoDevice"].device.touch.call_args_list == [
        call((929, 604)), call((1168, 668), 1000),
    ]


def test_auto_formation_waits_until_prompt_disappears_before_decide(stacked_formation):
    prompt, decided = formation_screen(prompt=True), formation_screen()
    stacked_formation["Detect"].side_effect = [prompt, decided]
    stacked_formation["_useAutoFormation"](prompt)
    prompt.locateFormationDecideButton.assert_not_called()
    assert stacked_formation["fgoDevice"].device.touch.call_args_list == [
        call((929, 604)), call((1168, 668), 1000),
    ]


def test_missing_heading_does_not_allow_saving_behind_visible_confirm_dialog(stacked_formation):
    prompt, decided = formation_screen(), formation_screen()
    prompt.locateAutoFormationConfirmButton.return_value = (929, 604)
    stacked_formation["Detect"].side_effect = [prompt, decided]
    stacked_formation["_useAutoFormation"](prompt)
    prompt.locateFormationDecideButton.assert_not_called()
    assert stacked_formation["fgoDevice"].device.touch.call_args_list == [
        call((929, 604)), call((1168, 668), 1000),
    ]


def test_confirm_button_wait_is_bounded_without_blind_taps(stacked_formation):
    opening = formation_screen(prompt=True)
    opening.locateAutoFormationConfirmButton.return_value = None
    stacked_formation["Detect"].return_value = opening
    with pytest.raises(stacked_formation["ScriptStop"], match="confirmation button not found"):
        stacked_formation["_useAutoFormation"](opening)
    assert stacked_formation["Detect"].call_count == 9
    stacked_formation["fgoDevice"].device.touch.assert_not_called()
    stacked_formation["fgoDevice"].device.perform.assert_not_called()


def test_decide_wait_is_bounded_without_confirming_visible_background_button(stacked_formation):
    prompt = formation_screen(prompt=True)
    stacked_formation["Detect"].return_value = prompt
    with pytest.raises(stacked_formation["ScriptStop"], match="decide button not found"):
        stacked_formation["_useAutoFormation"](prompt)
    assert stacked_formation["Detect"].call_count == 10
    prompt.locateFormationDecideButton.assert_not_called()
    stacked_formation["fgoDevice"].device.touch.assert_called_once_with((929, 604))


def test_auto_formation_opens_only_after_last_condition(stacked_formation):
    condition, party, prompt, decided = (
        formation_screen(restriction=True), formation_screen(),
        formation_screen(prompt=True), formation_screen(),
    )
    stacked_formation["Detect"].side_effect = [party, prompt, decided]
    actions = MagicMock()
    actions.attach_mock(stacked_formation["fgoDevice"].device.touch, "touch")
    actions.attach_mock(stacked_formation["fgoDevice"].device.perform, "perform")
    stacked_formation["_useAutoFormation"](condition)
    assert actions.mock_calls == [
        call.touch((639, 600), 700), call.perform("\xDE", (1000,)),
        call.touch((929, 604)), call.touch((1168, 668), 1000),
    ]


def test_repeated_conditions_are_bounded_without_starting_formation(stacked_formation):
    condition = formation_screen(restriction=True)
    stacked_formation["Detect"].return_value = condition
    with pytest.raises(stacked_formation["ScriptStop"], match="10 dismissals"):
        stacked_formation["_useAutoFormation"](condition)
    assert stacked_formation["fgoDevice"].device.touch.call_args_list == [call((639, 600), 700)] * 10
    stacked_formation["fgoDevice"].device.perform.assert_not_called()


def test_cancellation_between_condition_dialogs_stops_before_next_click(stacked_formation):
    condition = formation_screen(restriction=True)
    stacked_formation["Detect"].return_value = condition
    stacked_formation["schedule"].checkStop.side_effect = [
        None, stacked_formation["ScriptStop"]("cancelled"),
    ]
    with pytest.raises(stacked_formation["ScriptStop"], match="cancelled"):
        stacked_formation["_useAutoFormation"](condition)
    stacked_formation["fgoDevice"].device.touch.assert_called_once_with((639, 600), 700)
