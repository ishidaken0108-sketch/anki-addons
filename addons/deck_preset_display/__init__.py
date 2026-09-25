from __future__ import annotations

import html
import re
from typing import Any, Callable

from aqt.deckbrowser import DeckBrowser

ADDON_NAME = "Deck Preset and New Card Count in Deck List"


_NEW_COUNT_STYLE = (
    "margin-left:8px; color:#666; font-size:0.88em; "
    "font-weight:normal; white-space:nowrap;"
)


def _get_preset_name(deck_browser: DeckBrowser, deck_id: int) -> str:
    try:
        conf = deck_browser.mw.col.decks.config_dict_for_deck_id(deck_id)
        if isinstance(conf, dict):
            name = conf.get("name")
            if isinstance(name, str) and name.strip():
                return name
    except Exception:
        pass
    return "-"


def _deck_names_and_ids(deck_browser: DeckBrowser) -> list[tuple[int, str]]:
    """Return (deck_id, full_deck_name) pairs across supported Anki versions."""
    decks = deck_browser.mw.col.decks

    try:
        result = []
        for item in decks.all_names_and_ids():
            did = int(getattr(item, "id"))
            name = str(getattr(item, "name"))
            result.append((did, name))
        if result:
            return result
    except Exception:
        pass

    try:
        result = []
        all_decks = decks.all()
        if isinstance(all_decks, dict):
            iterable = all_decks.values()
        else:
            iterable = all_decks
        for deck in iterable:
            did = int(deck.get("id"))
            name = str(deck.get("name", ""))
            if name:
                result.append((did, name))
        return result
    except Exception:
        return []


def _build_new_card_total_cache(deck_browser: DeckBrowser) -> dict[int, int]:
    """
    Count cards that are still new/unstudied by card state, not by today's deck limit.

    We use cards.type = 0 so the number represents cards that have never been
    answered. This is independent from the daily new-card introduction limit.
    Parent decks include cards in their child decks.
    """
    col = deck_browser.mw.col
    try:
        rows = col.db.all("select did, count() from cards where type = 0 group by did")
    except Exception:
        return {}

    exact_count: dict[int, int] = {}
    for did, count in rows:
        try:
            exact_count[int(did)] = int(count)
        except Exception:
            continue

    names_and_ids = _deck_names_and_ids(deck_browser)
    id_to_name = {did: name for did, name in names_and_ids}
    cache: dict[int, int] = {}

    for did, name in names_and_ids:
        prefix = name + "::"
        total = 0
        for child_did, child_name in id_to_name.items():
            if child_name == name or child_name.startswith(prefix):
                total += exact_count.get(child_did, 0)
        cache[did] = total

    return cache


def _get_new_card_total(deck_browser: DeckBrowser, deck_id: int) -> int:
    cache = getattr(deck_browser, "_preset_display_new_card_cache", None)
    if not isinstance(cache, dict):
        cache = _build_new_card_total_cache(deck_browser)
        setattr(deck_browser, "_preset_display_new_card_cache", cache)
    try:
        return int(cache.get(int(deck_id), 0))
    except Exception:
        return 0


def _inject_new_count_after_deck_name(buf: str, count: int) -> str:
    label = html.escape(f"新規 {count}枚")
    badge = f'<span class="deck-new-total" style="{_NEW_COUNT_STYLE}">({label})</span>'

    # Preferred: add inside the deck-name cell so it appears immediately after the deck name.
    pattern = re.compile(
        r'(<td\b[^>]*class=["\']?[^>"\']*decktd[^>"\']*["\']?[^>]*>.*?)(</td>)',
        re.IGNORECASE | re.DOTALL,
    )
    new_buf, replaced = pattern.subn(r"\1" + badge + r"\2", buf, count=1)
    if replaced:
        return new_buf

    # Fallback for possible future HTML changes: place it after the first link in the row.
    return buf.replace("</a>", "</a>" + badge, 1)


if not getattr(DeckBrowser, "_preset_name_column_patched", False):
    _orig_render_deck_tree: Callable[..., str] = DeckBrowser._renderDeckTree
    _orig_render_deck_node: Callable[..., str] = DeckBrowser._render_deck_node
    _orig_top_level_drag_row: Callable[..., str] = DeckBrowser._topLevelDragRow

    def _renderDeckTree_with_preset(self: DeckBrowser, top: Any) -> str:
        setattr(self, "_preset_display_new_card_cache", None)
        buf = _orig_render_deck_tree(self, top)
        old_header = '<th class=optscol></th></tr>'
        new_header = '<th class="preset-col-header" align=start>Preset</th><th class=optscol></th></tr>'
        if old_header in buf:
            buf = buf.replace(old_header, new_header, 1)
        return buf

    def _render_deck_node_with_preset(self: DeckBrowser, node: Any, ctx: Any) -> str:
        buf = _orig_render_deck_node(self, node, ctx)
        deck_id = int(node.deck_id)

        new_total = _get_new_card_total(self, deck_id)
        buf = _inject_new_count_after_deck_name(buf, new_total)

        preset_name = html.escape(_get_preset_name(self, deck_id))
        old_tail = (
            '<td align=center class=opts><a onclick=\'return pycmd("opts:%d");\'>'
            '<img src=\'/_anki/imgs/gears.svg\' class=gears></a></td></tr>' % deck_id
        )
        new_tail = (
            '<td class="preset-col" align=start '
            'style="white-space:nowrap; padding-left:12px; padding-right:12px;">%s</td>'
            '<td align=center class=opts><a onclick=\'return pycmd("opts:%d");\'>'
            '<img src=\'/_anki/imgs/gears.svg\' class=gears></a></td></tr>'
            % (preset_name, deck_id)
        )
        if old_tail in buf:
            buf = buf.replace(old_tail, new_tail, 1)
        return buf

    def _topLevelDragRow_with_preset(self: DeckBrowser) -> str:
        return "<tr class='top-level-drag-row'><td colspan='7'>&nbsp;</td></tr>"

    DeckBrowser._renderDeckTree = _renderDeckTree_with_preset
    DeckBrowser._render_deck_node = _render_deck_node_with_preset
    DeckBrowser._topLevelDragRow = _topLevelDragRow_with_preset
    DeckBrowser._preset_name_column_patched = True
