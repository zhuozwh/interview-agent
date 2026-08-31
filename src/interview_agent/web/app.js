"use strict";

const state = {
  sessionId: null,
  currentTitle: "新会话",
  turns: [],
  sending: false,
  historyInfo: null,
  manualRetryQuestion: null,
  evidenceRequestSequence: 0,
};

const NON_FAILURE_STATUSES = new Set([
  "pending",
  "success",
  "no_evidence",
  "policy_refused",
]);
const RETRYABLE_ERROR_CODES = new Set([
  "incomplete_llm_output",
  "llm_connection_failed",
  "llm_invalid_response",
  "llm_rate_limited",
  "llm_service_unavailable",
  "llm_timeout",
]);

const elements = {
  sidebar: document.querySelector("#sidebar"),
  sidebarScrim: document.querySelector("#sidebar-scrim"),
  mobileMenu: document.querySelector("#mobile-menu"),
  historyList: document.querySelector("#history-list"),
  refreshHistory: document.querySelector("#refresh-history"),
  newChat: document.querySelector("#new-chat"),
  clearHistory: document.querySelector("#clear-history"),
  privacyButton: document.querySelector("#privacy-button"),
  privacyDialog: document.querySelector("#privacy-dialog"),
  historyPath: document.querySelector("#history-path"),
  historyRetention: document.querySelector("#history-retention"),
  historyLimits: document.querySelector("#history-limits"),
  sessionTitle: document.querySelector("#session-title"),
  welcome: document.querySelector("#welcome"),
  messageList: document.querySelector("#message-list"),
  composer: document.querySelector("#composer"),
  question: document.querySelector("#question"),
  send: document.querySelector("#send"),
  notice: document.querySelector("#notice"),
  evidenceScrim: document.querySelector("#evidence-scrim"),
  evidencePanel: document.querySelector("#evidence-panel"),
  evidenceClose: document.querySelector("#evidence-close"),
  evidenceStatus: document.querySelector("#evidence-status"),
  evidenceDetails: document.querySelector("#evidence-details"),
  evidenceNamespace: document.querySelector("#evidence-namespace"),
  evidencePath: document.querySelector("#evidence-path"),
  evidenceHeading: document.querySelector("#evidence-heading"),
  evidenceLines: document.querySelector("#evidence-lines"),
  evidenceScore: document.querySelector("#evidence-score"),
  evidenceContent: document.querySelector("#evidence-content"),
  evidenceTruncated: document.querySelector("#evidence-truncated"),
};

function setNotice(message) {
  elements.notice.textContent = message || "";
  elements.notice.hidden = !message;
}

function setSending(sending) {
  state.sending = sending;
  elements.send.disabled = sending;
  elements.question.disabled = sending;
  elements.send.querySelector("span").textContent = sending ? "…" : "↑";
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "时间未知";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function closeSidebar() {
  elements.sidebar.classList.remove("open");
  elements.sidebarScrim.hidden = true;
}

function openSidebar() {
  elements.sidebar.classList.add("open");
  elements.sidebarScrim.hidden = false;
}

function closeEvidence() {
  state.evidenceRequestSequence += 1;
  elements.evidencePanel.hidden = true;
  elements.evidenceScrim.hidden = true;
}

function showEvidencePanel() {
  closeSidebar();
  elements.evidencePanel.hidden = false;
  elements.evidenceScrim.hidden = false;
  elements.evidencePanel.focus();
}

function setEvidenceLoading(citation) {
  elements.evidenceDetails.hidden = true;
  elements.evidenceStatus.hidden = false;
  elements.evidenceStatus.classList.remove("error");
  elements.evidenceStatus.textContent = `正在读取 [${citation.citation_id}] 的当前本地原文…`;
}

function setEvidenceError(message) {
  elements.evidenceDetails.hidden = true;
  elements.evidenceStatus.hidden = false;
  elements.evidenceStatus.classList.add("error");
  elements.evidenceStatus.textContent = message;
}

function renderEvidence(evidence) {
  const namespaceLabels = {
    notes: "知识笔记",
    projects: "项目资料",
    resume: "简历资料",
  };
  elements.evidenceNamespace.textContent = namespaceLabels[evidence.source_namespace]
    || evidence.source_namespace;
  elements.evidencePath.textContent = evidence.relative_path;
  elements.evidenceHeading.textContent = evidence.heading_path?.length
    ? evidence.heading_path.join(" / ")
    : "未登记标题路径";
  elements.evidenceLines.textContent = `原引用 L${evidence.citation_start_line}-${evidence.citation_end_line} · 当前展示 L${evidence.excerpt_start_line}-${evidence.excerpt_end_line}`;
  elements.evidenceScore.textContent = Number.isFinite(evidence.score)
    ? `${evidence.score.toFixed(4)}（相关性，不是正确率）`
    : "未知";
  // 证据正文必须作为纯文本渲染，不能解释其中的 HTML 或脚本。
  elements.evidenceContent.textContent = evidence.content || "当前引用位置没有可显示的文本。";
  elements.evidenceTruncated.hidden = evidence.truncated !== true;
  elements.evidenceStatus.hidden = true;
  elements.evidenceDetails.hidden = false;
}

async function openEvidence(turn, citation) {
  if (!state.sessionId || !turn?.trace_id || !citation?.citation_id) {
    return;
  }
  showEvidencePanel();
  setEvidenceLoading(citation);
  const requestSequence = ++state.evidenceRequestSequence;
  const endpoint = [state.sessionId, turn.trace_id, citation.citation_id]
    .map((value) => encodeURIComponent(value))
    .join("/");
  try {
    const response = await fetch(`/api/evidence/${endpoint}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(evidenceErrorMessage(response.status));
    }
    if (requestSequence !== state.evidenceRequestSequence) {
      return;
    }
    renderEvidence(body);
  } catch (error) {
    if (requestSequence !== state.evidenceRequestSequence) {
      return;
    }
    setEvidenceError(error.message || "无法读取这条本地证据。");
  }
}

function evidenceErrorMessage(status) {
  const messages = {
    404: "这条已保存引用不存在，可能已被删除或清理。",
    410: "引用对应的本地文件或行号已经变化，当前无法安全读取。",
    422: "这条引用身份无效，页面不会尝试读取文件。",
    503: "本地历史或证据服务暂不可用，请检查启动窗口。",
  };
  return messages[status] || "无法读取这条本地证据。";
}

function resizeTextarea() {
  elements.question.style.height = "auto";
  elements.question.style.height = `${Math.min(elements.question.scrollHeight, 150)}px`;
}

function renderConversation() {
  elements.sessionTitle.textContent = state.currentTitle;
  elements.welcome.hidden = state.turns.length > 0;
  elements.messageList.replaceChildren();
  for (const turn of state.turns) {
    elements.messageList.append(createMessage("user", turn.question, turn.created_at));
    const assistantText = turn.answer || userFacingError(turn);
    elements.messageList.append(
      createMessage("assistant", assistantText, turn.created_at, turn),
    );
  }
}

function createMessage(role, text, createdAt, turn = null) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "你" : "IA";
  avatar.setAttribute("aria-hidden", "true");

  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const author = document.createElement("strong");
  author.textContent = role === "user" ? "你" : "Interview Agent";
  const time = document.createElement("span");
  time.textContent = formatTime(createdAt || new Date().toISOString());
  meta.append(author, time);

  const body = document.createElement("div");
  body.className = "answer-text";
  const failed = Boolean(turn?.status && !NON_FAILURE_STATUSES.has(turn.status));
  if (role === "assistant" && !failed) {
    appendSafeFormattedAnswer(body, text || "没有可显示的内容。");
  } else {
    body.textContent = text || "没有可显示的内容。";
  }
  if (failed) {
    body.classList.add("error");
  }
  content.append(meta, body);

  if (turn?.confidence && turn.confidence !== "not_applicable") {
    content.append(createConfidenceBadge(turn.confidence));
  }

  if (failed) {
    content.append(createErrorActions(turn));
  }

  if (turn && Array.isArray(turn.citations) && turn.citations.length) {
    const citations = document.createElement("div");
    citations.className = "citation-list";
    citations.setAttribute("aria-label", "回答引用");
    for (const citation of turn.citations) {
      const evidenceAvailable = turn.evidence_available === true
        && Boolean(state.sessionId)
        && Boolean(turn.trace_id);
      const chip = document.createElement(evidenceAvailable ? "button" : "span");
      chip.className = "citation-chip";
      const heading = citation.heading_path?.length ? ` · ${citation.heading_path.join(" / ")}` : "";
      chip.textContent = `[${citation.citation_id}] ${citation.relative_path}${heading} · L${citation.start_line}-${citation.end_line}`;
      if (evidenceAvailable) {
        chip.type = "button";
        chip.title = "打开当前本地 Markdown 原文核对引用";
        chip.addEventListener("click", () => openEvidence(turn, citation));
      } else {
        chip.classList.add("unavailable");
        chip.title = "本轮未成功保存到本地历史，无法通过受控身份重新打开证据。";
      }
      citations.append(chip);
    }
    content.append(citations);
  }

  if (turn && Array.isArray(turn.follow_up_questions) && turn.follow_up_questions.length) {
    const followUps = document.createElement("div");
    followUps.className = "follow-ups";
    for (const question of turn.follow_up_questions) {
      const button = document.createElement("button");
      button.className = "follow-up";
      button.type = "button";
      button.textContent = question;
      button.addEventListener("click", () => fillPrompt(question));
      followUps.append(button);
    }
    content.append(followUps);
  }

  article.append(avatar, content);
  return article;
}

function appendSafeFormattedAnswer(container, text) {
  const formatter = window.InterviewAgentSafeFormat;
  if (!formatter || typeof formatter.parseBlocks !== "function") {
    container.textContent = text;
    return;
  }
  const blocks = formatter.parseBlocks(text);
  if (!blocks.length) {
    container.textContent = "没有可显示的内容。";
    return;
  }
  for (const block of blocks) {
    if (block.type === "ordered-list" || block.type === "unordered-list") {
      const list = document.createElement(block.type === "ordered-list" ? "ol" : "ul");
      for (const item of block.items) {
        const listItem = document.createElement("li");
        appendInlineTokens(listItem, item);
        list.append(listItem);
      }
      container.append(list);
      continue;
    }
    const paragraph = document.createElement("p");
    appendInlineTokens(paragraph, block.tokens || []);
    container.append(paragraph);
  }
}

function appendInlineTokens(container, tokens) {
  for (const token of tokens) {
    if (token.type === "strong") {
      const strong = document.createElement("strong");
      strong.textContent = token.value;
      container.append(strong);
    } else if (token.type === "code") {
      const code = document.createElement("code");
      code.textContent = token.value;
      container.append(code);
    } else {
      container.append(document.createTextNode(token.value));
    }
  }
}

function createConfidenceBadge(confidence) {
  const labels = {
    high: "证据匹配：高",
    medium: "证据匹配：中，请核对引用",
    low: "证据匹配：低，仅作参考",
  };
  const badge = document.createElement("span");
  badge.className = `confidence-badge confidence-${confidence}`;
  badge.textContent = labels[confidence] || "证据匹配：未知";
  badge.title = "表示本轮检索证据强度，不是模型正确率；仍应核对引用内容。";
  return badge;
}

function createErrorActions(turn) {
  const wrapper = document.createElement("div");
  wrapper.className = "error-actions";
  if (isRetryableTurn(turn)) {
    const retry = document.createElement("button");
    retry.className = "retry-button";
    retry.type = "button";
    retry.textContent = "手动重试";
    retry.title = "重新检索并再次调用回答模型，可能产生一次费用";
    retry.addEventListener("click", () => requestManualRetry(turn));
    wrapper.append(retry);
  }

  const details = document.createElement("details");
  details.className = "error-details";
  const summary = document.createElement("summary");
  summary.textContent = "诊断信息";
  const diagnostics = document.createElement("p");
  const fields = [];
  if (turn.error_code) {
    fields.push(`错误码：${turn.error_code}`);
  }
  if (turn.trace_id) {
    fields.push(`追踪标识：${turn.trace_id}`);
  }
  diagnostics.textContent = fields.join("\n") || "没有可显示的诊断信息。";
  details.append(summary, diagnostics);
  wrapper.append(details);
  return wrapper;
}

function isRetryableTurn(turn) {
  return turn.error_retryable === true || RETRYABLE_ERROR_CODES.has(turn.error_code);
}

function requestManualRetry(turn) {
  if (state.sending || !turn?.question) {
    return;
  }
  const confirmed = window.confirm(
    "手动重试会重新检索，并可能产生一次远端模型调用和费用。是否继续？",
  );
  if (!confirmed) {
    return;
  }
  state.manualRetryQuestion = turn.question;
  fillPrompt(turn.question);
  elements.composer.requestSubmit();
}

function userFacingError(turn) {
  const messages = {
    incomplete_llm_output: "回答没有完整生成，系统未展示可能被截断的内容。请检查 LLM_MAX_TOKENS，或稍后手动重试。",
    llm_connection_failed: "无法连接回答模型。请检查网络、endpoint，并确认服务不是从受限环境启动。",
    llm_timeout: "回答模型超时；请求可能已被供应方接受，系统不会自动重复调用。",
    llm_authentication_failed: "回答模型拒绝了当前密钥或权限。请检查本机 .env 后重启。",
    llm_rate_limited: "回答模型当前限流。请稍后手动重试，不要连续提交。",
    llm_request_rejected: "回答模型拒绝了请求。请检查模型名称、endpoint 和兼容参数。",
    llm_service_unavailable: "回答模型服务暂时不可用。请稍后手动重试。",
    llm_invalid_response: "回答模型返回了无法安全解析的结果，本轮没有展示。",
    invalid_llm_response: "回答模型输出不符合本地安全合约，本轮没有展示。",
    service_unavailable: "本地运行时尚未准备好。请查看启动窗口中的检查结果并重启。",
    local_request_failed: "无法完成本地请求。请确认服务仍在运行后刷新页面。",
  };
  return messages[turn?.error_code] || statusMessage(turn?.status);
}

function statusMessage(status) {
  const messages = {
    no_evidence: "当前资料不足，无法给出有依据的回答。",
    policy_refused: "该请求超出当前允许的数据边界。",
    invalid_input: "输入不符合当前请求协议。",
    unsupported: "当前版本暂不支持这个请求。",
    tool_error: "本地检索暂时失败。",
    llm_error: "回答服务暂时失败。",
    invalid_output: "模型输出未通过引用、完整性或安全校验，本轮没有展示不可信内容。",
    internal_error: "应用内部处理失败，请根据追踪标识排查。",
  };
  return messages[status] || "请求没有返回可显示的回答。";
}

function fillPrompt(prompt) {
  elements.question.value = prompt;
  resizeTextarea();
  elements.question.focus();
}

async function loadHistory() {
  try {
    const response = await fetch("/api/history", { headers: { Accept: "application/json" } });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "无法读取本地历史。");
    }
    state.historyInfo = body;
    renderHistory(body.sessions || []);
    renderPrivacyInfo(body);
  } catch (error) {
    elements.historyList.replaceChildren(sidebarNote("本地历史暂不可用。请检查配置后重启。"));
    setNotice(error.message || "本地历史暂不可用。");
  }
}

function renderHistory(sessions) {
  elements.historyList.replaceChildren();
  if (!state.historyInfo?.enabled) {
    elements.historyList.append(sidebarNote("历史已在配置中关闭。"));
    return;
  }
  if (!sessions.length) {
    elements.historyList.append(sidebarNote("还没有保存的会话。"));
    return;
  }
  for (const session of sessions) {
    const wrapper = document.createElement("div");
    wrapper.className = "history-item";
    if (session.session_id === state.sessionId) {
      wrapper.classList.add("active");
    }
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "button");
    wrapper.setAttribute("aria-label", `打开会话：${session.title}`);

    const title = document.createElement("strong");
    title.textContent = session.title;
    const detail = document.createElement("small");
    detail.textContent = `${formatTime(session.updated_at)} · ${session.turn_count} 轮`;
    const remove = document.createElement("button");
    remove.className = "history-delete";
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `删除会话：${session.title}`);
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(session.session_id, session.title);
    });
    const open = () => openSession(session.session_id);
    wrapper.addEventListener("click", open);
    wrapper.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    wrapper.append(title, detail, remove);
    elements.historyList.append(wrapper);
  }
}

function sidebarNote(text) {
  const note = document.createElement("p");
  note.className = "sidebar-note";
  note.textContent = text;
  return note;
}

function renderPrivacyInfo(info) {
  elements.historyPath.textContent = info.enabled ? info.database_path : "历史已关闭";
  elements.historyRetention.textContent = info.enabled ? `${info.retention_days} 天` : "不保存";
  elements.historyLimits.textContent = info.enabled
    ? `${info.max_sessions} 会话 / 每会话 ${info.max_turns_per_session} 轮`
    : "不保存";
}

async function openSession(sessionId) {
  closeEvidence();
  setNotice("");
  try {
    const response = await fetch(`/api/history/${encodeURIComponent(sessionId)}`, {
      headers: { Accept: "application/json" },
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "无法读取会话。");
    }
    state.sessionId = body.session.session_id;
    state.currentTitle = body.session.title;
    state.turns = body.turns;
    renderConversation();
    await loadHistory();
    closeSidebar();
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  } catch (error) {
    setNotice(error.message || "无法读取会话。");
  }
}

function newConversation() {
  closeEvidence();
  state.sessionId = null;
  state.currentTitle = "新会话";
  state.turns = [];
  setNotice("");
  renderConversation();
  loadHistory();
  closeSidebar();
  elements.question.focus();
}

async function deleteSession(sessionId, title) {
  if (!window.confirm(`删除“${title}”的聊天正文？此操作无法撤销。`)) {
    return;
  }
  try {
    const response = await fetch(`/api/history/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      const body = await response.json();
      throw new Error(body.detail || "删除失败。");
    }
    if (state.sessionId === sessionId) {
      closeEvidence();
      state.sessionId = null;
      state.currentTitle = "新会话";
      state.turns = [];
      renderConversation();
    }
    await loadHistory();
  } catch (error) {
    setNotice(error.message || "删除失败。");
  }
}

async function clearHistory() {
  if (!window.confirm("清空全部聊天正文历史？此操作无法撤销。")) {
    return;
  }
  try {
    const response = await fetch("/api/history", { method: "DELETE" });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "清空失败。");
    }
    newConversation();
    setNotice(`已删除 ${body.deleted_sessions} 个本地会话。`);
  } catch (error) {
    setNotice(error.message || "清空失败。");
  }
}

async function submitQuestion(event) {
  event.preventDefault();
  if (state.sending) {
    return;
  }
  const question = elements.question.value.trim();
  if (!question) {
    elements.question.focus();
    return;
  }
  const previousQuestion = state.turns.length
    && state.manualRetryQuestion !== question
    ? state.turns[state.turns.length - 1].question
    : null;
  state.manualRetryQuestion = null;
  const optimisticTime = new Date().toISOString();
  const optimistic = {
    question,
    answer: "正在检索当前资料并校验引用…",
    status: "pending",
    created_at: optimisticTime,
    citations: [],
    follow_up_questions: [],
  };
  state.turns.push(optimistic);
  if (state.currentTitle === "新会话") {
    state.currentTitle = question.length <= 48 ? question : `${question.slice(0, 47)}…`;
  }
  elements.question.value = "";
  resizeTextarea();
  setNotice("");
  setSending(true);
  renderConversation();
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });

  const payload = { question };
  if (state.sessionId) {
    payload.session_id = state.sessionId;
  }
  if (previousQuestion) {
    payload.previous_question = previousQuestion;
  }

  try {
    const response = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (body.detail) {
      const serviceError = new Error(body.detail);
      serviceError.code = "service_unavailable";
      throw serviceError;
    }
    state.sessionId = body.session_id || state.sessionId;
    state.turns[state.turns.length - 1] = {
      trace_id: body.trace_id,
      created_at: optimisticTime,
      question,
      answer: body.answer,
      status: body.status,
      intent: body.intent,
      error_code: body.error?.code || null,
      error_message: body.error?.message || null,
      error_retryable: body.error?.retryable === true,
      confidence: body.confidence,
      citations: body.citations || [],
      follow_up_questions: body.follow_up_questions || [],
      evidence_available: body.history_status === "saved",
    };
    if (body.history_status === "failed") {
      setNotice("回答已生成，但本地历史保存失败。请先复制回答，不要自动重试。修复配置后重启应用。");
    } else if (body.history_status === "disabled") {
      setNotice("本地历史已关闭；刷新页面后本轮内容不会恢复。");
    }
    renderConversation();
    await loadHistory();
  } catch (error) {
    state.turns[state.turns.length - 1] = {
      ...optimistic,
      answer: null,
      status: "internal_error",
      error_code: error.code || "local_request_failed",
      error_message: error.message || "请求失败，请检查本地服务状态。",
      error_retryable: false,
    };
    renderConversation();
    setNotice("");
  } finally {
    setSending(false);
    elements.question.focus();
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }
}

elements.composer.addEventListener("submit", submitQuestion);
elements.question.addEventListener("input", resizeTextarea);
elements.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.composer.requestSubmit();
  }
});
elements.newChat.addEventListener("click", newConversation);
elements.refreshHistory.addEventListener("click", loadHistory);
elements.clearHistory.addEventListener("click", clearHistory);
elements.privacyButton.addEventListener("click", () => elements.privacyDialog.showModal());
elements.mobileMenu.addEventListener("click", openSidebar);
elements.sidebarScrim.addEventListener("click", closeSidebar);
elements.evidenceClose.addEventListener("click", closeEvidence);
elements.evidenceScrim.addEventListener("click", closeEvidence);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.evidencePanel.hidden) {
    closeEvidence();
  }
});
for (const suggestion of document.querySelectorAll("[data-prompt]")) {
  suggestion.addEventListener("click", () => fillPrompt(suggestion.dataset.prompt));
}

renderConversation();
loadHistory();
