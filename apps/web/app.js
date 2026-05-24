let currentSessionId = null;
let currentDraft = null;
let currentDraftVersionId = null;
let currentActionGuide = null;
let currentActionGuideVersionId = null;
let currentVersions = [];
let activePollId = null;
let currentImportedEpic = null;
let workspaceMode = "iis_mode";
let actionGuideOutdated = false;
let confirmedIisVersionId = null;
const PANEL_STORAGE_KEY = "agentic-workflow-workspace-widths";

const jiraState = {
  hasSavedToken: false,
  connected: false,
  projects: [],
  filteredProjects: [],
  epics: [],
  filteredEpics: [],
  selectedProjectKey: "",
  selectedEpic: null,
};

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
          savePanelWidths(
            leftPercent,
            parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--right-panel-width")),
          );
        } else {
          document.documentElement.style.setProperty("--right-panel-width", `${rightPercent}%`);
          savePanelWidths(
            parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--left-panel-width")),
            rightPercent,
          );
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
  document.getElementById("confirmIisButton").disabled = confirm;
  document.getElementById("reopenIisButton").disabled = confirm;
}

function updateCurrentEpicBadge() {
  const badge = document.getElementById("currentEpicBadge");
  if (!currentImportedEpic) {
    badge.textContent = "Current Epic: None selected";
    badge.classList.add("is-empty");
    document.getElementById("loadButton").disabled = true;
    return;
  }

  badge.textContent = `Current Epic: ${currentImportedEpic.id}`;
  badge.classList.remove("is-empty");
  document.getElementById("loadButton").disabled = false;
}

function updateWorkspaceModeUI() {
  const confirmButton = document.getElementById("confirmIisButton");
  const reopenButton = document.getElementById("reopenIisButton");
  const refineHeading = document.getElementById("refineHeading");
  const refineInput = document.getElementById("refineInput");
  const refineButton = document.getElementById("refineButton");
  const actionGuideMeta = document.getElementById("actionGuideMeta");
  const actionGuideOutdatedNode = document.getElementById("actionGuideOutdated");
  const emptyState = document.getElementById("actionGuideEmptyState");
  const draftEditor = document.getElementById("draftEditor");
  const rerunButton = document.getElementById("rerunButton");

  refineButton.disabled =
    !currentSessionId ||
    (workspaceMode === "action_guide_mode" ? !currentActionGuide : !currentDraft);
  confirmButton.disabled = !currentSessionId || !currentDraft || workspaceMode === "action_guide_mode";
  reopenButton.disabled = !currentSessionId || workspaceMode !== "action_guide_mode";
  rerunButton.disabled = !currentSessionId || workspaceMode === "action_guide_mode";

  if (workspaceMode === "action_guide_mode") {
    refineHeading.textContent = "Refine Implementation Action Guide";
    refineInput.placeholder = "Add reviewer guidance or answer a question to refine the current action guide...";
    refineButton.textContent = "Refine Action Guide";
    confirmButton.classList.add("hidden");
    reopenButton.classList.remove("hidden");
    draftEditor.readOnly = true;
  } else {
    refineHeading.textContent = "Refine Implementation Intent Specification";
    refineInput.placeholder = "Add reviewer guidance or answer a question to refine the current IIS...";
    refineButton.textContent = "Refine IIS";
    confirmButton.classList.remove("hidden");
    reopenButton.classList.add("hidden");
    confirmButton.textContent = actionGuideOutdated
      ? "Confirm IIS and Regenerate Action Guide"
      : "Confirm IIS and Generate Action Guide";
    draftEditor.readOnly = false;
  }

  if (currentActionGuide && currentActionGuide.source_iis_version_id) {
    actionGuideMeta.textContent = `Generated from IIS Version ${currentActionGuide.source_iis_version_id}`;
  } else {
    actionGuideMeta.textContent = "";
  }

  actionGuideOutdatedNode.classList.toggle("hidden", !actionGuideOutdated);
  emptyState.classList.toggle("hidden", Boolean(currentActionGuide));
}

function setCurrentEpic(epic, sourceType, message) {
  currentImportedEpic = {
    ...epic,
    sourceType,
  };
  resetWorkspace({ showWaiting: false });
  renderDescription(currentImportedEpic.description || "");
  updateCurrentEpicBadge();
  setStatus({
    title: "Ready",
    message,
    variant: "idle",
    busy: false,
  });
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
    if (status.actionGuide) {
      renderActionGuide(status.actionGuide);
    }
    workspaceMode = status.mode || workspaceMode;
    actionGuideOutdated = Boolean(status.actionGuideOutdated);
    confirmedIisVersionId = status.confirmedIisVersionId ?? confirmedIisVersionId;
    currentActionGuideVersionId = status.currentActionGuideVersionId ?? currentActionGuideVersionId;
    updateWorkspaceModeUI();
    if (status.status === "error") {
      setStatus({
        title: "Error",
        message: status.currentMessage || "The workflow failed.",
        variant: "error",
        busy: false,
      });
      return;
    }
    if (!["generated", "confirmed", "idle"].includes(status.status)) {
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
    list.classList.remove("is-filled");
    list.classList.add("is-empty");
    return;
  }
  list.classList.remove("is-empty");
  list.classList.add("is-filled");
  items.forEach((item) => {
    const node = document.createElement("li");
    node.textContent = item.path;
    list.appendChild(node);
  });
}

function renderIntent(intent) {
  const container = document.getElementById("intent");
  container.innerHTML = "";
  if (!intent) {
    container.classList.remove("is-filled");
    container.classList.add("is-empty");
    return;
  }
  container.classList.remove("is-empty");
  container.classList.add("is-filled");

  const cards = document.createElement("div");
  cards.className = "intent-cards";

  const entries = [
    ["Summary", intent.summary || ""],
    ["Technical Intent", intent.technical_intent || ""],
    ["Keywords", Array.isArray(intent.keywords) ? intent.keywords.join(", ") : ""],
    ["Suspected Areas", Array.isArray(intent.suspected_areas) ? intent.suspected_areas.join("\n") : ""],
    ["Retrieval Query", intent.query || ""],
  ].filter(([, value]) => value && String(value).trim());

  entries.forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "intent-card";

    const title = document.createElement("span");
    title.className = "intent-card-label";
    title.textContent = label;

    const body = document.createElement("div");
    body.className = "intent-card-value";
    body.textContent = value;

    card.appendChild(title);
    card.appendChild(body);
    cards.appendChild(card);
  });

  container.appendChild(cards);
}

function renderQuestions(items) {
  const list = document.getElementById("questionList");
  list.innerHTML = "";
  if (!items || items.length === 0) {
    list.classList.remove("is-filled");
    list.classList.add("is-empty");
    return;
  }
  list.classList.remove("is-empty");
  list.classList.add("is-filled");
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

function renderActionGuide(actionGuide) {
  currentActionGuide = actionGuide;
  const surface = document.getElementById("actionGuideSurface");
  const whatToDoList = document.getElementById("actionGuideWhatToDo");
  const whereToChangeList = document.getElementById("actionGuideWhereToChange");
  whatToDoList.innerHTML = "";
  whereToChangeList.innerHTML = "";

  if (!actionGuide) {
    surface.classList.remove("is-filled");
    surface.classList.add("is-empty");
    updateWorkspaceModeUI();
    return;
  }

  surface.classList.remove("is-empty");
  surface.classList.add("is-filled");

  (actionGuide.what_to_do || []).forEach((item) => {
    const node = document.createElement("li");
    node.textContent = item;
    whatToDoList.appendChild(node);
  });

  (actionGuide.where_to_change || []).forEach((item) => {
    const node = document.createElement("li");
    node.textContent = item;
    whereToChangeList.appendChild(node);
  });

  updateWorkspaceModeUI();
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
    node.textContent = "";
    return;
  }
  const stateLabel =
    workspaceMode === "action_guide_mode"
      ? `Confirmed Version ${currentDraft.version}`
      : actionGuideOutdated
        ? `Version ${currentDraft.version} • Editing again`
        : `Version ${currentDraft.version} • Draft`;
  node.textContent = stateLabel;
}

async function loadVersions() {
  if (!currentSessionId) {
    return;
  }
  const data = await fetchJson(`/api/sessions/${currentSessionId}/versions`);
  currentVersions = data.versions || [];
  currentDraftVersionId = data.currentDraftVersionId;
  currentActionGuideVersionId = data.currentActionGuideVersionId;
  renderVersions();
}

function renderVersions() {
  const list = document.getElementById("versionList");
  list.innerHTML = "";
  if (!currentVersions || currentVersions.length === 0) {
    list.classList.remove("is-filled");
    list.classList.add("is-empty");
    return;
  }
  list.classList.remove("is-empty");
  list.classList.add("is-filled");
  currentVersions.forEach((version) => {
    const node = document.createElement("li");

    const info = document.createElement("div");
    const title = document.createElement("strong");
    const artifactLabel = version.artifact_type === "action_guide" ? "Guide" : "IIS";
    title.textContent = `${artifactLabel} v${version.version_number}`;
    const meta = document.createElement("span");
    meta.className = "version-meta";
    const isCurrent =
      (version.artifact_type === "action_guide" && version.id === currentActionGuideVersionId) ||
      (version.artifact_type !== "action_guide" && version.id === currentDraftVersionId);
    const sourceSuffix =
      version.artifact_type === "action_guide" && version.source_iis_version_id
        ? ` • from IIS v${version.source_iis_version_id}`
        : "";
    meta.textContent = `${formatVersionSourceType(version.source_type)} • ${version.created_at}${sourceSuffix}${isCurrent ? " • current" : ""}`;
    info.appendChild(title);
    info.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "version-actions";
    const restoreButton = document.createElement("button");
    restoreButton.textContent = "Restore";
    restoreButton.disabled = isCurrent;
    restoreButton.addEventListener("click", () => restoreVersion(version.id));
    actions.appendChild(restoreButton);

    node.appendChild(info);
    node.appendChild(actions);
    list.appendChild(node);
  });
}

function formatVersionSourceType(sourceType) {
  const mapping = {
    initial_generate: "initial generate",
    refine: "refine",
    rerun_retrieval: "rerun retrieval",
    restore_version: "restore",
    manual_edit: "manual edit",
    generated_from_confirmed_iis: "generated",
  };
  return mapping[sourceType] || sourceType.replaceAll("_", " ");
}

function collectRefinePayload() {
  const userMessage = document.getElementById("refineInput").value;
  const answeredQuestions = (currentDraft?.open_questions || [])
    .filter((item) => item.status === "open" && userMessage.trim())
    .slice(0, 1)
    .map((item) => ({ id: item.id, answer: userMessage.trim() }));

  return { userMessage, answeredQuestions };
}

function setListEmptyState(elementId, message) {
  const list = document.getElementById(elementId);
  list.innerHTML = "";
  list.classList.remove("is-filled");
  list.classList.add("is-empty");
  if (!message) {
    return;
  }
  const node = document.createElement("li");
  node.className = "empty-state";
  node.textContent = message;
  list.appendChild(node);
}

function resetWorkspace({ showWaiting = false } = {}) {
  stopStatusPolling();
  currentSessionId = null;
  currentActionGuide = null;
  renderIntent(null);
  renderEvidence([]);
  currentDraft = null;
  currentDraftVersionId = null;
  currentActionGuideVersionId = null;
  currentVersions = [];
  workspaceMode = "iis_mode";
  actionGuideOutdated = false;
  confirmedIisVersionId = null;
  document.getElementById("draftEditor").value = "";
  document.getElementById("draftWaiting").textContent = showWaiting
    ? "Waiting for Implementation Intent Specification generation..."
    : "";
  document.getElementById("draftWaiting").style.display = showWaiting ? "block" : "none";
  const description = document.getElementById("description");
  description.textContent = showWaiting ? "Waiting for Epic context..." : "";
  description.classList.remove("is-filled");
  description.classList.add("is-empty");
  document.getElementById("intent").textContent = showWaiting
    ? "Waiting for retrieval intent..."
    : "";
  setListEmptyState(
    "evidenceList",
    showWaiting ? "Waiting for code evidence..." : "",
  );
  setListEmptyState(
    "questionList",
    showWaiting ? "Waiting for open questions..." : "",
  );
  setListEmptyState(
    "versionList",
    showWaiting ? "No versions yet." : "",
  );
  renderActionGuide(null);
  renderVersionMeta();
  updateWorkspaceModeUI();
}

function renderDescription(text) {
  const description = document.getElementById("description");
  description.textContent = text || "";
  description.classList.toggle("is-filled", Boolean(text));
  description.classList.toggle("is-empty", !text);
}

function getCurrentEpicInput() {
  if (currentImportedEpic) {
    return { sourceType: "jira", epic: currentImportedEpic };
  }
  return null;
}

async function generate() {
  const epicInput = getCurrentEpicInput();
  if (!epicInput) {
    setStatus({
      title: "Ready",
        message: "Import an Epic from Jira before generating.",
        variant: "idle",
        busy: false,
      });
    return;
  }
  setStatus({ title: "In Progress", message: "Preparing session...", variant: "busy", busy: true });
  applyBusyState({ generate: true, refine: true, rerun: true, confirm: true });

  try {
    const epic = epicInput.epic;

    resetWorkspace({ showWaiting: true });
    renderDescription(epic.description || "");
    workspaceMode = "iis_mode";
    updateWorkspaceModeUI();

    const session = await fetchJson("/api/sessions", {
      method: "POST",
      body: JSON.stringify(epicInput),
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
    renderActionGuide(generated.actionGuide || null);
    workspaceMode = generated.mode || "iis_mode";
    actionGuideOutdated = Boolean(generated.actionGuideOutdated);
    confirmedIisVersionId = generated.confirmedIisVersionId ?? null;
    renderVersionMeta();
    updateWorkspaceModeUI();
    await loadVersions();
    setStatus({ title: "Ready", message: "Implementation Intent Specification generated.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
    updateWorkspaceModeUI();
  }
}

async function refine() {
  if (!currentSessionId || !currentDraft) {
    return;
  }
  if (workspaceMode === "iis_mode") {
    syncDraftFromEditor();
  }
  const { userMessage, answeredQuestions } = collectRefinePayload();
  setStatus({
    title: "In Progress",
    message:
      workspaceMode === "action_guide_mode"
        ? "Refining Implementation Action Guide..."
        : "Refining Implementation Intent Specification...",
    variant: "busy",
    busy: true,
  });
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
    if (refined.draft) {
      renderDraft(refined.draft);
    }
    if (refined.actionGuide) {
      renderActionGuide(refined.actionGuide);
    }
    renderVersionMeta();
    await loadVersions();
    document.getElementById("refineInput").value = "";
    setStatus({
      title: "Ready",
      message:
        workspaceMode === "action_guide_mode"
          ? "Implementation Action Guide refined."
          : "Implementation Intent Specification refined.",
      variant: "idle",
      busy: false,
    });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
    updateWorkspaceModeUI();
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
    workspaceMode = "iis_mode";
    actionGuideOutdated = Boolean(result.actionGuideOutdated);
    renderVersionMeta();
    updateWorkspaceModeUI();
    await loadVersions();
    document.getElementById("refineInput").value = "";
    setStatus({
      title: "Ready",
      message: "Retrieval rerun completed. Implementation Intent Specification updated.",
      variant: "idle",
      busy: false,
    });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
    updateWorkspaceModeUI();
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
    currentActionGuideVersionId = result.currentActionGuideVersionId ?? currentActionGuideVersionId;
    if (result.draft) {
      renderDraft(result.draft);
    }
    if ("actionGuide" in result) {
      renderActionGuide(result.actionGuide);
    }
    workspaceMode = result.mode || workspaceMode;
    actionGuideOutdated = Boolean(result.actionGuideOutdated);
    renderVersionMeta();
    updateWorkspaceModeUI();
    await loadVersions();
    setStatus({ title: "Ready", message: "Version restored.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
    updateWorkspaceModeUI();
  }
}

async function confirmIis() {
  if (!currentSessionId) {
    return;
  }
  syncDraftFromEditor();
  setStatus({ title: "In Progress", message: "Generating Implementation Action Guide...", variant: "busy", busy: true });
  applyBusyState({ generate: true, refine: true, rerun: true, confirm: true });
  startStatusPolling(currentSessionId);

  try {
    const result = await fetchJson(`/api/sessions/${currentSessionId}/confirm-iis`, {
      method: "POST",
      body: JSON.stringify({ currentDraft }),
    });
    renderDraft(result.draft);
    renderActionGuide(result.actionGuide);
    workspaceMode = result.mode || "action_guide_mode";
    confirmedIisVersionId = result.confirmedIisVersionId ?? confirmedIisVersionId;
    currentActionGuideVersionId = result.currentActionGuideVersionId ?? currentActionGuideVersionId;
    actionGuideOutdated = false;
    renderVersionMeta();
    updateWorkspaceModeUI();
    await loadVersions();
    setStatus({ title: "Ready", message: "Implementation Action Guide generated from the confirmed IIS.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    stopStatusPolling();
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
    updateWorkspaceModeUI();
  }
}

async function reopenIis() {
  if (!currentSessionId) {
    return;
  }
  setStatus({ title: "In Progress", message: "Reopening Implementation Intent Specification...", variant: "busy", busy: true });
  applyBusyState({ generate: true, refine: true, rerun: true, confirm: true });
  try {
    const result = await fetchJson(`/api/sessions/${currentSessionId}/reopen-iis`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    workspaceMode = result.mode || "iis_mode";
    actionGuideOutdated = Boolean(result.actionGuideOutdated);
    if (result.draft) {
      renderDraft(result.draft);
    }
    if ("actionGuide" in result) {
      renderActionGuide(result.actionGuide);
    }
    renderVersionMeta();
    updateWorkspaceModeUI();
    setStatus({ title: "Ready", message: "Implementation Intent Specification reopened for editing.", variant: "idle", busy: false });
  } catch (error) {
    setStatus({ title: "Error", message: error.message, variant: "error", busy: false });
  } finally {
    applyBusyState({ generate: false, refine: false, rerun: false, confirm: false });
    updateWorkspaceModeUI();
  }
}

function setJiraModalStatus(message, variant = "idle") {
  const node = document.getElementById("jiraModalStatus");
  node.textContent = message || "";
  node.dataset.variant = variant;
}

function setJiraCredentialState(message) {
  document.getElementById("jiraCredentialState").textContent = message;
}

function setJiraFindEpicEnabled(enabled) {
  document.getElementById("jiraFindEpicSection").classList.toggle("is-disabled", !enabled);
  document.getElementById("jiraIssueKeyInput").disabled = !enabled;
  document.getElementById("jiraLoadByKeyButton").disabled = !enabled;
  document.getElementById("jiraProjectSearch").disabled = !enabled;
  document.getElementById("jiraEpicSearch").disabled = !enabled || !jiraState.selectedProjectKey;
}

function openJiraModal() {
  document.getElementById("jiraModal").classList.remove("hidden");
  document.getElementById("jiraModal").setAttribute("aria-hidden", "false");
  resetJiraModalSelection();
  setJiraFindEpicEnabled(false);
  loadJiraCredentialStatus().catch((error) => {
    setJiraModalStatus(error.message, "error");
  });
}

function closeJiraModal() {
  document.getElementById("jiraModal").classList.add("hidden");
  document.getElementById("jiraModal").setAttribute("aria-hidden", "true");
}

function resetJiraModalSelection() {
  jiraState.projects = [];
  jiraState.filteredProjects = [];
  jiraState.epics = [];
  jiraState.filteredEpics = [];
  jiraState.selectedProjectKey = "";
  jiraState.selectedEpic = null;
  document.getElementById("jiraProjectSearch").value = "";
  document.getElementById("jiraEpicSearch").value = "";
  document.getElementById("jiraIssueKeyInput").value = "";
  renderProjectList();
  renderEpicList();
  renderSelectedJiraEpic();
}

async function loadJiraCredentialStatus() {
  setJiraModalStatus("Checking Jira credential status...", "busy");
  const result = await fetchJson("/api/jira/credential-status");
  jiraState.hasSavedToken = Boolean(result.hasSavedToken);
  jiraState.connected = jiraState.hasSavedToken;
  if (jiraState.connected) {
    setJiraCredentialState("Connected using saved local credential.");
    setJiraModalStatus("Connected to Jira.", "idle");
    setJiraFindEpicEnabled(true);
    await loadJiraProjects();
  } else {
    setJiraCredentialState("Jira token required.");
    setJiraModalStatus("Enter a Jira token to continue.", "idle");
    setJiraFindEpicEnabled(false);
  }
}

async function connectJira() {
  const token = document.getElementById("jiraTokenInput").value.trim();
  const rememberLocally = document.getElementById("jiraRememberCheckbox").checked;
  if (!token) {
    setJiraModalStatus("A Jira personal token is required.", "error");
    return;
  }

  setJiraModalStatus("Validating Jira access...", "busy");
  document.getElementById("jiraConnectButton").disabled = true;
  try {
    await fetchJson("/api/jira/connect", {
      method: "POST",
      body: JSON.stringify({ token, rememberLocally }),
    });
    jiraState.connected = true;
    jiraState.hasSavedToken = jiraState.hasSavedToken || rememberLocally;
    setJiraCredentialState(
      rememberLocally
        ? "Connected and saved locally for future use."
        : "Connected using the current session credential.",
    );
    setJiraModalStatus("Connected to Jira.", "idle");
    setJiraFindEpicEnabled(true);
    await loadJiraProjects();
  } catch (error) {
    setJiraModalStatus(error.message, "error");
    jiraState.connected = false;
    setJiraFindEpicEnabled(false);
  } finally {
    document.getElementById("jiraConnectButton").disabled = false;
  }
}

async function loadJiraProjects() {
  if (!jiraState.connected) {
    return;
  }
  setJiraModalStatus("Loading projects...", "busy");
  const result = await fetchJson("/api/jira/projects");
  jiraState.projects = result.projects || [];
  jiraState.filteredProjects = jiraState.projects.slice();
  renderProjectList();
  setJiraModalStatus("Projects loaded.", "idle");
}

function renderProjectList() {
  const list = document.getElementById("jiraProjectList");
  list.innerHTML = "";
  jiraState.filteredProjects.forEach((project) => {
    const item = document.createElement("li");
    item.className = project.key === jiraState.selectedProjectKey ? "is-selected" : "";
    item.innerHTML = `
      <span class="selection-item-title">${project.key}</span>
      <span class="selection-item-meta">${project.name}</span>
    `;
    item.addEventListener("click", () => selectJiraProject(project));
    list.appendChild(item);
  });
}

function filterJiraProjects() {
  const query = document.getElementById("jiraProjectSearch").value.trim().toLowerCase();
  jiraState.filteredProjects = jiraState.projects.filter((project) => {
    if (!query) {
      return true;
    }
    return (
      project.key.toLowerCase().includes(query) ||
      project.name.toLowerCase().includes(query)
    );
  });
  renderProjectList();
}

async function selectJiraProject(project) {
  jiraState.selectedProjectKey = project.key;
  jiraState.epics = [];
  jiraState.filteredEpics = [];
  jiraState.selectedEpic = null;
  renderProjectList();
  renderEpicList();
  renderSelectedJiraEpic();
  document.getElementById("jiraEpicSearch").disabled = false;

  setJiraModalStatus(`Loading Epics for ${project.key}...`, "busy");
  const result = await fetchJson(`/api/jira/projects/${encodeURIComponent(project.key)}/epics`);
  jiraState.epics = result.epics || [];
  jiraState.filteredEpics = jiraState.epics.slice();
  renderEpicList();
  setJiraModalStatus(`Loaded ${jiraState.epics.length} Epics for ${project.key}.`, "idle");
}

function renderEpicList() {
  const list = document.getElementById("jiraEpicList");
  list.innerHTML = "";
  jiraState.filteredEpics.forEach((epic) => {
    const item = document.createElement("li");
    item.className = jiraState.selectedEpic?.id === epic.key ? "is-selected" : "";
    item.innerHTML = `
      <span class="selection-item-title">${epic.key}</span>
      <span class="selection-item-meta">${epic.summary}${epic.status ? ` • ${epic.status}` : ""}</span>
    `;
    item.addEventListener("click", () => loadJiraEpic(epic.key));
    list.appendChild(item);
  });
}

function filterJiraEpics() {
  const query = document.getElementById("jiraEpicSearch").value.trim().toLowerCase();
  jiraState.filteredEpics = jiraState.epics.filter((epic) => {
    if (!query) {
      return true;
    }
    return (
      epic.key.toLowerCase().includes(query) ||
      (epic.summary || "").toLowerCase().includes(query)
    );
  });
  renderEpicList();
}

async function loadJiraEpic(issueKey) {
  setJiraModalStatus(`Loading Epic ${issueKey}...`, "busy");
  const epic = await fetchJson(`/api/jira/epics/${encodeURIComponent(issueKey)}`);
  jiraState.selectedEpic = epic;
  renderEpicList();
  renderSelectedJiraEpic();
  setJiraModalStatus(`Epic ${issueKey} loaded.`, "idle");
}

function renderSelectedJiraEpic() {
  const keyNode = document.getElementById("jiraSelectedEpicKey");
  const titleNode = document.getElementById("jiraSelectedEpicTitle");
  const previewNode = document.getElementById("jiraSelectedEpicPreview");
  const useButton = document.getElementById("jiraUseEpicButton");

  if (!jiraState.selectedEpic) {
    keyNode.textContent = "No Epic selected.";
    titleNode.textContent = "-";
    previewNode.textContent = "";
    useButton.disabled = true;
    return;
  }

  keyNode.textContent = jiraState.selectedEpic.id || "";
  titleNode.textContent = jiraState.selectedEpic.title || "";
  previewNode.textContent = (jiraState.selectedEpic.description || "").slice(0, 1200);
  useButton.disabled = false;
}

async function loadJiraEpicByKey() {
  const issueKey = document.getElementById("jiraIssueKeyInput").value.trim().toUpperCase();
  if (!issueKey) {
    setJiraModalStatus("A full issue key is required.", "error");
    return;
  }
  try {
    await loadJiraEpic(issueKey);
  } catch (error) {
    setJiraModalStatus(error.message, "error");
  }
}

function useImportedJiraEpic() {
  if (!jiraState.selectedEpic) {
    return;
  }
  setCurrentEpic(
    jiraState.selectedEpic,
    "jira",
    `Epic ${jiraState.selectedEpic.id} imported from Jira. Ready to generate.`,
  );
  closeJiraModal();
}

document.getElementById("loadButton").addEventListener("click", generate);
document.getElementById("refineButton").addEventListener("click", refine);
document.getElementById("rerunButton").addEventListener("click", rerunRetrieval);
document.getElementById("confirmIisButton").addEventListener("click", confirmIis);
document.getElementById("reopenIisButton").addEventListener("click", reopenIis);
document.getElementById("draftEditor").addEventListener("input", () => {
  syncDraftFromEditor();
  renderVersionMeta();
});
document.getElementById("jiraImportButton").addEventListener("click", openJiraModal);
document.getElementById("jiraModalClose").addEventListener("click", closeJiraModal);
document.getElementById("jiraConnectButton").addEventListener("click", connectJira);
document.getElementById("jiraProjectSearch").addEventListener("input", filterJiraProjects);
document.getElementById("jiraEpicSearch").addEventListener("input", filterJiraEpics);
document.getElementById("jiraLoadByKeyButton").addEventListener("click", loadJiraEpicByKey);
document.getElementById("jiraUseEpicButton").addEventListener("click", useImportedJiraEpic);
document.getElementById("jiraModal").addEventListener("click", (event) => {
  if (event.target.id === "jiraModal") {
    closeJiraModal();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeJiraModal();
  }
});

loadSavedPanelWidths();
setupResizablePanels();
resetWorkspace({ showWaiting: false });
updateCurrentEpicBadge();
renderSelectedJiraEpic();
updateWorkspaceModeUI();
