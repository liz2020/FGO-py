"""Interlude navigation and battle reuse without importing game globals."""

import ast
from itertools import chain, count, repeat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call
import re

import cv2
import numpy
import pytest
from pponnxcr import TextSystem

from tests.test_formation import load_definitions


ROOT = Path(__file__).parent.parent
DETECTORS = {
    "_locateInterludeLabel", "isInterludeList", "locateInterludeEntrance",
    "locateInterludeReturn", "locateInterludeStart", "isInterludeListEnd",
    "getInterludeEntries", "locateForcedSupport",
    "_loc", "_isListEnd",
    "locateServantUpdateCloseButton",
    "locateStorySkipConfirmButton",
}


@pytest.fixture(scope="module")
def ocr():
    system = TextSystem("ZHS")
    return SimpleNamespace(
        detect_and_ocr=system.detect_and_ocr,
        ocrArea=lambda im: [result.text for result in system.detect_and_ocr(im)],
        ocrText=lambda im: system.ocr_single_line(im)[0],
    )


@pytest.fixture
def detector(ocr):
    namespace = load_definitions("fgoDetect.py", DETECTORS, {
        "numpy": numpy, "cv2": cv2, "re": re,
    })
    cls = type("Detector", (), {name: namespace[name] for name in DETECTORS})
    d = cls()
    d.ocr = ocr
    label = cv2.imread(str(ROOT / "FGO-py" / "fgoImage" / "cn" / "interludelabel.png"), cv2.IMREAD_UNCHANGED)
    assert label is not None
    bar = cv2.imread(str(ROOT / "FGO-py" / "fgoImage" / "listbar.png"), cv2.IMREAD_UNCHANGED)
    d.tmpl = SimpleNamespace(
        INTERLUDELABEL=(label[..., :3], label[..., 3]),
        LISTBAR=(bar[..., :3], bar[..., 3]),
    )
    d.im = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    d._crop = lambda rect: d.im[rect[1]:rect[3], rect[0]:rect[2]]
    d._ocrText = lambda rect: d.ocr.ocrText(d._crop(rect))
    return d


def inject_fixture(d, name, rect=(620, 95, 1230, 590)):
    im = cv2.imread(str(ROOT / "tests" / "fixtures" / f"{name}.png"))
    assert im is not None
    d.im[rect[1]:rect[3], rect[0]:rect[2]] = im
    return d


@pytest.mark.parametrize("fixture,names,stages", [
    ("interlude-list", ["愤怒过后", "传说的猎人"], [False, False]),
    ("interlude-stage-rows", ["百感交集如楔子", "大江山，金时除鬼"], [False, True]),
    ("interlude-stage-map", ["大江山，金时除鬼"], [False]),
    ("interlude-locked", [], []),
])
def test_real_rows_exclude_locked_free_and_partial_entries(detector, fixture, names, stages):
    entries = inject_fixture(detector, fixture).getInterludeEntries()
    assert [entry["name"] for entry in entries] == names
    assert [entry["stage"] for entry in entries] == stages
    assert all(110 < entry["target"][1] < 590 for entry in entries)


def test_completed_interlude_is_not_selected(detector):
    inject_fixture(detector, "interlude-list")
    detector.ocr = SimpleNamespace(
        detect_and_ocr=lambda im: [],
        ocrArea=lambda im: ["已获取"],
        ocrText=lambda im: "愤怒过后",
    )
    assert detector.getInterludeEntries() == []


def test_real_story_only_confirmation_targets_task_start(detector):
    inject_fixture(detector, "interlude-no-battle", (350, 230, 990, 610))
    pos = detector.locateInterludeStart()
    assert pos is not None
    assert 750 < pos[0] < 930 and 530 < pos[1] < 590


def test_real_defeat_allowed_confirmation_targets_start(detector):
    inject_fixture(detector, "interlude-defeat-allowed", (350, 200, 990, 610))
    pos = detector.locateInterludeStart()
    assert pos is not None
    assert 800 < pos[0] < 890 and 540 < pos[1] < 590


@pytest.mark.parametrize("text,expected_labels", [
    ("该关卡将在战斗前进行编队。是否开始关卡？", {"开始"}),
    ("该任务没有战斗。是否开始任务？", {"任务开始"}),
    ("该关卡即便在战斗中败北，关卡剧情依然会推进。是否现在开始挑战关卡？", {"开始"}),
    ("是否现在开始挑战关卡？", None),
    ("该关卡即便在战斗中败北，关卡剧情依然会推进。", None),
    ("该任务没有战斗", None),
    ("是否开始任务？", None),
])
def test_start_dialog_requires_matching_confirmation(detector, text, expected_labels):
    detector.ocr = MagicMock()
    detector.ocr.ocrArea.return_value = [text]
    detector._locateInterludeLabel = MagicMock(return_value=(840, 560))
    result = detector.locateInterludeStart()
    if expected_labels is None:
        assert result is None
        detector._locateInterludeLabel.assert_not_called()
    else:
        assert result == (840, 560)
        detector._locateInterludeLabel.assert_called_once_with(expected_labels, (700, 510, 990, 610))


@pytest.mark.parametrize("fixture,expected", [
    ("interlude-list-bar", False), ("interlude-locked-bar", True),
])
def test_real_scrollbar_only_reports_exhaustion_at_bottom(detector, fixture, expected):
    inject_fixture(detector, fixture, (1239, 0, 1277, 720))
    assert detector.isInterludeListEnd() is expected


def test_real_single_npc_support_is_recognized(detector):
    inject_fixture(detector, "interlude-npc", (150, 160, 1140, 675))
    assert detector.locateForcedSupport() == (845, 203)


def test_mixed_npc_and_player_support_is_not_treated_as_locked(detector):
    detector.ocr = MagicMock()
    detector.ocr.ocrArea.side_effect = [["客将从者"], ["玩家助战"], []]
    assert detector.locateForcedSupport() is None


def screen(**overrides):
    d = MagicMock()
    for method in (
        "isApEmpty", "isBattleDefeated", "isTurnBegin", "isChooseFriend",
        "isBattleFormation", "isBattleFormationRestriction", "isAutoFormationPrompt",
        "isStorySkip", "isInterludeList", "isInterludeListEnd", "isQuestListBegin",
        "isMainInterface", "isTerminal",
        "isBattleFinished", "isSpecialDropSuspended", "isSpecialDropRainbowBox",
    ):
        getattr(d, method).return_value = False
    for method in ("locateInterludeStart", "locateInterludeReturn", "locateInterludeEntrance"):
        getattr(d, method).return_value = None
    d.getInterludeEntries.return_value = []
    for method, value in overrides.items():
        getattr(d, method).return_value = value
    return d


@pytest.fixture
def navigation():
    ticks = count(step=.1)
    return load_definitions("fgoKernel.py", {"findNextInterlude"}, {
        "time": SimpleNamespace(monotonic=lambda: next(ticks)),
        "XDetect": SimpleNamespace(region="CN"),
        "schedule": MagicMock(), "fgoDevice": MagicMock(), "Detect": MagicMock(),
        "_dismissServantUpdate": MagicMock(return_value=False),
    })


def feed_navigation(namespace, screens):
    namespace["Detect"].side_effect = chain(screens, repeat(screens[-1]))


def entry(stage=False, progress="0"):
    return {"name": "大江山，金时除鬼", "stage": stage, "target": (950, 170), "progress": progress}


def test_story_stage_opens_matching_interlude_and_waits_for_transitions(navigation):
    listing = screen(isInterludeList=True, isQuestListBegin=True, getInterludeEntries=[entry(stage=True)])
    stage = screen(locateInterludeReturn=(100, 130), getInterludeEntries=[entry()])
    support = screen(isChooseFriend=True)
    feed_navigation(navigation, [listing, listing, stage, stage, support])
    result = navigation["findNextInterlude"]()
    assert result == {"ready": True, "entry": entry()}
    assert navigation["fgoDevice"].device.touch.call_args_list == [
        call((950, 170), 1200), call((950, 170), 1200),
    ]


def test_story_only_confirmation_continues_to_existing_auto_battle_entry(navigation):
    listing = screen(isInterludeList=True, isQuestListBegin=True, getInterludeEntries=[entry()])
    confirmation = screen(locateInterludeStart=(840, 560), isInterludeList=True)
    story = screen(isStorySkip=True)
    feed_navigation(navigation, [listing, confirmation, story])
    assert navigation["findNextInterlude"]() == {"ready": True, "entry": entry()}
    assert navigation["fgoDevice"].device.touch.call_args_list == [
        call((950, 170), 1200), call((840, 560), 1200),
    ]


@pytest.mark.parametrize("skip_story", [True, False])
def test_auto_battle_confirms_quest_start_before_endpoints_and_generic_popups(skip_story):
    ticks = count(step=.1)
    ns = load_definitions("fgoKernel.py", {"skipStoryToBattle"}, {
        "time": SimpleNamespace(time=lambda: next(ticks)),
        "schedule": MagicMock(), "Detect": MagicMock(), "fgoDevice": MagicMock(),
        "logger": MagicMock(), "_advanceStory": MagicMock(),
        "_dismissServantUpdate": MagicMock(return_value=False),
    })
    confirmation = screen(locateInterludeStart=(840, 560), isMainInterface=True)
    formation_confirmation = screen(locateInterludeStart=(840, 560), isMainInterface=True)
    battle = screen(isTurnBegin=True)
    ns["Detect"].side_effect = [confirmation, formation_confirmation, battle]
    assert ns["skipStoryToBattle"](skip_story=skip_story) == "battle"
    assert ns["fgoDevice"].device.touch.call_args_list == [call((840, 560), 1200)] * 2
    ns["_advanceStory"].assert_not_called()


def test_existing_stage_returns_to_list_before_selecting_another_interlude(navigation):
    stage = screen(locateInterludeReturn=(100, 130))
    listing = screen(isInterludeList=True, isQuestListBegin=True, isInterludeListEnd=True)
    feed_navigation(navigation, [stage, stage, listing])
    assert not navigation["findNextInterlude"]()["ready"]
    navigation["fgoDevice"].device.touch.assert_called_once_with((100, 130), 1200)


def test_list_rewinds_before_declaring_exhaustion(navigation):
    bottom = screen(isInterludeList=True, isInterludeListEnd=True)
    top = screen(isInterludeList=True, isQuestListBegin=True, isInterludeListEnd=True)
    feed_navigation(navigation, [bottom, top])
    assert navigation["findNextInterlude"]() == {"ready": False, "entry": None}
    navigation["fgoDevice"].device.swipe.assert_called_once_with((1000, 180), (1000, 560))


def test_failed_selection_times_out_instead_of_reporting_exhaustion(navigation):
    listing = screen(isInterludeList=True, isQuestListBegin=True, getInterludeEntries=[entry()])
    feed_navigation(navigation, [listing])
    with pytest.raises(TimeoutError, match="known endpoint"):
        navigation["findNextInterlude"](timeout_s=1)
    navigation["fgoDevice"].device.touch.assert_called_once()


def test_unknown_page_times_out_without_blind_taps(navigation):
    feed_navigation(navigation, [screen()])
    with pytest.raises(TimeoutError):
        navigation["findNextInterlude"](timeout_s=1)
    navigation["fgoDevice"].device.touch.assert_not_called()
    navigation["fgoDevice"].device.press.assert_not_called()


def test_ap_recovery_is_a_blocker_without_consuming_resources(navigation):
    feed_navigation(navigation, [screen(isApEmpty=True)])
    with pytest.raises(RuntimeError, match="not enough AP"):
        navigation["findNextInterlude"]()
    navigation["fgoDevice"].device.touch.assert_not_called()


def test_navigation_honors_cancellation_before_clicking(navigation):
    class Cancelled(Exception):
        pass

    navigation["schedule"].checkStop.side_effect = Cancelled
    with pytest.raises(Cancelled):
        navigation["findNextInterlude"]()
    navigation["Detect"].assert_not_called()
    navigation["fgoDevice"].device.touch.assert_not_called()


@pytest.fixture
def worker():
    tree = ast.parse((ROOT / "FGO-py" / "fgoTaskQueue.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TaskWorker")
    execute = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_execute")
    namespace = {
        "Task": SimpleNamespace, "fgoKernel": MagicMock(),
        "_report_progress": MagicMock(), "_run_auto_battle_sync": MagicMock(),
        "_parse_idle_action_delay": float,
    }
    exec(compile(ast.Module(body=[execute], type_ignores=[]), "fgoTaskQueue.py", "exec"), namespace)
    return namespace


def test_queue_reuses_auto_battle_until_navigation_reports_exhaustion(worker):
    worker["fgoKernel"].findNextInterlude.side_effect = [
        {"ready": True, "entry": entry(progress="0")},
        {"ready": True, "entry": entry(progress="8")},
        {"ready": False, "entry": None},
    ]
    task = SimpleNamespace(type="auto_interlude", params={"support_name": "player"})
    assert worker["_execute"](None, task) == {"segments": 2, "exhausted": True}
    assert worker["_run_auto_battle_sync"].call_count == 2
    worker["_run_auto_battle_sync"].assert_called_with(
        auto_select_support=True, skip_story=True, timeout_s=180,
        idle_action_delay_s=.5, support_name="player", strict=True,
    )
    worker["fgoKernel"].Battle.assert_not_called()
    worker["_report_progress"].assert_called_with(2, 2, "done", "All available interludes complete")


def test_stalled_interlude_does_not_loop_forever(worker):
    worker["fgoKernel"].findNextInterlude.return_value = {"ready": True, "entry": entry()}
    with pytest.raises(RuntimeError, match="no visible progress"):
        worker["_execute"](None, SimpleNamespace(type="auto_interlude", params={}))
    assert worker["_run_auto_battle_sync"].call_count == 3
    assert not any(args[2] == "done" for args, _ in worker["_report_progress"].call_args_list)


@pytest.mark.parametrize("won,cleaned,error", [
    (False, True, RuntimeError), (True, False, TimeoutError), (None, False, TimeoutError),
])
def test_interlude_runner_surfaces_defeat_and_cleanup_failure(won, cleaned, error):
    ns = load_definitions("fgoTaskQueue.py", {"_run_auto_battle_sync"}, {
        "fgoKernel": MagicMock(), "logger": MagicMock(),
    })
    ns["fgoKernel"].skipStoryToBattle.return_value = "battle"
    ns["fgoKernel"].Battle.return_value.return_value = won
    ns["fgoKernel"].skipStoryAfterBattle.return_value = cleaned
    with pytest.raises(error):
        ns["_run_auto_battle_sync"](strict=True)
    assert ns["fgoKernel"].skipStoryToBattle.call_args.kwargs["stop_on_ap_empty"] is True


def test_interlude_runner_cleans_up_direct_story_completion_without_reporting_defeat():
    ns = load_definitions("fgoTaskQueue.py", {"_run_auto_battle_sync"}, {
        "fgoKernel": MagicMock(), "logger": MagicMock(),
    })
    ns["fgoKernel"].skipStoryToBattle.return_value = "battle"
    ns["fgoKernel"].Battle.return_value.return_value = None
    ns["fgoKernel"].skipStoryAfterBattle.return_value = True
    assert ns["_run_auto_battle_sync"](strict=True) == {"pre_battle": "battle"}
    ns["fgoKernel"].Battle.assert_called_once_with(idleActionDelay=.5, allowStoryCompletion=True)
    ns["fgoKernel"].skipStoryAfterBattle.assert_called_once_with(skip_story=True)


@pytest.fixture
def battle_loop():
    tree = ast.parse((ROOT / "FGO-py" / "fgoKernel.py").read_text(encoding="utf-8"))
    battle = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Battle")
    ns = {
        "Turn": MagicMock(), "Detect": MagicMock(), "logger": MagicMock(),
        "time": SimpleNamespace(time=lambda: 0), "schedule": MagicMock(),
        "fgoDevice": MagicMock(),
    }
    exec(compile(ast.Module(body=[battle], type_ignores=[]), "fgoKernel.py", "exec"), ns)

    def feed(frames):
        frames = iter(frames)
        def detect(*args):
            d = next(frames)
            ns["Detect"].cache = d
            return d
        ns["Detect"].side_effect = detect
    ns["feed"] = feed
    return ns


@pytest.mark.parametrize("completion", ["story", "skip_confirmation"])
def test_battle_hands_direct_story_transition_back_to_cleanup(battle_loop, completion):
    begin = screen(isTurnBegin=True)
    story = screen(isStorySkip=completion == "story")
    story.locateStorySkipConfirmButton.return_value = (827, 562) if completion == "skip_confirmation" else None
    battle_loop["feed"]([begin, story])
    battle = battle_loop["Battle"](allowStoryCompletion=True)
    assert battle() is None
    assert battle.turn == 1
    assert battle.result["material"] == {}
    battle_loop["schedule"].checkDefeated.assert_not_called()


def test_battle_hands_real_story_skip_dialog_back_to_cleanup(battle_loop, detector):
    inject_fixture(detector, "interlude-direct-story-skip", (350, 200, 990, 610))
    pos = detector.locateStorySkipConfirmButton()
    assert pos is not None
    begin = screen(isTurnBegin=True)
    story = screen(locateStorySkipConfirmButton=pos)
    battle_loop["feed"]([begin, story])
    assert battle_loop["Battle"](allowStoryCompletion=True)() is None


@pytest.mark.parametrize("allow_story_completion", [False, True])
def test_battle_does_not_treat_initial_story_as_a_completed_battle(battle_loop, allow_story_completion):
    initial = screen(isStorySkip=True)
    begin = screen(isTurnBegin=True)
    finish = screen(isBattleFinished=True, getMaterial={"test_drop": 1})
    battle_loop["feed"]([initial, begin, finish, finish])
    battle = battle_loop["Battle"](allowStoryCompletion=allow_story_completion)
    assert battle() is True
    assert battle.turn == 1
    assert battle.result["material"] == {"test_drop": 1}


def test_default_battle_still_waits_for_a_result_after_a_story_frame(battle_loop):
    begin = screen(isTurnBegin=True)
    story = screen(isStorySkip=True)
    finish = screen(isBattleFinished=True, getMaterial={})
    battle_loop["feed"]([begin, story, finish, finish])
    assert battle_loop["Battle"]()() is True
    story.isStorySkip.assert_not_called()


def test_explicit_defeat_still_takes_precedence_over_story_completion(battle_loop):
    begin = screen(isTurnBegin=True)
    defeat = screen(isBattleDefeated=True, isStorySkip=True)
    battle_loop["feed"]([begin, defeat])
    assert battle_loop["Battle"](allowStoryCompletion=True)() is False
    battle_loop["schedule"].checkDefeated.assert_called_once_with()
    defeat.isStorySkip.assert_not_called()


def test_mandatory_support_bypasses_named_support_search():
    ns = load_definitions("fgoKernel.py", {"_selectSupport"}, {
        "Detect": MagicMock(), "fgoDevice": MagicMock(), "logger": MagicMock(),
        "_selectSupportByName": MagicMock(), "_selectTopSupport": MagicMock(),
    })
    ns["Detect"].return_value.locateForcedSupport.return_value = (845, 203)
    ns["_selectSupport"]("a player not available in this quest")
    ns["fgoDevice"].device.touch.assert_called_once_with((845, 203), 700)
    ns["_selectSupportByName"].assert_not_called()
    ns["_selectTopSupport"].assert_not_called()


def test_pre_battle_ap_recovery_stops_before_story_actions():
    ns = load_definitions("fgoKernel.py", {"skipStoryToBattle"}, {
        "time": SimpleNamespace(time=lambda: 0), "logger": MagicMock(),
        "schedule": MagicMock(), "Detect": MagicMock(), "_advanceStory": MagicMock(),
    })
    ns["Detect"].return_value.isApEmpty.return_value = True
    with pytest.raises(RuntimeError, match="not enough AP"):
        ns["skipStoryToBattle"](stop_on_ap_empty=True)
    ns["_advanceStory"].assert_not_called()


def test_real_servant_update_targets_close_not_profile(detector):
    inject_fixture(detector, "interlude-servant-update", (220, 230, 930, 620))
    pos = detector.locateServantUpdateCloseButton()
    assert pos is not None
    assert 400 < pos[0] < 480 and 530 < pos[1] < 590


@pytest.mark.parametrize("heading,button,found", [
    ("弗拉德三世的从者资料更新！", "关闭", True),
    ("從者資料更新！", "關閉", True),
    ("从者资料更新！", "确认简介", False),
    ("是否跳过剧情？", "关闭", False),
    ("资料更新", "关闭", False),
])
def test_servant_update_requires_matching_message_and_close_label(detector, heading, button, found):
    detector.ocr = MagicMock()
    detector.ocr.ocrArea.return_value = [heading]
    detector.ocr.detect_and_ocr.return_value = [
        SimpleNamespace(text=button, box=numpy.array([[180, 30], [260, 30], [260, 70], [180, 70]])),
    ]
    assert (detector.locateServantUpdateCloseButton() is not None) is found
    if "从者资料更新" not in heading and "從者資料更新" not in heading:
        detector.ocr.detect_and_ocr.assert_not_called()


@pytest.mark.parametrize("flow", [
    "skipStoryToBattle", "skipStoryAfterBattle", "_advanceBattleResult", "findNextInterlude",
])
@pytest.mark.parametrize("skip_story", [False, True])
def test_consecutive_servant_updates_are_closed_before_endpoint_detection(flow, skip_story):
    ticks = count(step=.1)
    ns = load_definitions("fgoKernel.py", {flow, "_dismissServantUpdate"}, {
        "time": SimpleNamespace(time=lambda: next(ticks), monotonic=lambda: next(ticks)),
        "schedule": MagicMock(), "Detect": MagicMock(), "fgoDevice": MagicMock(),
        "logger": MagicMock(), "XDetect": SimpleNamespace(region="CN"),
        "_advanceStory": MagicMock(), "_dismissFriendRequest": MagicMock(return_value=False),
    })
    updates = [screen(isTurnBegin=True) for _ in range(3)]
    for update in updates:
        update.locateServantUpdateCloseButton.return_value = (440, 562)
    done = screen(isTurnBegin=True)
    done.locateServantUpdateCloseButton.return_value = None
    ns["Detect"].side_effect = updates + [done]
    kwargs = {"skip_story": skip_story} if flow in {"skipStoryToBattle", "skipStoryAfterBattle"} else {}
    ns[flow](**kwargs)
    assert ns["fgoDevice"].device.touch.call_args_list == [call((440, 562), 700)] * 3
    ns["fgoDevice"].device.perform.assert_not_called()
    ns["_advanceStory"].assert_not_called()
    for update in updates:
        update.isTurnBegin.assert_not_called()


def test_servant_update_wait_respects_navigation_timeout():
    ticks = count(step=.1)
    ns = load_definitions("fgoKernel.py", {"findNextInterlude", "_dismissServantUpdate"}, {
        "time": SimpleNamespace(monotonic=lambda: next(ticks)), "schedule": MagicMock(),
        "Detect": MagicMock(), "fgoDevice": MagicMock(), "logger": MagicMock(),
        "XDetect": SimpleNamespace(region="CN"),
    })
    ns["Detect"].return_value.locateServantUpdateCloseButton.return_value = (440, 562)
    with pytest.raises(TimeoutError):
        ns["findNextInterlude"](timeout_s=1)
    assert 0 < ns["fgoDevice"].device.touch.call_count <= 10
