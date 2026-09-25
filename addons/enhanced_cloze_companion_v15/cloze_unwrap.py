"""Helpers for removing preview wrappers without changing their inner text."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass


# Mirrors the Enhanced Cloze card template. The preview occurrence index follows
# this expression's order in the Content field.
_CLOZE_RE = re.compile(
    r"\{\{c(?P<cid>\d+)::(?P<answer>[\W\w]*?)(?:::(?P<hint>[\W\w]*?))?\}\}",
    re.IGNORECASE,
)

# ``n`` is the literal alphabetic marker used by this notation.
# Whitespace immediately after the colon and before the closing brackets is
# treated as wrapper whitespace, so [[n: text ]] becomes text.
_BRACKET_N_RE = re.compile(
    r"\[\[\s*n\s*:\s*(?P<answer>[\W\w]*?)\s*\]\]",
    re.IGNORECASE,
)
_HTML_STRIP_RE = re.compile(r"<[^>]*>")
_WHITESPACE_RE = re.compile(r"\s+")
_HTML_TAG_RE = re.compile(
    r"<!--[\W\w]*?-->|<\s*(?P<closing>/?)\s*(?P<tag>[A-Za-z][\w:-]*)\b[^>]*>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UnwrapResult:
    text: str
    changed: bool
    anchor: int | None = None
    list_item_deleted: bool = False


def _replace_match(text: str, match: re.Match[str]) -> UnwrapResult:
    answer = match.group("answer")
    return UnwrapResult(
        text=text[: match.start()] + answer + text[match.end() :],
        changed=True,
        anchor=match.start(),
    )


def _cloze_match_at_index(text: str, index: int) -> re.Match[str] | None:
    if index < 0:
        return None
    for current_index, match in enumerate(_CLOZE_RE.finditer(text)):
        if current_index == index:
            return match
    return None


def cloze_answer_at_index(text: str, index: int) -> str | None:
    """Return the source answer HTML of one regular Anki cloze."""

    match = _cloze_match_at_index(text, index)
    return match.group("answer") if match is not None else None


def unwrap_cloze_at_index(
    text: str, index: int, expected_cid: str | None = None
) -> UnwrapResult:
    """Remove one ``{{cN::...}}`` wrapper and retain its answer."""

    match = _cloze_match_at_index(text, index)
    if match is None:
        return UnwrapResult(text=text, changed=False)

    if expected_cid is not None and match.group("cid") != str(expected_cid):
        return UnwrapResult(text=text, changed=False)

    return _replace_match(text, match)


def _normalized_bracket_wrapper(value: str) -> str:
    """Normalize a displayed/source wrapper for reliable matching."""

    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = _HTML_STRIP_RE.sub("", value)
    value = _WHITESPACE_RE.sub(" ", value).strip()

    match = _BRACKET_N_RE.fullmatch(value)
    if match:
        answer = _WHITESPACE_RE.sub(" ", match.group("answer")).strip()
        return f"[[n:{answer}]]"
    return value


def _select_bracket_match(
    matches: list[re.Match[str]],
    index: int,
    expected_wrapper: str | None,
    expected_occurrence: int | None,
) -> re.Match[str] | None:
    if expected_wrapper:
        expected_key = _normalized_bracket_wrapper(expected_wrapper)
        matching = [
            match
            for match in matches
            if _normalized_bracket_wrapper(match.group(0)) == expected_key
        ]
        if matching:
            occurrence = expected_occurrence if expected_occurrence is not None else 0
            if 0 <= occurrence < len(matching):
                return matching[occurrence]
            if len(matching) == 1:
                return matching[0]

    if 0 <= index < len(matches):
        return matches[index]
    return None


def _unwrap_bracket_in_regular_cloze(
    text: str,
    parent_cloze_index: int,
    index: int,
    expected_wrapper: str | None,
    expected_occurrence: int | None,
) -> UnwrapResult:
    """Unwrap ``[[n:...]]`` inside one rendered regular cloze answer."""

    outer = _cloze_match_at_index(text, parent_cloze_index)
    if outer is None:
        return UnwrapResult(text=text, changed=False)

    answer = outer.group("answer")
    matches = list(_BRACKET_N_RE.finditer(answer))
    selected = _select_bracket_match(
        matches, index, expected_wrapper, expected_occurrence
    )
    if selected is None:
        return UnwrapResult(text=text, changed=False)

    inner_result = _replace_match(answer, selected)
    answer_start, answer_end = outer.span("answer")
    return UnwrapResult(
        text=text[:answer_start] + inner_result.text + text[answer_end:],
        changed=True,
        anchor=answer_start + selected.start(),
    )


def _unwrap_bracket_outside_regular_clozes(
    text: str,
    index: int,
    expected_wrapper: str | None,
    expected_occurrence: int | None,
) -> UnwrapResult:
    """Unwrap a bracket cloze rendered outside all ``{{cN::...}}`` spans."""

    cloze_ranges = [match.span() for match in _CLOZE_RE.finditer(text)]

    def is_outside(match: re.Match[str]) -> bool:
        start = match.start()
        return not any(range_start <= start < range_end for range_start, range_end in cloze_ranges)

    matches = [match for match in _BRACKET_N_RE.finditer(text) if is_outside(match)]
    selected = _select_bracket_match(
        matches, index, expected_wrapper, expected_occurrence
    )
    if selected is None:
        return UnwrapResult(text=text, changed=False)
    return _replace_match(text, selected)


def unwrap_bracket_n(
    text: str,
    index: int,
    expected_wrapper: str | None = None,
    expected_occurrence: int | None = None,
    scope_kind: str | None = None,
    parent_cloze_index: int | None = None,
) -> UnwrapResult:
    """Remove one alphabetic ``[[n: ... ]]`` wrapper and retain its inner text.

    Enhanced Cloze 1.24 converts ``[[n:...]]`` into a ``.nested-cloze`` span
    before the Browser Preview add-on runs.  ``scope_kind`` maps that rendered
    element back either to a particular regular cloze answer or to text outside
    all regular clozes.
    """

    if scope_kind == "outer-cloze" and parent_cloze_index is not None:
        return _unwrap_bracket_in_regular_cloze(
            text,
            parent_cloze_index,
            index,
            expected_wrapper,
            expected_occurrence,
        )

    if scope_kind == "outside-clozes":
        return _unwrap_bracket_outside_regular_clozes(
            text,
            index,
            expected_wrapper,
            expected_occurrence,
        )

    matches = list(_BRACKET_N_RE.finditer(text))
    selected = _select_bracket_match(
        matches, index, expected_wrapper, expected_occurrence
    )
    if selected is None:
        return UnwrapResult(text=text, changed=False)
    return _replace_match(text, selected)



def _enclosing_list_item_span(
    text: str, anchor: int
) -> tuple[int, int, int, int] | None:
    """Return the outer/inner bounds of the ``<li>`` containing ``anchor``."""

    if anchor < 0 or anchor > len(text):
        return None

    stack: list[tuple[int, int]] = []
    for match in _HTML_TAG_RE.finditer(text):
        if match.start() > anchor:
            break
        tag = (match.group("tag") or "").lower()
        if tag != "li":
            continue
        closing = bool(match.group("closing"))
        self_closing = match.group(0).rstrip().endswith("/>")
        if closing:
            if stack:
                stack.pop()
        elif not self_closing:
            stack.append((match.start(), match.end()))

    if not stack:
        return None

    outer_start, inner_start = stack[-1]
    depth = 0
    for match in _HTML_TAG_RE.finditer(text, outer_start):
        tag = (match.group("tag") or "").lower()
        if tag != "li":
            continue
        closing = bool(match.group("closing"))
        self_closing = match.group(0).rstrip().endswith("/>")
        if closing:
            depth -= 1
            if depth == 0:
                return outer_start, match.end(), inner_start, match.start()
        elif not self_closing:
            depth += 1
    return None


def _delete_list_item_if_last_cloze(result: UnwrapResult) -> UnwrapResult:
    """Delete the containing list item when no supported cloze remains in it."""

    if not result.changed or result.anchor is None:
        return result

    span = _enclosing_list_item_span(result.text, result.anchor)
    if span is None:
        return result

    outer_start, outer_end, inner_start, inner_end = span
    list_item_html = result.text[inner_start:inner_end]
    if _CLOZE_RE.search(list_item_html) or _BRACKET_N_RE.search(list_item_html):
        return result

    return UnwrapResult(
        text=result.text[:outer_start] + result.text[outer_end:],
        changed=True,
        anchor=outer_start,
        list_item_deleted=True,
    )


def unwrap_preview_wrapper_at_index(
    text: str,
    wrapper_kind: str,
    index: int,
    expected_cid: str | None = None,
    expected_wrapper: str | None = None,
    expected_occurrence: int | None = None,
    scope_kind: str | None = None,
    parent_cloze_index: int | None = None,
) -> UnwrapResult:
    """Dispatch preview deletion to the matching wrapper syntax."""

    if wrapper_kind == "anki-cloze":
        result = unwrap_cloze_at_index(text, index, expected_cid)
    elif wrapper_kind == "bracket-n":
        result = unwrap_bracket_n(
            text,
            index,
            expected_wrapper=expected_wrapper,
            expected_occurrence=expected_occurrence,
            scope_kind=scope_kind,
            parent_cloze_index=parent_cloze_index,
        )
    else:
        result = UnwrapResult(text=text, changed=False)
    return _delete_list_item_if_last_cloze(result)


def _normalized_list_item_signature(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = _HTML_STRIP_RE.sub(" ", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def _list_item_spans(text: str) -> list[tuple[int, int, int, int]]:
    """Return source spans for matched <li>...</li> elements in source order."""

    stack: list[tuple[int, int]] = []
    spans: list[tuple[int, int, int, int]] = []
    for match in _HTML_TAG_RE.finditer(text):
        tag = (match.group("tag") or "").lower()
        if tag != "li":
            continue
        closing = bool(match.group("closing"))
        self_closing = match.group(0).rstrip().endswith("/>")
        if closing:
            if not stack:
                continue
            outer_start, inner_start = stack.pop()
            spans.append((outer_start, match.end(), inner_start, match.start()))
        elif not self_closing:
            stack.append((match.start(), match.end()))
    spans.sort(key=lambda span: span[0])
    return spans


def delete_list_item_at_index(
    text: str,
    index: int,
    expected_signature: str | None = None,
) -> UnwrapResult:
    """Delete one complete ``<li>`` selected by its source-DOM ordinal.

    ``expected_signature`` is a safety/fallback check generated from the hidden
    ``#enhanced-cloze-content`` DOM. It prevents a stale/misaligned click from
    deleting a different item when browser HTML repair changes list structure.
    """

    spans = _list_item_spans(text)
    selected: tuple[int, int, int, int] | None = None
    expected = _normalized_list_item_signature(expected_signature or "")

    if 0 <= index < len(spans):
        candidate = spans[index]
        candidate_sig = _normalized_list_item_signature(
            text[candidate[2] : candidate[3]]
        )
        if not expected or candidate_sig == expected:
            selected = candidate

    if selected is None and expected:
        matching = [
            span
            for span in spans
            if _normalized_list_item_signature(text[span[2] : span[3]]) == expected
        ]
        if len(matching) == 1:
            selected = matching[0]

    if selected is None:
        return UnwrapResult(text=text, changed=False)

    outer_start, outer_end, _, _ = selected
    return UnwrapResult(
        text=text[:outer_start] + text[outer_end:],
        changed=True,
        anchor=outer_start,
        list_item_deleted=True,
    )
