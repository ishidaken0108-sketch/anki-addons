from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from anki.models import MODEL_CLOZE
from anki.utils import pointVersion
from aqt import appVersion, gui_hooks, mw

ANKI_VERSION = tuple(int(p) for p in appVersion.split("."))

if TYPE_CHECKING or pointVersion() >= 45:
    from anki.collection import BrowserColumns
    from aqt.browser import CellRow, Column, SearchContext
    from aqt.browser.table import ItemId

COLUMN_KEY = "genuineClozeNumber"
COLUMN_LABEL = "Genuine Cloze #"
COLUMN_TOOLTIP = "Sort by genuine cloze number (c1, c2, c3, ...) in the Browse screen."
META_KEY = "genuine_cloze_sort"
BROWSER_NOTES_MODE_KEY = "browserTableShowNotesMode"


@dataclass(frozen=True)
class SortInfo:
    is_cloze: bool
    number: int | None
    text: str


_display_cache: dict[tuple[bool, int], str] = {}
_cloze_mid_cache: set[int] | None = None


def _clear_cache() -> None:
    _display_cache.clear()


def _browser_is_notes_mode() -> bool:
    try:
        return bool(mw.col.conf.get(BROWSER_NOTES_MODE_KEY, False))
    except Exception:
        return False


def _cloze_mid_set() -> set[int]:
    global _cloze_mid_cache
    if _cloze_mid_cache is None:
        mids: set[int] = set()
        for notetype in mw.col.models.all():
            if notetype and notetype.get("type") == MODEL_CLOZE:
                mids.add(int(notetype["id"]))
        _cloze_mid_cache = mids
    return _cloze_mid_cache


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _query_sort_info(ids: Sequence[int], notes_mode: bool) -> dict[int, SortInfo]:
    if not ids:
        return {}

    cloze_mids = _cloze_mid_set()
    info: dict[int, SortInfo] = {}
    placeholders = _placeholders(len(ids))

    if notes_mode:
        rows = mw.col.db.all(
            f"""
            select n.id, n.mid, c.ord
            from notes n
            join cards c on c.nid = n.id
            where n.id in ({placeholders})
            order by n.id asc, c.ord asc
            """,
            *ids,
        )
        collected: dict[int, list[int]] = {}
        mids_by_nid: dict[int, int] = {}
        for nid, mid, ord_ in rows:
            nid = int(nid)
            mid = int(mid)
            mids_by_nid[nid] = mid
            if mid in cloze_mids:
                collected.setdefault(nid, []).append(int(ord_) + 1)

        for nid in ids:
            mid = mids_by_nid.get(int(nid))
            numbers = sorted(set(collected.get(int(nid), []))) if mid in cloze_mids else []
            if numbers:
                info[int(nid)] = SortInfo(True, numbers[0], ", ".join(str(n) for n in numbers))
            else:
                info[int(nid)] = SortInfo(False, None, "")
    else:
        rows = mw.col.db.all(
            f"""
            select c.id, c.ord, n.mid
            from cards c
            join notes n on n.id = c.nid
            where c.id in ({placeholders})
            """,
            *ids,
        )
        for cid, ord_, mid in rows:
            cid = int(cid)
            if int(mid) in cloze_mids:
                number = int(ord_) + 1
                info[cid] = SortInfo(True, number, str(number))
            else:
                info[cid] = SortInfo(False, None, "")

        for cid in ids:
            info.setdefault(int(cid), SortInfo(False, None, ""))

    return info


def _prime_display_cache(ids: Sequence[int], notes_mode: bool) -> dict[int, SortInfo]:
    info = _query_sort_info(ids, notes_mode)
    for item_id, sort_info in info.items():
        _display_cache[(notes_mode, int(item_id))] = sort_info.text
    return info


def add_browser_column(columns: dict[str, Column]) -> None:
    if pointVersion() >= 231000:
        columns[COLUMN_KEY] = Column(
            key=COLUMN_KEY,
            cards_mode_label=COLUMN_LABEL,
            notes_mode_label=COLUMN_LABEL,
            sorting_cards=BrowserColumns.SORTING_ASCENDING,
            sorting_notes=BrowserColumns.SORTING_ASCENDING,
            uses_cell_font=False,
            alignment=BrowserColumns.ALIGNMENT_CENTER,
            cards_mode_tooltip=COLUMN_TOOLTIP,
            notes_mode_tooltip=COLUMN_TOOLTIP,
        )
    else:
        columns[COLUMN_KEY] = Column(
            key=COLUMN_KEY,
            cards_mode_label=COLUMN_LABEL,
            notes_mode_label=COLUMN_LABEL,
            sorting=BrowserColumns.SORTING_ASCENDING,
            uses_cell_font=False,
            alignment=BrowserColumns.ALIGNMENT_CENTER,
            cards_mode_tooltip=COLUMN_TOOLTIP,
            notes_mode_tooltip=COLUMN_TOOLTIP,
        )


def on_search(context: SearchContext) -> None:
    if getattr(context, "ids", None) is not None:
        return

    if isinstance(context.order, Column) and context.order.key == COLUMN_KEY:
        notes_mode = _browser_is_notes_mode()
        if getattr(context, "addon_metadata", None) is None:
            context.addon_metadata = {}
        context.addon_metadata[META_KEY] = {
            "notes_mode": notes_mode,
            "reverse": bool(context.reverse),
        }
        context.order = "n.id asc" if notes_mode else "c.id asc"


def on_browser_did_search(context: SearchContext) -> None:
    metadata = getattr(context, "addon_metadata", None) or {}
    meta = metadata.get(META_KEY)
    ids = getattr(context, "ids", None)
    if not meta or not ids:
        return

    notes_mode = bool(meta.get("notes_mode", False))
    reverse = bool(meta.get("reverse", False))
    info = _prime_display_cache(ids, notes_mode)

    cloze_ids = [item_id for item_id in ids if info.get(int(item_id), SortInfo(False, None, "")).is_cloze]
    non_cloze_ids = [item_id for item_id in ids if not info.get(int(item_id), SortInfo(False, None, "")).is_cloze]

    cloze_ids = sorted(
        cloze_ids,
        key=lambda item_id: (info[int(item_id)].number or 0, int(item_id)),
        reverse=reverse,
    )

    context.ids = cloze_ids + non_cloze_ids


def _fallback_row_text(item_id: int, is_note: bool) -> str:
    info = _query_sort_info([int(item_id)], notes_mode=is_note)
    return info.get(int(item_id), SortInfo(False, None, "")).text


def on_browser_did_fetch_row(
    card_or_note_id: ItemId, is_note: bool, row: CellRow, columns: Sequence[str]
) -> None:
    try:
        idx = columns.index(COLUMN_KEY)
    except ValueError:
        return

    item_id = int(card_or_note_id)
    row.cells[idx].text = _display_cache.get((is_note, item_id), _fallback_row_text(item_id, is_note))


def _invalidate_notetype_cache(*_args, **_kwargs) -> None:
    global _cloze_mid_cache
    _cloze_mid_cache = None
    _clear_cache()


gui_hooks.browser_did_fetch_columns.append(add_browser_column)
gui_hooks.browser_will_search.append(on_search)
gui_hooks.browser_did_search.append(on_browser_did_search)
gui_hooks.browser_did_fetch_row.append(on_browser_did_fetch_row)
gui_hooks.operation_did_execute.append(lambda _changes, _handler: _clear_cache())
gui_hooks.collection_did_load.append(lambda _col: _invalidate_notetype_cache())
