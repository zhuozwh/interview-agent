"use strict";

(function initializeSafeFormatter(root) {
  const MAX_FORMAT_CHARACTERS = 16_000;

  function appendText(tokens, value) {
    if (!value) {
      return;
    }
    const previous = tokens[tokens.length - 1];
    if (previous?.type === "text") {
      previous.value += value;
      return;
    }
    tokens.push({ type: "text", value });
  }

  function parseInline(value) {
    const text = String(value ?? "").slice(0, MAX_FORMAT_CHARACTERS);
    const tokens = [];
    let cursor = 0;
    while (cursor < text.length) {
      const boldStart = text.indexOf("**", cursor);
      const codeStart = text.indexOf("`", cursor);
      const candidates = [boldStart, codeStart].filter((position) => position >= 0);
      if (!candidates.length) {
        appendText(tokens, text.slice(cursor));
        break;
      }

      const markerStart = Math.min(...candidates);
      appendText(tokens, text.slice(cursor, markerStart));
      if (markerStart === boldStart) {
        const markerEnd = text.indexOf("**", markerStart + 2);
        if (markerEnd > markerStart + 2) {
          tokens.push({ type: "strong", value: text.slice(markerStart + 2, markerEnd) });
          cursor = markerEnd + 2;
          continue;
        }
        appendText(tokens, "**");
        cursor = markerStart + 2;
        continue;
      }

      const markerEnd = text.indexOf("`", markerStart + 1);
      if (markerEnd > markerStart + 1) {
        tokens.push({ type: "code", value: text.slice(markerStart + 1, markerEnd) });
        cursor = markerEnd + 1;
        continue;
      }
      appendText(tokens, "`");
      cursor = markerStart + 1;
    }
    return tokens;
  }

  function parseBlocks(value) {
    const normalized = String(value ?? "")
      .slice(0, MAX_FORMAT_CHARACTERS)
      .replace(/\r\n?/g, "\n");
    const blocks = [];
    let paragraphLines = [];

    function flushParagraph() {
      if (!paragraphLines.length) {
        return;
      }
      blocks.push({ type: "paragraph", tokens: parseInline(paragraphLines.join("\n")) });
      paragraphLines = [];
    }

    for (const rawLine of normalized.split("\n")) {
      const line = rawLine.trimEnd();
      if (!line.trim()) {
        flushParagraph();
        continue;
      }
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      const unordered = line.match(/^\s*[-+]\s+(.+)$/);
      const listType = ordered ? "ordered-list" : unordered ? "unordered-list" : null;
      if (!listType) {
        paragraphLines.push(line);
        continue;
      }

      flushParagraph();
      const content = ordered ? ordered[1] : unordered[1];
      const previous = blocks[blocks.length - 1];
      if (previous?.type === listType) {
        previous.items.push(parseInline(content));
      } else {
        blocks.push({ type: listType, items: [parseInline(content)] });
      }
    }
    flushParagraph();
    return blocks;
  }

  const formatter = Object.freeze({ parseBlocks, parseInline });
  root.InterviewAgentSafeFormat = formatter;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = formatter;
  }
})(typeof window === "undefined" ? globalThis : window);
