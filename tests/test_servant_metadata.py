"""Recognition data regressions without loading devices or OCR models."""

import ast
from pathlib import Path
import runpy

import cv2
import numpy
import pytest


ROOT = Path(__file__).parent.parent / "FGO-py"
NEW_SERVANTS = {
    435, 437, 438, 439, 440, 441, 442, 444, 445, 446,
    447, 448, 449, 450, 451, 452, 453, 454, 455, 456,
    457, 458, 459, 461, 462, 463, 464, 465, 466, 467,
    468, 469, 470, 471, 472, 473, 474, 475, 476, 477,
}


@pytest.fixture(scope="module")
def metadata():
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(ROOT)
        return runpy.run_path(str(ROOT / "fgoMetadata.py"))


@pytest.fixture(scope="module")
def detector_type(metadata):
    tree = ast.parse((ROOT / "fgoDetect.py").read_text(encoding="utf-8"))
    detector = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "XDetectBase")
    methods = {
        "_crop", "_loc", "_select", "getFieldServant",
        "getFieldServantClassRank", "getTeamServantClassRank",
    }
    namespace = {"cv2": cv2, "numpy": numpy, **metadata}
    class_templates = next(node for node in tree.body
                           if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == "CLASS"
                                   for target in node.targets))
    selected = [node for node in detector.body
                if isinstance(node, ast.FunctionDef) and node.name in methods]
    assert {node.name for node in selected} == methods
    exec(compile(ast.Module(body=[class_templates, *selected], type_ignores=[]),
                 "fgoDetect.py", "exec"), namespace)
    return type("Detector", (), {name: namespace[name] for name in methods}), namespace["CLASS"]


def test_catalog_includes_upstream_servants(metadata):
    data = metadata["servantData"]
    assert len(data) == 465
    assert max(data) == 477
    assert NEW_SERVANTS <= data.keys()
    assert set(metadata["servantImg"]) == set(data)


def test_metadata_preserves_battle_tuple_schema(metadata):
    for servant_id, record in metadata["servantData"].items():
        assert len(record) == 6, servant_id
        class_id, _, _, _, hougu, skills = record
        assert 0 <= class_id <= 14, servant_id
        assert len(hougu) == 2, servant_id
        assert hougu[0] in range(3) and hougu[1] in range(3), servant_id
        assert len(skills) == 3, servant_id
        assert all(len(skill) == 2 and skill[0] in range(10)
                   and skill[1] in range(9) for skill in skills), servant_id


def test_all_servant_images_have_complete_masked_tiles(metadata):
    for servant_id, (cards, portraits, _) in metadata["servantImg"].items():
        for name, tiles, height in (("card", cards, 47), ("portrait", portraits, 63)):
            image = cv2.imread(str(ROOT / "fgoImage" / "servant" / str(servant_id)
                                   / f"{name}.png"), cv2.IMREAD_UNCHANGED)
            assert image is not None, (servant_id, name)
            assert image.shape[2] == 4, (servant_id, name)
            assert image.shape[0] % height == 0, (servant_id, name)
            assert tiles, (servant_id, name)
            for color, mask in tiles:
                assert color.shape[:2] == mask.shape, (servant_id, name)
                assert color.shape[0] == height, (servant_id, name)
                assert numpy.any(mask), (servant_id, name)


@pytest.mark.parametrize("class_id", [1, 3, 7, 10, 13, 14])
def test_grand_class_recognition_in_battle_and_team(metadata, detector_type, class_id):
    detector, templates = detector_type
    index = metadata["classImg"][0].index([class_id, 3])
    d = detector()
    d.im = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    for pos in range(3):
        image, _ = templates[125][index]
        height, width = image.shape[:2]
        d.im[618:618 + height, 13 + 318 * pos:13 + 318 * pos + width] = image
        assert d.getFieldServantClassRank(pos) == [class_id, 3]
    for pos in range(6):
        image, _ = templates[100][index]
        height, width = image.shape[:2]
        left = 30 + 200 * pos + 15 * (pos > 2)
        d.im[133:133 + height, left:left + width] = image
    assert d.getTeamServantClassRank() == [[class_id, 3]] * 6


@pytest.mark.parametrize("servant_id", sorted(NEW_SERVANTS))
def test_new_servants_are_identified_by_existing_portrait_detector(
        metadata, detector_type, servant_id):
    detector, _ = detector_type
    d = detector()
    d.im = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    image, _ = metadata["servantImg"][servant_id][1][0]
    height, width = image.shape[:2]
    d.im[421:421 + height, 120:120 + width] = image
    d.getFieldServantClassRank = lambda pos: [metadata["servantData"][servant_id][0], 2]
    assert d.getFieldServant(0) == servant_id
