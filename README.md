# Agentic-Workflow

Agentic-Workflow is an Epic analysis workbench for turning an Epic description into an `Implementation Intent Specification`, then deriving a finer-grained `Implementation Action Guide`, with code retrieval, refinement, version history, and local traceable persistence.

## What the application currently does

The current UI-driven flow is:

1. An Epic is imported from Jira through the `Import from Jira` modal.
2. The Epic description is summarized into retrieval intent.
3. A retrieval query is generated from the description and summarized intent.
4. Code evidence is retrieved from a local embedding model and local vector store.
5. An `Implementation Intent Specification` (IIS) is generated through the configured LLM mode.
6. The IIS can be refined without rebuilding evidence.
7. Retrieval can be explicitly re-run with the latest answers and notes while the workbench is still in IIS mode.
8. The current IIS version can be confirmed to generate an `Implementation Action Guide`.
9. The IIS can later be reopened for editing, which marks the current Action Guide as outdated until it is regenerated.
10. Version history can restore both IIS and Action Guide artifacts.
11. Workflow state is persisted locally in SQLite.

The backend still contains a local Epic repository under `data/epics/` for development support and normalization compatibility, but the current browser workbench is centered on Jira import rather than local file browsing.

## Repository map

### Top-level entry points

- `/run.py`
  Cross-platform startup entry point.

- `/requirements.txt`
  Python dependency manifest.

- `/.env`
  Active runtime configuration with machine-specific and sensitive values.

- `/.env.example`
  Runtime configuration template.

- `/README.md`
  Project overview and operator guide.

## Backend architecture

### Runtime core

- `/apps/api/src/server.py`
  Main HTTP server entry point. Request routing, workflow orchestration, partial status updates, session operations, and UI-facing JSON responses are defined here.

- `/apps/api/src/config.py`
  Central configuration loader. Default non-sensitive values, `.env` loading, environment-variable overrides, and path resolution are handled here.

- `/apps/api/src/prompt_loader.py`
  Prompt text and few-shot assets are loaded and rendered here.

### Epic ingestion

- `/apps/api/src/modules/epics/repository.py`
  Local Epic JSON files are loaded and normalized here. The same normalization path is also reused for imported Epic-shaped payloads.

- `/apps/api/src/modules/epics/jira_provider.py`
  Jira-backed Epic loading is handled here. Credential validation, project listing, Epic listing, issue-key loading, optional token persistence, and project-key filtering are defined here.

- `/apps/api/src/modules/epics/what_to_do_parser.py`
  Historical `whatToDo` text is parsed into structured steps and file-change entries here.

### Integration layer

- `/apps/api/src/modules/integrations/llm_adapter.py`
  Retrieval-intent generation, retrieval-query generation, IIS generation, Action Guide generation, refinement, remote authentication, token caching, and response parsing are handled here.

- `/apps/api/src/modules/integrations/code_rag_adapter.py`
  Code-evidence retrieval is handled here. Local embedding loading, vector-store loading, FAISS search, ranking, and evidence shaping are defined here.

### Session, versions, and persistence

- `/apps/api/src/modules/sessions/store.py`
  Local workflow persistence is handled here. SQLite tables for sessions, retrieval versions, evidence snapshots, artifact versions, and user events are created and maintained here.

- `/apps/api/src/modules/shared/models.py`
  Shared internal data models are defined here. Session, retrieval intent, evidence, IIS, Action Guide, version-record, and parsed Epic data classes live here.

## Prompt system

Prompt assets live in:

- `/apps/api/src/prompts/`

### Prompt text files

- `/apps/api/src/prompts/retrieval_intent_system.txt`
  Rules for turning an Epic description into retrieval intent.

- `/apps/api/src/prompts/retrieval_query_system.txt`
  Rules for turning the Epic context and retrieval intent into a code-search query.

- `/apps/api/src/prompts/draft_generation_system.txt`
  Rules for generating the `Implementation Intent Specification` from Epic input and code evidence.

- `/apps/api/src/prompts/refine_open_questions_system.txt`
  Rules for refining the IIS and generating or preserving open questions.

- `/apps/api/src/prompts/implementation_action_guide_system.txt`
  Rules for generating the `Implementation Action Guide` from the Epic description, retrieval intent, code evidence, and the confirmed IIS.

- `/apps/api/src/prompts/refine_implementation_action_guide_system.txt`
  Rules for refining the `Implementation Action Guide`.

### Few-shot JSON files

- `/apps/api/src/prompts/retrieval_query_fewshot.json`
  Active few-shot file for `description -> retrieval query` examples.

- `/apps/api/src/prompts/retrieval_intent_fewshot.json`
  Reserved interface for future retrieval-intent few-shot examples. Currently kept as an empty array unless explicitly populated.

- `/apps/api/src/prompts/draft_generation_fewshot.json`
  Reserved interface for future IIS-generation few-shot examples. Currently kept as an empty array unless explicitly populated.

- `/apps/api/src/prompts/refine_open_questions_fewshot.json`
  Reserved interface for future refine/open-question few-shot examples. Currently kept as an empty array unless explicitly populated.

- `/apps/api/src/prompts/implementation_action_guide_fewshot.json`
  Few-shot examples for `description -> implementation action guide` mappings.

## Frontend architecture

- `/apps/web/index.html`
  Static workbench structure. The three-column layout, headings, Jira import modal, buttons, info popovers, and panel placeholders are defined here.

- `/apps/web/styles.css`
  Workbench layout, visual hierarchy, resizable columns, display surfaces, modal sizing, sticky modal headers, editor styling, and button hierarchy are defined here.

- `/apps/web/app.js`
  Browser-side workflow logic is implemented here. Jira import, IIS generation/refinement, Action Guide generation/refinement, mode switching, status polling, version rendering, restore actions, editor syncing, and UI state transitions are handled here.

## Local data and persistence

- `/data/epics/`
  Local Epic JSON input files kept for development support, repository normalization, and compatibility testing.

- `/data/agentic_workflow.sqlite3`
  Local SQLite persistence store. Session state, retrieval snapshots, evidence items, IIS versions, Action Guide versions, and user events are stored here.

## Tools for local export and inspection

Export and inspection helpers live in:

- `/tools/`

### Shared helper

- `/tools/export_utils.py`
  Shared SQLite readers and JSON export helpers used by the standalone export scripts.

### Standalone export scripts

- `/tools/export_session_timeline.py`
  Exports one session as a timeline-style JSON file.

- `/tools/export_draft_version.py`
  Exports one draft version as JSON.

- `/tools/export_retrieval_version.py`
  Exports one retrieval version and linked evidence as JSON.

- `/tools/export_version_diff.py`
  Exports a JSON diff between two draft version numbers in one session.

- `/tools/export_trace_pack.py`
  Exports a session-level trace pack as JSON.

These scripts are intentionally configured through editable values at the top of each file. They are designed to be run as:

```bash
python tools/export_session_timeline.py
python tools/export_version_diff.py
```

without passing command-line arguments.

## Current persistence model

The application currently uses SQLite as the main persistence backend.

The SQLite database stores:

- sessions
- retrieval versions
- evidence items
- artifact versions
- user events

Many values inside the database are stored as JSON text fields, such as:

- IIS and Action Guide payloads
- retrieval keywords
- suspected areas
- event payloads

This means the current setup is:

- storage backend: SQLite
- nested structured payloads: JSON inside SQLite fields

There is currently no alternate JSON-file storage mode for the main application workflow.

## Runtime modes

The current default runtime shape is:

- Epic source in the UI: Jira import
- Code RAG: local
- LLM: remote

The runtime behavior is controlled in `/apps/api/src/config.py` and can be overridden through `/.env`.

## Where to change what

### Secrets and machine-specific values

Edit:

- `/.env`

Typical values changed there:

- `AGENTIC_WORKFLOW_EPIC_DATA_DIR`
- `AGENTIC_WORKFLOW_PROMPT_DIR`
- `AGENTIC_WORKFLOW_STORAGE_DB_PATH`
- `AGENTIC_WORKFLOW_JIRA_BASE_URL`
- `AGENTIC_WORKFLOW_JIRA_PERSONAL_TOKEN`
- `AGENTIC_WORKFLOW_JIRA_VISIBLE_PROJECT_KEYS`
- `AGENTIC_WORKFLOW_CODE_RAG_EMBEDDING_MODEL_PATH`
- `AGENTIC_WORKFLOW_CODE_RAG_VECTOR_STORE_PATH`
- `AGENTIC_WORKFLOW_LLM_ENDPOINT`
- `AGENTIC_WORKFLOW_LLM_API_PATH`
- `AGENTIC_WORKFLOW_LLM_CERT_PATH`
- `AGENTIC_WORKFLOW_LLM_AUTH_URL`
- `AGENTIC_WORKFLOW_LLM_API_KEY`
- `AGENTIC_WORKFLOW_LLM_CLIENT_ID`
- `AGENTIC_WORKFLOW_LLM_CLIENT_SECRET`

### Non-sensitive runtime defaults

Edit:

- `/apps/api/src/config.py`

Typical edits:

- Jira project-list and Epic-list limits
- local retrieval filters
- local model paths
- `top_k`
- allowed extensions
- LLM mode
- model name
- timeout
- temperature
- `max_tokens`

### Prompt behavior

Edit:

- `/apps/api/src/prompts/`

Typical edits:

- retrieval-intent format
- retrieval-query guidance
- `Implementation Intent Specification` structure
- `Implementation Action Guide` structure
- open-question style
- refinement behavior
- retrieval-query few-shot examples

### Jira project filtering

If Jira project discovery should be narrowed for performance or focus, edit:

- `AGENTIC_WORKFLOW_JIRA_VISIBLE_PROJECT_KEYS`

Example:

```env
AGENTIC_WORKFLOW_JIRA_VISIBLE_PROJECT_KEYS=PARK,AMA,GEMINI
```

When this value is empty, all accessible Jira projects are shown. When it is populated, only the listed project keys are shown in the Jira import modal.

### Integration logic

Edit:

- `/apps/api/src/modules/integrations/code_rag_adapter.py`
- `/apps/api/src/modules/integrations/llm_adapter.py`

Typical reasons:

- changing retrieval ranking behavior
- changing remote request formatting
- changing local model loading logic
- changing evidence shaping
- changing token caching or auth handling

### Frontend UI behavior

Edit:

- `/apps/web/index.html`
- `/apps/web/styles.css`
- `/apps/web/app.js`

Typical reasons:

- changing panel layout
- changing status messaging
- changing waiting states
- changing version history rendering
- changing evidence presentation
- changing workflow buttons

## Configuration precedence

The current precedence is:

1. Environment variables exported before startup
2. Values loaded from `/.env`
3. Defaults defined in `/apps/api/src/config.py`

The `AGENTIC_WORKFLOW_*` prefix is used for runtime environment variables.

## Startup

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the application:

```bash
python run.py
```

Open the browser UI:

- [http://127.0.0.1:8000](http://127.0.0.1:8000)
