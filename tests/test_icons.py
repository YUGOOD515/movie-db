"""Ensure the shipped SVG subset covers real template and viewmodel references."""

import ast
from pathlib import Path
import xml.etree.ElementTree as ET

from jinja2 import Environment, nodes

from services import data_reset, settings
from viewmodels.home import COLLECTION_ICONS, GENRE_ICONS, build_collection_cards
from viewmodels.media_labels import chip


ROOT = Path(__file__).resolve().parents[1]
SPRITE = ROOT / "static/vendor/lucide/sprite.svg"


def test_template_and_viewmodel_icons_are_shipped():
    symbols = ET.parse(SPRITE).getroot()
    available = {symbol.attrib["id"] for symbol in symbols}
    assert len(available) == len(symbols), "Duplicate SVG symbol IDs"
    assert all(len(symbol) and symbol.attrib.get("viewBox") == "0 0 24 24" for symbol in symbols)

    references = set(GENRE_ICONS.values()) | set(COLLECTION_ICONS.values()) | {"tag", "library"}
    references.update(part["icon"] for part in data_reset.PARTS)
    deletion = ast.parse((ROOT / "viewmodels/deletion.py").read_text(encoding="utf-8"))
    for mapping in (node for node in ast.walk(deletion) if isinstance(node, ast.Dict)):
        references.update(value.value for key, value in zip(mapping.keys, mapping.values)
                          if isinstance(key, ast.Constant) and key.value == "icon"
                          and isinstance(value, ast.Constant))
    for section in settings.SECTIONS:
        references.add(section.icon)
        references.update(group.icon for group in section.groups)
    for key in ("duration", "channels", "resolution", "video_codec", "frame_rate",
                "audio_codec", "dynamic_range", "size", "container"):
        references.add(chip(key, "", "")["icon"])

    environment = Environment()
    for template in (ROOT / "templates").glob("*.html"):
        tree = environment.parse(template.read_text(encoding="utf-8"))
        for call in tree.find_all(nodes.Call):
            name = getattr(call.node, "name", "")
            index = {"icon": 0, "ui_icon": 0, "empty_state": 1, "image_fallback": 1}.get(name)
            if index is not None and len(call.args) > index:
                argument = call.args[index]
                constants = [argument] if isinstance(argument, nodes.Const) else argument.find_all(nodes.Const)
                references.update(node.value for node in constants if isinstance(node.value, str))
            for keyword in call.kwargs:
                if keyword.key == "icon" and isinstance(keyword.value, nodes.Const):
                    references.add(keyword.value.value)
        for macro in tree.find_all(nodes.Macro):
            for argument, default in zip(macro.args[-len(macro.defaults):], macro.defaults):
                if argument.name == "icon" and isinstance(default, nodes.Const):
                    references.add(default.value)
        for assignment in tree.find_all(nodes.Assign):
            if getattr(assignment.target, "name", "") == "nav_groups":
                references.update(link.items[2].value for link in assignment.node.find_all(nodes.Tuple)
                                  if len(link.items) == 4 and isinstance(link.items[2], nodes.Const))
    references.discard(None)
    references.discard("")
    assert not references - available, f"Missing Lucide icons: {sorted(references - available)}"


def test_collection_icons_keep_legacy_database_values():
    # URL construction is unrelated to mapping existing persisted icon names.
    from flask import Flask

    app = Flask(__name__)
    app.add_url_rule("/collection/<int:collection_id>", endpoint="collection_detail")
    names = ("film", "tv", "bookmark", "heart-fill", "collection", "unknown", None)
    rows = [{"collection_id": index, "icon_name": name} for index, name in enumerate(names)]
    with app.test_request_context():
        cards = build_collection_cards(rows)
    assert [card["icon"] for card in cards] == ["film", "tv", "bookmark", "heart", "library", "library", "library"]
    assert [row["icon_name"] for row in rows] == list(names)
