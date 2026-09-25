from __future__ import annotations

from collections.abc import Iterable

from anki.collection import OpChanges
from anki.decks import DeckCollapseScope, DeckTreeNode
from aqt import mw
from aqt.deckbrowser import DeckBrowser
from aqt.operations import CollectionOp


EXPAND_CMD = "expand_all_decks"
COLLAPSE_CMD = "collapse_all_decks"


def _iter_nodes(nodes: Iterable[DeckTreeNode]) -> Iterable[DeckTreeNode]:
    for node in nodes:
        yield node
        if node.children:
            yield from _iter_nodes(node.children)


def _set_all_collapsed(deck_browser: DeckBrowser, collapsed: bool) -> None:
    def op(col) -> OpChanges:
        tree = col.sched.deck_due_tree()
        for node in _iter_nodes(tree.children):
            if node.children:
                col.decks.set_collapsed(
                    deck_id=node.deck_id,
                    collapsed=collapsed,
                    scope=DeckCollapseScope.REVIEWER,
                )
        return OpChanges()

    CollectionOp(
        parent=deck_browser.mw,
        op=op,
    ).success(lambda _: deck_browser.refresh()).run_in_background(initiator=deck_browser)


_original_link_handler = DeckBrowser._linkHandler


def _patched_link_handler(self: DeckBrowser, url: str):
    if url == EXPAND_CMD:
        _set_all_collapsed(self, False)
        return False
    if url == COLLAPSE_CMD:
        _set_all_collapsed(self, True)
        return False
    return _original_link_handler(self, url)


DeckBrowser._linkHandler = _patched_link_handler

# add buttons only once
_existing_cmds = {item[1] for item in DeckBrowser.drawLinks}
if EXPAND_CMD not in _existing_cmds:
    DeckBrowser.drawLinks.append(["", EXPAND_CMD, "すべて開く"])
if COLLAPSE_CMD not in _existing_cmds:
    DeckBrowser.drawLinks.append(["", COLLAPSE_CMD, "すべて閉じる"])
