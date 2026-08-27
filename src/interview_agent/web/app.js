"use strict";

const state = {
  sessionId: null,
  currentTitle: "新会话",
  turns: [],
  sending: false,
  historyInfo: null,
};

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
    const assistantText = turn.answer || turn.error_message || statusMessage(turn.status);
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

  const body = document.createElement("p");
  body.className = "answer-text";
  body.textContent = text || "没有可显示的内容。";
  if (turn && turn.status && !["success", "no_evidence", "policy_refused"].includes(turn.status)) {
    body.classList.add("error");
  }
  content.append(meta, body);

  if (turn && Array.isArray(turn.citations) && turn.citations.length) {
    const citations = document.createElement("div");
    citations.className = "citation-list";
    citations.setAttribute("aria-label", "回答引用");
    for (const citation of turn.citations) {
      const chip = document.createElement("span");
      chip.className = "citation-chip";
      const heading = citation.heading_path?.length ? ` · ${citation.heading_path.join(" / ")}` : "";
      chip.textContent = `[${citation.citation_id}] ${citation.relative_path}${heading} · L${citation.start_line}-${citation.end_line}`;
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

function statusMessage(status) {
  const messages = {
    no_evidence: "当前资料不足，无法给出有依据的回答。",
    policy_refused: "该请求超出当前允许的数据边界。",
    invalid_input: "输入不符合当前请求协议。",
    unsupported: "当前版本暂不支持这个请求。",
    tool_error: "本地检索暂时失败。",
    llm_error: "回答服务暂时失败。",
    invalid_output: "模型输出未通过引用或安全校验。",
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
    ? state.turns[state.turns.length - 1].question
    : null;
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
      throw new Error(body.detail);
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
      confidence: body.confidence,
      citations: body.citations || [],
      follow_up_questions: body.follow_up_questions || [],
    };
    if (body.history_status === "failed") {
      setNotice("回答已生成，但本地历史保存失败。请先复制回答，不要自动重试。修复配置后重启应用。");
    } else if (body.history_status === "disabled") {
      setNotice("本地历史已关闭；刷新页面后本轮内容不会恢复。");
    }
    renderConversation();
    await loadHistory();
    if (!response.ok && body.error?.message) {
      setNotice(body.error.message);
    }
  } catch (error) {
    state.turns[state.turns.length - 1] = {
      ...optimistic,
      answer: null,
      status: "internal_error",
      error_message: error.message || "请求失败，请检查本地服务状态。",
    };
    renderConversation();
    setNotice(error.message || "请求失败，请检查本地服务状态。");
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
for (const suggestion of document.querySelectorAll("[data-prompt]")) {
  suggestion.addEventListener("click", () => fillPrompt(suggestion.dataset.prompt));
}

renderConversation();
loadHistory();
