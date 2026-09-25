"""Enhanced Cloze companion tools for Browser Preview and Reviewer."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from aqt import gui_hooks
from aqt.browser.previewer import Previewer
from aqt.reviewer import Reviewer

from .cloze_unwrap import (
    cloze_answer_at_index,
    delete_list_item_at_index,
    unwrap_preview_wrapper_at_index,
)

MODEL_NAME = "Enhanced Cloze 2.1 v2"

_MESSAGE_PREFIX = "enhanced-cloze-companion:"
_PREVIEW_KINDS = {"previewQuestion", "previewAnswer"}
_REVIEW_KINDS = {"reviewQuestion", "reviewAnswer"}
_SUPPORTED_KINDS = _PREVIEW_KINDS | _REVIEW_KINDS
_PATCH_MARKER = "_enhanced_cloze_preview_no_reload_patch_v21"
_SUPPRESS_UNTIL = "_enhanced_cloze_preview_suppress_until_v21"
_SUPPRESS_CARD = "_enhanced_cloze_preview_suppress_card_v21"


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
        "isPreview": kind in _PREVIEW_KINDS,
    }
    config = json.dumps(payload, ensure_ascii=False)

    script = r"""
<script>
(() => {
    const config = __CONFIG__;
    const stateClozeSelector = ".genuine-cloze, .pseudo-cloze";
    const bracketClozeSelector = ".enhanced-bracket-n-cloze";
    const listDeleteButtonSelector = ".enhanced-cloze-list-delete-button";
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
        hoveredListDeleteButton: null,
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
        const deleteButton = deleteButtonFromTarget(event.target);
        controller.hoveredListDeleteButton = deleteButton;
        controller.hoveredCloze = deleteButton ? null : visibleDeletableFromTarget(event.target);
    }

    function onMouseOut(event) {
        const related = event.relatedTarget;
        const nextDeleteButton = related instanceof Element ? deleteButtonFromTarget(related) : null;
        controller.hoveredListDeleteButton = nextDeleteButton;

        if (nextDeleteButton) {
            controller.hoveredCloze = null;
            return;
        }
        if (controller.hoveredCloze && related instanceof Node && controller.hoveredCloze.contains(related)) return;
        controller.hoveredCloze = related instanceof Element ? visibleDeletableFromTarget(related) : null;
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

    function captureStableViewportAnchor(preferred) {
        const root = document.getElementById("enhanced-clozes");
        if (!root) return { element: null, top: 0, scrollY: window.scrollY };

        let element = preferred && preferred.isConnected ? preferred : null;
        if (!element) {
            const probeY = Math.max(70, Math.min(window.innerHeight * 0.28, window.innerHeight - 70));
            const candidates = Array.from(root.querySelectorAll("li"));
            element = candidates.find((candidate) => {
                const rect = candidate.getBoundingClientRect();
                return rect.top <= probeY && rect.bottom >= probeY;
            }) || candidates.find((candidate) => candidate.getBoundingClientRect().bottom > 0) || root;
        }
        return {
            element,
            top: element.getBoundingClientRect().top,
            scrollY: window.scrollY,
        };
    }

    function restoreStableViewportAnchor(anchor) {
        if (!anchor) return;
        const apply = () => {
            if (anchor.element && anchor.element.isConnected) {
                const delta = anchor.element.getBoundingClientRect().top - anchor.top;
                if (Math.abs(delta) > 0.5) window.scrollBy(0, delta);
            } else {
                window.scrollTo(window.scrollX, anchor.scrollY);
            }
        };
        apply();
        window.requestAnimationFrame(() => {
            apply();
            window.requestAnimationFrame(apply);
        });
    }

    function closeOneEnhancedCloze(element) {
        if (!element || !element.isConnected) return;
        try {
            if (typeof window.toggleCloze === "function") {
                const hadSetting = typeof window.revealPseudoClozesByDefault !== "undefined";
                const oldSetting = hadSetting ? window.revealPseudoClozesByDefault : undefined;
                if (hadSetting) window.revealPseudoClozesByDefault = false;
                window.toggleCloze(element, "hint");
                if (hadSetting) window.revealPseudoClozesByDefault = oldSetting;
                return;
            }
        } catch (_) { }
        element.setAttribute("show-state", "hint");
    }

    function closeAllClozesPreservingView() {
        const root = document.getElementById("enhanced-clozes");
        if (!root) return;
        const anchor = captureStableViewportAnchor(null);
        stopAutomaticScrolling();

        // Close outer regular clozes first. Reopening one later will recreate its
        // nested [[n:...]] children in their normal closed state.
        for (const element of Array.from(root.querySelectorAll(
            ".genuine-cloze:not(.nested-cloze), .pseudo-cloze:not(.nested-cloze)"
        ))) {
            closeOneEnhancedCloze(element);
        }
        // [[n:...]] that live outside a regular cloze remain in the DOM.
        for (const element of Array.from(root.querySelectorAll(".nested-cloze"))) {
            closeOneEnhancedCloze(element);
        }
        restoreStableViewportAnchor(anchor);
    }

    function installCloseAllButton() {
        if (!config.isPreview) return;
        const old = document.getElementById("enhanced-cloze-close-all-button");
        if (old) old.remove();
        const button = document.createElement("button");
        button.id = "enhanced-cloze-close-all-button";
        button.type = "button";
        button.textContent = "全Clozeを閉じる";
        button.title = "すべてのClozeを閉じる（スクロール位置を維持）";
        Object.assign(button.style, {
            position: "fixed",
            top: "8px",
            right: "10px",
            zIndex: "2147483647",
            padding: "5px 9px",
            borderRadius: "6px",
            border: "1px solid rgba(127,127,127,.55)",
            background: "var(--canvas, rgba(250,250,250,.94))",
            color: "inherit",
            fontSize: "12px",
            lineHeight: "1.4",
            cursor: "pointer",
            opacity: "0.88",
            boxShadow: "0 1px 4px rgba(0,0,0,.15)",
        });
        addListener(button, "click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            closeAllClozesPreservingView();
        }, true);
        document.body.appendChild(button);
    }

    function installListDeleteButtonStyle() {
        if (!config.isPreview) return;
        const old = document.getElementById("enhanced-cloze-list-delete-button-style");
        if (old) old.remove();
        const style = document.createElement("style");
        style.id = "enhanced-cloze-list-delete-button-style";
        style.textContent = `
            ${listDeleteButtonSelector} {
                display: inline-flex !important;
                align-items: center;
                justify-content: center;
                box-sizing: border-box;
                width: 16px;
                height: 16px;
                margin: 0 4px 0 2px;
                padding: 0;
                vertical-align: 0.08em;
                border: 1px solid transparent;
                border-radius: 4px;
                background: transparent;
                color: currentColor;
                font-family: Arial, sans-serif;
                font-size: 13px;
                font-weight: 600;
                line-height: 14px;
                opacity: 0.18;
                cursor: default;
                user-select: none;
                -webkit-user-select: none;
            }
            #enhanced-clozes ol > li:hover > ${listDeleteButtonSelector},
            ${listDeleteButtonSelector}:hover {
                opacity: 0.82;
                border-color: rgba(127,127,127,.45);
                background: rgba(127,127,127,.10);
            }
            ${listDeleteButtonSelector}:hover {
                opacity: 1;
            }
        `;
        document.head.appendChild(style);
    }

    function ensureListDeleteButtons(root) {
        if (!config.isPreview || !root) return;

        // v16 briefly used a left-gutter overlay. Remove any stale copies so
        // nothing competes with Enhanced Cloze's own left-edge click target.
        for (const stale of Array.from(document.querySelectorAll(
            ".enhanced-cloze-list-marker-zone"
        ))) {
            stale.remove();
        }

        for (const li of Array.from(root.querySelectorAll("ol > li"))) {
            if (!li.parentElement || li.parentElement.tagName !== "OL") continue;
            const first = li.firstElementChild;
            if (first && first.matches(listDeleteButtonSelector)) continue;

            const button = document.createElement("button");
            button.type = "button";
            button.className = listDeleteButtonSelector.slice(1);
            button.textContent = "×";
            button.title = "マウスを合わせてBackspaceでこの箇条書きを削除";
            button.setAttribute("aria-label", "マウスを合わせてBackspaceでこの箇条書きを削除");
            button.setAttribute("tabindex", "-1");
            li.insertBefore(button, li.firstChild);
        }
    }

    function deleteButtonFromTarget(target) {
        if (!config.isPreview) return null;
        if (!(target instanceof Element)) return null;
        return target.closest(listDeleteButtonSelector);
    }

    function ownedOrderedLists(owner, root) {
        if (owner === root) {
            return Array.from(root.querySelectorAll("ol")).filter((list) => {
                const parentLi = list.closest("li");
                return !parentLi || !root.contains(parentLi);
            });
        }
        return Array.from(owner.querySelectorAll("ol")).filter((list) =>
            list.closest("li") === owner
        );
    }

    function directListItems(list) {
        return Array.from(list.children).filter((child) => child.tagName === "LI");
    }

    function orderedListPath(li, root) {
        const reversed = [];
        let current = li;
        while (current && root.contains(current)) {
            const list = current.parentElement;
            if (!list || list.tagName !== "OL") return null;
            const itemIndex = directListItems(list).indexOf(current);
            if (itemIndex < 0) return null;

            const parentLi = list.parentElement && list.parentElement.closest("li");
            if (parentLi && root.contains(parentLi)) {
                const lists = ownedOrderedLists(parentLi, root);
                const listIndex = lists.indexOf(list);
                if (listIndex < 0) return null;
                reversed.push([listIndex, itemIndex]);
                current = parentLi;
            } else {
                const lists = ownedOrderedLists(root, root);
                const listIndex = lists.indexOf(list);
                if (listIndex < 0) return null;
                reversed.push([listIndex, itemIndex]);
                break;
            }
        }
        return reversed.reverse();
    }

    function listItemFromPath(root, path) {
        if (!root || !Array.isArray(path) || !path.length) return null;
        let owner = root;
        let item = null;
        for (const segment of path) {
            const listIndex = Number(segment && segment[0]);
            const itemIndex = Number(segment && segment[1]);
            const lists = ownedOrderedLists(owner, root);
            const list = lists[listIndex];
            if (!list) return null;
            const items = directListItems(list);
            item = items[itemIndex] || null;
            if (!item) return null;
            owner = item;
        }
        return item;
    }

    function listDeletionInfo(li) {
        const root = document.getElementById("enhanced-clozes");
        const source = document.getElementById("enhanced-cloze-content");
        if (!root || !source || !root.contains(li)) return null;
        const path = orderedListPath(li, root);
        if (!path) return null;
        const sourceLi = listItemFromPath(source, path);
        if (!sourceLi) return null;
        const sourceItems = Array.from(source.querySelectorAll("li"));
        const index = sourceItems.indexOf(sourceLi);
        if (index < 0) return null;
        return {
            index,
            signature: sourceLi.innerHTML || sourceLi.textContent || "",
        };
    }

    function firstVisibleContentX(li) {
        if (!li) return null;
        const walker = document.createTreeWalker(
            li,
            NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT,
            {
                acceptNode(node) {
                    if (node === li) return NodeFilter.FILTER_SKIP;
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        const element = node;
                        if (element.tagName === "OL" || element.tagName === "UL") {
                            return NodeFilter.FILTER_REJECT;
                        }
                        if (element.matches(`script, style, .enhanced-cloze-list-marker-zone, ${listDeleteButtonSelector}`)) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        return NodeFilter.FILTER_SKIP;
                    }
                    return /\S/.test(node.nodeValue || "")
                        ? NodeFilter.FILTER_ACCEPT
                        : NodeFilter.FILTER_SKIP;
                },
            }
        );
        const textNode = walker.nextNode();
        if (textNode) {
            try {
                const range = document.createRange();
                const length = (textNode.nodeValue || "").length;
                range.setStart(textNode, 0);
                range.setEnd(textNode, Math.min(1, length));
                const rect = range.getBoundingClientRect();
                if (rect && Number.isFinite(rect.left) && rect.width >= 0) return rect.left;
            } catch (_) { }
        }

        // Images or other non-text content may be the first thing in a list item.
        for (const child of Array.from(li.children)) {
            if (child.tagName === "OL" || child.tagName === "UL") continue;
            const rect = child.getBoundingClientRect();
            if (rect && rect.width > 0) return rect.left;
        }
        const rect = li.getBoundingClientRect();
        return Number.isFinite(rect.left) ? rect.left : null;
    }

    function markerListItemFromPoint(event) {
        if (!config.isPreview) return null;
        const root = document.getElementById("enhanced-clozes");
        if (!root) return null;

        const x = Number(event.clientX);
        const y = Number(event.clientY);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;

        const matches = [];
        for (const li of Array.from(root.querySelectorAll("ol > li"))) {
            if (!root.contains(li) || !li.parentElement || li.parentElement.tagName !== "OL") continue;
            const style = window.getComputedStyle(li);
            if (String(style.listStyleType || "").toLowerCase() === "none") continue;

            const liRect = li.getBoundingClientRect();
            if (liRect.bottom < 0 || liRect.top > window.innerHeight) continue;
            const fontSize = Number.parseFloat(style.fontSize || "16") || 16;
            let lineHeight = Number.parseFloat(style.lineHeight || "");
            if (!Number.isFinite(lineHeight)) lineHeight = fontSize * 1.45;

            const contentX = firstVisibleContentX(li);
            if (!Number.isFinite(contentX)) continue;
            const listRect = li.parentElement.getBoundingClientRect();

            // An ordered-list marker lives in the gutter between the OL's left
            // edge and the first-line content. Add only a few pixels on the text
            // side so ordinary right-clicks in the sentence are not destructive.
            const markerLeft = Math.min(listRect.left, contentX - Math.max(46, fontSize * 4.0));
            const markerRight = contentX + Math.max(4, fontSize * 0.25);
            const firstLineTop = liRect.top - 5;
            const firstLineBottom = liRect.top + lineHeight + 6;
            if (x < markerLeft || x > markerRight || y < firstLineTop || y > firstLineBottom) continue;

            // Prefer the marker whose expected center is closest to the click.
            // This disambiguates nested ordered lists whose first lines overlap.
            const markerCenter = contentX - Math.max(fontSize * 0.9, 12);
            const depth = (() => {
                let d = 0;
                let node = li.parentElement;
                while (node && node !== root) {
                    if (node.tagName === "OL") d += 1;
                    node = node.parentElement;
                }
                return d;
            })();
            matches.push({ li, score: Math.abs(x - markerCenter) - depth * 0.01 });
        }

        matches.sort((a, b) => a.score - b.score);
        return matches.length ? matches[0].li : null;
    }

    function reindexAfterWholeListItemDeletion(root, removedRegularIndexes) {
        const data = window.enhancedClozesData;
        if (data && removedRegularIndexes.length) {
            const descending = Array.from(new Set(removedRegularIndexes)).sort((a, b) => b - a);
            for (const removedIndex of descending) {
                for (const key of ["clozeId", "answers", "hints"]) {
                    if (Array.isArray(data[key]) && removedIndex >= 0 && removedIndex < data[key].length) {
                        data[key].splice(removedIndex, 1);
                    }
                }
            }
        }
        const regulars = Array.from(root.querySelectorAll(
            ".genuine-cloze:not(.nested-cloze), .pseudo-cloze:not(.nested-cloze)"
        ));
        regulars.forEach((element, index) => element.setAttribute("index", String(index)));
        wrapVisibleBracketNClozes(root);
        reindexBracketClozes(root);
    }

    function deleteWholeListItem(li, event) {
        if (controller.pending || !li || !li.isConnected) return;
        const info = listDeletionInfo(li);
        if (!info) return;

        const root = document.getElementById("enhanced-clozes");
        if (!root) return;
        const removedRegularIndexes = Array.from(li.querySelectorAll(
            ".genuine-cloze:not(.nested-cloze), .pseudo-cloze:not(.nested-cloze)"
        )).map((element) => Number.parseInt(element.getAttribute("index") || "", 10))
          .filter((index) => Number.isFinite(index));
        // Match the automatic "last cloze removed" path: do not perform any
        // explicit scroll restoration here. Removing the <li> in-place lets the
        // WebView keep its natural viewport, which is the behavior used by
        // applyRegularClozeDeletion()/applyBracketDeletion().

        event.preventDefault();
        event.stopImmediatePropagation();
        controller.pending = true;
        const message = config.messagePrefix + JSON.stringify({
            action: "delete-list-item",
            cardId: config.cardId,
            noteId: config.noteId,
            kind: config.kind,
            listItemIndex: info.index,
            listItemSignature: info.signature,
        });

        pycmd(message, (result) => {
            try {
                if (!result || result.ok !== true || !li.isConnected) return;
                li.remove();
                const source = document.getElementById("enhanced-cloze-content");
                if (source && typeof result.content === "string") source.innerHTML = result.content;
                reindexAfterWholeListItemDeletion(root, removedRegularIndexes);
                ensureListDeleteButtons(root);
                controller.hoveredCloze = null;
            } finally {
                controller.pending = false;
            }
        });
    }

    function onKeyDown(event) {
        if (controller.pending) return;
        if (event.key !== "Backspace" || event.ctrlKey || event.metaKey || event.altKey) return;
        const cloze = controller.hoveredCloze;

        const active = document.activeElement;
        if (active && active !== document.body &&
            (active.matches("input, textarea") || active.isContentEditable)) return;

        const deleteButton = controller.hoveredListDeleteButton;
        if (config.isPreview && deleteButton && deleteButton.isConnected) {
            const li = deleteButton.closest("li");
            if (li && li.tagName === "LI") {
                deleteWholeListItem(li, event);
                controller.hoveredListDeleteButton = null;
                return;
            }
        }

        if (!cloze || !cloze.isConnected) return;
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
        installCloseAllButton();
        installListDeleteButtonStyle();
        ensureListDeleteButtons(root);
        addListener(document, "mouseover", onMouseOver, true);
        addListener(document, "mouseout", onMouseOut, true);
        addListener(document, "pointerdown", (event) => {
            cancelRestoreFromUserInput();
            if (deleteButtonFromTarget(event.target)) return;
            const cloze = visibleDeletableFromTarget(event.target);
            if (cloze) rememberNextCloze(cloze);
        }, true);
        addListener(document, "click", (event) => {
            if (!deleteButtonFromTarget(event.target)) return;
            // The × is a hover target only. Deletion is performed with Backspace.
            event.preventDefault();
            event.stopImmediatePropagation();
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
            ensureListDeleteButtons(root);
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


def _on_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    if not message.startswith(_MESSAGE_PREFIX):
        return handled
    if not isinstance(context, (Previewer, Reviewer)):
        return handled

    try:
        data = json.loads(message[len(_MESSAGE_PREFIX) :])
        if isinstance(context, Previewer):
            card = context.card()
        else:
            card = context.card
        if card is None:
            return (True, {"ok": False, "reason": "no-card"})
        if int(data["cardId"]) != int(card.id) or int(data["noteId"]) != int(card.nid):
            return (True, {"ok": False, "reason": "stale-card"})
        if not _is_enhanced_cloze_card(card):
            return (True, {"ok": False, "reason": "wrong-note-type"})

        editor = None
        if isinstance(context, Previewer):
            note, editor = _note_for_preview_context(context, card)
        else:
            note = card.note()

        action = str(data.get("action", "unwrap"))
        if action == "delete-list-item":
            # Marker-click deletion is deliberately a Browser Preview-only tool.
            # Backspace cloze unwrapping remains available in both Preview/Review.
            if not isinstance(context, Previewer):
                return (True, {"ok": False, "reason": "preview-only"})
            result = delete_list_item_at_index(
                note["Content"],
                int(data.get("listItemIndex", -1)),
                str(data.get("listItemSignature", "")),
            )
        else:
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
            return (True, {"ok": False, "reason": "target-not-found"})

        note["Content"] = result.text
        if isinstance(context, Previewer):
            _suppress_same_card_reload(context, int(card.id))

        # Direct collection save keeps the current WebView DOM intact. Preview
        # additionally refreshes the Browser editor/rows while suppressing the
        # same-card preview redraw that would reset scrolling.
        changes = note.col.update_note(note)
        note.load()

        if isinstance(context, Previewer):
            if editor is not None:
                _reload_browser_editor(editor)
            try:
                gui_hooks.operation_did_execute(changes, editor or context)
            except Exception as error:
                print(f"Enhanced Cloze companion browser refresh hook failed: {error}")
        else:
            # Reviewer must use the new note text when the user flips to the
            # answer side, but the currently visible side must not redraw.
            try:
                card.load()
            except Exception as error:
                print(f"Enhanced Cloze companion reviewer card reload failed: {error}")

        response: dict[str, Any] = {
            "ok": True,
            "content": result.text,
            "listItemDeleted": result.list_item_deleted,
        }
        parent_index = int(data.get("parentClozeIndex", -1))
        if action != "delete-list-item" and str(data.get("scopeKind", "")) == "outer-cloze" and parent_index >= 0:
            outer_answer = cloze_answer_at_index(result.text, parent_index)
            if outer_answer is not None:
                response["outerAnswer"] = outer_answer
        return (True, response)
    except Exception as error:
        print(f"Enhanced Cloze companion operation failed: {error}")
        return (True, {"ok": False, "reason": "exception"})


def setup_enhanced_cloze_companion() -> None:
    gui_hooks.card_will_show.append(_on_card_will_show)
    gui_hooks.previewer_did_init.append(_patch_previewer)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)
