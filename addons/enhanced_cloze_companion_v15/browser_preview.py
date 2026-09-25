"""Delete cloze wrappers in Browser Preview and Reviewer without reloading the card DOM."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from aqt import gui_hooks
from aqt.browser.previewer import Previewer
from aqt.reviewer import Reviewer

from .cloze_unwrap import cloze_answer_at_index, unwrap_preview_wrapper_at_index
MODEL_NAME = "Enhanced Cloze 2.1 v2"

_MESSAGE_PREFIX = "enhanced-cloze-companion:unwrap:"
_SUPPORTED_KINDS = {"previewQuestion", "previewAnswer", "reviewQuestion", "reviewAnswer"}
_PATCH_MARKER = "_enhanced_cloze_companion_no_reload_patch_v14"
_SUPPRESS_UNTIL = "_enhanced_cloze_companion_suppress_until_v14"
_SUPPRESS_CARD = "_enhanced_cloze_companion_suppress_card_v14"


def _is_enhanced_cloze_card(card: Any) -> bool:
    try:
        return card.note().note_type()["name"] == MODEL_NAME
    except Exception:
        return False


def _preview_script(card_id: int, note_id: int, kind: str) -> str:
    payload = {
        "cardId": int(card_id),
        "noteId": int(note_id),
        "kind": kind,
        "messagePrefix": _MESSAGE_PREFIX,
    }
    config = json.dumps(payload, ensure_ascii=False)

    script = r"""
<script>
(() => {
    const config = __CONFIG__;
    const stateClozeSelector = ".genuine-cloze, .pseudo-cloze";
    const bracketClozeSelector = ".enhanced-bracket-n-cloze";
    const deletableSelector = `${stateClozeSelector}, ${bracketClozeSelector}`;
    const restoreContextKey = `${config.cardId}:${config.kind}`;

    // This store is intentionally independent from deletion handling. It only
    // remembers the cloze immediately after the user's last cloze operation, so
    // a normal Browser Editor refresh can return to that place.
    window.__enhancedClozeEditRestoreStore =
        window.__enhancedClozeEditRestoreStore || {
            contextKey: null,
            anchor: null,
        };
    const editRestoreStore = window.__enhancedClozeEditRestoreStore;
    const restoreAnchor = editRestoreStore.contextKey === restoreContextKey
        ? editRestoreStore.anchor
        : null;
    if (editRestoreStore.contextKey !== restoreContextKey) {
        editRestoreStore.contextKey = restoreContextKey;
        editRestoreStore.anchor = null;
    }

    function isAlphabeticNestedCloze(element) {
        return !!element &&
            element.classList.contains("nested-cloze") &&
            String(element.getAttribute("data-cid") || "").toLowerCase() === "n";
    }

    function outerRegularCloze(element) {
        if (!element || !element.parentElement) return null;
        return element.parentElement.closest(
            ".genuine-cloze:not(.nested-cloze), .pseudo-cloze:not(.nested-cloze)"
        );
    }

    function alphabeticNestedClozesInScope(root, outer) {
        return Array.from(root.querySelectorAll(".nested-cloze")).filter((element) => {
            if (!isAlphabeticNestedCloze(element)) return false;
            const parent = outerRegularCloze(element);
            return outer ? parent === outer : parent === null;
        });
    }
    const bracketExpression = /\[\[\s*n\s*:\s*([\s\S]*?)\s*\]\]/gi;

    // v1-v7 stored anchors and timers on window. They are deliberately removed:
    // Deletions still never reload the preview. The edit-restore store above is separate.
    const oldController = window.__enhancedClozePreviewController;
    if (oldController && typeof oldController.cleanup === "function") {
        oldController.cleanup();
    }
    delete window.__enhancedClozePreviewStateStore;
    delete window.__enhancedClozePendingDeletionStore;
    delete window.__enhancedClozeCaptureBeforeRender;

    const controller = {
        hoveredCloze: null,
        pending: false,
        observer: null,
        listeners: [],
        timers: new Set(),
        wrappingBrackets: false,
        restoreCancelled: false,
    };
    window.__enhancedClozePreviewController = controller;

    function addListener(target, type, handler, options) {
        target.addEventListener(type, handler, options);
        controller.listeners.push([target, type, handler, options]);
    }

    function schedule(callback, delay = 0) {
        const timer = window.setTimeout(() => {
            controller.timers.delete(timer);
            callback();
        }, delay);
        controller.timers.add(timer);
        return timer;
    }

    controller.cleanup = function() {
        for (const [target, type, handler, options] of controller.listeners) {
            target.removeEventListener(type, handler, options);
        }
        controller.listeners.length = 0;
        if (controller.observer) {
            controller.observer.disconnect();
            controller.observer = null;
        }
        for (const timer of controller.timers) {
            window.clearTimeout(timer);
        }
        controller.timers.clear();
    };

    function normalizeAnchorText(value) {
        return String(value || "")
            .replace(/<br\s*\/?>/gi, " ")
            .replace(/<[^>]*>/g, " ")
            .replace(/&nbsp;/gi, " ")
            .replace(/\s+/g, " ")
            .trim();
    }

    function anchorDataForElement(element) {
        if (!element) return null;
        const root = document.getElementById("enhanced-clozes");
        if (!root || !root.contains(element)) return null;

        let kind = "regular";
        let cid = String(element.getAttribute("cid") || "");
        let answer = "";

        if (isAlphabeticNestedCloze(element)) {
            kind = "nested-n";
            cid = "n";
            answer = String(element.getAttribute("data-answer") || "");
        } else if (element.matches(bracketClozeSelector)) {
            kind = "literal-n";
            cid = "n";
            answer = String(
                element.dataset.wrapperText || element.textContent || ""
            );
        } else {
            const index = Number.parseInt(element.getAttribute("index") || "", 10);
            const data = window.enhancedClozesData;
            if (Number.isFinite(index) && data && Array.isArray(data.answers)) {
                answer = String(data.answers[index] || "");
            } else {
                answer = String(element.getAttribute("data-answer") || element.textContent || "");
            }
        }

        const signature = JSON.stringify([
            kind,
            cid,
            normalizeAnchorText(answer),
        ]);
        return { kind, cid, answer, signature };
    }

    function orderedDeletableClozes() {
        const root = document.getElementById("enhanced-clozes");
        return root ? Array.from(root.querySelectorAll(deletableSelector)) : [];
    }

    function descriptorForElement(element, elements) {
        const data = anchorDataForElement(element);
        if (!data) return null;
        const elementIndex = elements.indexOf(element);
        if (elementIndex < 0) return null;
        const sameBefore = elements.slice(0, elementIndex).filter((candidate) => {
            const candidateData = anchorDataForElement(candidate);
            return candidateData && candidateData.signature === data.signature;
        }).length;
        return {
            signature: data.signature,
            occurrence: sameBefore,
            overallIndex: elementIndex,
        };
    }

    function regularFallbackDescriptor(element, elements) {
        if (!element) return null;
        const regular = element.matches(
            ".genuine-cloze:not(.nested-cloze), .pseudo-cloze:not(.nested-cloze)"
        ) ? element : outerRegularCloze(element);
        return regular ? descriptorForElement(regular, elements) : null;
    }

    function rememberNextCloze(element) {
        if (!element || !element.isConnected) return;
        const elements = orderedDeletableClozes();
        const index = elements.indexOf(element);
        if (index < 0) return;

        const target = elements[index + 1] || element;
        const descriptor = descriptorForElement(target, elements);
        if (!descriptor) return;
        const rect = target.getBoundingClientRect();
        const viewportTop = Math.max(
            50,
            Math.min(rect.top, Math.max(50, window.innerHeight - 120))
        );

        editRestoreStore.contextKey = restoreContextKey;
        editRestoreStore.anchor = {
            descriptor,
            fallbackDescriptor: regularFallbackDescriptor(target, elements),
            viewportTop,
        };
    }

    function locateDescriptorExact(descriptor, elements) {
        if (!descriptor) return null;
        const matching = elements.filter((element) => {
            const data = anchorDataForElement(element);
            return data && data.signature === descriptor.signature;
        });
        if (matching.length) {
            const occurrence = Number(descriptor.occurrence) || 0;
            if (occurrence >= 0 && occurrence < matching.length) {
                return matching[occurrence];
            }
            if (matching.length === 1) return matching[0];
        }
        return null;
    }

    function locateDescriptorByIndex(descriptor, elements) {
        if (!descriptor) return null;
        const fallbackIndex = Number(descriptor.overallIndex);
        if (Number.isFinite(fallbackIndex) && elements.length) {
            return elements[Math.max(0, Math.min(elements.length - 1, fallbackIndex))];
        }
        return null;
    }

    function locateRestoreTarget(anchor) {
        const elements = orderedDeletableClozes();
        if (!elements.length) return null;
        return locateDescriptorExact(anchor && anchor.descriptor, elements) ||
            locateDescriptorExact(anchor && anchor.fallbackDescriptor, elements) ||
            locateDescriptorByIndex(anchor && anchor.descriptor, elements) ||
            locateDescriptorByIndex(anchor && anchor.fallbackDescriptor, elements);
    }

    function stopAutomaticScrolling() {
        try {
            if (window.jQuery) window.jQuery("html, body").stop(true, false);
        } catch (_) { }
    }

    function restoreToLastOperatedNext(anchor) {
        if (!anchor) return;
        controller.restoreCancelled = false;

        const restore = () => {
            if (controller.restoreCancelled) return;
            const target = locateRestoreTarget(anchor);
            if (!target) return;
            stopAutomaticScrolling();
            const desiredTop = Number.isFinite(Number(anchor.viewportTop))
                ? Number(anchor.viewportTop)
                : 80;
            const delta = target.getBoundingClientRect().top - desiredTop;
            if (Math.abs(delta) > 1) window.scrollBy(0, delta);
        };

        const updateHooks = typeof onUpdateHook !== "undefined"
            ? onUpdateHook
            : window.onUpdateHook;
        const shownHooks = typeof onShownHook !== "undefined"
            ? onShownHook
            : window.onShownHook;
        if (Array.isArray(updateHooks)) updateHooks.push(restore);
        if (Array.isArray(shownHooks)) shownHooks.push(restore);

        restore();
        window.requestAnimationFrame(() => {
            restore();
            window.requestAnimationFrame(restore);
        });
        for (const delay of [30, 80, 160, 300, 550, 900, 1300]) {
            schedule(restore, delay);
        }
    }

    function cancelRestoreFromUserInput() {
        controller.restoreCancelled = true;
    }

    function collectBracketTextChunks(node, chunks) {
        if (node.nodeType === Node.TEXT_NODE) {
            chunks.push({ node, text: node.nodeValue || "" });
            return;
        }
        if (!(node instanceof Element)) return;
        if (node.matches(`${bracketClozeSelector}, script, style, textarea`)) {
            chunks.push({ node: null, text: "\u0000" });
            return;
        }
        if (node.tagName === "BR") {
            chunks.push({ node: null, text: "\n" });
            return;
        }

        const blockTags = new Set([
            "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "DIV", "DL", "FIELDSET",
            "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "H1", "H2", "H3", "H4",
            "H5", "H6", "HEADER", "HR", "LI", "MAIN", "NAV", "OL", "P", "PRE",
            "SECTION", "TABLE", "TR", "UL",
        ]);
        const isBlock = blockTags.has(node.tagName);
        if (isBlock && chunks.length) chunks.push({ node: null, text: "\u0000" });
        for (const child of node.childNodes) collectBracketTextChunks(child, chunks);
        if (isBlock) chunks.push({ node: null, text: "\u0000" });
    }

    function textPositionForOffset(chunks, offset) {
        let cursor = 0;
        for (const chunk of chunks) {
            const next = cursor + chunk.text.length;
            if (chunk.node && offset >= cursor && offset <= next) {
                return { node: chunk.node, offset: Math.min(offset - cursor, chunk.text.length) };
            }
            cursor = next;
        }
        return null;
    }

    function normalizeWrapperText(value) {
        return String(value || "")
            .replace(/\s+/g, " ")
            .trim()
            .replace(/^\[\[\s*n\s*:\s*/i, "[[n:")
            .replace(/\s*\]\]$/, "]] ")
            .trim();
    }

    function reindexBracketClozes(root) {
        const counts = Object.create(null);
        Array.from(root.querySelectorAll(bracketClozeSelector)).forEach((element, index) => {
            const wrapperText = element.textContent || "";
            const key = normalizeWrapperText(wrapperText);
            const occurrence = counts[key] || 0;
            counts[key] = occurrence + 1;
            element.dataset.wrapperIndex = String(index);
            element.dataset.wrapperText = wrapperText;
            element.dataset.wrapperOccurrence = String(occurrence);
        });
    }

    function wrapVisibleBracketNClozes(root) {
        if (!root || controller.wrappingBrackets) return;
        controller.wrappingBrackets = true;
        try {
            const chunks = [];
            collectBracketTextChunks(root, chunks);
            const combined = chunks.map((chunk) => chunk.text).join("");
            bracketExpression.lastIndex = 0;
            const matches = [];
            let match;
            while ((match = bracketExpression.exec(combined)) !== null) {
                if (!match[0].includes("\u0000")) {
                    matches.push({ start: match.index, end: bracketExpression.lastIndex });
                }
                if (match[0].length === 0) bracketExpression.lastIndex += 1;
            }

            for (let index = matches.length - 1; index >= 0; index -= 1) {
                const item = matches[index];
                const start = textPositionForOffset(chunks, item.start);
                const end = textPositionForOffset(chunks, item.end);
                if (!start || !end) continue;
                const range = document.createRange();
                range.setStart(start.node, start.offset);
                range.setEnd(end.node, end.offset);
                const span = document.createElement("span");
                span.className = "enhanced-bracket-n-cloze";
                span.dataset.wrapperKind = "bracket-n";
                span.style.cursor = "pointer";
                span.append(range.extractContents());
                range.insertNode(span);
                range.detach();
            }
            reindexBracketClozes(root);
        } finally {
            controller.wrappingBrackets = false;
        }
    }

    function visibleDeletableFromTarget(target) {
        if (!(target instanceof Element)) return null;
        const cloze = target.closest(deletableSelector);
        const root = document.getElementById("enhanced-clozes");
        return cloze && root && root.contains(cloze) ? cloze : null;
    }

    function onMouseOver(event) {
        controller.hoveredCloze = visibleDeletableFromTarget(event.target);
    }

    function onMouseOut(event) {
        if (!controller.hoveredCloze) return;
        const related = event.relatedTarget;
        if (related instanceof Node && controller.hoveredCloze.contains(related)) return;
        controller.hoveredCloze = visibleDeletableFromTarget(related);
    }

    function deletionInfo(cloze) {
        const root = document.getElementById("enhanced-clozes");
        if (!root) return null;

        // Enhanced Cloze 1.24 converts [[n:...]] to a .nested-cloze span before
        // this add-on is installed.  It has data-cid/data-answer, but no index.
        if (isAlphabeticNestedCloze(cloze)) {
            const outer = outerRegularCloze(cloze);
            const scoped = alphabeticNestedClozesInScope(root, outer);
            const index = scoped.indexOf(cloze);
            if (index < 0) return null;

            const answerHtml = cloze.getAttribute("data-answer") || "";
            let parentClozeIndex = -1;
            if (outer) {
                parentClozeIndex = Number.parseInt(outer.getAttribute("index") || "", 10);
                if (!Number.isFinite(parentClozeIndex)) return null;
            }

            const sameAnswerBefore = scoped.slice(0, index).filter((element) =>
                String(element.getAttribute("data-answer") || "") === answerHtml
            ).length;

            return {
                wrapperKind: "bracket-n",
                index,
                cid: "n",
                wrapperText: `[[n:${answerHtml}]]`,
                wrapperOccurrence: sameAnswerBefore,
                scopeKind: outer ? "outer-cloze" : "outside-clozes",
                parentClozeIndex,
                renderedNested: true,
                answerHtml,
            };
        }

        // Fallback for literal [[n:...]] that remains visible in unusual card HTML.
        if (cloze.matches(bracketClozeSelector)) {
            const index = Number.parseInt(cloze.dataset.wrapperIndex || "", 10);
            const occurrence = Number.parseInt(cloze.dataset.wrapperOccurrence || "", 10);
            if (!Number.isFinite(index)) return null;
            return {
                wrapperKind: "bracket-n",
                index,
                cid: "n",
                wrapperText: cloze.dataset.wrapperText || cloze.textContent || "",
                wrapperOccurrence: Number.isFinite(occurrence) ? occurrence : 0,
                scopeKind: null,
                parentClozeIndex: -1,
                renderedNested: false,
                answerHtml: "",
            };
        }

        const index = Number.parseInt(cloze.getAttribute("index") || "", 10);
        if (!Number.isFinite(index)) return null;
        return {
            wrapperKind: "anki-cloze",
            index,
            cid: cloze.getAttribute("cid") || "",
            wrapperText: "",
            wrapperOccurrence: 0,
            scopeKind: null,
            parentClozeIndex: -1,
            renderedNested: false,
            answerHtml: "",
        };
    }

    function fragmentFromHtml(html) {
        const template = document.createElement("template");
        template.innerHTML = String(html || "");
        return template.content;
    }

    function replaceElementWithHtml(element, html) {
        const fragment = fragmentFromHtml(html);
        element.replaceWith(fragment);
    }

    function renderAnswerAfterOuterClozeDeletion(answer) {
        // Enhanced Cloze's template converts [[n:...]] (and numbered nested
        // wrappers) with renderNestedClozes().  Reuse that exact renderer here
        // instead of inserting the raw answer, otherwise the wrapper remains as
        // literal text after the outer {{cN::...}} is removed.
        if (typeof window.renderNestedClozes === "function") {
            try {
                return window.renderNestedClozes(
                    String(answer || ""),
                    window.enhancedClozeCurrentOrd
                );
            } catch (_) { }
        }
        return String(answer || "");
    }

    function prepareInsertedNestedClozes(root) {
        if (!root) return;
        for (const element of root.querySelectorAll(".nested-cloze")) {
            element.style.cursor = "pointer";
            element.classList.add("disable-select");
        }
    }

    function applyRegularClozeDeletion(cloze, info, result) {
        const data = window.enhancedClozesData;
        const removeListItem = result.listItemDeleted === true;
        const listItem = removeListItem ? cloze.closest("li") : null;

        if (!removeListItem || !listItem) {
            const answer = data && Array.isArray(data.answers)
                ? (data.answers[info.index] || "")
                : (cloze.textContent || "");
            const renderedAnswer = renderAnswerAfterOuterClozeDeletion(answer);
            replaceElementWithHtml(cloze, renderedAnswer);
        } else {
            listItem.remove();
        }

        if (data) {
            for (const key of ["clozeId", "answers", "hints"]) {
                if (Array.isArray(data[key])) data[key].splice(info.index, 1);
            }
        }

        const root = document.getElementById("enhanced-clozes");
        if (root) {
            for (const element of root.querySelectorAll(stateClozeSelector)) {
                const index = Number.parseInt(element.getAttribute("index") || "", 10);
                if (Number.isFinite(index) && index > info.index) {
                    element.setAttribute("index", String(index - 1));
                }
            }
        }

        const source = document.getElementById("enhanced-cloze-content");
        if (source && typeof result.content === "string") source.innerHTML = result.content;
        prepareInsertedNestedClozes(root);
        wrapVisibleBracketNClozes(root);
    }

    function applyBracketDeletion(cloze, info, result) {
        const removeListItem = result.listItemDeleted === true;
        const listItem = removeListItem ? cloze.closest("li") : null;
        if (removeListItem && listItem) {
            listItem.remove();
        } else {
            let answerHtml = "";
            if (info.renderedNested) {
                answerHtml = info.answerHtml || cloze.getAttribute("data-answer") || cloze.innerHTML || "";
            } else {
                const html = cloze.innerHTML || "";
                const match = html.match(/^\s*\[\[\s*n\s*:\s*([\s\S]*?)\s*\]\]\s*$/i);
                answerHtml = match ? match[1] : (cloze.textContent || "");
            }
            replaceElementWithHtml(cloze, answerHtml);
        }

        const data = window.enhancedClozesData;
        if (info.scopeKind === "outer-cloze" && data && Array.isArray(data.answers) &&
            Number.isFinite(info.parentClozeIndex) && typeof result.outerAnswer === "string") {
            data.answers[info.parentClozeIndex] = result.outerAnswer;
        }

        const source = document.getElementById("enhanced-cloze-content");
        if (source && typeof result.content === "string") source.innerHTML = result.content;
        const root = document.getElementById("enhanced-clozes");
        wrapVisibleBracketNClozes(root);
        if (root) reindexBracketClozes(root);
    }

    function onKeyDown(event) {
        if (controller.pending) return;
        if (event.key !== "Backspace" || event.ctrlKey || event.metaKey || event.altKey) return;
        const cloze = controller.hoveredCloze;
        if (!cloze || !cloze.isConnected) return;

        const active = document.activeElement;
        if (active && active !== document.body &&
            (active.matches("input, textarea") || active.isContentEditable)) return;

        const info = deletionInfo(cloze);
        if (!info) return;

        // Remember the following cloze before this one disappears from the DOM.
        // This state is only used by a later normal editor refresh.
        rememberNextCloze(cloze);

        event.preventDefault();
        event.stopImmediatePropagation();
        controller.pending = true;

        const message = config.messagePrefix + JSON.stringify({
            cardId: config.cardId,
            noteId: config.noteId,
            kind: config.kind,
            wrapperKind: info.wrapperKind,
            index: info.index,
            cid: info.cid,
            wrapperText: info.wrapperText,
            wrapperOccurrence: info.wrapperOccurrence,
            scopeKind: info.scopeKind,
            parentClozeIndex: info.parentClozeIndex,
        });

        pycmd(message, (result) => {
            try {
                if (!result || result.ok !== true || !cloze.isConnected) return;
                if (info.wrapperKind === "bracket-n") {
                    applyBracketDeletion(cloze, info, result);
                } else {
                    applyRegularClozeDeletion(cloze, info, result);
                }
                controller.hoveredCloze = null;
            } finally {
                controller.pending = false;
            }
        });
    }

    function install() {
        const root = document.getElementById("enhanced-clozes");
        if (!root) return false;
        wrapVisibleBracketNClozes(root);
        addListener(document, "mouseover", onMouseOver, true);
        addListener(document, "mouseout", onMouseOut, true);
        addListener(document, "pointerdown", (event) => {
            cancelRestoreFromUserInput();
            const cloze = visibleDeletableFromTarget(event.target);
            if (cloze) rememberNextCloze(cloze);
        }, true);
        addListener(document, "contextmenu", (event) => {
            const cloze = visibleDeletableFromTarget(event.target);
            if (cloze) rememberNextCloze(cloze);
        }, true);
        addListener(window, "wheel", cancelRestoreFromUserInput, { passive: true });
        addListener(window, "touchstart", cancelRestoreFromUserInput, { passive: true });
        addListener(document, "keydown", onKeyDown, true);
        controller.observer = new MutationObserver(() => {
            if (!controller.wrappingBrackets) wrapVisibleBracketNClozes(root);
        });
        controller.observer.observe(root, { childList: true, subtree: true });
        restoreToLastOperatedNext(restoreAnchor);
        return true;
    }

    let attempts = 0;
    const wait = () => {
        if (install()) return;
        attempts += 1;
        if (attempts < 200) window.setTimeout(wait, 10);
    };
    wait();
})();
</script>
"""
    return script.replace("__CONFIG__", config, 1)


def _on_card_will_show(text: str, card: Any, kind: str) -> str:
    if kind not in _SUPPORTED_KINDS or not _is_enhanced_cloze_card(card):
        return text
    try:
        return text + _preview_script(int(card.id), int(card.nid), kind)
    except Exception:
        return text


def _patch_previewer(previewer: Previewer) -> None:
    if getattr(previewer, _PATCH_MARKER, False):
        return

    original_render_scheduled: Callable[[], None] = previewer._render_scheduled

    def render_scheduled_without_recent_same_card_reload() -> None:
        try:
            until = float(getattr(previewer, _SUPPRESS_UNTIL, 0.0) or 0.0)
            suppressed_card = int(getattr(previewer, _SUPPRESS_CARD, 0) or 0)
            card = previewer.card()
            current_card = int(card.id) if card is not None else 0
            if current_card == suppressed_card and time.monotonic() < until:
                return
        except Exception:
            pass
        original_render_scheduled()

    previewer._render_scheduled = render_scheduled_without_recent_same_card_reload  # type: ignore[method-assign]
    setattr(previewer, _PATCH_MARKER, True)


def _suppress_same_card_reload(previewer: Previewer, card_id: int) -> None:
    setattr(previewer, _SUPPRESS_CARD, int(card_id))
    setattr(previewer, _SUPPRESS_UNTIL, time.monotonic() + 2.0)


def _note_for_preview_context(context: Previewer, card: Any) -> tuple[Any, Any | None]:
    browser = getattr(context, "_parent", None)
    editor = getattr(browser, "editor", None)
    editor_note = getattr(editor, "note", None)
    if editor_note is not None and int(editor_note.id) == int(card.nid):
        return editor_note, editor
    return card.note(), None


def _reload_browser_editor(editor: Any) -> None:
    try:
        focus_to = getattr(editor, "currentField", None)
        editor.loadNote(focusTo=focus_to)
    except Exception as error:
        print(f"Enhanced Cloze editor refresh failed: {error}")


def _card_for_context(context: Any) -> Any | None:
    if isinstance(context, Previewer):
        return context.card()
    if isinstance(context, Reviewer):
        return context.card
    return None


def _note_for_context(context: Any, card: Any) -> tuple[Any, Any | None]:
    if isinstance(context, Previewer):
        return _note_for_preview_context(context, card)
    # Reviewer does not have a simultaneously editable Browser note. Reloading
    # is unnecessary here because this JS action starts from the currently
    # rendered card and updates the collection synchronously.
    return card.note(), None


def _finish_context_update(
    context: Any, card: Any, note: Any, changes: Any, editor: Any | None
) -> None:
    if isinstance(context, Previewer):
        _suppress_same_card_reload(context, int(card.id))
        # Preserve v12's Preview behavior exactly: refresh the note object after
        # the synchronous collection update, then refresh Browser Editor only.
        note.load()
        if editor is not None:
            _reload_browser_editor(editor)
        # Keep Browser rows/undo state current. The Previewer request generated
        # by this hook is suppressed for this same card.
        gui_hooks.operation_did_execute(changes, editor or context)
        return

    if isinstance(context, Reviewer):
        # Card.render_output() and Card.note() are cached. Invalidate both so
        # showing the answer after a question-side deletion uses the saved
        # Content, while deliberately leaving the current DOM untouched.
        card.load()
        # Passing Reviewer itself as handler is important: Reviewer.op_executed
        # will not schedule NOTE_TEXT redraw when the change originated here.
        # Other windows still receive the operation and can refresh normally.
        gui_hooks.operation_did_execute(changes, context)


def _on_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    if not message.startswith(_MESSAGE_PREFIX):
        return handled
    if not isinstance(context, (Previewer, Reviewer)):
        return handled

    try:
        data = json.loads(message[len(_MESSAGE_PREFIX) :])
        card = _card_for_context(context)
        if card is None:
            return (True, {"ok": False, "reason": "no-card"})
        if int(data["cardId"]) != int(card.id) or int(data["noteId"]) != int(card.nid):
            return (True, {"ok": False, "reason": "stale-card"})
        if not _is_enhanced_cloze_card(card):
            return (True, {"ok": False, "reason": "wrong-note-type"})

        note, editor = _note_for_context(context, card)
        result = unwrap_preview_wrapper_at_index(
            note["Content"],
            str(data.get("wrapperKind", "anki-cloze")),
            int(data["index"]),
            str(data.get("cid", "")),
            str(data.get("wrapperText", "")),
            int(data.get("wrapperOccurrence", 0)),
            str(data.get("scopeKind")) if data.get("scopeKind") else None,
            int(data.get("parentClozeIndex", -1)),
        )
        if not result.changed:
            return (True, {"ok": False, "reason": "wrapper-not-found"})

        note["Content"] = result.text

        # Save synchronously through Collection, but keep the current card DOM.
        # Preview has its normal same-card render suppressed; Reviewer reports the
        # operation as self-originated so its op_executed() does not redraw it.
        changes = note.col.update_note(note)
        try:
            _finish_context_update(context, card, note, changes, editor)
        except Exception as error:
            print(f"Enhanced Cloze context refresh hook failed: {error}")

        response: dict[str, Any] = {
            "ok": True,
            "content": result.text,
            "listItemDeleted": result.list_item_deleted,
        }
        parent_index = int(data.get("parentClozeIndex", -1))
        if str(data.get("scopeKind", "")) == "outer-cloze" and parent_index >= 0:
            outer_answer = cloze_answer_at_index(result.text, parent_index)
            if outer_answer is not None:
                response["outerAnswer"] = outer_answer
        return (True, response)
    except Exception as error:
        print(f"Enhanced Cloze unwrap failed: {error}")
        return (True, {"ok": False, "reason": "exception"})


def setup_browser_preview_tools() -> None:
    gui_hooks.card_will_show.append(_on_card_will_show)
    gui_hooks.previewer_did_init.append(_patch_previewer)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)
