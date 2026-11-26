"""Command-card selection regressions without loading game/OCR globals."""

import ast
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)

    def select(colors, sealed=None):
        detector.getCardColor.return_value = colors
        detector.isCardSealed.return_value = sealed or [False] * 5
        detector.isHouguReady.return_value = [False] * 3
        detector.getFieldServantNp.return_value = 100
        detector.getCardResist.return_value = [0] * 8
        detector.getCardCriticalRate.return_value = [0] * 5
        detector.getCardServant.return_value = [101, 102, 103, 101, 102]
        state = SimpleNamespace(
            normalAttackOnly=False,
            servant=[(identity, 0, 0, 0, 0, (0, 0)) for identity in (101, 102, 103)],
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
