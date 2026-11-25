"""Free-quest catalog and navigation regressions without loading devices or OCR."""

import ast
import logging
import os
import re
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import cv2
import numpy
import pytest


ROOT = Path(__file__).parent.parent / "FGO-py"


@pytest.fixture(scope="module")
def map_not_found_error():
    tree = ast.parse((ROOT / "fgoDetect.py").read_text(encoding="utf-8"))
    error = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MapNotFoundError")
    namespace = {}
    exec(compile(ast.Module(body=[error], type_ignores=[]), "fgoDetect.py", "exec"), namespace)
    return namespace["MapNotFoundError"]


@pytest.fixture(scope="module")
def quest_tuples():
    tree = ast.parse((ROOT / "fgoMetadata.py").read_text(encoding="utf-8"))
    selected = [
        node for node in tree.body
        if (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in {"missionQuest", "quest"}
                    for t in node.targets))
        or (isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name) and node.target.id == "quest")
    ]
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "fgoMetadata.py", "exec"),
         namespace)
    return namespace["quest"]


@pytest.fixture
def catalog(monkeypatch, quest_tuples):
    monkeypatch.setitem(sys.modules, "fgoMetadata", SimpleNamespace(quest=quest_tuples))
    monkeypatch.setitem(sys.modules, "fgoLogging", SimpleNamespace(getLogger=logging.getLogger))
    return runpy.run_path(str(ROOT / "fgoQuestCatalog.py"))["build_catalog"]


@pytest.fixture
def ordeal_metadata(monkeypatch):
    tree = ast.parse((ROOT / "fgoMetadata.py").read_text(encoding="utf-8"))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in {"ordealMapNodes", "mapLandmarkImg"}
                for t in node.targets)
    ]
    namespace = {"cv2": cv2, "numpy": numpy, "os": os}
    monkeypatch.chdir(ROOT)
    exec(compile(ast.Module(body=selected, type_ignores=[]), "fgoMetadata.py", "exec"),
         namespace)
    return namespace


@pytest.fixture
def navigation(ordeal_metadata, map_not_found_error):
    tree = ast.parse((ROOT / "fgoReishift.py").read_text(encoding="utf-8"))
    namespace = {
        "cv2": cv2, "numpy": numpy, "getLogger": logging.getLogger,
        "fgoDevice": SimpleNamespace(device=Mock()),
        "schedule": SimpleNamespace(sleep=Mock()),
        "Detect": Mock(),
        "ordealMapNodes": ordeal_metadata["ordealMapNodes"],
        "MapNotFoundError": map_not_found_error,
    }
    selected = [node for node in tree.body
                if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "fgoReishift.py", "exec"),
         namespace)
    return namespace


@pytest.fixture
def detector(ordeal_metadata, map_not_found_error):
    tree = ast.parse((ROOT / "fgoDetect.py").read_text(encoding="utf-8"))
    base = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "XDetectBase")
    names = {"_crop", "_loc", "findMapCamera"}
    selected = [n for n in base.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {
        "cv2": cv2, "numpy": numpy,
        "ordealMapNodes": ordeal_metadata["ordealMapNodes"],
        "mapLandmarkImg": ordeal_metadata["mapLandmarkImg"],
        "MapNotFoundError": map_not_found_error,
        "mapImg": {(5, c): cv2.imread(str(ROOT / "fgoImage" / "map" / "atlas" / f"5-{c}.png"))
                   for c in (2, 3, 4)},
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "fgoDetect.py", "exec"),
         namespace)
    return type("Detector", (), {name: namespace[name] for name in names})()


@pytest.fixture
def selection(monkeypatch, catalog):
    parse_i18n = runpy.run_path(str(ROOT / "fgoQuestCatalog.py"))["_parse_i18n"]
    monkeypatch.setitem(sys.modules, "fgoQuestCatalog", SimpleNamespace(_parse_i18n=parse_i18n))
    tree = ast.parse((ROOT / "fgoKernel.py").read_text(encoding="utf-8"))
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_selectQuest")
    namespace = {
        "fgoDevice": SimpleNamespace(device=Mock()), "Detect": Mock(),
        "XDetect": SimpleNamespace(region="CN"),
        "ScriptStop": RuntimeError, "logger": logging.getLogger("selection"),
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "fgoKernel.py", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("language", ["zh", "ja"])
def test_all_three_chapters_have_complete_named_free_quests(catalog, navigation, language):
    part = next(p for p in catalog(language)["parts"] if p["id"] == "5")
    chapters = {c["id"]: c for c in part["chapters"]}
    for chapter, count in ((2, 12), (3, 15), (4, 13)):
        entries = chapters[f"5-{chapter}"]["quests"]
        assert len(entries) == count
        assert len({e["id"] for e in entries}) == count
        assert len({e["name"] for e in entries}) == count
        for entry in entries:
            assert len(entry["tuple"]) == 4
            assert not entry["name"].startswith("Quest ")
            node = navigation["place"][tuple(entry["tuple"][:3])]
            assert isinstance(node, navigation["Map"])
            assert numpy.isfinite(node.coord).all()


def test_multi_quest_school_and_shinjuku_keep_distinct_subquest_indices(quest_tuples, catalog):
    school = [q for q in quest_tuples if q[:3] == (5, 2, 2)]
    shinjuku = [q for q in quest_tuples if q[:3] == (5, 2, 5)]
    assert [q[-1] for q in school] == [0, 1, 2, 3]
    assert [q[-1] for q in shinjuku] == [0, 1]
    chapter = next(c for p in catalog()["parts"] for c in p["chapters"] if c["id"] == "5-2")
    names = [e["name"] for e in chapter["quests"] if e["tuple"][:3] == [5, 2, 2]]
    assert names == [
        "学校 / 宁静的放学后", "学校 / 调查七大不可思议",
        "学校 / 突然叫出", "学校 / 留校特训",
    ]


@pytest.mark.parametrize("quest", [(5, 2, 2, 3), (5, 3, 14, 0), (5, 4, 12, 0)])
def test_new_chapter_route_starts_at_terminal_banner_not_world_group(navigation, quest):
    visits = []
    navigation["place"] = {quest[:i]: lambda key=quest[:i]: visits.append(key)
                           for i in range(1, 4)}
    navigation["reishift"](quest)
    assert visits == [quest[:2], quest[:3]]


def test_existing_paper_moon_navigation_is_preserved(navigation):
    quest = (5, 1, 0, 0)
    visits = []
    navigation["place"] = {quest[:i]: lambda key=quest[:i]: visits.append(key)
                           for i in range(1, 4)}
    navigation["reishift"](quest)
    assert visits == [(5,), (5, 1), (5, 1, 0)]


def test_chapter_scrolls_then_clicks_matching_banner_again_on_world(navigation):
    locations = iter([None, None, (730, 200), (730, 160)])
    frames = iter([numpy.zeros((10, 10, 3), numpy.uint8), numpy.full((10, 10, 3), 10, numpy.uint8)])
    detect = SimpleNamespace(
        isMainInterface=lambda: True, isQuestListBegin=lambda: True,
        findChapter=lambda name: next(locations),
        _crop=lambda rect: next(frames),
    )
    navigation["Detect"].return_value = detect
    navigation["OrdealChapter"]((5, 2))()
    device = navigation["fgoDevice"].device
    assert device.swipe.call_count == 2
    assert [call.args for call in device.touch.call_args_list] == [
        ((730, 200),), ((730, 160),),
    ]


def test_new_chapter_search_reverses_when_the_top_of_the_list_is_reached(navigation):
    locations = iter([None, None, None, (730, 200), (730, 160)])
    navigation["Detect"].return_value = SimpleNamespace(
        isMainInterface=lambda: True,
        findChapter=lambda name: next(locations),
        _crop=lambda rect: numpy.zeros((10, 10, 3), numpy.uint8),
    )
    navigation["OrdealChapter"]((5, 2))()
    assert [call.args[:2] for call in navigation["fgoDevice"].device.swipe.call_args_list] == [
        ((1000, 200), (1000, 600)), ((1000, 600), (1000, 200)),
    ]


def test_missing_world_banner_fails_without_blind_second_tap(navigation):
    locations = iter([(730, 200), None])
    navigation["Detect"].return_value = SimpleNamespace(
        isMainInterface=lambda: True, isQuestListBegin=lambda: True,
        findChapter=lambda name: next(locations),
    )
    with pytest.raises(RuntimeError, match="not found on white Earth"):
        navigation["OrdealChapter"]((5, 2))()
    navigation["fgoDevice"].device.touch.assert_called_once_with((730, 200))


def test_live_map_crop_localizes_school_and_market_at_clickable_positions(detector, navigation):
    crop = cv2.imread(str(Path(__file__).parent / "fixtures" / "ordeal-ii-map.png"))
    detector.im = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    detector.im[200:520, 200:1080] = crop
    camera = detector.findMapCamera((5, 2))
    assert numpy.linalg.norm(camera - (910, 827)) < 6
    market = (640, 360) + navigation["place"][(5, 2, 0)].coord - camera
    school = (640, 360) + navigation["place"][(5, 2, 2)].coord - camera
    assert 550 < market[0] < 630 and 375 < market[1] < 440
    assert 780 < school[0] < 850 and 445 < school[1] < 510


@pytest.mark.parametrize("chapter", [2, 3, 4])
def test_unrecognized_new_map_fails_before_navigating(detector, chapter):
    detector.im = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    with pytest.raises(RuntimeError, match="Cannot locate chapter"):
        detector.findMapCamera((5, chapter))


@pytest.mark.parametrize("chapter, filename, expected", [
    (3, "ordeal-iii-map.png", (1282.5, 2122)),
    (3, "ordeal-iii-marked-map.png", (1282.5, 2122)),
    (4, "ordeal-iv-lower-map.png", (1295.75, 3324.5)),
    (4, "ordeal-iv-mid-map.png", (1284.75, 2355.25)),
    (4, "ordeal-iv-horizontal-map.png", (1240.5, 3830)),
])
def test_live_scenery_overlays_use_unique_landmarks(detector, chapter, filename, expected):
    detector.im = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    crop = cv2.imread(str(Path(__file__).parent / "fixtures" / filename))
    detector.im[100:100 + crop.shape[0], 200:1080] = crop
    camera = detector.findMapCamera((5, chapter))
    assert numpy.linalg.norm(camera - expected) < 4


def test_conflicting_landmark_matches_reject_the_map(detector, monkeypatch):
    detector.im = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    namespace = detector.findMapCamera.__func__.__globals__
    image = next(iter(namespace["mapLandmarkImg"].values()))
    detector.im[200:200 + image[0].shape[0], 400:400 + image[0].shape[1]] = image[0]
    monkeypatch.setitem(namespace, "mapLandmarkImg", {(5, 3, 0): image, (5, 3, 1): image})
    detector._loc = lambda *args: (.01, 0, (200, 100), (0, 0))
    with pytest.raises(RuntimeError, match="Ambiguous free-quest landmarks"):
        detector.findMapCamera((5, 3))


@pytest.mark.parametrize("filename, top, chapter", [
    ("ordeal-ii-map.png", 200, 3), ("ordeal-ii-map.png", 200, 4),
    ("ordeal-iii-map.png", 100, 2), ("ordeal-iii-map.png", 100, 4),
    ("ordeal-iv-lower-map.png", 100, 2), ("ordeal-iv-lower-map.png", 100, 3),
])
def test_other_chapter_maps_do_not_produce_a_tappable_camera(detector, filename, top, chapter):
    detector.im = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    crop = cv2.imread(str(Path(__file__).parent / "fixtures" / filename))
    detector.im[top:top + crop.shape[0], 200:1080] = crop
    with pytest.raises(RuntimeError):
        detector.findMapCamera((5, chapter))


@pytest.mark.parametrize("vector", [(0, 1500), (0, -1500), (1500, 0), (-1500, 0)])
def test_new_map_drag_moves_toward_target_without_fast_fling(navigation, vector):
    navigation["OrdealMap"]((5, 4, 11), (0, 0))._pan(numpy.asarray(vector))
    start, end, duration = navigation["fgoDevice"].device.swipe.call_args.args
    displacement = start - end
    assert numpy.dot(displacement, vector) > 0
    assert abs(displacement[0]) <= 480
    assert abs(displacement[1]) <= 240
    assert duration >= 1000
    assert cv2.pointPolygonTest(navigation["Map"].poly, tuple(start), False) > 0
    assert cv2.pointPolygonTest(navigation["Map"].poly, tuple(end), False) > 0
    navigation["schedule"].sleep.assert_called_once()


def test_unknown_initial_map_does_not_start_dragging(navigation, map_not_found_error):
    navigation["Detect"].return_value.findMapCamera.side_effect = map_not_found_error("wrong map")
    node = navigation["OrdealMap"]((5, 4, 11), (1280, 3800))
    with pytest.raises(map_not_found_error):
        node._camera()
    navigation["fgoDevice"].device.swipe.assert_not_called()
    navigation["fgoDevice"].device.touch.assert_not_called()


def test_brief_map_occlusion_requires_relocalization_before_any_tap(navigation, map_not_found_error):
    before = numpy.asarray((1280, 2000))
    after = numpy.asarray((1280, 2400))
    navigation["Detect"].return_value.findMapCamera.side_effect = [
        before, map_not_found_error("scenery overlay"), after,
    ]
    node = navigation["OrdealMap"]((5, 4, 11), (1280, 3800))
    assert numpy.array_equal(node._camera(), before)
    assert numpy.array_equal(node._camera(), after)
    navigation["fgoDevice"].device.swipe.assert_called_once()
    navigation["fgoDevice"].device.touch.assert_not_called()


def test_persistent_occlusion_stops_after_two_bounded_recovery_drags(navigation, map_not_found_error):
    node = navigation["OrdealMap"]((5, 4, 11), (1280, 3800))
    node._lastCamera = numpy.asarray((1280, 2000))
    navigation["Detect"].return_value.findMapCamera.side_effect = map_not_found_error("unrecognized")
    with pytest.raises(map_not_found_error):
        node._camera()
    assert navigation["fgoDevice"].device.swipe.call_count == 2
    navigation["fgoDevice"].device.touch.assert_not_called()


def test_unreachable_node_has_a_total_drag_limit(navigation):
    node = navigation["OrdealMap"]((5, 4, 11), (1280, 3800))
    for _ in range(node.MAX_PANS):
        node._pan(numpy.asarray((0, 1500)))
    with pytest.raises(RuntimeError, match="Cannot reach free-quest node"):
        node._pan(numpy.asarray((0, 1500)))
    assert navigation["fgoDevice"].device.swipe.call_count == node.MAX_PANS


def test_unavailable_free_quest_fails_instead_of_waiting_on_an_unopened_list(navigation):
    detect = navigation["Detect"].return_value
    detect.isMainInterface.return_value = True
    detect.findMapCamera.return_value = numpy.asarray((1280, 3800))
    detect.isQuestFreeContains.return_value = False
    node = navigation["OrdealMap"]((5, 4, 11), (1280, 3800))
    with pytest.raises(RuntimeError, match="check its unlock requirements"):
        node()
    assert navigation["fgoDevice"].device.touch.call_count == 1
    assert detect.isQuestFreeContains.call_count == 5


def test_successful_new_map_keeps_names_visible(navigation):
    detect = navigation["Detect"].return_value
    detect.isMainInterface.return_value = True
    detect.findMapCamera.return_value = numpy.asarray((1280, 3800))
    detect.isQuestFreeContains.return_value = True
    navigation["OrdealMap"]((5, 4, 11), (1280, 3800))()
    navigation["fgoDevice"].device.press.assert_not_called()
    assert navigation["fgoDevice"].device.touch.call_count == 1


@pytest.mark.parametrize("index,key", [(0, "8"), (1, "4"), (2, "L")])
def test_first_three_quests_keep_existing_selection(selection, index, key):
    selection["_selectQuest"](index)
    selection["fgoDevice"].device.press.assert_called_once_with(key)
    selection["Detect"].assert_not_called()


@pytest.mark.parametrize("region,title", [("CN", "留校特训"), ("JP", "いのこり特訓")])
def test_fourth_school_quest_scrolls_then_selects_exact_title(selection, region, title):
    selection["XDetect"].region = region
    d = selection["Detect"].return_value
    d.locateQuestName.side_effect = [None, (830, 480)]
    selection["_selectQuest"](3, (5, 2, 2, 3))
    assert d.locateQuestName.call_args_list == [call(title), call(title)]
    selection["fgoDevice"].device.swipe.assert_called_once_with((1000, 550), (1000, 300), 800)
    selection["fgoDevice"].device.touch.assert_called_once_with((830, 480))
    selection["fgoDevice"].device.press.assert_not_called()


def test_fourth_quest_can_already_be_visible(selection):
    selection["Detect"].return_value.locateQuestName.return_value = (830, 480)
    selection["_selectQuest"](3, (5, 2, 2, 3))
    selection["fgoDevice"].device.swipe.assert_not_called()
    selection["fgoDevice"].device.touch.assert_called_once_with((830, 480))


def test_missing_fourth_quest_stops_without_selecting_another(selection):
    selection["Detect"].return_value.locateQuestName.return_value = None
    with pytest.raises(RuntimeError, match="unlock requirements"):
        selection["_selectQuest"](3, (5, 2, 2, 3))
    assert selection["fgoDevice"].device.swipe.call_count == 6
    assert selection["Detect"].return_value.locateQuestName.call_count == 7
    selection["fgoDevice"].device.touch.assert_not_called()
    selection["fgoDevice"].device.press.assert_not_called()


def test_fourth_quest_is_checked_after_final_allowed_scroll(selection):
    selection["Detect"].return_value.locateQuestName.side_effect = [None] * 6 + [(830, 480)]
    selection["_selectQuest"](3, (5, 2, 2, 3))
    assert selection["fgoDevice"].device.swipe.call_count == 6
    selection["fgoDevice"].device.touch.assert_called_once_with((830, 480))


def test_fourth_quest_does_not_scroll_an_unopened_list(selection):
    selection["Detect"].return_value.isQuestFreeContains.return_value = False
    with pytest.raises(RuntimeError, match="list is not visible"):
        selection["_selectQuest"](3, (5, 2, 2, 3))
    selection["fgoDevice"].device.swipe.assert_not_called()
    selection["fgoDevice"].device.touch.assert_not_called()


@pytest.mark.parametrize("quest", [None, (5, 2, 2), (5, 2, 2, 2)])
def test_fourth_quest_requires_matching_tuple(selection, quest):
    with pytest.raises(ValueError, match="matching quest tuple"):
        selection["_selectQuest"](3, quest)
    selection["Detect"].assert_not_called()


def test_quest_title_locator_rejects_other_titles_and_offsets_ocr_box():
    tree = ast.parse((ROOT / "fgoDetect.py").read_text(encoding="utf-8"))
    base = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "XDetectBase")
    method = next(n for n in base.body if isinstance(n, ast.FunctionDef) and n.name == "locateQuestName")
    namespace = {"numpy": numpy, "re": re}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "fgoDetect.py", "exec"), namespace)
    d = SimpleNamespace(_crop=Mock(), ocr=Mock())
    box = numpy.asarray(((20, 380), (100, 380), (100, 400), (20, 400)))
    d.ocr.detect_and_ocr.return_value = [SimpleNamespace(text="突然叫出", box=box)]
    assert namespace["locateQuestName"](d, "留校特训") is None
    d.ocr.detect_and_ocr.return_value.append(SimpleNamespace(text="留校 特训", box=box))
    assert namespace["locateQuestName"](d, "留校特训") == (820, 480)
    d._crop.assert_called_with((760, 90, 1060, 600))


def test_main_keeps_fourth_quest_identity_when_returning_to_list(selection):
    tree = ast.parse((ROOT / "fgoKernel.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Main")
    method = next(n for n in main.body if isinstance(n, ast.FunctionDef) and n.name == "__call__")
    namespace = {
        **selection, "_selectQuest": Mock(), "serialize": lambda lock: lambda func: func,
        "mutex": None, "schedule": Mock(), "_dismissPreBattlePopup": Mock(return_value=False),
        "_confirmBattleStart": Mock(), "_advanceBattleResult": Mock(),
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), "fgoKernel.py", "exec"), namespace)
    d = namespace["Detect"].return_value
    namespace["Detect"].cache = d
    d.isBattleContinue.return_value = False
    d.isSkillCastFailed.return_value = False
    d.isApEmpty.return_value = False
    d.isBattleFormationRestriction.return_value = False
    battle = Mock(return_value=True)
    battle.result = {"turn": 1, "time": 1, "material": {}}
    proc = SimpleNamespace(
        prepare=Mock(), battleClass=lambda: battle,
        battleCount=0, battleTurn=0, battleTime=0, material={}, teamIndex=0, autoFormation=False,
        chooseFriend=Mock(side_effect=[None, RuntimeError("stop before another battle")]),
    )
    with pytest.raises(RuntimeError, match="stop before"):
        namespace["__call__"](proc, 3, quest=(5, 2, 2, 3))
    assert namespace["_selectQuest"].call_args_list == [
        call(3, (5, 2, 2, 3)), call(3, (5, 2, 2, 3)),
    ]
