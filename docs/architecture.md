# Agentic-Workflow Architecture

Agentic-Workflow is a local-first Epic analysis workflow for turning Epic descriptions into structured `What to Do` drafts, with explicit retrieval, refinement, version history, and local persistence.

This document describes the current implementation shape, the main execution flow, and the boundaries between the major parts of the system.

## System overview

The application currently combines:

- local Epic input files
- prompt-driven LLM stages
- local code retrieval from embeddings and FAISS
- browser-based refinement and review
- SQLite persistence for versions, evidence, and events

The system is designed so that:

- Epic input sources can later expand beyond local JSON
- local persistence can later be replaced by a cloud database
- prompt behavior can evolve without changing orchestration structure
- session history and decision trace can be recovered from structured stored data

## Current repository shape

### Top-level runtime files

- `/run.py`
  Cross-platform startup entry point.

- `/requirements.txt`
  Python dependency manifest.

- `/.env`
  Active machine-specific configuration.

- `/.env.example`
  Runtime configuration template.

### Backend

- `/apps/api/src/server.py`
  Main HTTP server and workflow orchestration.

- `/apps/api/src/config.py`
  Runtime configuration loading, `.env` resolution, and path normalization.

- `/apps/api/src/prompt_loader.py`
  Prompt text and few-shot loading.

### Frontend

- `/apps/web/index.html`
  Static workbench structure.

- `/apps/web/styles.css`
  Workbench layout, panel hierarchy, and UI states.

- `/apps/web/app.js`
  Browser interaction flow, polling, rendering, version actions, and editor synchronization.

### Local data and persistence

- `/data/epics/`
  Local Epic JSON source files.

- `/data/agentic_workflow.sqlite3`
  Local SQLite persistence database.

### Export and inspection helpers

- `/tools/`
  Standalone SQLite-to-JSON export helpers for local inspection and demo-stage analysis.

## Architectural layers

The current codebase is easiest to understand in four layers.

### 1. Workflow layer

This layer coordinates the end-to-end steps of the workbench.

Primary file:

- `/apps/api/src/server.py`

Current workflow actions include:

- session creation
- initial draft generation
- draft refinement
- retrieval re-run
- version restore
- final confirmation
- partial progress exposure to the UI while generation is still running

This layer should remain independent from the details of:

- where Epic data comes from
- how the LLM is called
- how code evidence is retrieved
- how state is stored

### 2. Provider and parsing layer

This layer shapes raw Epic input into internal records.

Primary files:

- `/apps/api/src/modules/epics/repository.py`
- `/apps/api/src/modules/epics/what_to_do_parser.py`

Current behavior:

- Epic JSON files are loaded from local disk
- nested raw issue structures are normalized into internal fields
- `description` remains raw text
- historical `what_to_do` text is preserved and parsed into structured steps and file changes

This layer is the natural place for a future Jira-backed Epic provider.

### 3. Integration layer

This layer is responsible for external or specialized capabilities.

Primary files:

- `/apps/api/src/modules/integrations/llm_adapter.py`
- `/apps/api/src/modules/integrations/code_rag_adapter.py`

#### LLM adapter responsibilities

The LLM adapter currently handles:

- retrieval-intent generation
- retrieval-query generation
- draft generation
- refinement and open-question regeneration
- remote authentication for BMW-style LLM access
- token reuse through in-process caching
- remote response parsing

The current LLM configuration supports:

- `remote` mode as the main active path
- reserved structure for future `local` mode evolution

#### Code RAG adapter responsibilities

The Code RAG adapter currently handles:

- loading the local embedding model
- loading local JSON embedding vectors
- building a local FAISS index
- generating an embedding from the retrieval query
- retrieving top-k evidence
- shaping raw retrieval output into UI-facing evidence items

The current Code RAG configuration supports:

- `local` mode as the main active path
- reserved structure for future `remote` mode evolution

### 4. Persistence layer

This layer stores workflow state and history.

Primary file:

- `/apps/api/src/modules/sessions/store.py`

The current persistence backend is SQLite.

Current tables include:

- `sessions`
- `retrieval_versions`
- `evidence_items`
- `draft_versions`
- `user_events`

The persistence layer currently stores enough information to support:

- current session state
- retrieval history
- evidence snapshots
- draft version history
- restore operations
- event-based trace reconstruction

## Main execution flow

### Initial draft generation

The current initial path is:

1. A local Epic is selected in the browser UI.
2. A session is created and persisted.
3. The Epic description is shown immediately in the UI.
4. Retrieval intent is generated from the Epic description.
5. A retrieval query is generated from the Epic description and retrieval intent.
6. Local code retrieval runs against the embedding store.
7. Evidence is persisted as a retrieval version snapshot.
8. A `What to Do` draft is generated from the Epic description and evidence.
9. The draft is persisted as a draft version.
10. Version history becomes available in the UI.

### Refinement flow

The refine flow does not rebuild retrieval.

It currently:

1. syncs the editor text back to the backend if it was manually changed
2. combines current draft state with user input and answered questions
3. generates a new refined draft
4. stores the result as a new draft version

Retrieval intent and evidence stay unchanged during refine.

### Retrieval re-run flow

The re-run retrieval flow is explicitly separated from refine.

It currently:

1. syncs the current draft if it was manually edited
2. reuses the original Epic description plus the latest user answers/notes
3. regenerates retrieval intent and retrieval query
4. reruns local code retrieval
5. stores a new retrieval version
6. regenerates the draft from the updated retrieval result
7. stores the new draft as a new draft version

This separation is intentional so that:

- refine remains lightweight and predictable
- retrieval changes are explicit
- version history can distinguish pure draft edits from evidence changes

### Restore flow

Restore does not erase history.

Instead:

1. a previous draft version is selected
2. its stored draft payload is loaded
3. a new draft version is created from that older state

This keeps the historical chain intact while allowing rollback in the UI.

## Prompt architecture

Prompt assets live in:

- `/apps/api/src/prompts/`

### Active system prompts

- `/apps/api/src/prompts/retrieval_intent_system.txt`
- `/apps/api/src/prompts/retrieval_query_system.txt`
- `/apps/api/src/prompts/draft_generation_system.txt`
- `/apps/api/src/prompts/refine_open_questions_system.txt`

### Few-shot strategy

The system currently keeps few-shot interfaces available for multiple stages, but only one path is actively emphasized:

- `/apps/api/src/prompts/retrieval_query_fewshot.json`

This file is intended for `description -> retrieval query` examples.

The following files are preserved as future extension points and can remain empty arrays until needed:

- `/apps/api/src/prompts/retrieval_intent_fewshot.json`
- `/apps/api/src/prompts/draft_generation_fewshot.json`
- `/apps/api/src/prompts/refine_open_questions_fewshot.json`

This design keeps the interfaces stable while allowing future prompt enrichment without backend restructuring.

## Frontend interaction model

The browser workbench is currently organized into three functional panels.

### Left panel: Context & Retrieval

This panel is used for:

- Epic context
- retrieval intent
- code evidence
- explicit retrieval actions

### Center panel: Draft Workspace

This panel is the main work surface and is used for:

- the current `What to Do` draft
- manual editing
- current version display
- final confirmation

### Right panel: Review & History

This panel is used for:

- open questions
- refinement input
- version history
- historical `What-to-Do` reference

### UI progress model

The UI intentionally exposes workflow phases rather than percentage progress.

Typical phases include:

- generating retrieval intent
- generating retrieval query
- searching code evidence
- drafting
- refining
- re-running retrieval
- restoring version
- confirming

Partial results are exposed progressively so the UI can show:

- Epic context first
- retrieval intent next
- evidence next
- draft last

instead of waiting for the full workflow to finish before rendering anything.

## Persistence and traceability

The current SQLite-backed structure is intentionally designed to support later trace-pack generation.

Important persisted elements already include:

- session metadata
- retrieval versions
- evidence snapshots
- draft versions
- user events

This allows later reconstruction of:

- what was generated
- what changed between versions
- whether retrieval was rerun
- which evidence supported each draft state
- which user actions contributed to the final state

The current export helpers under `/tools/` are built around this structure.

## Current export tooling

The `/tools/` directory contains standalone inspection scripts that read the SQLite database and export JSON for local debugging and demo analysis.

These tools currently cover:

- full session timeline export
- single draft version export
- single retrieval version export
- version diff export
- trace-pack export

They are configured through editable values at the top of each script instead of command-line arguments.

## Configuration model

Runtime configuration is split into two layers.

### Defaults

- `/apps/api/src/config.py`

This file contains:

- non-sensitive default values
- local retrieval defaults
- model defaults
- database path defaults
- path normalization logic

### Overrides

- `/.env`

This file is intended for:

- machine-specific paths
- credentials
- endpoints
- secret keys

Configuration precedence is:

1. exported environment variables
2. values from `/.env`
3. defaults from `/apps/api/src/config.py`

The environment-variable prefix is:

- `AGENTIC_WORKFLOW_*`

## Migration direction

The current codebase is local-first, but several boundaries are already aligned with future expansion.

### Future Epic providers

The current local Epic repository can later be complemented by a Jira-backed provider without changing the workflow orchestration model.

### Future persistence backends

SQLite is currently the local persistence backend, but the repository-style persistence layer can later be replaced with a cloud database implementation.

### Future trace pack and audit export

The current event and version structure is already suitable for later trace-pack generation, confirmation snapshots, and audit-style export.

## Summary

The current architecture is centered on:

- explicit workflow actions
- prompt-driven generation stages
- local retrieval evidence
- structured draft version history
- SQLite-based local persistence
- future-friendly boundaries for providers, persistence, and trace export

This keeps the current demo usable while preserving a clean path toward more formal cloud-backed workflow evolution.
