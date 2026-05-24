# Agentic-Workflow Architecture

Agentic-Workflow is an Epic analysis workflow for turning Epic descriptions into an `Implementation Intent Specification`, then deriving an `Implementation Action Guide`, with explicit retrieval, refinement, version history, and local persistence.

This document describes the current implementation shape, the main execution flow, and the boundaries between the major parts of the system.

## System overview

The application currently combines:

- Jira-imported Epic input in the browser workbench
- prompt-driven LLM stages
- local code retrieval from embeddings and FAISS
- browser-based refinement and review
- SQLite persistence for versions, evidence, and events

The system is designed so that:

- Epic input sources can later expand beyond local JSON
- the UI-facing Epic source can evolve without changing the workflow orchestration model
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
  Static workbench structure, including the Jira import modal and the three-panel workbench.

- `/apps/web/styles.css`
  Workbench layout, panel hierarchy, UI states, modal sizing, and sticky modal header behavior.

- `/apps/web/app.js`
  Browser interaction flow, Jira import, polling, rendering, version actions, and editor synchronization.

### Local data and persistence

- `/data/epics/`
  Local Epic JSON source files kept for backend compatibility and development-side normalization support.

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
- initial IIS generation
- IIS refinement
- IIS confirmation and Action Guide generation
- IIS reopen
- Action Guide refinement
- retrieval re-run
- version restore
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

This layer now also supports imported Epic-shaped payload normalization and remains the natural place for Epic-source expansion.

### 3. Integration layer

This layer is responsible for external or specialized capabilities.

Primary files:

- `/apps/api/src/modules/integrations/llm_adapter.py`
- `/apps/api/src/modules/integrations/code_rag_adapter.py`

#### LLM adapter responsibilities

The LLM adapter currently handles:

- retrieval-intent generation
- retrieval-query generation
- IIS generation
- Action Guide generation
- IIS refinement and open-question regeneration
- Action Guide refinement
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
- IIS version history
- Action Guide version history
- restore operations
- event-based trace reconstruction

## Main execution flow

### Initial IIS generation

The current initial path is:

1. An Epic is imported from Jira in the browser UI.
2. A session is created and persisted.
3. The Epic description is shown immediately in the UI.
4. Retrieval intent is generated from the Epic description.
5. A retrieval query is generated from the Epic description and retrieval intent.
6. Local code retrieval runs against the embedding store.
7. Evidence is persisted as a retrieval version snapshot.
8. An `Implementation Intent Specification` is generated from the Epic description and evidence.
9. The IIS is persisted as an artifact version.
10. Version history becomes available in the UI.

### IIS refinement flow

The refine flow does not rebuild retrieval.

It currently:

1. syncs the editor text back to the backend if it was manually changed
2. combines current IIS state with user input and answered questions
3. generates a new refined IIS
4. stores the result as a new IIS version

Retrieval intent and evidence stay unchanged during refine.

### Retrieval re-run flow

The re-run retrieval flow is explicitly separated from refine.

It currently:

1. syncs the current IIS if it was manually edited
2. reuses the original Epic description plus the latest user answers/notes
3. regenerates retrieval intent and retrieval query
4. reruns local code retrieval
5. stores a new retrieval version
6. regenerates the IIS from the updated retrieval result
7. stores the new IIS as a new artifact version

This separation is intentional so that:

- IIS refinement remains lightweight and predictable
- retrieval changes are explicit
- version history can distinguish pure IIS edits from evidence changes

### IIS confirmation and Action Guide generation

After the IIS is ready, the workflow enters a second stage:

1. the current IIS version is confirmed
2. the confirmed IIS version id is recorded in session state
3. an `Implementation Action Guide` is generated from:
   - the original Epic description
   - the retrieval intent and retrieval query
   - the current evidence set
   - the confirmed IIS
4. the Action Guide is stored as its own artifact version
5. the workbench switches into Action Guide mode

### IIS reopen and Action Guide invalidation

The confirmed IIS is not permanently locked.

Instead:

1. the IIS can be reopened for editing
2. the workbench switches back to IIS mode
3. once the IIS changes, the current Action Guide is marked outdated
4. the IIS must be reconfirmed to regenerate the Action Guide from the latest version

### Action Guide refinement

Once the Action Guide exists:

1. the right-hand refine flow targets the Action Guide instead of the IIS
2. the Action Guide is refined as its own artifact stream
3. the guide keeps a link to the IIS version it was generated from

### Restore flow

Restore does not erase history.

Instead:

1. a previous IIS or Action Guide version is selected
2. its stored artifact payload is loaded
3. a new artifact version is created from that older state

This keeps the historical chain intact while allowing rollback in the UI.

## Prompt architecture

Prompt assets live in:

- `/apps/api/src/prompts/`

### Active system prompts

- `/apps/api/src/prompts/retrieval_intent_system.txt`
- `/apps/api/src/prompts/retrieval_query_system.txt`
- `/apps/api/src/prompts/draft_generation_system.txt`
- `/apps/api/src/prompts/refine_open_questions_system.txt`
- `/apps/api/src/prompts/implementation_action_guide_system.txt`
- `/apps/api/src/prompts/refine_implementation_action_guide_system.txt`

### Few-shot strategy

The system currently keeps few-shot interfaces available for multiple stages, but only one path is actively emphasized:

- `/apps/api/src/prompts/retrieval_query_fewshot.json`

This file is intended for `description -> retrieval query` examples.

The following files are preserved as future extension points and can remain empty arrays until needed:

- `/apps/api/src/prompts/retrieval_intent_fewshot.json`
- `/apps/api/src/prompts/draft_generation_fewshot.json`
- `/apps/api/src/prompts/refine_open_questions_fewshot.json`
- `/apps/api/src/prompts/implementation_action_guide_fewshot.json`

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

- the current `Implementation Intent Specification`
- IIS editing and confirmation
- the current `Implementation Action Guide`
- source-version and outdated state display

### Right panel: Review & History

This panel is used for:

- open questions
- refinement input
- version history

The refine target changes by mode:

- in IIS mode, refine updates the IIS
- in Action Guide mode, refine updates the Action Guide

### UI progress model

The UI intentionally exposes workflow phases rather than percentage progress.

Typical phases include:

- generating retrieval intent
- generating retrieval query
- searching code evidence
- generating the IIS
- refining the IIS
- generating the Action Guide
- refining the Action Guide
- re-running retrieval
- restoring version
- confirming the IIS

Partial results are exposed progressively so the UI can show:

- Epic context first
- retrieval intent next
- evidence next
- draft last

instead of waiting for the full workflow to finish before rendering anything.

### Current Epic source behavior

The current browser workbench exposes Jira import as the active Epic-loading path.

The local Epic repository still exists in the backend for:

- development support
- repository-level normalization
- compatibility with imported Epic-shaped payloads

but it is no longer the main interactive import path in the UI.

## Persistence and traceability

The current SQLite-backed structure is intentionally designed to support later trace-pack generation.

Important persisted elements already include:

- session metadata
- retrieval versions
- evidence snapshots
- IIS versions
- Action Guide versions
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

The current codebase keeps local persistence and local retrieval, but several boundaries are already aligned with future expansion.

### Future Epic providers

The current Jira-backed browser path and the local repository normalization layer are intentionally separated so that additional Epic providers can later be added without changing the workflow orchestration model.

### Future persistence backends

SQLite is currently the local persistence backend, but the repository-style persistence layer can later be replaced with a cloud database implementation.

### Future trace pack and audit export

The current event and version structure is already suitable for later trace-pack generation, confirmation snapshots, and audit-style export.

## Summary

The current architecture is centered on:

- explicit workflow actions
- Jira-based Epic import in the current UI
- prompt-driven generation stages
- local retrieval evidence
- structured draft version history
- SQLite-based local persistence
- future-friendly boundaries for providers, persistence, and trace export

This keeps the current demo usable while preserving a clean path toward more formal cloud-backed workflow evolution.
