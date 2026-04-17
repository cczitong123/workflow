let currentSessionId = null;
let currentDraft = null;

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

loadEpics().catch((error) => {
  document.getElementById("description").textContent = error.message;
});
