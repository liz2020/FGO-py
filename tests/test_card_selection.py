"""Command-card selection regressions without loading game/OCR globals."""

import ast
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import numpy
import pytest


@pytest.fixture
def select_cards():
    source = Path(__file__).parent.parent / "FGO-py" / "fgoKernel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    turn = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Turn")
    method = next(node for node in turn.body if isinstance(node, ast.FunctionDef) and node.name == "selectCard")
    method.decorator_list = []
    detector = MagicMock()
    namespace = {
        "Detect": MagicMock(return_value=detector, cache=detector),
        "numpy": numpy,
        "permutations": permutations,
        "fgoDevice": MagicMock(),
        "logger": MagicMock(),
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)

    def select(colors, sealed=None, identities=(101, 102, 103)):
        detector.getCardColor.return_value = colors
        detector.isCardSealed.return_value = sealed or [False] * 5
        detector.isHouguReady.return_value = [False] * 3
        detector.getFieldServantNp.return_value = 100
        detector.getCardResist.return_value = [0] * 8
        detector.getCardCriticalRate.return_value = [0] * 5
        detector.getCardServant.return_value = [101, 102, 103, 101, 102]
        state = SimpleNamespace(
            normalAttackOnly=False,
            servant=[(identity, 0, 0, 0, 0, (0, 0)) for identity in identities],
            stage=1, stageTotal=3, stageTurn=2, target=0,
            enemy=[100000, 0, 0, 0, 0, 0],
        )
        return namespace["selectCard"](state)[:3]

    return select


def test_first_card_bonus_uses_selected_card_not_leftmost_hand_card(select_cards):
    assert select_cards([0, 2, 0, 2, 0]) == "214"


def test_quick_chain_bonus_can_outweigh_mixed_buster_cards(select_cards):
    assert select_cards([1, 1, 1, 2, 2]) == "123"


def test_sealed_quick_card_does_not_complete_a_chain(select_cards):
    assert "3" not in select_cards([1, 1, 1, 2, 2], [False, False, True, False, False])


def test_unknown_servants_report_recognition_error_instead_of_empty_min(select_cards):
    with pytest.raises(RuntimeError, match="No identified servant candidates"):
        select_cards([0, 1, 2, 0, 1], identities=(0, 0, 0))


def servant_frame(identities, hp=(1000, 1000, 1000)):
    frame = MagicMock()
    frame.getStage.return_value = 1
    frame.getStageTotal.return_value = 3
    frame.isServantDead.return_value = False
    frame.getFieldServant.side_effect = lambda pos: identities[pos]
    frame.getFieldServantHp.side_effect = lambda pos: hp[pos]
    frame.getEnemyHp.return_value = 100000
    return frame


@pytest.fixture
def battle_turn():
    source = Path(__file__).parent.parent / "FGO-py" / "fgoKernel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    turn = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Turn")
    names = {"__init__", "__call__", "_retryUnknownServants"}
    methods = [node for node in turn.body if isinstance(node, ast.FunctionDef) and node.name in names]

    def make(frames):
        frames = iter(frames)
        detect = MagicMock()

        def capture(*args):
            frame = next(frames)
            if isinstance(frame, Exception):
                raise frame
            detect.cache = frame
            return frame

        detect.side_effect = capture
        record = (2, 0, 1, 0, (0, 0), ((0, 0), (0, 0), (0, 0)))
        namespace = {
            "Detect": detect, "logger": MagicMock(), "fgoDevice": MagicMock(),
            "servantData": dict.fromkeys((101, 102, 103), record),
        }
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)
        cls = type("Turn", (), {name: namespace[name] for name in names})
        state = cls()
        state.dispatchSkill = MagicMock()
        state.selectCard = MagicMock(return_value="12345")
        return state, namespace

    return make


@pytest.mark.parametrize("initial_hp", [(0, 0, 0), (1000, 1000, 1000)])
def test_unloaded_servant_is_reidentified_before_attack(battle_turn, initial_hp):
    initial = servant_frame((0, 0, 0), initial_hp)
    loaded = servant_frame((0, 0, 101), (0, 0, 1000))
    state, namespace = battle_turn([initial, loaded])
    state(1)
    assert [record[0] for record in state.servant] == [0, 0, 101]
    assert namespace["Detect"].call_args_list == [call(.2), call(.5)]
    loaded.setupServantDead.assert_called_once()
    state.selectCard.assert_called_once()


def test_previously_unknown_slot_is_rechecked_without_portrait_change(battle_turn):
    initial = servant_frame((101, 102, 0))
    loaded = servant_frame((101, 102, 103))
    state, namespace = battle_turn([initial, loaded])
    record = namespace["servantData"][101]
    state.servant = [(identity,) + record for identity in (101, 102, 0)]
    state.countDown[0] = [[5, 5, 5], [6, 6, 6], [7, 7, 7]]
    state(2)
    initial.getFieldServant.assert_called_once_with(2)
    loaded.getFieldServant.assert_called_once_with(2)
    assert [record[0] for record in state.servant] == [101, 102, 103]
    assert state.countDown[0] == [[5, 5, 5], [6, 6, 6], [0, 0, 0]]


def test_empty_slots_do_not_delay_recognized_living_servant(battle_turn):
    state, namespace = battle_turn([servant_frame((0, 0, 101), (0, 0, 1000))])
    state(1)
    namespace["Detect"].assert_called_once_with(.2)
    namespace["logger"].warning.assert_not_called()
    state.selectCard.assert_called_once()


def test_recognition_retries_are_bounded_and_do_not_tap_attack_on_failure(battle_turn):
    frames = [servant_frame((0, 0, 0)) for _ in range(3)]
    state, namespace = battle_turn(frames)
    with pytest.raises(RuntimeError, match="after 3 recognition attempts"):
        state(1)
    assert namespace["Detect"].call_args_list == [call(.2), call(.5), call(.5)]
    assert all(frame.getFieldServant.call_count == 3 for frame in frames)
    namespace["logger"].error.assert_called_once()
    namespace["fgoDevice"].device.perform.assert_not_called()
    state.dispatchSkill.assert_not_called()
    state.selectCard.assert_not_called()


def test_partial_recognition_keeps_existing_battle_behavior_after_retries(battle_turn):
    frames = [servant_frame((101, 0, 0), (1000, 1000, 0)) for _ in range(3)]
    state, namespace = battle_turn(frames)
    state(1)
    assert [record[0] for record in state.servant] == [101, 0, 0]
    assert namespace["Detect"].call_args_list == [call(.2), call(.5), call(.5)]
    assert "still unresolved" in namespace["logger"].warning.call_args.args[0]
    state.selectCard.assert_called_once()


def test_cancellation_during_retry_is_not_swallowed(battle_turn):
    class ScriptStop(Exception):
        pass

    state, namespace = battle_turn([servant_frame((0, 0, 0)), ScriptStop("cancelled")])
    with pytest.raises(ScriptStop, match="cancelled"):
        state(1)
    namespace["fgoDevice"].device.perform.assert_not_called()
    state.selectCard.assert_not_called()
