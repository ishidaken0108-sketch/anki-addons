"""Code-defined processing presets for Review Word Capture.

The Anki settings screen stores only the preset ID. When a new special behavior is
needed, add a new preset here and ship an updated add-on. Users can then select it
for any source/colon/destination rule without editing Python themselves.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable

from aqt import mw


PresetHandler = Callable[[Any, Any, set[str]], None]


def _first_image_tag(value: str) -> str:
    match = re.search(r"<img\b[^>]*>", value or "", flags=re.IGNORECASE | re.DOTALL)
    return match.group(0).strip() if match else ""


def _content_image1_note_image1(source_card: Any, new_note: Any, field_names: set[str]) -> None:
    """Mnemonics = first image from source Content + first image from source Note."""
    if "Mnemonics" not in field_names or not mw.col:
        return

    try:
        source_note = source_card.note()
        source_fields = set(mw.col.models.field_names(source_note.note_type()))
    except Exception:
        return

    pieces: list[str] = []
    for source_field in ("Content", "Note"):
        if source_field not in source_fields:
            continue
        image = _first_image_tag(source_note[source_field])
        if image:
            pieces.append(image)

    new_note["Mnemonics"] = "<br><br>".join(pieces)


def _strip_cloze_markup(value: str) -> str:
    """Turn {{c1::text}} / {{c1::text::hint}} into plain text."""
    text = value or ""
    pattern = re.compile(r"\{\{c\d+::(.*?)(?:::(.*?))?\}\}", flags=re.DOTALL)

    # A few passes also handle simple nested clozes without risking an endless loop.
    for _ in range(8):
        replaced = pattern.sub(lambda m: m.group(1), text)
        if replaced == text:
            break
        text = replaced
    return text


def _plain_one_line(value: str) -> str:
    """Convert Anki field HTML to a single readable line of plain text."""
    text = value or ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:div|p|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text, flags=re.DOTALL)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _first_sound_token(value: str) -> str:
    match = re.search(r"\[sound:[^\]\r\n]+\]", value or "", flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _content_body_without_cloze(value: str) -> str:
    """Source Content -> body text, excluding sound/media and cloze wrappers."""
    text = value or ""
    text = re.sub(r"\[sound:[^\]\r\n]+\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<img\b[^>]*>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = _strip_cloze_markup(text)
    return _plain_one_line(text)


def _japanese_translation_line(value: str) -> str:
    """Return the value of the first Mnemonics line starting with 日本語訳: / 日本語訳：."""
    text = value or ""
    # Preserve semantic line boundaries before dropping HTML tags.
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:div|p|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text, flags=re.DOTALL)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")

    for line in text.split("\n"):
        match = re.match(r"^\s*日本語訳\s*[:：]\s*(.*?)\s*$", line)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _audio_body_translation_3lines(source_card: Any, new_note: Any, field_names: set[str]) -> None:
    """Mnemonics = audio, de-clozed Content body, and 日本語訳 from source Mnemonics.

    Output is always three logical HTML lines:
      音声:[sound:...]
      本文:...
      日本語訳:...
    """
    if "Mnemonics" not in field_names or not mw.col:
        return

    try:
        source_note = source_card.note()
        source_fields = set(mw.col.models.field_names(source_note.note_type()))
    except Exception:
        return

    content = source_note["Content"] if "Content" in source_fields else ""
    mnemonics = source_note["Mnemonics"] if "Mnemonics" in source_fields else ""

    sound = _first_sound_token(content)
    body = _content_body_without_cloze(content)
    translation = _japanese_translation_line(mnemonics)

    # Body/translation are plain text; escape them before placing them into an HTML field.
    body_html = html.escape(body, quote=False)
    translation_html = html.escape(translation, quote=False)

    new_note["Mnemonics"] = (
        f"音声:{sound}<br>"
        f"本文:{body_html}<br>"
        f"日本語訳:{translation_html}"
    )


def _do_nothing(source_card: Any, new_note: Any, field_names: set[str]) -> None:
    return


# Add future presets here. Keep IDs stable once released because config stores them.
PRESETS: list[dict[str, Any]] = [
    {
        "id": "none",
        "name": "何もしない",
        "handler": _do_nothing,
    },
    {
        "id": "content_image1_note_image1",
        "name": "Content画像1枚 + Note画像1枚",
        "handler": _content_image1_note_image1,
    },
    {
        "id": "audio_body_translation_3lines",
        "name": "音声 + 本文(Cloze除去) + 日本語訳（3行）",
        "handler": _audio_body_translation_3lines,
    },
]


def preset_choices() -> list[tuple[str, str]]:
    return [(str(p["name"]), str(p["id"])) for p in PRESETS]


def preset_name(preset_id: str) -> str:
    for preset in PRESETS:
        if preset.get("id") == preset_id:
            return str(preset.get("name", preset_id))
    return preset_id


def has_preset(preset_id: str) -> bool:
    return any(p.get("id") == preset_id for p in PRESETS)


def apply_preset(preset_id: str, source_card: Any, new_note: Any, field_names: set[str]) -> None:
    for preset in PRESETS:
        if preset.get("id") == preset_id:
            handler = preset.get("handler")
            if callable(handler):
                handler(source_card, new_note, field_names)
            return
    raise KeyError(f"Unknown processing preset: {preset_id}")
