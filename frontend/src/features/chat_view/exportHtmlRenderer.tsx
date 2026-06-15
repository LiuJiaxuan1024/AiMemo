import { renderToStaticMarkup } from "react-dom/server";

import { getCodeHighlighter, supportedCodeBlockLanguages } from "../../shared/editor/codeHighlighter";
import { ExportConversationView } from "./ExportConversationView";
import type { ConversationExportSnapshot, ConversationMultiExportSnapshot } from "./types";

type ExportRenderableSnapshot = ConversationExportSnapshot | ConversationMultiExportSnapshot;

export async function buildConversationExportHtml(snapshot: ExportRenderableSnapshot): Promise<string> {
  const safeSnapshot = sanitizeExportSnapshot(snapshot);
  const staticMarkup = renderToStaticMarkup(<ExportConversationView snapshot={safeSnapshot} />);
  const mermaidMarkup = await renderExportMermaidBlocks(staticMarkup);
  const copyableMarkup = ensureExportCodeCopyButtons(mermaidMarkup);
  const highlightedMarkup = await highlightExportCodeBlocks(copyableMarkup);
  const markup = ensureExportCodeScrollContainers(highlightedMarkup);
  const title = exportDocumentTitle(safeSnapshot);
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${escapeHtml(title)} - AiMemo 对话导出</title>
  <style>${EXPORT_VIEW_CSS}</style>
</head>
<body>
  <div id="root">${markup}</div>
  <script type="application/json" id="aimemo-export-data">${jsonScript(safeSnapshot)}</script>
  <script>${EXPORT_INTERACTION_JS}</script>
</body>
</html>`;
}

async function highlightExportCodeBlocks(markup: string): Promise<string> {
  if (typeof document === "undefined") {
    return markup;
  }
  const template = document.createElement("template");
  template.innerHTML = markup;
  const codeNodes = Array.from(template.content.querySelectorAll<HTMLElement>(".markdown-code-block pre > code"))
    .filter((codeNode) => {
      const block = codeNode.closest(".markdown-code-block");
      return !isMermaidCodeBlock(block, codeNode);
    });
  if (codeNodes.length === 0) {
    return markup;
  }

  let highlighter: Awaited<ReturnType<typeof getCodeHighlighter>> | null = null;
  for (const codeNode of codeNodes) {
    const codeText = codeNode.textContent ?? "";
    if (!codeText.trim()) {
      continue;
    }
    const language = resolveExportHighlightLanguage(codeNode.className);
    try {
      highlighter ??= await getCodeHighlighter();
      const highlighted = highlighter.codeToHtml(codeText, {
        lang: language,
        theme: "github-dark",
      });
      const wrapper = document.createElement("div");
      wrapper.className = "markdown-code-highlight";
      wrapper.innerHTML = highlighted;
      codeNode.closest("pre")?.replaceWith(wrapper);
    } catch {
      // 保留原始 pre/code，导出仍然可读。
    }
  }
  return template.innerHTML;
}

function ensureExportCodeScrollContainers(markup: string): string {
  if (typeof document === "undefined") {
    return markup;
  }
  const template = document.createElement("template");
  template.innerHTML = markup;
  template.content.querySelectorAll<HTMLElement>(".markdown-code-block").forEach((block) => {
    if (block.classList.contains("markdown-code-block--mermaid")) {
      return;
    }
    if (block.querySelector(":scope > .markdown-code-scroll")) {
      return;
    }
    const scroll = document.createElement("div");
    scroll.className = "markdown-code-scroll";
    const toolbar = block.querySelector(":scope > .markdown-code-block__toolbar");
    const movableChildren = Array.from(block.children).filter((child) => child !== toolbar);
    for (const child of movableChildren) {
      scroll.appendChild(child);
    }
    block.appendChild(scroll);
  });
  return template.innerHTML;
}

async function renderExportMermaidBlocks(markup: string): Promise<string> {
  if (typeof document === "undefined") {
    return markup;
  }
  const template = document.createElement("template");
  template.innerHTML = markup;
  const blocks = Array.from(template.content.querySelectorAll<HTMLElement>(".markdown-code-block"))
    .map((block) => {
      const sourceCode = block.querySelector<HTMLElement>(".markdown-mermaid-source code");
      const visibleCode = block.querySelector<HTMLElement>("pre > code");
      const explicitMermaidCode = block.querySelector<HTMLElement>("pre > code.language-mermaid");
      const codeNode = sourceCode ?? explicitMermaidCode ?? (isMermaidCodeBlock(block, visibleCode) ? visibleCode : null);
      return codeNode ? { block, codeNode } : null;
    })
    .filter((item): item is { block: HTMLElement; codeNode: HTMLElement } => item !== null);
  if (blocks.length === 0) {
    return markup;
  }

  let mermaid: Awaited<typeof import("mermaid")>["default"] | null = null;
  for (const [index, { block, codeNode }] of blocks.entries()) {
    const chart = codeNode.textContent?.replace(/\n$/, "") ?? "";
    if (!chart.trim()) {
      continue;
    }
    block.classList.add("markdown-code-block--mermaid");
    const toolbar = ensureCodeToolbar(block, "mermaid");
    ensureCopyButton(toolbar, "复制 Mermaid 源码");
    block.querySelectorAll(".mermaid-viewer, .markdown-mermaid-error, pre").forEach((node) => node.remove());
    const source = document.createElement("pre");
    source.className = "markdown-mermaid-source";
    source.hidden = true;
    source.setAttribute("aria-hidden", "true");
    source.innerHTML = `<code class="language-mermaid">${escapeHtml(chart)}</code>`;
    block.appendChild(source);

    try {
      mermaid ??= (await import("mermaid")).default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "loose",
        theme: "base",
        themeVariables: {
          fontFamily: "Inter, ui-sans-serif, system-ui",
          lineColor: "#98a2b3",
          primaryBorderColor: "#cbd5e1",
          primaryColor: "#f8fafc",
          textColor: "#1d2433",
        },
      });
      const result = await mermaid.render(`aimemo-export-mermaid-${index}`, chart);
      const viewer = document.createElement("div");
      viewer.className = "mermaid-viewer";
      viewer.innerHTML = [
        '<div class="mermaid-zoom-indicator">100%</div>',
        '<div class="mermaid-pan-surface">',
        `<div class="markdown-mermaid-svg">${result.svg}</div>`,
        "</div>",
      ].join("");
      block.appendChild(viewer);
    } catch (error) {
      const fallback = document.createElement("pre");
      fallback.className = "markdown-mermaid-error";
      fallback.textContent = error instanceof Error ? error.message : "Mermaid 渲染失败";
      block.appendChild(fallback);
    }
  }
  return template.innerHTML;
}

function ensureExportCodeCopyButtons(markup: string): string {
  if (typeof document === "undefined") {
    return markup;
  }
  const template = document.createElement("template");
  template.innerHTML = markup;
  template.content.querySelectorAll<HTMLElement>(".markdown-code-block").forEach((block) => {
    const language =
      block.querySelector(".markdown-code-block__language")?.textContent?.trim() ||
      resolveExportHighlightLanguage(block.querySelector("pre > code")?.className ?? "");
    const toolbar = ensureCodeToolbar(block, language || "code");
    ensureCopyButton(toolbar, language === "mermaid" ? "复制 Mermaid 源码" : "复制代码");
  });
  return template.innerHTML;
}

function ensureCodeToolbar(block: HTMLElement, language: string): HTMLElement {
  let toolbar = block.querySelector<HTMLElement>(".markdown-code-block__toolbar");
  if (!toolbar) {
    toolbar = document.createElement("div");
    toolbar.className = "markdown-code-block__toolbar";
    block.insertBefore(toolbar, block.firstChild);
  }
  let label = toolbar.querySelector<HTMLElement>(".markdown-code-block__language");
  if (!label) {
    label = document.createElement("span");
    label.className = "markdown-code-block__language";
    toolbar.insertBefore(label, toolbar.firstChild);
  }
  label.textContent = language || "code";
  return toolbar;
}

function exportCodeBlockLanguage(block: Element | null, codeNode?: Element | null): string {
  const labelLanguage = block
    ?.querySelector(".markdown-code-block__language")
    ?.textContent
    ?.trim()
    .toLowerCase();
  if (labelLanguage) {
    return labelLanguage;
  }
  const match = /(?:^|\s)language-([^\s]+)/.exec(codeNode?.className ?? "");
  return (match?.[1] ?? "").toLowerCase();
}

function isMermaidCodeBlock(block: Element | null, codeNode?: Element | null): boolean {
  if (!block) {
    return false;
  }
  if (block.classList.contains("markdown-code-block--mermaid")) {
    return true;
  }
  return exportCodeBlockLanguage(block, codeNode) === "mermaid";
}

function ensureCopyButton(toolbar: HTMLElement, label: string): void {
  if (toolbar.querySelector(".markdown-code-copy")) {
    return;
  }
  const button = document.createElement("button");
  button.className = "markdown-code-copy";
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = COPY_ICON_SVG;
  toolbar.appendChild(button);
}

const exportHighlightAliases = Object.entries(supportedCodeBlockLanguages).reduce<Record<string, string>>(
  (aliases, [language, registration]) => {
    aliases[language] = language;
    for (const alias of registration.aliases ?? []) {
      aliases[alias] = language;
    }
    return aliases;
  },
  {},
);

const COPY_ICON_SVG =
  '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>';

const CHECK_ICON_SVG =
  '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"></path></svg>';

function resolveExportHighlightLanguage(className: string): string {
  const match = /(?:^|\s)language-([^\s]+)/.exec(className);
  const rawLanguage = (match?.[1] ?? "text").toLowerCase();
  return exportHighlightAliases[rawLanguage] ?? "text";
}

export function conversationExportFilename(snapshot: ExportRenderableSnapshot): string {
  const title = exportDocumentTitle(snapshot);
  const safeTitle = title
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80) || "conversation";
  const exportedAt = isMultiSnapshot(snapshot) ? snapshot.exported_at : snapshot.conversation.exported_at;
  const stamp = exportedAt
    .replace(/[^\d]/g, "")
    .slice(0, 14);
  return `${safeTitle}-${stamp || "export"}.html`;
}

function jsonScript(snapshot: ExportRenderableSnapshot): string {
  return JSON.stringify(snapshot)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026");
}

function sanitizeExportSnapshot(snapshot: ExportRenderableSnapshot): ExportRenderableSnapshot {
  if (isMultiSnapshot(snapshot)) {
    return {
      ...snapshot,
      conversations: snapshot.conversations.map((conversation) =>
        sanitizeExportSnapshot(conversation) as ConversationExportSnapshot,
      ),
    };
  }
  return {
    ...snapshot,
    graphs: {},
    messages: snapshot.messages.map((message) => ({ ...message, graph_id: null })),
  };
}

function isMultiSnapshot(snapshot: ExportRenderableSnapshot): snapshot is ConversationMultiExportSnapshot {
  return "conversations" in snapshot;
}

function exportDocumentTitle(snapshot: ExportRenderableSnapshot): string {
  if (!isMultiSnapshot(snapshot)) {
    return snapshot.conversation.title.trim() || "conversation";
  }
  if (snapshot.conversations.length === 1) {
    return snapshot.conversations[0].conversation.title.trim() || "conversation";
  }
  return `AiMemo-${snapshot.conversations.length}-conversations`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const EXPORT_INTERACTION_JS = String.raw`
(function () {
  var exportData = null;

  function closestElement(target, selector) {
    return target instanceof Element ? target.closest(selector) : null;
  }

  function readExportData() {
    if (exportData) {
      return exportData;
    }
    var script = document.getElementById("aimemo-export-data");
    if (!script) {
      exportData = { graphs: {} };
      return exportData;
    }
    try {
      exportData = JSON.parse(script.textContent || "{}");
    } catch (error) {
      exportData = { graphs: {} };
    }
    return exportData;
  }

  function exportMessages() {
    var data = readExportData();
    if (Array.isArray(data.messages)) {
      return data.messages;
    }
    if (!Array.isArray(data.conversations)) {
      return [];
    }
    return data.conversations.reduce(function (messages, conversation) {
      if (conversation && Array.isArray(conversation.messages)) {
        return messages.concat(conversation.messages);
      }
      return messages;
    }, []);
  }

  function messageById(messageId) {
    var messages = exportMessages();
    for (var index = 0; index < messages.length; index += 1) {
      if (String(messages[index].id) === String(messageId)) {
        return messages[index];
      }
    }
    return null;
  }

  function normalizeMarkText(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function rangesOverlap(left, right) {
    return left.start < right.end && left.end > right.start;
  }

  function rangeFromThreadPosition(text, thread) {
    var position = thread.position;
    if (!position || position.start < 0 || position.end <= position.start || position.start >= text.length) {
      return null;
    }
    var end = Math.min(position.end, text.length);
    var slice = text.slice(position.start, end);
    if (normalizeMarkText(slice) !== normalizeMarkText(thread.original_text)) {
      return null;
    }
    return { start: position.start, end: end, thread: thread };
  }

  function rangeFromFirstTextMatch(text, thread) {
    var sourceText = String(thread.original_text || "");
    if (!sourceText) {
      return null;
    }
    var index = text.indexOf(sourceText);
    if (index < 0) {
      return null;
    }
    return { start: index, end: index + sourceText.length, thread: thread };
  }

  function resolveFollowupMarkRanges(text, threads) {
    var ranges = [];
    var sortedThreads = threads.slice().sort(function (left, right) {
      var leftStart = left.position ? left.position.start : Number.MAX_SAFE_INTEGER;
      var rightStart = right.position ? right.position.start : Number.MAX_SAFE_INTEGER;
      if (leftStart !== rightStart) {
        return leftStart - rightStart;
      }
      return String(right.original_text || "").length - String(left.original_text || "").length;
    });
    for (var index = 0; index < sortedThreads.length; index += 1) {
      var thread = sortedThreads[index];
      var range = rangeFromThreadPosition(text, thread) || rangeFromFirstTextMatch(text, thread);
      if (!range) {
        continue;
      }
      if (ranges.some(function (existing) { return rangesOverlap(existing, range); })) {
        continue;
      }
      ranges.push(range);
    }
    return ranges.sort(function (left, right) { return left.start - right.start; });
  }

  function markableTextNodes(root) {
    var nodes = [];
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var parent = node.parentElement;
        if (!parent || !node.textContent) {
          return NodeFilter.FILTER_REJECT;
        }
        if (!node.textContent.trim()) {
          return NodeFilter.FILTER_REJECT;
        }
        if (
          parent.closest(
            [
              "pre",
              "code",
              "button",
              "a",
              ".markdown-code-block__toolbar",
              ".segment-followup-mark",
              ".chat-message-attachments",
              ".chat-segment__tools",
              ".chat-segment-thoughts",
              ".chat-thought-recap",
              ".chat-tool-card",
              ".chat-tool-process-window"
            ].join(", ")
          )
        ) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var node = walker.nextNode();
    while (node) {
      nodes.push(node);
      node = walker.nextNode();
    }
    return nodes;
  }

  function hydrateSegmentFollowupMarks() {
    var messages = exportMessages();
    for (var messageIndex = 0; messageIndex < messages.length; messageIndex += 1) {
      var message = messages[messageIndex];
      var threads = (message.followup_threads || []).filter(function (thread) {
        return thread && String(thread.original_text || "").trim();
      });
      if (message.role !== "assistant" || threads.length === 0) {
        continue;
      }
      var body = document.querySelector('[data-export-message-body="' + String(message.id) + '"]');
      if (!body || body.getAttribute("data-export-followups-hydrated") === "true") {
        continue;
      }
      var nodeSpans = [];
      var textCursor = 0;
      markableTextNodes(body).forEach(function (textNode) {
        var text = textNode.textContent || "";
        if (!text) {
          return;
        }
        nodeSpans.push({ end: textCursor + text.length, node: textNode, start: textCursor, text: text });
        textCursor += text.length;
      });
      var ranges = resolveFollowupMarkRanges(
        nodeSpans.map(function (span) { return span.text; }).join(""),
        threads
      );
      if (ranges.length === 0) {
        body.setAttribute("data-export-followups-hydrated", "true");
        continue;
      }
      nodeSpans.forEach(function (span) {
        var marks = ranges.filter(function (range) {
          return range.start < span.end && range.end > span.start;
        });
        if (marks.length === 0) {
          return;
        }
        var localCursor = 0;
        var fragment = document.createDocumentFragment();
        marks.forEach(function (range) {
          var localStart = Math.max(0, range.start - span.start);
          var localEnd = Math.min(span.text.length, range.end - span.start);
          if (localEnd <= localStart) {
            return;
          }
          if (localStart > localCursor) {
            fragment.appendChild(document.createTextNode(span.text.slice(localCursor, localStart)));
          }
          var mark = document.createElement("span");
          mark.className = "segment-followup-mark";
          mark.setAttribute("role", "button");
          mark.setAttribute("tabindex", "0");
          mark.textContent = span.text.slice(localStart, localEnd);
          mark.title = "查看这个片段的追问";
          mark.setAttribute("aria-label", "查看片段追问：" + String(range.thread.original_text || ""));
          mark.setAttribute("data-export-followup-mark", String(message.id));
          mark.setAttribute("data-export-followup-segment", String(range.thread.segment_id || ""));
          fragment.appendChild(mark);
          localCursor = localEnd;
        });
        if (localCursor < span.text.length) {
          fragment.appendChild(document.createTextNode(span.text.slice(localCursor)));
        }
        span.node.replaceWith(fragment);
      });
      body.setAttribute("data-export-followups-hydrated", "true");
    }
  }

  function setActiveFollowupMarks(messageId, segmentId) {
    document.querySelectorAll(".segment-followup-mark[data-export-followup-mark]").forEach(function (mark) {
      mark.classList.toggle(
        "is-active",
        mark.getAttribute("data-export-followup-mark") === String(messageId) &&
          mark.getAttribute("data-export-followup-segment") === String(segmentId)
      );
    });
  }

  function followupThreadSource(messageId, segmentId) {
    var section = document.getElementById("followups-" + messageId);
    if (!section) {
      return null;
    }
    var threads = section.querySelectorAll("[data-export-followup-thread]");
    for (var index = 0; index < threads.length; index += 1) {
      if (threads[index].getAttribute("data-export-followup-thread") === String(segmentId)) {
        var wrapper = document.createElement("section");
        wrapper.className = "aimemo-export-followups aimemo-export-followups--single";
        var message = messageById(messageId);
        var title = document.createElement("header");
        title.innerHTML = '<h2>片段追问</h2>';
        wrapper.appendChild(title);
        wrapper.appendChild(threads[index].cloneNode(true));
        if (message && message.created_at) {
          var meta = document.createElement("p");
          meta.className = "aimemo-export-followup-origin";
          meta.textContent = "来自 " + message.created_at + " 的 AiMemo 回复";
          wrapper.appendChild(meta);
        }
        return wrapper;
      }
    }
    return section;
  }

  function openFollowupThread(messageId, segmentId) {
    var source = followupThreadSource(messageId, segmentId);
    if (!source) {
      return;
    }
    setActiveFollowupMarks(messageId, segmentId);
    openModal("片段追问", source);
  }

  function ensureModal() {
    var existing = document.querySelector(".aimemo-export-modal-backdrop");
    if (existing) {
      return existing;
    }
    var backdrop = document.createElement("div");
    backdrop.className = "aimemo-export-modal-backdrop";
    backdrop.hidden = true;
    backdrop.innerHTML = [
      '<section class="aimemo-export-modal" role="dialog" aria-modal="true" aria-label="导出详情">',
      '  <header class="aimemo-export-modal__header">',
      '    <div>',
      '      <h2></h2>',
      '      <p>当前回复中的局部讨论</p>',
      '    </div>',
      '    <button type="button" aria-label="关闭"><span aria-hidden="true">&times;</span></button>',
      '  </header>',
      '  <div class="aimemo-export-modal__body"></div>',
      '</section>'
    ].join("");
    document.body.appendChild(backdrop);
    backdrop.addEventListener("click", function (event) {
      if (event.target === backdrop || closestElement(event.target, ".aimemo-export-modal__header button")) {
        closeModal();
      }
    });
    return backdrop;
  }

  function openModal(title, source) {
    if (!source) {
      return;
    }
    var backdrop = ensureModal();
    var heading = backdrop.querySelector(".aimemo-export-modal__header h2");
    var body = backdrop.querySelector(".aimemo-export-modal__body");
    if (!heading || !body) {
      return;
    }
    heading.textContent = title;
    body.innerHTML = "";
    body.appendChild(source.cloneNode(true));
    initializeMermaidViewers(body);
    backdrop.classList.toggle(
      "aimemo-export-modal-backdrop--followups",
      source.classList && source.classList.contains("aimemo-export-followups"),
    );
    backdrop.hidden = false;
    document.body.classList.add("aimemo-export-modal-open");
  }

  function openImagePreview(button) {
    var src = button && button.getAttribute("data-export-image-preview");
    if (!src) {
      return;
    }
    var name = button.getAttribute("data-export-image-name") || "图片";
    var figure = document.createElement("figure");
    figure.className = "aimemo-export-image-preview";
    var image = document.createElement("img");
    image.alt = name;
    image.src = src;
    var caption = document.createElement("figcaption");
    caption.textContent = name;
    figure.appendChild(image);
    figure.appendChild(caption);
    openModal(name, figure);
  }

  function closeModal() {
    var backdrop = document.querySelector(".aimemo-export-modal-backdrop");
    if (!backdrop) {
      return;
    }
    backdrop.hidden = true;
    backdrop.classList.remove("aimemo-export-modal-backdrop--followups");
    document.body.classList.remove("aimemo-export-modal-open");
  }

  function toggleConversationSidebar() {
    document.body.classList.toggle("aimemo-export-sidebar-open");
  }

  function closeConversationSidebar() {
    document.body.classList.remove("aimemo-export-sidebar-open");
  }

  function selectConversation(conversationId) {
    if (!conversationId) {
      return;
    }
    document.querySelectorAll("[data-export-conversation]").forEach(function (section) {
      section.hidden = section.getAttribute("data-export-conversation") !== String(conversationId);
    });
    document.querySelectorAll("[data-export-conversation-card]").forEach(function (card) {
      card.classList.toggle(
        "chat-conv-card--active",
        card.getAttribute("data-export-conversation-card") === String(conversationId)
      );
    });
    var main = document.querySelector(".aimemo-export-chat-main");
    if (main) {
      main.scrollTop = 0;
    }
    closeConversationSidebar();
    closeModal();
  }

  var copyIconSvg = '${COPY_ICON_SVG}';
  var checkIconSvg = '${CHECK_ICON_SVG}';
  var initializedMermaidViewers = typeof WeakSet === "function" ? new WeakSet() : null;
  var mermaidMinScale = 0.35;
  var mermaidMaxScale = 3;
  var mermaidZoomStep = 1.18;

  function clampNumber(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function initializeMermaidViewers(root) {
    var scope = root || document;
    var viewers = Array.prototype.slice.call(scope.querySelectorAll(".mermaid-viewer"));
    viewers.forEach(function (viewer) {
      if (initializedMermaidViewers && initializedMermaidViewers.has(viewer)) {
        return;
      }
      if (initializedMermaidViewers) {
        initializedMermaidViewers.add(viewer);
      }
      var surface = viewer.querySelector(".mermaid-pan-surface");
      var graph = viewer.querySelector(".markdown-mermaid-svg");
      var svg = graph ? graph.querySelector("svg") : null;
      var indicator = viewer.querySelector(".mermaid-zoom-indicator");
      if (!surface || !graph || !svg) {
        return;
      }
      graph.style.transform = "";
      var baseViewBox = readSvgViewBox(svg);
      var viewport = {
        scale: 1,
        x: baseViewBox.x,
        y: baseViewBox.y,
        width: baseViewBox.width,
        height: baseViewBox.height
      };
      var drag = {
        active: false,
        moved: false,
        pointerId: null,
        startX: 0,
        startY: 0,
        originX: baseViewBox.x,
        originY: baseViewBox.y
      };
      var touchPointers = {};
      var pinch = {
        active: false,
        startDistance: 1,
        startPoint: null,
        startScale: 1
      };

      function readSvgViewBox(currentSvg) {
        var rawViewBox = currentSvg.getAttribute("viewBox");
        if (rawViewBox) {
          var parts = rawViewBox.trim().split(/[\s,]+/).map(Number);
          if (parts.length === 4 && parts.every(function (part) { return Number.isFinite(part); }) && parts[2] > 0 && parts[3] > 0) {
            return { x: parts[0], y: parts[1], width: parts[2], height: parts[3] };
          }
        }
        var width = Number.parseFloat(currentSvg.getAttribute("width") || "") || currentSvg.clientWidth || 800;
        var height = Number.parseFloat(currentSvg.getAttribute("height") || "") || currentSvg.clientHeight || 420;
        try {
          var bbox = currentSvg.getBBox();
          if (bbox.width > 0 && bbox.height > 0) {
            return { x: bbox.x, y: bbox.y, width: bbox.width, height: bbox.height };
          }
        } catch (error) {
          // Some browsers can fail getBBox on exported SVG fragments.
        }
        return { x: 0, y: 0, width: width, height: height };
      }

      function renderViewport() {
        svg.setAttribute(
          "viewBox",
          viewport.x + " " + viewport.y + " " + viewport.width + " " + viewport.height
        );
        if (indicator) {
          indicator.textContent = Math.round(viewport.scale * 100) + "%";
        }
      }

      function pointToSvg(clientX, clientY) {
        var rect = svg.getBoundingClientRect();
        var pointerX = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
        var pointerY = rect.height > 0 ? (clientY - rect.top) / rect.height : 0.5;
        return {
          x: viewport.x + viewport.width * pointerX,
          y: viewport.y + viewport.height * pointerY,
          ratioX: pointerX,
          ratioY: pointerY
        };
      }

      function zoomAt(clientX, clientY, factor) {
        var nextScale = clampNumber(viewport.scale * factor, mermaidMinScale, mermaidMaxScale);
        var point = pointToSvg(clientX, clientY);
        var nextWidth = baseViewBox.width / nextScale;
        var nextHeight = baseViewBox.height / nextScale;
        viewport = {
          scale: nextScale,
          x: point.x - nextWidth * point.ratioX,
          y: point.y - nextHeight * point.ratioY,
          width: nextWidth,
          height: nextHeight
        };
        renderViewport();
      }

      function activeTouchPoints() {
        return Object.keys(touchPointers).map(function (pointerId) {
          return touchPointers[pointerId];
        });
      }

      function distanceBetween(left, right) {
        return Math.hypot(left.x - right.x, left.y - right.y);
      }

      function midpoint(left, right) {
        return {
          x: (left.x + right.x) / 2,
          y: (left.y + right.y) / 2
        };
      }

      function startPinchGesture() {
        var points = activeTouchPoints().slice(0, 2);
        if (points.length < 2) {
          return;
        }
        var center = midpoint(points[0], points[1]);
        pinch = {
          active: true,
          startDistance: Math.max(1, distanceBetween(points[0], points[1])),
          startPoint: pointToSvg(center.x, center.y),
          startScale: viewport.scale
        };
        drag.active = false;
        surface.classList.remove("is-dragging");
      }

      function updatePinchGesture() {
        var points = activeTouchPoints().slice(0, 2);
        if (!pinch.active || !pinch.startPoint || points.length < 2) {
          return false;
        }
        var center = midpoint(points[0], points[1]);
        var nextScale = clampNumber(
          pinch.startScale * (distanceBetween(points[0], points[1]) / pinch.startDistance),
          mermaidMinScale,
          mermaidMaxScale
        );
        var currentPoint = pointToSvg(center.x, center.y);
        var nextWidth = baseViewBox.width / nextScale;
        var nextHeight = baseViewBox.height / nextScale;
        viewport = {
          scale: nextScale,
          x: pinch.startPoint.x - nextWidth * currentPoint.ratioX,
          y: pinch.startPoint.y - nextHeight * currentPoint.ratioY,
          width: nextWidth,
          height: nextHeight
        };
        renderViewport();
        return true;
      }

      surface.addEventListener("wheel", function (event) {
        event.preventDefault();
        event.stopPropagation();
        zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? mermaidZoomStep : 1 / mermaidZoomStep);
      }, { passive: false });

      surface.addEventListener("pointerdown", function (event) {
        if (event.button !== 0) {
          return;
        }
        if (event.pointerType === "touch") {
          touchPointers[event.pointerId] = { x: event.clientX, y: event.clientY };
          if (activeTouchPoints().length >= 2) {
            startPinchGesture();
          }
        }
        drag = {
          active: event.pointerType !== "touch" || activeTouchPoints().length < 2,
          moved: false,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          originX: viewport.x,
          originY: viewport.y
        };
        surface.classList.add("is-dragging");
        event.preventDefault();
        event.stopPropagation();
        try {
          surface.setPointerCapture(event.pointerId);
        } catch (error) {
          // Pointer capture is best-effort in exported HTML.
        }
      });

      surface.addEventListener("pointermove", function (event) {
        if (event.pointerType === "touch" && touchPointers[event.pointerId]) {
          touchPointers[event.pointerId] = { x: event.clientX, y: event.clientY };
          if (updatePinchGesture()) {
            event.preventDefault();
            event.stopPropagation();
            return;
          }
        }
        if (!drag.active || drag.pointerId !== event.pointerId) {
          return;
        }
        var dx = event.clientX - drag.startX;
        var dy = event.clientY - drag.startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
          drag.moved = true;
        }
        var rect = svg.getBoundingClientRect();
        var svgDx = rect.width > 0 ? (dx / rect.width) * viewport.width : 0;
        var svgDy = rect.height > 0 ? (dy / rect.height) * viewport.height : 0;
        viewport.x = drag.originX - svgDx;
        viewport.y = drag.originY - svgDy;
        renderViewport();
        event.preventDefault();
        event.stopPropagation();
      });

      function finishPointer(event) {
        if (event.pointerType === "touch") {
          delete touchPointers[event.pointerId];
          if (activeTouchPoints().length < 2) {
            pinch.active = false;
          }
        }
        if (!drag.active || drag.pointerId !== event.pointerId) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        surface.classList.remove("is-dragging");
        if (!drag.moved) {
          zoomAt(event.clientX, event.clientY, event.ctrlKey ? 1 / mermaidZoomStep : mermaidZoomStep);
        }
        drag.active = false;
        try {
          surface.releasePointerCapture(event.pointerId);
        } catch (error) {
          // Pointer capture may not have been established.
        }
        event.preventDefault();
        event.stopPropagation();
      }

      surface.addEventListener("pointerup", finishPointer);
      surface.addEventListener("pointercancel", finishPointer);
      surface.addEventListener("dblclick", function (event) {
        viewport = {
          scale: 1,
          x: baseViewBox.x,
          y: baseViewBox.y,
          width: baseViewBox.width,
          height: baseViewBox.height
        };
        renderViewport();
        event.preventDefault();
        event.stopPropagation();
      });

      renderViewport();
    });
  }

  function codeTextForCopy(button) {
    var block = closestElement(button, ".markdown-code-block");
    if (!block) {
      return "";
    }
    var source = block.querySelector(".markdown-mermaid-source code");
    if (source) {
      return (source.textContent || "").replace(/\n$/, "");
    }
    var highlighted = block.querySelector(".markdown-code-highlight code");
    if (highlighted) {
      return (highlighted.textContent || "").replace(/\n$/, "");
    }
    var code = block.querySelector("pre > code");
    if (code) {
      return (code.textContent || "").replace(/\n$/, "");
    }
    return "";
  }

  function setCopyButtonState(button, copied) {
    var block = closestElement(button, ".markdown-code-block");
    var isMermaid = block && block.classList.contains("markdown-code-block--mermaid");
    var copyLabel = isMermaid ? "复制 Mermaid 源码" : "复制代码";
    button.classList.toggle("is-copied", copied);
    button.innerHTML = copied ? checkIconSvg : copyIconSvg;
    button.title = copied ? "已复制" : copyLabel;
    button.setAttribute("aria-label", copied ? "代码已复制" : copyLabel);
    if (copied) {
      window.setTimeout(function () {
        setCopyButtonState(button, false);
      }, 1400);
    }
  }

  function fallbackCopyText(text) {
    return new Promise(function (resolve, reject) {
      var textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.top = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      try {
        if (document.execCommand("copy")) {
          resolve();
        } else {
          reject(new Error("copy command failed"));
        }
      } catch (error) {
        reject(error);
      } finally {
        textarea.remove();
      }
    });
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () {
        return fallbackCopyText(text);
      });
    }
    return fallbackCopyText(text);
  }

  function copyCode(button) {
    var text = codeTextForCopy(button);
    if (!text) {
      return;
    }
    copyText(text)
      .then(function () {
        setCopyButtonState(button, true);
      })
      .catch(function () {
        button.classList.add("is-copy-failed");
        button.title = "复制失败，请手动选择代码";
        window.setTimeout(function () {
          button.classList.remove("is-copy-failed");
          button.title = "复制代码";
        }, 1800);
      });
  }

  document.addEventListener("click", function (event) {
    var toggleSidebarButton = closestElement(event.target, "[data-export-toggle-sidebar]");
    if (toggleSidebarButton) {
      event.preventDefault();
      toggleConversationSidebar();
      return;
    }
    var closeSidebarButton = closestElement(event.target, "[data-export-close-sidebar]");
    if (closeSidebarButton) {
      event.preventDefault();
      closeConversationSidebar();
      return;
    }
    var copyButton = closestElement(event.target, ".markdown-code-copy");
    if (copyButton) {
      event.preventDefault();
      copyCode(copyButton);
      return;
    }
    var conversationButton = closestElement(event.target, "[data-export-select-conversation]");
    if (conversationButton) {
      event.preventDefault();
      selectConversation(conversationButton.getAttribute("data-export-select-conversation"));
      return;
    }
    var imageButton = closestElement(event.target, "[data-export-image-preview]");
    if (imageButton) {
      event.preventDefault();
      openImagePreview(imageButton);
      return;
    }
    var followupMark = closestElement(event.target, ".segment-followup-mark[data-export-followup-mark]");
    if (followupMark) {
      event.preventDefault();
      openFollowupThread(
        followupMark.getAttribute("data-export-followup-mark"),
        followupMark.getAttribute("data-export-followup-segment")
      );
      return;
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeConversationSidebar();
    }
    var conversationButton = closestElement(event.target, "[data-export-select-conversation]");
    if (conversationButton && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      selectConversation(conversationButton.getAttribute("data-export-select-conversation"));
      return;
    }
    var imageButton = closestElement(event.target, "[data-export-image-preview]");
    if (imageButton && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      openImagePreview(imageButton);
      return;
    }
    var followupMark = closestElement(event.target, ".segment-followup-mark[data-export-followup-mark]");
    if (followupMark && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      openFollowupThread(
        followupMark.getAttribute("data-export-followup-mark"),
        followupMark.getAttribute("data-export-followup-segment")
      );
      return;
    }
    if (event.key === "Escape") {
      closeModal();
    }
  });

  hydrateSegmentFollowupMarks();
  initializeMermaidViewers(document);
})();
`;

const EXPORT_VIEW_CSS = `
:root {
  --color-bg-base: #faf7ef;
  --color-bg-surface: #fffdf8;
  --color-bg-sunken: #f3eddf;
  --color-bg-muted: #f6f0e5;
  --color-border-subtle: #eadfcd;
  --color-border-strong: #d9c8ad;
  --color-divider: #eee2d0;
  --color-brand-50: #edf8ef;
  --color-brand-100: #dff1e3;
  --color-brand-300: #a7cdb0;
  --color-brand-500: #6aa376;
  --color-brand-600: #5c9367;
  --color-brand-700: #3f764c;
  --color-brand-ink: #315f3b;
  --color-warning-bg: #fff2d7;
  --color-warning: #9a5a21;
  --color-danger-bg: #fdecec;
  --color-danger: #b94444;
  --color-text-strong: #29251f;
  --color-text-body: #3f3a33;
  --color-text-muted: #7c7163;
  --color-text-faint: #a39a8d;
  --color-text-on-brand: #ffffff;
  --font-family-base: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
  --font-family-display: Georgia, "Times New Roman", "Microsoft YaHei", serif;
  --font-family-mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  --font-size-xs: 12px;
  --font-size-sm: 13px;
  --font-size-base: 14px;
  --font-size-lg: 16px;
  --font-size-xl: 20px;
  --leading-base: 1.62;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --radius-xs: 4px;
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-pill: 999px;
  --shadow-sm: 0 8px 20px rgba(96, 75, 49, 0.06);
  --shadow-md: 0 16px 36px rgba(96, 75, 49, 0.11);
  --shadow-sticker: 0 12px 28px rgba(73, 117, 83, 0.18);
  --shadow-focus: 0 0 0 3px rgba(106, 163, 118, 0.2);
  background: var(--color-bg-base);
  color: var(--color-text-body);
  font-family: var(--font-family-base);
  font-size: var(--font-size-base);
  line-height: var(--leading-base);
}
* { box-sizing: border-box; }
html,
body,
#root { min-height: 100%; }
body {
  background: var(--color-bg-base);
  color: var(--color-text-body);
  margin: 0;
  overflow-y: auto;
}
button,
input,
textarea { font: inherit; }
button:focus-visible,
a:focus-visible,
summary:focus-visible {
  box-shadow: var(--shadow-focus);
  outline: none;
}
a { color: inherit; }
::selection {
  background: var(--color-brand-100);
  color: var(--color-brand-ink);
}
::-webkit-scrollbar {
  height: 10px;
  width: 10px;
}
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--color-border-strong);
  background-clip: content-box;
  border: 2px solid transparent;
  border-radius: var(--radius-pill);
}
::-webkit-scrollbar-thumb:hover {
  background: var(--color-brand-300);
  background-clip: content-box;
}
* {
  scrollbar-color: var(--color-border-strong) transparent;
  scrollbar-width: thin;
}
.aimemo-export-shell {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 100vh;
}
.aimemo-export-appbar {
  align-items: center;
  background: rgba(250, 247, 239, 0.92);
  border-bottom: 1px solid var(--color-divider);
  display: flex;
  gap: var(--space-5);
  justify-content: space-between;
  min-width: 0;
  padding: 10px 20px;
  position: sticky;
  top: 0;
  z-index: 30;
}
.aimemo-export-brand,
.aimemo-export-nav {
  align-items: center;
  display: flex;
}
.aimemo-export-brand {
  gap: var(--space-3);
  min-width: 0;
}
.aimemo-export-brand-mark {
  align-items: center;
  background: var(--color-brand-500);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sticker);
  color: var(--color-text-on-brand);
  display: inline-flex;
  font-family: var(--font-family-display);
  font-weight: 700;
  height: 34px;
  justify-content: center;
  width: 34px;
}
.aimemo-export-brand div {
  display: grid;
  gap: 2px;
}
.aimemo-export-brand strong {
  color: var(--color-text-strong);
  font-family: var(--font-family-display);
  font-size: var(--font-size-lg);
  letter-spacing: 0.04em;
}
.aimemo-export-brand small {
  color: var(--color-text-muted);
  font-size: var(--font-size-xs);
}
.aimemo-export-nav {
  background: var(--color-bg-sunken);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-pill);
  gap: var(--space-1);
  padding: var(--space-1);
}
.aimemo-export-nav span {
  border-radius: var(--radius-pill);
  color: var(--color-text-muted);
  display: inline-flex;
  font-size: var(--font-size-sm);
  font-weight: 650;
  min-height: 32px;
  padding: 0 var(--space-4);
  align-items: center;
}
.aimemo-export-nav span.active {
  background: var(--color-brand-500);
  box-shadow: var(--shadow-sm);
  color: var(--color-text-on-brand);
}
.chat-shell {
  background: var(--color-bg-base);
  display: grid;
  gap: var(--space-4);
  grid-template-columns: 260px minmax(0, 1fr);
  min-height: auto;
  min-width: 0;
  overflow: visible;
  padding: var(--space-4);
  position: relative;
}
.chat-sidebar,
.chat-main {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.chat-sidebar {
  background: var(--color-bg-sunken);
  display: grid;
  gap: var(--space-3);
  grid-template-rows: auto minmax(0, 1fr);
  max-height: calc(100vh - 104px);
  padding: var(--space-4);
  position: sticky;
  top: 84px;
}
.chat-sidebar header,
.chat-main-header {
  align-items: center;
  display: flex;
  gap: var(--space-3);
  justify-content: space-between;
}
.chat-sidebar h2,
.chat-main-header h2 {
  color: var(--color-text-strong);
  font-family: var(--font-family-display);
  font-size: var(--font-size-lg);
  font-weight: 650;
  letter-spacing: 0.02em;
  margin: 0;
}
.chat-sidebar-toggle {
  align-items: center;
  background: rgba(255, 252, 244, 0.96);
  border: 1px solid rgba(127, 92, 61, 0.22);
  color: var(--color-brand-700);
  cursor: pointer;
  justify-content: center;
}
.chat-sidebar-toggle {
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  box-shadow: var(--shadow-md);
  display: none;
  height: 44px;
  left: 0;
  padding: 0;
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 30px;
  z-index: 30;
}
.aimemo-export-sidebar-scrim {
  display: none;
}
.chat-sidebar-toggle:hover,
.chat-sidebar-toggle:focus-visible {
  background: var(--color-brand-50);
  color: var(--color-brand-ink);
  outline: none;
}
.aimemo-export-sidebar-icon-close {
  display: none;
}
body.aimemo-export-sidebar-open .aimemo-export-sidebar-toggle {
  background: var(--color-brand-500);
  color: var(--color-text-on-brand);
}
body.aimemo-export-sidebar-open .aimemo-export-sidebar-icon-open {
  display: none;
}
body.aimemo-export-sidebar-open .aimemo-export-sidebar-icon-close {
  display: block;
}
.compact-markdown {
  min-width: 0;
}
.compact-markdown__block,
.compact-markdown__list,
.compact-markdown__item {
  display: inline;
}
.compact-markdown__item + .compact-markdown__item::before {
  content: " · ";
}
.compact-markdown code {
  background: rgba(127, 92, 61, 0.1);
  border-radius: 4px;
  font-size: 0.92em;
  padding: 0 3px;
}
.compact-markdown strong {
  color: var(--color-text-strong);
  font-weight: 800;
}
.compact-markdown__link {
  color: var(--color-brand-700);
  font-weight: 700;
}
.chat-main-header p {
  color: var(--color-text-muted);
  font-size: var(--font-size-sm);
  margin: 3px 0 0;
}
.chat-conversation-list {
  align-content: start;
  display: grid;
  gap: 6px;
  min-height: 0;
  overflow: auto;
  padding: 4px 2px 8px;
  scrollbar-gutter: stable;
}
.chat-conv-card {
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-height: 92px;
  overflow: hidden;
  position: relative;
  transition:
    background 160ms ease,
    box-shadow 160ms ease,
    transform 160ms ease;
}
.chat-conv-card::before {
  background: transparent;
  border-radius: 2px;
  content: "";
  inset: 10px auto 10px 0;
  position: absolute;
  width: 3px;
}
.chat-conv-card--active {
  background: var(--color-brand-50);
  box-shadow: var(--shadow-sm);
}
.chat-conv-card--active::before {
  background: var(--color-brand-500);
}
.chat-conv-card__button {
  -webkit-appearance: none;
  align-items: flex-start;
  appearance: none;
  background: transparent;
  border: 0;
  border-radius: inherit;
  box-shadow: none;
  color: inherit;
  cursor: pointer;
  display: grid;
  gap: var(--space-3);
  grid-template-columns: auto minmax(0, 1fr);
  min-width: 0;
  padding: var(--space-3) var(--space-2) var(--space-3) var(--space-4);
  text-align: left;
  text-decoration: none;
  width: 100%;
}
.chat-conv-card__button:link,
.chat-conv-card__button:visited,
.chat-conv-card__button:hover,
.chat-conv-card__button:active {
  color: inherit;
  text-decoration: none;
}
.chat-conv-card__icon {
  align-items: center;
  background: var(--color-brand-500);
  border-radius: var(--radius-sm);
  display: inline-flex;
  height: 36px;
  justify-content: center;
  margin-top: 1px;
  width: 36px;
}
.chat-conv-card__icon svg {
  color: var(--color-text-on-brand);
  height: 16px;
  width: 16px;
}
.chat-conv-card__body {
  display: grid;
  gap: 4px;
  grid-template-rows: auto minmax(0, auto) auto;
  min-width: 0;
}
.chat-conv-card__title {
  color: var(--color-brand-ink);
  font-size: var(--font-size-base);
  font-weight: 650;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-conv-card__summary {
  color: var(--color-text-muted);
  display: -webkit-box;
  font-size: var(--font-size-sm);
  line-height: 1.4;
  max-height: 2.8em;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.chat-conv-card__meta {
  color: var(--color-text-faint);
  display: block;
  font-size: var(--font-size-xs);
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-main {
  display: grid;
  gap: var(--space-3);
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: auto auto auto;
  overflow: visible;
  padding: var(--space-5);
}
.aimemo-export-conversation {
  display: grid;
  gap: var(--space-3);
  grid-template-columns: minmax(0, 1fr);
  min-width: 0;
}
.aimemo-export-conversation[hidden] {
  display: none;
}
.chat-main-header {
  border-bottom: 1px solid var(--color-divider);
  padding-bottom: var(--space-3);
}
.aimemo-export-hero {
  align-items: flex-start;
  flex-direction: column;
  justify-content: flex-start;
  max-width: 100%;
  min-width: 0;
  width: 100%;
}
.aimemo-export-hero__title {
  max-width: 100%;
  min-width: 0;
  width: 100%;
}
.aimemo-export-summary {
  color: var(--color-text-muted);
  margin-top: var(--space-2);
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  width: 100%;
}
.aimemo-export-summary summary {
  cursor: pointer;
  font-size: var(--font-size-xs);
  font-weight: 800;
  width: fit-content;
}
.aimemo-export-summary-body {
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
}
.aimemo-export-summary[open] .aimemo-export-summary-body {
  animation: aimemo-export-summary-reveal 220ms cubic-bezier(0.2, 0, 0, 1);
}
.aimemo-export-summary-markdown {
  box-sizing: border-box;
  color: var(--color-text-body);
  font-size: var(--font-size-sm);
  line-height: 1.65;
  margin: 6px 0 0;
  max-width: 100%;
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: normal;
  word-break: break-word;
}
.aimemo-export-summary-markdown p,
.aimemo-export-summary-markdown ul,
.aimemo-export-summary-markdown ol {
  box-sizing: border-box;
  max-width: 100%;
  min-width: 0;
  margin: 6px 0 0;
  overflow-wrap: anywhere;
  white-space: normal;
  word-break: break-word;
}
.aimemo-export-summary-markdown * {
  box-sizing: border-box;
  max-width: 100%;
  overflow-wrap: anywhere;
  white-space: normal;
  word-break: break-word;
}
.aimemo-export-summary-markdown ul,
.aimemo-export-summary-markdown ol {
  padding-left: 1.25em;
}
.aimemo-export-summary-markdown li {
  display: list-item;
  min-width: 0;
}
.aimemo-export-summary-markdown code {
  white-space: normal;
}
@keyframes aimemo-export-summary-reveal {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.aimemo-export-meta {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  justify-content: flex-start;
  margin: var(--space-2) 0 0;
}
.aimemo-export-meta div {
  align-items: baseline;
  background: rgba(255, 249, 237, 0.78);
  border: 1px solid rgba(154, 117, 83, 0.24);
  border-radius: var(--radius-sm);
  display: inline-flex;
  flex: 0 0 auto;
  gap: 6px;
  min-height: 34px;
  padding: 0 var(--space-3);
  white-space: nowrap;
}
.aimemo-export-meta dt {
  color: var(--color-text-muted);
  font-size: var(--font-size-xs);
}
.aimemo-export-meta dd {
  color: var(--color-text-strong);
  font-weight: 700;
  margin: 0;
  overflow-wrap: normal;
  white-space: nowrap;
}
.chat-message-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 100%;
  min-height: 0;
  min-width: 0;
  overflow: visible;
  overscroll-behavior: auto;
  padding-right: var(--space-1);
}
.chat-message {
  align-items: flex-start;
  display: flex;
  gap: var(--space-2);
  min-width: 0;
  position: relative;
  width: 100%;
}
.chat-message.user {
  justify-content: flex-end;
}
.chat-message.assistant {
  justify-content: flex-start;
}
.aimemo-export-message-frame {
  display: contents;
}
.aimemo-export-message-meta {
  display: none;
}
.chat-message-bubble {
  align-items: flex-start;
  border-radius: var(--radius-md);
  display: block;
  flex: 0 1 auto;
  max-width: min(760px, 82%);
  min-width: 0;
  overflow-wrap: anywhere;
  padding: var(--space-3) var(--space-4);
  width: fit-content;
}
.chat-message.user .chat-message-bubble {
  background: var(--color-brand-500);
  border-bottom-right-radius: var(--radius-xs);
  box-shadow: var(--shadow-sticker);
  color: var(--color-text-on-brand);
}
.chat-message.assistant .chat-message-bubble {
  background: var(--color-bg-surface);
  border: 1px solid transparent;
  border-bottom-left-radius: var(--radius-xs);
  box-shadow: var(--shadow-sm);
  color: var(--color-text-body);
  max-width: min(760px, 100%);
  width: min(760px, 100%);
}
.chat-message-content {
  display: grid;
  gap: 8px;
  grid-template-columns: minmax(0, 1fr);
  max-width: 100%;
  min-width: 0;
  overflow: visible;
}
.chat-message-content p,
.markdown-message p,
.markdown-body p {
  line-height: 1.68;
  margin: 0 0 0.9em;
  overflow-wrap: anywhere;
}
.chat-message-content p:last-child,
.markdown-message p:last-child,
.markdown-body p:last-child {
  margin-bottom: 0;
}
.chat-answer-stream,
.chat-segment-timeline,
.chat-chronological-timeline {
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(0, 1fr);
  max-width: 100%;
  min-width: 0;
}
.chat-segment,
.chat-segment__tools {
  display: grid;
  gap: 8px;
  grid-template-columns: minmax(0, 1fr);
  max-width: 100%;
  min-width: 0;
}
.markdown-body h1,
.markdown-body h2,
.markdown-body h3,
.markdown-message h1,
.markdown-message h2,
.markdown-message h3 {
  color: var(--color-text-strong);
  font-family: var(--font-family-display);
  line-height: 1.3;
  margin: 1.1em 0 0.55em;
}
.markdown-body h1 { font-size: 24px; }
.markdown-body h2 { font-size: 21px; }
.markdown-body h3 { font-size: 17px; }
.markdown-message h1 { font-size: 24px; }
.markdown-message h2 { font-size: 21px; }
.markdown-message h3 { font-size: 17px; }
.markdown-body ul,
.markdown-body ol,
.markdown-message ul,
.markdown-message ol {
  margin: 0 0 0.9em;
  padding-left: 1.4em;
}
.markdown-body li + li,
.markdown-message li + li {
  margin-top: 0.25em;
}
.markdown-body table,
.markdown-message table {
  border-collapse: collapse;
  display: block;
  max-width: 100%;
  overflow: auto;
}
.markdown-body th,
.markdown-body td,
.markdown-message th,
.markdown-message td {
  border: 1px solid var(--color-border-subtle);
  padding: 6px 8px;
}
.markdown-message {
  max-width: 100%;
  min-width: 0;
  overflow-wrap: anywhere;
}
.markdown-code-block {
  background: #f7fbf5;
  border: 1px solid #dcebd9;
  border-radius: var(--radius-sm);
  margin: 12px 0;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
}
.markdown-code-block__toolbar {
  align-items: center;
  background: #edf6ee;
  border-bottom: 1px solid #dcebd9;
  color: #59725d;
  display: flex;
  font-size: var(--font-size-xs);
  font-weight: 800;
  justify-content: space-between;
  min-height: 34px;
  padding: 6px 9px;
}
.markdown-code-copy {
  align-items: center;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid #cfe4d4;
  border-radius: 6px;
  color: #58795e;
  cursor: pointer;
  display: inline-flex;
  height: 28px;
  justify-content: center;
  padding: 0;
  width: 28px;
}
.markdown-code-copy svg {
  height: 15px;
  width: 15px;
}
.markdown-code-copy.is-copied {
  background: var(--color-brand-100);
  border-color: var(--color-brand-300);
  color: var(--color-brand-700);
}
.markdown-code-copy.is-copy-failed {
  background: var(--color-danger-bg);
  border-color: #f3b6b6;
  color: var(--color-danger);
}
.markdown-code-scroll {
  max-height: min(62vh, 680px);
  max-width: 100%;
  min-width: 0;
  overflow: auto;
  scrollbar-gutter: stable both-edges;
}
.markdown-code-block pre {
  background: transparent;
  margin: 0;
  max-width: none;
  overflow: auto;
  padding: 12px 14px;
  white-space: pre;
  width: max-content;
  min-width: 100%;
}
.markdown-code-block--mermaid .mermaid-viewer {
  min-height: 0;
  position: relative;
}
.markdown-code-block--mermaid .mermaid-pan-surface {
  background: #f8fafc;
  border: 0;
  border-radius: 0;
  cursor: zoom-in;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  touch-action: none;
  user-select: none;
}
.markdown-code-block--mermaid .mermaid-pan-surface.is-dragging {
  cursor: grabbing;
}
.markdown-code-block--mermaid .mermaid-pan-surface svg,
.markdown-code-block--mermaid .mermaid-pan-surface svg * {
  user-drag: none;
  -webkit-user-drag: none;
  user-select: none;
}
.markdown-code-block--mermaid .mermaid-zoom-indicator {
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid #d0d5dd;
  border-radius: var(--radius-pill);
  color: #667085;
  font-size: var(--font-size-xs);
  line-height: 1;
  padding: 5px 8px;
  pointer-events: none;
  position: absolute;
  right: 10px;
  top: 10px;
  z-index: 1;
}
.markdown-mermaid-svg {
  min-width: max-content;
  padding: 16px;
  transform-origin: 0 0;
  will-change: transform;
}
.markdown-mermaid-svg svg {
  display: block;
  height: auto;
  max-width: none;
}
.markdown-mermaid-error {
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 0;
  color: #9a3412;
  margin: 0;
  overflow: auto;
  padding: 12px;
  white-space: pre-wrap;
}
.markdown-mermaid-source[hidden] {
  display: none !important;
}
.markdown-code-highlight {
  max-width: none;
  min-width: 100%;
  overflow: visible;
  width: max-content;
}
.markdown-code-highlight pre.shiki {
  background: #0f172a !important;
  border-radius: 0;
  margin: 0;
  max-width: none;
  min-width: 100%;
  overflow: auto;
  padding: 12px 14px;
  width: max-content;
}
.markdown-code-highlight code {
  display: block;
  min-width: max-content;
}
pre,
code {
  font-family: var(--font-family-mono);
}
code {
  font-size: 0.94em;
}
.chat-command-result {
  background: rgba(255, 253, 248, 0.86);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
  display: grid;
  gap: var(--space-3);
  min-width: min(520px, 100%);
  padding: var(--space-3);
}
.chat-command-result header,
.chat-command-result footer {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.chat-command-result header {
  justify-content: space-between;
}
.chat-command-result p {
  color: var(--color-text-body);
  margin: 0;
}
.chat-command-result code {
  background: var(--color-bg-sunken);
  border-radius: var(--radius-xs);
  color: var(--color-text-muted);
  font-family: var(--font-family-mono);
  font-size: 12px;
  padding: 3px 6px;
}
.chat-command-result__status {
  align-items: center;
  background: #ecfdf3;
  border: 1px solid #abefc6;
  border-radius: 999px;
  color: #067647;
  display: inline-flex;
  font-size: 12px;
  font-weight: 750;
  gap: 5px;
  padding: 4px 8px;
}
.chat-command-result.is-noop .chat-command-result__status {
  background: var(--color-bg-muted);
  border-color: var(--color-border-subtle);
  color: var(--color-text-muted);
}
.chat-command-result.is-failed .chat-command-result__status {
  background: #fff1f0;
  border-color: #fecaca;
  color: #b42318;
}
.chat-command-result.is-needs_input .chat-command-result__status,
.chat-command-result.is-pending_confirmation .chat-command-result__status {
  background: #fff7ed;
  border-color: #fed7aa;
  color: #b45309;
}
.chat-command-result dl {
  display: grid;
  gap: 7px;
  margin: 0;
}
.chat-command-result dl > div {
  display: grid;
  gap: var(--space-2);
  grid-template-columns: minmax(104px, 0.34fr) minmax(0, 1fr);
}
.chat-command-result dt {
  color: var(--color-text-muted);
  font-size: 13px;
}
.chat-command-result dd {
  color: var(--color-text-strong);
  font-size: 13px;
  font-weight: 650;
  margin: 0;
  overflow-wrap: anywhere;
}
.chat-command-result footer span {
  background: var(--color-bg-muted);
  border-radius: 999px;
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 700;
  padding: 3px 7px;
  text-transform: uppercase;
}
.chat-message-attachments {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.chat-message-attachment {
  align-items: center;
  background: rgba(255, 253, 248, 0.76);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  box-shadow: 0 8px 22px rgba(72, 55, 32, 0.08);
  color: inherit;
  cursor: pointer;
  display: inline-grid;
  font: inherit;
  gap: var(--space-2);
  grid-template-columns: minmax(0, 1fr) auto;
  max-width: min(360px, 100%);
  padding: var(--space-2);
  text-align: left;
  text-decoration: none;
}
.chat-message-attachment:hover {
  border-color: var(--color-brand-300);
}
.chat-message-attachment span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-message-attachment small {
  color: var(--color-text-muted);
}
.chat-message-attachment--image {
  grid-template-columns: 72px minmax(0, 1fr);
  width: min(320px, 100%);
}
.chat-message-attachment--image img {
  aspect-ratio: 1;
  border-radius: var(--radius-sm);
  background: var(--color-bg-sunken);
  display: block;
  object-fit: cover;
  width: 72px;
}
.chat-message.user .chat-message-attachment {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.28);
  box-shadow: none;
}
.chat-message.user .chat-message-attachment small {
  color: rgba(255, 255, 255, 0.72);
}
.aimemo-export-message-actions {
  display: grid;
  flex: 0 0 auto;
  gap: 6px;
  margin-top: 0;
}
.aimemo-export-message-actions button {
  align-items: center;
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: 6px;
  color: var(--color-text-body);
  cursor: pointer;
  display: inline-flex;
  font-size: 0;
  height: 34px;
  justify-content: center;
  padding: 0;
  position: relative;
  width: 34px;
}
.aimemo-export-message-actions button:hover {
  background: var(--color-brand-50);
  border-color: var(--color-brand-300);
  color: var(--color-brand-700);
}
.aimemo-export-message-actions svg {
  height: 16px;
  width: 16px;
}
.aimemo-export-message-actions button[data-export-action-has-items="true"]::after {
  background: #f97316;
  border: 2px solid #ffffff;
  border-radius: var(--radius-pill);
  box-shadow: 0 5px 12px rgba(249, 115, 22, 0.35);
  content: "";
  height: 12px;
  position: absolute;
  right: -4px;
  top: -4px;
  width: 12px;
}
.chat-input-bar {
  align-items: end;
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  display: grid;
  gap: var(--space-2);
  grid-template-columns: minmax(0, 1fr) auto;
  padding: var(--space-2);
}
.chat-input-bar textarea {
  background: transparent;
  border: 0;
  border-radius: var(--radius-md);
  color: var(--color-text-body);
  line-height: var(--leading-base);
  min-height: 44px;
  max-height: 140px;
  padding: var(--space-2) var(--space-3);
  resize: none;
}
.chat-input-bar textarea::placeholder {
  color: var(--color-text-faint);
}
.chat-input-bar button {
  align-items: center;
  background: var(--color-brand-500);
  border: 0;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  color: var(--color-text-on-brand);
  display: inline-flex;
  font-weight: 650;
  height: 44px;
  justify-content: center;
  min-height: 44px;
  padding: 0 var(--space-5);
}
.chat-input-bar textarea:disabled,
.chat-input-bar button:disabled {
  cursor: default;
  opacity: 0.68;
}
.segment-followup-mark {
  -webkit-box-decoration-break: clone;
  appearance: none;
  background: linear-gradient(180deg, rgba(250, 204, 21, 0.2) 0%, rgba(250, 204, 21, 0.42) 18%, rgba(250, 204, 21, 0.48) 78%, rgba(250, 204, 21, 0.24) 100%);
  border: 0;
  border-radius: 2px;
  box-decoration-break: clone;
  color: inherit;
  cursor: pointer;
  display: inline;
  font: inherit;
  line-height: inherit;
  margin: 0 0.03em;
  padding: 0 0.08em;
  text-align: inherit;
}
.segment-followup-mark:hover {
  background: linear-gradient(180deg, rgba(250, 204, 21, 0.28) 0%, rgba(250, 204, 21, 0.52) 18%, rgba(250, 204, 21, 0.6) 78%, rgba(250, 204, 21, 0.32) 100%);
}
.segment-followup-mark.is-active {
  background: linear-gradient(180deg, rgba(126, 211, 154, 0.22) 0%, rgba(126, 211, 154, 0.42) 18%, rgba(103, 176, 128, 0.52) 78%, rgba(126, 211, 154, 0.26) 100%);
  color: #35643d;
}
.markdown-code-block .segment-followup-mark {
  color: inherit;
  margin: 0;
  padding: 0 0.08em;
  text-shadow: none;
}
.aimemo-export-message > .aimemo-export-followups {
  display: none;
}
.aimemo-export-followups {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  min-width: 0;
  padding: var(--space-4);
}
.aimemo-export-followups > header {
  align-items: center;
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.aimemo-export-followups h2 {
  color: var(--color-text-strong);
  font-family: var(--font-family-display);
  font-size: var(--font-size-lg);
  margin: 0;
}
.segment-followup-panel__item {
  background: var(--color-bg-base);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  margin-top: var(--space-2);
  min-width: 0;
  overflow: hidden;
  padding: var(--space-3);
}
.segment-followup-panel__summary {
  align-items: center;
  cursor: pointer;
  display: grid;
  gap: 5px 7px;
  grid-template-columns: auto minmax(0, 1fr) auto;
  list-style: none;
  min-width: 0;
}
.segment-followup-panel__summary::-webkit-details-marker {
  display: none;
}
.segment-followup-panel__badge,
.segment-followup-panel__status {
  border-radius: var(--radius-pill);
  display: inline-flex;
  font-size: var(--font-size-xs);
  font-weight: 800;
  padding: 2px 8px;
  width: fit-content;
}
.segment-followup-panel__badge {
  background: var(--color-brand-100);
  color: var(--color-brand-700);
}
.segment-followup-panel__status {
  background: var(--color-bg-muted);
  color: var(--color-text-muted);
}
.segment-followup-panel__status--failed {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}
.segment-followup-panel__status--answered {
  background: #ecfdf3;
  color: #067647;
}
.segment-followup-panel__status--pending {
  background: #fff7ed;
  color: #b54708;
}
.segment-followup-panel__source-text {
  color: var(--color-text-muted);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.segment-followup-panel__summary strong {
  color: var(--color-text-strong);
  font-size: var(--font-size-sm);
  grid-column: 1 / -1;
  line-height: 1.45;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.segment-followup-panel__summary small {
  color: var(--color-text-muted);
  font-size: var(--font-size-xs);
  font-weight: 700;
  grid-column: 2 / -1;
  min-width: 0;
}
.segment-followup-thread-turns {
  display: grid;
  gap: var(--space-3);
  margin-top: var(--space-3);
  min-width: 0;
}
.segment-followup-turn {
  border-top: 1px solid var(--color-divider);
  display: grid;
  gap: var(--space-2);
  min-width: 0;
  padding-top: var(--space-3);
}
.segment-followup-turn__question span,
.segment-followup-turn__answer span,
.aimemo-export-followup-origin {
  color: var(--color-text-muted);
  display: block;
  font-size: var(--font-size-xs);
  font-weight: 800;
  margin-bottom: 4px;
}
.aimemo-export-followup-origin {
  margin: var(--space-3) 0 0;
}
.segment-followup-turn__question,
.segment-followup-turn__answer,
.segment-followup-turn__answer .markdown-message {
  min-width: 0;
  max-width: 100%;
}
.segment-followup-turn__question {
  display: grid;
  gap: 6px;
  grid-template-columns: auto minmax(0, 1fr);
}
.segment-followup-turn__question p {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}
.segment-followup-turn__answer .markdown-message,
.aimemo-export-modal__body .markdown-message {
  overflow-wrap: anywhere;
}
.aimemo-export-modal-open {
  overflow: hidden;
}
.aimemo-export-modal-backdrop {
  align-items: center;
  background: rgba(22, 18, 14, 0.58);
  display: flex;
  inset: 0;
  justify-content: center;
  padding: 24px;
  position: fixed;
  z-index: 999;
}
.aimemo-export-modal-backdrop[hidden] {
  display: none;
}
.aimemo-export-modal {
  background: var(--color-bg-base);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  box-shadow: 0 24px 80px rgba(20, 16, 10, 0.28);
  color: var(--color-text-body);
  display: flex;
  flex-direction: column;
  max-height: min(86vh, 920px);
  max-width: min(1040px, 94vw);
  min-width: 0;
  overflow: hidden;
  width: 100%;
}
.aimemo-export-modal__header {
  align-items: center;
  background: var(--color-bg-surface);
  border-bottom: 1px solid var(--color-border-subtle);
  display: flex;
  justify-content: space-between;
  padding: 12px 14px;
}
.aimemo-export-modal__header > div {
  min-width: 0;
}
.aimemo-export-modal__header h2 {
  color: var(--color-text-strong);
  font-family: var(--font-family-display);
  font-size: var(--font-size-lg);
  margin: 0;
}
.aimemo-export-modal__header p {
  color: var(--color-text-muted);
  display: none;
  font-size: var(--font-size-sm);
  margin: 2px 0 0;
}
.aimemo-export-modal__header button {
  align-items: center;
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: inline-flex;
  flex: 0 0 auto;
  font-size: 20px;
  height: 32px;
  justify-content: center;
  line-height: 1;
  min-height: 32px;
  padding: 0;
  width: 32px;
}
.aimemo-export-modal__body {
  min-width: 0;
  overflow: auto;
  padding: var(--space-4);
}
.aimemo-export-modal__body .aimemo-export-followups {
  display: block;
  margin: 0;
  max-width: 100%;
}
.aimemo-export-modal-backdrop--followups .aimemo-export-modal {
  border-color: #dbe6ff;
  max-width: calc(100vw - 56px);
  width: min(1040px, calc(100vw - 56px));
}
.aimemo-export-modal-backdrop--followups .aimemo-export-modal__header {
  background: #ffffff;
}
.aimemo-export-modal-backdrop--followups .aimemo-export-modal__header p {
  display: block;
}
.aimemo-export-modal-backdrop--followups .aimemo-export-modal__body {
  background: #fbfcff;
  padding: 10px;
}
.aimemo-export-modal-backdrop--followups .aimemo-export-modal__body .aimemo-export-followups {
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
  padding: 0;
}
.aimemo-export-modal-backdrop--followups .aimemo-export-modal__body .aimemo-export-followups > header {
  display: none;
}
.aimemo-export-modal-backdrop--followups .segment-followup-panel__item {
  background: #ffffff;
  border-color: #8ea6ff;
  border-radius: 8px;
  box-shadow: 0 0 0 2px rgba(124, 156, 255, 0.12);
  margin-top: 0;
  padding: 0;
}
.aimemo-export-modal-backdrop--followups .segment-followup-panel__summary {
  padding: 10px 11px;
}
.aimemo-export-modal-backdrop--followups .segment-followup-thread-turns {
  border-left: 3px solid #bfdbfe;
  margin: 0 11px 11px;
  max-height: min(62vh, 620px);
  overflow: auto;
  padding-left: 10px;
  padding-right: 4px;
  scrollbar-gutter: stable;
}
.aimemo-export-modal-backdrop--followups .segment-followup-turn {
  border-top: 0;
  padding-top: 0;
}
.aimemo-export-modal-backdrop--followups .segment-followup-turn + .segment-followup-turn {
  border-top: 1px solid #edf2f7;
  padding-top: 10px;
}
.aimemo-export-modal-backdrop--followups .aimemo-export-followup-origin {
  color: var(--color-text-muted);
  font-size: var(--font-size-xs);
  margin: 8px 2px 0;
}
.aimemo-export-image-preview {
  display: grid;
  gap: var(--space-3);
  margin: 0;
  place-items: center;
}
.aimemo-export-image-preview img {
  background: #111827;
  border-radius: var(--radius-sm);
  display: block;
  max-height: calc(86vh - 150px);
  max-width: min(960px, calc(94vw - 80px));
  object-fit: contain;
}
.aimemo-export-image-preview figcaption {
  color: var(--color-text-muted);
  font-size: var(--font-size-sm);
  max-width: min(960px, calc(94vw - 80px));
  overflow-wrap: anywhere;
  text-align: center;
}
@media (max-width: 980px) {
  .chat-shell {
    grid-template-columns: minmax(0, 1fr);
    overflow: visible;
  }
  .chat-sidebar-toggle {
    display: flex;
  }
  .aimemo-export-sidebar-toggle {
    position: fixed;
    top: 50vh;
    z-index: 81;
  }
  .chat-main-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .aimemo-export-meta {
    justify-content: flex-start;
  }
  .aimemo-export-sidebar-scrim {
    background: rgba(29, 36, 51, 0.32);
    border: 0;
    cursor: pointer;
    display: block;
    inset: 0;
    opacity: 0;
    padding: 0;
    pointer-events: none;
    position: fixed;
    transition:
      opacity 220ms var(--ease-standard),
      visibility 0s linear 220ms;
    visibility: hidden;
    z-index: 79;
  }
  .aimemo-export-sidebar {
    border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
    box-shadow: 18px 0 46px rgba(29, 36, 51, 0.18);
    display: grid;
    height: 100vh;
    left: 0;
    max-height: none;
    position: fixed;
    top: 0;
    transform: translateX(calc(-100% - 12px));
    transition: transform 220ms cubic-bezier(0.2, 0, 0, 1);
    width: min(360px, calc(100vw - 44px));
    will-change: transform;
    z-index: 80;
  }
  body.aimemo-export-sidebar-open .aimemo-export-sidebar {
    transform: translateX(0);
  }
  body.aimemo-export-sidebar-open .aimemo-export-sidebar-scrim {
    opacity: 1;
    pointer-events: auto;
    transition-delay: 0s;
    visibility: visible;
  }
  @media (prefers-reduced-motion: reduce) {
    .aimemo-export-sidebar,
    .aimemo-export-sidebar-scrim {
      transition-duration: 0ms;
    }
    .aimemo-export-summary[open] .aimemo-export-summary-body {
      animation: none;
    }
  }
  .chat-main {
    min-height: calc(100vh - 88px);
  }
}
@media (max-width: 780px) {
  .aimemo-export-appbar {
    align-items: flex-start;
    flex-direction: column;
    gap: var(--space-3);
  }
  .aimemo-export-nav {
    max-width: 100%;
    overflow: auto;
  }
  .chat-shell {
    padding: var(--space-3);
  }
  .chat-main {
    border-radius: var(--radius-md);
    padding: var(--space-3);
  }
  .chat-main-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .aimemo-export-meta {
    justify-content: flex-start;
  }
  .chat-message.user .chat-message-bubble,
  .chat-message.assistant .chat-message-bubble {
    max-width: 100%;
    width: 100%;
  }
  .aimemo-export-modal-backdrop {
    align-items: flex-start;
    padding: 76px 8px 14px;
  }
  .aimemo-export-modal {
    max-height: calc(100vh - 92px);
    max-width: calc(100vw - 16px);
  }
  .aimemo-export-modal-backdrop--followups .aimemo-export-modal {
    max-width: calc(100vw - 16px);
    width: calc(100vw - 16px);
  }
  .aimemo-export-modal__body {
    padding: 10px;
  }
  .aimemo-export-modal-backdrop--followups .segment-followup-thread-turns {
    max-height: calc(100vh - 282px);
  }
}
`;
