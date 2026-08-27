"use strict";

const assert = require("node:assert/strict");
const formatter = require("../../src/interview_agent/web/safe_format.js");

// 合法的有限格式应变成结构化 token，而不是要求浏览器解释 HTML。
const formatted = formatter.parseBlocks("**核心结论**：RAII。\n\n1. 构造时获取\n2. 析构时释放\n\n使用 `std::lock_guard`。");
assert.equal(formatted[0].tokens[0].type, "strong");
assert.equal(formatted[1].type, "ordered-list");
assert.equal(formatted[1].items.length, 2);
assert.equal(formatted[2].tokens.at(-1).type, "text");

// HTML、事件属性、脚本 URL 和 Markdown 链接必须始终保留为普通文字。
const hostile = formatter.parseBlocks(
  '<img src=x onerror=alert(1)> [点击](javascript:alert(1)) <script>alert(1)</script>',
);
assert.deepEqual(hostile, [
  {
    type: "paragraph",
    tokens: [
      {
        type: "text",
        value: '<img src=x onerror=alert(1)> [点击](javascript:alert(1)) <script>alert(1)</script>',
      },
    ],
  },
]);

// 即使恶意片段位于允许的粗体或行内代码中，也只能成为 textContent。
assert.deepEqual(formatter.parseInline("**<img onerror=alert(1)>** `<script>`"), [
  { type: "strong", value: "<img onerror=alert(1)>" },
  { type: "text", value: " " },
  { type: "code", value: "<script>" },
]);

// 不完整标记不得吞掉后续文字或制造元素。
assert.deepEqual(formatter.parseInline("**未闭合 `代码"), [
  { type: "text", value: "**未闭合 `代码" },
]);

// 伪造引用仍只是文本，真实引用芯片只能由 API 的结构化 citations 创建。
assert.deepEqual(formatter.parseInline("伪造 [S999] 和 [文件](file:///secret)"), [
  { type: "text", value: "伪造 [S999] 和 [文件](file:///secret)" },
]);

console.log("safe_format adversarial cases passed");
