# Enhanced Cloze Preview Manual Audio
# For Anki 25.09.4+
#
# This add-on does not modify Enhanced Cloze itself.
# It suppresses automatic playback of [sound:...] files found in the
# Enhanced Cloze "Content" and "Note" fields in Browser Preview only.
#
# Important implementation detail:
# Anki may reuse the list returned by card.question_av_tags()/answer_av_tags()
# for clicked-audio indexes. Therefore we only filter that list temporarily:
# Previewer calls av_player.play_tags() immediately after this hook, and
# AVPlayer copies the list into its queue. We restore the original list on
# the next Qt event-loop turn so clicked audio/replay keep their original
# indexes and contents.

from __future__ import annotations

import html
import re
from typing import Any

from anki.sound import SoundOrVideoTag
from aqt import gui_hooks
from aqt.qt import QTimer

_SOUND_RE = re.compile(r"\[sound:([^\]]+)\]", re.IGNORECASE)
_ENHANCED_CLOZE_MARKER = "ENHANCED_CLOZE"


def _is_browser_previewer(context: Any) -> bool:
    """Return True only for Anki's Browser Previewer context."""
    cls = context.__class__
    return cls.__module__ == "aqt.browser.previewer" and "Previewer" in cls.__name__


def _is_enhanced_cloze_card(card: Any, note: Any) -> bool:
    """Limit the behavior to Enhanced Cloze cards, including renamed copies."""
    try:
        notetype = note.note_type()
        name = str((notetype or {}).get("name", ""))
        if name.lower().startswith("enhanced cloze"):
            return True
    except Exception:
        pass

    try:
        template = card.template()
        qfmt = str((template or {}).get("qfmt", ""))
        afmt = str((template or {}).get("afmt", ""))
        return _ENHANCED_CLOZE_MARKER in qfmt or _ENHANCED_CLOZE_MARKER in afmt
    except Exception:
        return False


def _blocked_sound_filenames(note: Any) -> set[str]:
    """Return audio filenames from Content and Note that must not autoplay."""
    blocked: set[str] = set()

    for field_name in ("Content", "Note"):
        try:
            field_value = str(note[field_name])
        except Exception:
            continue

        blocked.update(
            html.unescape(match).strip()
            for match in _SOUND_RE.findall(field_value)
            if match.strip()
        )

    return blocked


def _restore_tags(tags: list[Any], original: list[Any]) -> None:
    """Restore the AV-tag list after Previewer has copied the filtered queue."""
    try:
        tags[:] = original
    except Exception:
        # Never let this helper interfere with Previewer if another add-on
        # changes the object unexpectedly.
        pass


def _prevent_content_autoplay_in_preview(tags: list[Any], side: str, context: Any) -> None:
    """Temporarily filter Content/Note audio from Browser Preview autoplay only."""
    if not _is_browser_previewer(context):
        return

    try:
        card = context.card()
    except Exception:
        return
    if card is None:
        return

    try:
        note = card.note()
    except Exception:
        return

    if not _is_enhanced_cloze_card(card, note):
        return

    blocked = _blocked_sound_filenames(note)
    if not blocked:
        return

    original = tags[:]
    filtered = [
        tag
        for tag in original
        if not (
            isinstance(tag, SoundOrVideoTag)
            and html.unescape(str(tag.filename)).strip() in blocked
        )
    ]

    if len(filtered) == len(original):
        return

    # Previewer calls av_player.play_tags(tags) synchronously immediately
    # after this hook. AVPlayer copies tags, so the filtered queue is retained.
    # Restore the source list on the next event-loop turn so play:q:N/play:a:N
    # indexes continue to match Anki's rendered replay buttons.
    tags[:] = filtered
    QTimer.singleShot(0, lambda t=tags, o=original: _restore_tags(t, o))


gui_hooks.av_player_will_play_tags.append(_prevent_content_autoplay_in_preview)
