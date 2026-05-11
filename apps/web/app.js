let currentSessionId = null;
let currentDraft = null;
let currentDraftVersionId = null;
let currentVersions = [];
let activePollId = null;
const PANEL_STORAGE_KEY = "agentic-workflow-workspace-widths";

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  let body = null;
  try {
    body = await response.json();
  } catch (_error) {
    body = null;
  }

  if (!response.ok) {
    const message = body?.error || `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return body;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function loadSavedPanelWidths() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(PANEL_STORAGE_KEY) || "{}");
    if (saved.left) {
      document.documentElement.style.setProperty("--left-panel-width", saved.left);
    }
    if (saved.right) {
      document.documentElement.style.setProperty("--right-panel-width", saved.right);
    }
  } catch (_error) {
    // Ignore malformed saved layout state.
  }
}

function savePanelWidths(leftPercent, rightPercent) {
  window.localStorage.setItem(
    PANEL_STORAGE_KEY,
    JSON.stringify({
      left: `${leftPercent}%`,
      right: `${rightPercent}%`,
    }),
  );
}

function setupResizablePanels() {
  const workspace = document.getElementById("workspace");
  const handles = document.querySelectorAll("[data-resize-handle]");
  if (!workspace || window.innerWidth <= 1100) {
    return;
  }

  handles.forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const activeHandle = event.currentTarget;
      const workspaceRect = workspace.getBoundingClientRect();
      activeHandle.classList.add("is-active");
      activeHandle.setPointerCapture(event.pointerId);

      const onPointerMove = (moveEvent) => {
        const relativeX = moveEvent.clientX - workspaceRect.left;
        const leftPercent = clamp((relativeX / workspaceRect.width) * 100, 20, 55);
        const rightPercent = clamp(((workspaceRect.right - moveEvent.clientX) / workspaceRect.width) * 100, 20, 45);

        if (activeHandle.dataset.resizeHandle === "left") {
          document.documentElement.style.setProperty("--left-panel-width", `${leftPercent}%`);
          savePanelWidths(leftPercent, parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--right-panel-width")));
        } else {
          document.documentElement.style.setProperty("--right-panel-width", `${rightPercent}%`);
          savePanelWidths(parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--left-panel-width")), rightPercent);
        }
      };

      const stopDragging = () => {
        activeHandle.classList.remove("is-active");
        activeHandle.removeEventListener("pointermove", onPointerMove);
        activeHandle.removeEventListener("pointerup", stopDragging);
        activeHandle.removeEventListener("pointercancel", stopDragging);
      };

      activeHandle.addEventListener("pointermove", onPointerMove);
      activeHandle.addEventListener("pointerup", stopDragging);
      activeHandle.addEventListener("pointercancel", stopDragging);
    });
  });
}

function setStatus({ title, message, variant = "idle", busy = false }) {
  const banner = document.getElementById("statusBanner");
  banner.classList.remove("is-idle", "is-busy", "is-error");
  banner.classList.add(
    variant === "error" ? "is-error" : busy ? "is-busy" : "is-idle",
  );
  document.getElementById("statusTitle").textContent = title;
  document.getElementById("statusMessage").textContent = message;
}

function applyBusyState({ generate = false, refine = false, rerun = false, confirm = false }) {
  document.getElementById("loadButton").disabled = generate;
  document.getElementById("refineButton").disabled = refine;
  document.getElementById("rerunButton").disabled = rerun;
  document.getElementById("confirmButton").disabled = confirm;
}

async function pollSessionStatus(sessionId) {
  if (!sessionId) {
    return;
  }
  try {
    const status = await fetchJson(`/api/sessions/${sessionId}`);
    if (status.retrievalIntent) {
      renderIntent(status.retrievalIntent);
    }
    if (Array.isArray(status.evidence) && status.evidence.length > 0) {
      renderEvidence(status.evidence);
    }
    if (status.draft) {
      renderDraft(status.draft);
      renderVersionMeta();
    }
    if (status.status === "error") {
      setStatus({
        title: "Error",
        message: status.currentMessage || "The workflow failed.",
        variant: "error",
        busy: false,
      });
      return;
    }
    if (status.status !== "generated" && status.status !== "confirmed" && status.status !== "idle") {
      setStatus({
        title: "In Progress",
        message: status.currentMessage || status.currentPhase || "Working...",
        variant: "busy",
        busy: true,
      });
      return;
    }
    if (status.currentMessage) {
      setStatus({
        title: status.status === "confirmed" ? "Confirmed" : "Ready",
        message: status.currentMessage,
        variant: "idle",
        busy: false,
      });
    }
  } catch (_error) {
    // Ignore transient polling failures.
  }
}

function startStatusPolling(sessionId) {
  stopStatusPolling();
  activePollId = window.setInterval(() => {
    pollSessionStatus(sessionId);
  }, 700);
}

function stopStatusPolling() {
  if (activePollId !== null) {
    window.clearInterval(activePollId);
    activePollId = null;
  }
}

function renderEvidence(items) {
  const list = document.getElementById("evidenceList");
  list.innerHTML = "";
  if (!items || items.length === 0) {
    const node = document.createElement("li");
    node.className = "empty-state";
    node.textContent = "Waiting for code evidence...";
    list.appendChild(node);
    return;
  }
  items.forEach((item) => {
    const node = document.createElement("li");
    node.textContent = `${item.path}: ${item.suggestedChange}`;
    list.appendChild(node);
  });
}

function renderIntent(intent) {
  document.getElementById("intent").textContent = intent
    ? JSON.stringify(intent, null, 2)
    : "Waiting for retrieval intent...";
}

function renderQuestions(items) {
  const list = document.getElementById("questionList");
  list.innerHTML = "";
  if (!items || items.length === 0) {
    const node = document.createElement("li");
    node.className = "empty-state";
    node.textContent = "Waiting for open questions...";
    list.appendChild(node);
    return;
  }
  items.forEach((item) => {
    const node = document.createElement("li");
    const answer = item.answer ? ` Answer: ${item.answer}` : "";
    node.textContent = `${item.question} (${item.status}).${answer}`;
    list.appendChild(node);
  });
}

function renderDraft(draft) {
  currentDraft = draft;
  document.getElementById("draftEditor").value = draft?.raw_text || "";
  document.getElementById("draftWaiting").style.display = draft ? "none" : "block";
  renderQuestions(draft?.open_questions || []);
}

function syncDraftFromEditor() {
  if (!currentDraft) {
    return null;
  }
  const editorValue = document.getElementById("draftEditor").value;
  currentDraft = {
    ...currentDraft,
    raw_text: editorValue,
  };
  return currentDraft;
}

function renderVersionMeta() {
  const node = document.getElementById("versionMeta");
  if (!currentDraft) {
    node.textContent = "No draft generated yet.";
    return;
  }
  node.textContent = `Version ${currentDraft.version}${currentDraft.summary ? ` • ${currentDraft.summary}` : ""}`;
}

async function loadVersions() {
  if (!currentSessionId) {
    return;
  }
  const data = await fetchJson(`/api/sessions/${currentSessionId}/versions`);
  currentVersions = data.versions || [];
  currentDraftVersionId = data.currentDraftVersionId;
  renderVersions();
}

function renderVersions() {
  const list = document.getElementById("versionList");
  list.innerHTML = "";
  if (!currentVersions || currentVersions.length === 0) {
    const node = document.createElement("li");
    node.className = "empty-state";
    node.textContent = "No versions yet.";
    list.appendChild(node);
    return;
  }
  currentVersions.forEach((version) => {
    const node = document.createElement("li");

    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `Version ${version.version_number}`;
    const meta = document.createElement("span");
    meta.className = "version-meta";
    meta.textContent = `${version.source_type} • ${version.created_at}${version.id === currentDraftVersionId ? " • current" : ""}`;
    info.appendChild(title);
    info.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "version-actions";
    const restoreButton = document.createElement("button");
    restoreButton.textContent = "Restore";
    restoreButton.disabled = version.id === currentDraftVersionId;
    restoreButton.addEventListener("click", () => restoreVersion(version.id));
    actions.appendChild(restoreButton);

    node.appendChild(info);
    node.appendChild(actions);
    list.appendChild(node);
  });
}

function collectRefinePayload() {
  const userMessage = document.getElementById("refineInput").value;
  const answeredQuestions = (currentDraft?.open_questions || [])
    .filter((item) => item.status === "open" && userMessage.trim())
    .slice(0, 1)
    .map((item) => ({ id: item.id, answer: userMessage.trim() }));

  return { userMessage, answeredQuestions };
}

async function loadEpics() {
  const epics = await fetchJson("/api/epics");
  const select = document.getElementById("epicSelect");
  epics.forEach((epic) => {
    const option = document.createElement("option");
    option.value = epic.id;
    option.textContent = `${epic.id} - ${epic.title}`;
    select.appendChild(option);
  });
}

async function generate() {
  const epicId = document.getElementById("epicSelect").value;
  setStatus({ title: "In Progress", message: "Preparing session...", variant: "busy", busy: true });
  applyBusyState({ generate: true, refine: true, rerun: true, confirm: true });

  try {
    const epic = await fetchJson(`/api/epics/${epicId}`);
    document.getElementById("description").textContent = epic.description || "";
    document.getElementById("groundTruth").textContent = epic.parsedWhatToDo
      ? JSON.stringify(epic.parsedWhatToDo, null, 2)
      : "No historical reference available.";
    renderIntent(null);
    renderEvidence([]);
    currentDraft = null;
    currentDraftVersionId = null;
    currentVersions = [];
    document.getElementById("draftEditor").value = "";
    document.getElementById("draftWaiting").style.display = "block";
    document.getElementById("questionList").innerHTML = "";
    document.getElementById("versionList").innerHTML = "";
    renderVersionMeta();

    const session = await fetchJson("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ epicId }),
    });
    currentSessionId = session.sessionId;
    startStatusPolling(currentSessionId);

    const generated = await fetchJson(`/api/sessions/${currentSessionId}/generate`, {
      method: "POST",
      body: JSON.stringify({}),
    });

    renderIntent(generated.retrievalIntent);
    renderEvidence(generated.evidence);
    renderDraft(generated.draft);
    renderVersionMeta();
    await loadVersions();
    setStatus({ title: "Ready", message: "Draft generated.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
  }
}

async function refine() {
  if (!currentSessionId || !currentDraft) {
    return;
  }
  syncDraftFromEditor();
  const { userMessage, answeredQuestions } = collectRefinePayload();
  setStatus({ title: "In Progress", message: "Refining draft...", variant: "busy", busy: true });
  applyBusyState({ generate: true, refine: true, rerun: true, confirm: true });
  startStatusPolling(currentSessionId);

  try {
    const refined = await fetchJson(`/api/sessions/${currentSessionId}/refine`, {
      method: "POST",
      body: JSON.stringify({
        userMessage,
        answeredQuestions,
        currentDraft,
      }),
    });
    renderDraft(refined.draft);
    renderVersionMeta();
    await loadVersions();
    document.getElementById("refineInput").value = "";
    setStatus({ title: "Ready", message: "Draft refined.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
  }
}

async function rerunRetrieval() {
  if (!currentSessionId) {
    return;
  }
  syncDraftFromEditor();
  const { userMessage, answeredQuestions } = collectRefinePayload();
  setStatus({ title: "In Progress", message: "Re-running retrieval...", variant: "busy", busy: true });
  applyBusyState({ generate: true, refine: true, rerun: true, confirm: true });
  startStatusPolling(currentSessionId);

  try {
    const result = await fetchJson(`/api/sessions/${currentSessionId}/rerun-retrieval`, {
      method: "POST",
      body: JSON.stringify({
        userMessage,
        answeredQuestions,
        currentDraft,
      }),
    });
    renderIntent(result.retrievalIntent);
    renderEvidence(result.evidence);
    renderDraft(result.draft);
    renderVersionMeta();
    await loadVersions();
    document.getElementById("refineInput").value = "";
    setStatus({ title: "Ready", message: "Retrieval rerun completed.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
  }
}

async function restoreVersion(versionId) {
  if (!currentSessionId) {
    return;
  }
  setStatus({ title: "In Progress", message: "Restoring selected version...", variant: "busy", busy: true });
  applyBusyState({ generate: true, refine: true, rerun: true, confirm: true });
  startStatusPolling(currentSessionId);

  try {
    const result = await fetchJson(`/api/sessions/${currentSessionId}/restore/${versionId}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    currentDraftVersionId = result.currentDraftVersionId;
    renderDraft(result.draft);
    renderVersionMeta();
    await loadVersions();
    setStatus({ title: "Ready", message: "Version restored.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
  }
}

async function confirmDraft() {
  if (!currentSessionId) {
    return;
  }
  syncDraftFromEditor();
  setStatus({ title: "In Progress", message: "Confirming final draft...", variant: "busy", busy: true });
  applyBusyState({ generate: true, refine: true, rerun: true, confirm: true });
  startStatusPolling(currentSessionId);

  try {
    const result = await fetchJson(`/api/sessions/${currentSessionId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ currentDraft }),
    });
    document.getElementById("draftEditor").value = result.exportText;
    if (currentDraft) {
      currentDraft = {
        ...currentDraft,
        raw_text: result.exportText,
      };
    }
    renderVersionMeta();
    await loadVersions();
    setStatus({ title: "Confirmed", message: "Final draft confirmed.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
  }
}

document.getElementById("loadButton").addEventListener("click", generate);
document.getElementById("refineButton").addEventListener("click", refine);
document.getElementById("rerunButton").addEventListener("click", rerunRetrieval);
document.getElementById("confirmButton").addEventListener("click", confirmDraft);
document.getElementById("draftEditor").addEventListener("input", () => {
  syncDraftFromEditor();
  renderVersionMeta();
});

loadSavedPanelWidths();
setupResizablePanels();
loadEpics().catch((error) => {
  setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  document.getElementById("description").textContent = error.message;
});
