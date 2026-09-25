from __future__ import annotations

import html as html_lib
import re

_BREAK = "\uE000REVIEW_APPEND_BREAK\uE001"


def _visible_text(fragment: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", "", fragment or "")
    text = html_lib.unescape(text).replace("\xa0", " ")
    return text.strip()


def is_effectively_empty(fragment: str) -> bool:
    return not _visible_text(fragment)


def flatten_html_lines(value: str) -> list[str]:
    """Turn block/list based HTML into inline fragments separated by logical lines.

    This prevents source <li> elements from becoming nested <li> elements when the
    mnemonic is inserted into the target list item.
    """
    if not value:
        return []

    s = value.replace("\r\n", "\n").replace("\r", "\n")

    # Lists are only containers here. Their individual items become lines.
    s = re.sub(r"(?is)<\s*/?\s*(?:ol|ul)\b[^>]*>", "", s)
    s = re.sub(r"(?is)<\s*li\b[^>]*>", "", s)
    s = re.sub(r"(?is)<\s*/\s*li\s*>", _BREAK, s)

    # Common editor block separators.
    s = re.sub(r"(?is)<\s*br\s*/?\s*>", _BREAK, s)
    s = re.sub(r"(?is)<\s*(?:div|p)\b[^>]*>", "", s)
    s = re.sub(r"(?is)<\s*/\s*(?:div|p)\s*>", _BREAK, s)
    s = s.replace("\n", _BREAK)

    parts: list[str] = []
    for raw in s.split(_BREAK):
        part = raw.strip()
        if part and not is_effectively_empty(part):
            parts.append(part)
    return parts


def find_semantic_colon(fragment: str) -> int | None:
    """Return the first ':'/'：' that is text, not inside an HTML tag or cloze.

    This avoids treating style="color:red" or {{c1::...}} as the mnemonic
    separator.
    """
    in_tag = False
    cloze_depth = 0
    i = 0
    while i < len(fragment):
        if fragment.startswith("{{", i):
            cloze_depth += 1
            i += 2
            continue
        if cloze_depth and fragment.startswith("}}", i):
            cloze_depth -= 1
            i += 2
            continue

        ch = fragment[i]
        if cloze_depth == 0:
            if ch == "<":
                in_tag = True
            elif ch == ">" and in_tag:
                in_tag = False
            elif not in_tag and ch in (":", "："):
                return i
        i += 1
    return None


def _strip_right_leading_space(value: str) -> str:
    # Anki's editor often inserts &nbsp; around punctuation.
    previous = None
    out = value
    while previous != out:
        previous = out
        out = re.sub(r"^(?:\s|&nbsp;|&#160;|&#xA0;)+", "", out, flags=re.I)
    return out


def _strip_right_trailing_space(value: str) -> str:
    previous = None
    out = value
    while previous != out:
        previous = out
        out = re.sub(r"(?:\s|&nbsp;|&#160;|&#xA0;)+$", "", out, flags=re.I)
    return out


def _already_single_cloze(value: str) -> bool:
    return bool(re.fullmatch(r"(?is)\{\{c\d+::.*\}\}", value.strip()))


def wrap_c1(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if _already_single_cloze(value):
        return value
    return "{{c1::" + value + "}}"


def format_mnemonics(value: str) -> str:
    """Format Mnemonics for insertion into one target <li>.

    Rules:
    - Empty -> empty.
    - If no logical line contains ':' or '：': cloze the *entire* Mnemonics value.
    - If a colon exists: process each logical line separately. On a colon line,
      only the text after the first semantic colon is clozed. A line without a
      colon in a mixed value is clozed in full.
    """
    lines = flatten_html_lines(value)
    if not lines:
        return ""

    colon_positions = [find_semantic_colon(line) for line in lines]
    has_any_colon = any(pos is not None for pos in colon_positions)

    if not has_any_colon:
        # Important: one cloze around the whole mnemonic, even if it had several
        # HTML lines originally.
        joined = "<br>".join(lines)
        return wrap_c1(joined)

    formatted: list[str] = []
    for line, pos in zip(lines, colon_positions):
        if pos is None:
            formatted.append(wrap_c1(line))
            continue

        left = line[: pos + 1]
        right = line[pos + 1 :]
        right = _strip_right_trailing_space(_strip_right_leading_space(right))
        if right:
            formatted.append(left + wrap_c1(right))
        else:
            # Keep a label-only line readable instead of creating an empty cloze.
            formatted.append(left)

    return "<br>".join(part for part in formatted if part)


def append_list_item(target_html: str, item_html: str) -> str:
    """Append item_html to the last populated ol/ul.

    Trailing empty <ol></ol> blocks are ignored. If no populated list exists,
    the last empty list is used; if no list exists, a new <ol> is appended.
    """
    li_html = f"<li>{item_html}</li>"
    pattern = re.compile(r"(?is)<(ol|ul)\b[^>]*>.*?</\1\s*>")
    matches = list(pattern.finditer(target_html or ""))

    chosen = None
    for match in matches:
        if re.search(r"(?is)<li\b", match.group(0)):
            chosen = match

    if chosen is None and matches:
        chosen = matches[-1]

    if chosen is None:
        base = target_html or ""
        return base + f"<ol>{li_html}</ol>"

    block = chosen.group(0)
    tag = chosen.group(1)
    close_match = list(re.finditer(rf"(?is)</{tag}\s*>", block))[-1]
    new_block = block[: close_match.start()] + li_html + block[close_match.start() :]
    return (target_html or "")[: chosen.start()] + new_block + (target_html or "")[chosen.end() :]
