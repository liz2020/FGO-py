"""Quest catalog — parses i18n files and cross-references with fgoReishift.place."""
import xml.etree.ElementTree as ET
from pathlib import Path
from fgoReishift import place
from fgoLogging import getLogger

logger = getLogger('QuestCatalog')

# Part groupings: maps part index to human key
PARTS = {
    0: "daily",    # 迦勒底之门
    1: "part1",    # Part 1
    2: "part1.5",  # Epic of Remnant
    3: "part2",    # Cosmos in the Lostbelt
    4: "part2.5",  # Ordeal Call prelude chapters
    5: "ordeal",   # Ordeal Call / 平面之月
}


def _parse_i18n(lang: str = "zh") -> dict[str, str]:
    """Parse quest names from fgoI18n.{lang}.ts, returns {source: translation}."""
    path = Path(__file__).parent / f"fgoI18n.{lang}.ts"
    if not path.exists():
        logger.warning(f"i18n file not found: {path}")
        return {}

    tree = ET.parse(path)
    root = tree.getroot()
    names = {}
    for context in root.findall("context"):
        ctx_name = context.findtext("name", "")
        if ctx_name != "quest":
            continue
        for msg in context.findall("message"):
            source = msg.findtext("source", "")
            translation = msg.findtext("translation", "")
            if source and translation:
                names[source] = translation
    return names


def _get_navigable_quests() -> set[tuple]:
    """Get the set of quest tuples that have navigation entries in fgoReishift.place."""
    result = set()
    for key in place:
        if isinstance(key, tuple):
            result.add(key)
    return result


def build_catalog(lang: str = "zh") -> dict:
    """Build structured quest catalog for the web UI.

    Returns:
        {
            "parts": [
                {
                    "id": "1",
                    "name": "Part 1",
                    "chapters": [
                        {
                            "id": "1-0",
                            "name": "冬木",
                            "quests": [
                                {"id": "1-0-0", "name": "未确认坐标X-A", "tuple": [1,0,0]},
                                ...
                            ]
                        }
                    ]
                }
            ]
        }
    """
    names = _parse_i18n(lang)
    navigable = _get_navigable_quests()

    # Group navigable quests by (part, chapter)
    chapters_map: dict[tuple[int, int], list[tuple]] = {}
    for q in navigable:
        if len(q) >= 3:
            key = (q[0], q[1])
            chapters_map.setdefault(key, []).append(q)

    # Group chapters by part
    parts_map: dict[int, list[tuple[int, int]]] = {}
    for part_idx, chap_idx in sorted(chapters_map.keys()):
        parts_map.setdefault(part_idx, []).append((part_idx, chap_idx))

    # Build result
    parts = []
    for part_idx in sorted(parts_map.keys()):
        chapter_keys = parts_map[part_idx]
        part_name = names.get(f"{part_idx}", f"Part {part_idx}")

        chapters = []
        for p, c in sorted(chapter_keys):
            chapter_key = f"{p}-{c}"
            chapter_name = names.get(chapter_key, f"Chapter {c}")

            quests = []
            quest_tuples = sorted(chapters_map[(p, c)], key=lambda q: q[2])
            for qt in quest_tuples:
                quest_key = "-".join(str(x) for x in qt) + "-0"
                quest_name = names.get(quest_key, f"Quest {qt[-1]}")
                quests.append({
                    "id": "-".join(str(x) for x in qt),
                    "name": quest_name,
                    "tuple": list(qt),
                })

            chapters.append({
                "id": chapter_key,
                "name": chapter_name,
                "quests": quests,
            })

        parts.append({
            "id": str(part_idx),
            "name": part_name,
            "chapters": chapters,
        })

    return {"parts": parts}


# Cached catalog
_catalog_cache: dict[str, dict] = {}


def get_catalog(lang: str = "zh") -> dict:
    """Get quest catalog (cached)."""
    if lang not in _catalog_cache:
        _catalog_cache[lang] = build_catalog(lang)
    return _catalog_cache[lang]
