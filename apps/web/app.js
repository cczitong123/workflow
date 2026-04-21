let currentSessionId = null;
let currentDraft = null;
const PANEL_STORAGE_KEY = "bmwcode-workspace-widths";

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
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

function renderEvidence(items) {
  const list = document.getElementById("evidenceList");
  list.innerHTML = "";
  items.forEach((item) => {
    const node = document.createElement("li");
    node.textContent = `${item.path}: ${item.suggestedChange}`;
    list.appendChild(node);
  });
}

function renderQuestions(items) {
  const list = document.getElementById("questionList");
  list.innerHTML = "";
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
  renderQuestions(draft?.open_questions || []);
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
  const epic = await fetchJson(`/api/epics/${epicId}`);
  document.getElementById("description").textContent = epic.description || "";

  const session = await fetchJson("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ epicId }),
  });
  currentSessionId = session.sessionId;

  const generated = await fetchJson(`/api/sessions/${currentSessionId}/generate`, {
    method: "POST",
    body: JSON.stringify({}),
  });

  document.getElementById("intent").textContent = JSON.stringify(
    generated.retrievalIntent,
    null,
    2,
  );
  document.getElementById("groundTruth").textContent = JSON.stringify(
    generated.groundTruth,
    null,
    2,
  );
  renderEvidence(generated.evidence);
  renderDraft(generated.draft);
}

async function refine() {
  if (!currentSessionId || !currentDraft) {
    return;
  }
  const userMessage = document.getElementById("refineInput").value;
  const answeredQuestions = (currentDraft.open_questions || [])
    .filter((item) => item.status === "open" && userMessage.trim())
    .slice(0, 1)
    .map((item) => ({ id: item.id, answer: userMessage.trim() }));

  const refined = await fetchJson(`/api/sessions/${currentSessionId}/refine`, {
    method: "POST",
    body: JSON.stringify({
      userMessage,
      answeredQuestions,
      currentDraft,
    }),
  });
  renderDraft(refined.draft);
}

async function confirmDraft() {
  if (!currentSessionId) {
    return;
  }
  const result = await fetchJson(`/api/sessions/${currentSessionId}/confirm`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  document.getElementById("draftEditor").value = result.exportText;
}

document.getElementById("loadButton").addEventListener("click", generate);
document.getElementById("refineButton").addEventListener("click", refine);
document.getElementById("confirmButton").addEventListener("click", confirmDraft);

loadSavedPanelWidths();
setupResizablePanels();
loadEpics().catch((error) => {
  document.getElementById("description").textContent = error.message;
});
