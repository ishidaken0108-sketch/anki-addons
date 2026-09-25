from __future__ import annotations

import html
import urllib.parse
from typing import Any

from aqt import mw
from aqt.editor import Editor, EditorMode, pics

CONFIG_DEFAULT_WIDTH = 500


def _target_width_px() -> int:
    config: dict[str, Any] = mw.addonManager.getConfig(__name__) or {}
    width = config.get("target_width_px", CONFIG_DEFAULT_WIDTH)
    try:
        width = int(width)
    except Exception:
        width = CONFIG_DEFAULT_WIDTH
    return max(1, width)


_original_fname_to_link = Editor.fnameToLink


def _patched_fname_to_link(self: Editor, fname: str) -> str:
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""

    if self.editorMode == EditorMode.ADD_CARDS and ext in pics:
        name = urllib.parse.quote(fname.encode("utf8"))
        width = _target_width_px()
        return f'<img src="{name}" width="{width}" style="height: auto;">'

    if ext in pics:
        name = urllib.parse.quote(fname.encode("utf8"))
        return f'<img src="{name}">'

    return f"[sound:{html.escape(fname, quote=False)}]"


Editor.fnameToLink = _patched_fname_to_link
