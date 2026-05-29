# Agentic-Workflow Architecture

Agentic-Workflow is an Epic analysis workflow for turning Epic descriptions into an `Implementation Intent Specification`, then deriving `Software Requirements` from the confirmed IIS, with explicit retrieval, refinement, version history, and local persistence.

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
- IIS confirmation
- IIS reopen
- Software Requirements generation
- Software Requirements refinement
- retrieval re-run
- version restore
- partial progress exposure to the UI while generation is still running

This layer remains intentionally independent from the details of:

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

This layer also supports imported Epic-shaped payload normalization and remains the natural place for Epic-source expansion.

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
- Software Requirements generation
- IIS refinement and open-question regeneration
- Software Requirements refinement
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
- Software Requirements version history
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

The current IIS output is intentionally more execution-oriented than an earlier high-level summary. It is the single editable implementation artifact that sits between the raw Epic description and the downstream Software Requirements.

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
2. reuses the original Epic description plus the latest user answers and notes
3. regenerates retrieval intent and retrieval query
4. reruns local code retrieval
5. stores a new retrieval version
6. regenerates the IIS from the updated retrieval result
7. stores the new IIS as a new artifact version

This separation is intentional so that:

- IIS refinement remains lightweight and predictable
- retrieval changes are explicit
- version history can distinguish pure IIS edits from evidence changes

### IIS confirmation

After the IIS is ready, the workflow can move into a confirmed state:

1. the current IIS version is confirmed
2. the confirmed IIS version id is recorded in session state
3. the IIS becomes read-only in the workbench
4. the workbench switches into Software Requirements mode

Confirmation does not automatically generate Software Requirements. It only locks the current IIS as the approved upstream source.

### Software Requirements generation

Once the IIS is confirmed:

1. the user explicitly triggers Software Requirements generation
2. `Software Requirements` are generated from:
   - the original Epic description
   - the confirmed IIS
3. the Software Requirements are stored as their own artifact version
4. the workbench stays in Software Requirements mode

### IIS reopen and Software Requirements invalidation

If the user decides to revise the IIS after confirmation:

1. the IIS is reopened
2. the workbench switches back into IIS mode
3. once the IIS changes, the current Software Requirements are marked outdated
4. the IIS must be reconfirmed to regenerate Software Requirements from the latest version

### Software Requirements refinement

Once the Software Requirements exist:

1. the right-hand refine flow targets the Software Requirements instead of the IIS
2. the Software Requirements are refined as their own artifact stream
3. manual edits in the Software Requirements editor are synced back before refine calls

### Version restore

The current restore model is artifact-aware:

1. a previous IIS or Software Requirements version is selected
2. the restored content is re-saved as a new current version
3. session pointers are updated to that newly restored version
4. mode and outdated state are recomputed from the restored artifact type and source IIS linkage

The system does not delete later versions when restoring. Restore produces a new head version.

## Prompt system

Prompt assets live in:

- `/apps/api/src/prompts/`

### Active prompt files

- `/apps/api/src/prompts/retrieval_intent_system.txt`
- `/apps/api/src/prompts/retrieval_query_system.txt`
- `/apps/api/src/prompts/draft_generation_system.txt`
- `/apps/api/src/prompts/refine_open_questions_system.txt`
- `/apps/api/src/prompts/software_requirements_system.txt`
- `/apps/api/src/prompts/refine_software_requirements_system.txt`

### Few-shot assets

- `/apps/api/src/prompts/retrieval_query_fewshot.json`
  Active few-shot examples for `description -> retrieval query`.

- `/apps/api/src/prompts/software_requirements_fewshot.json`
  Preferred few-shot direction for the Software Requirements stage. Current strategy is to use clean `description -> software requirements` paired examples rather than noisy assumptions or historical low-quality `whatToDo`.

Other prompt few-shot files remain available as extension points but are currently optional or empty.

## Data-model boundaries

The main workflow artifacts are currently:

### Retrieval intent

Represents:

- Epic understanding for code retrieval
- a technical summary
- keywords
- suspected areas
- the retrieval query

### Implementation Intent Specification

Represents:

- fine-grained implementation rules
- `what_to_do`-style behavior decomposition
- `where_to_change` guidance
- open questions for uncertainty handling

It is stored as the main upper editable artifact.

### Software Requirements

Represents:

- atomic `shall` requirements
- traceability summary back to Epic and IIS inputs
- a downstream, testable, implementation-independent requirements layer

It is stored as a separate artifact stream and records which IIS version it was generated from.

## Persistence model

The SQLite store uses:

- a single `draft_versions` table for multiple artifact streams
- an `artifact_type` field to distinguish IIS and Software Requirements
- event records for workflow traceability
- session pointers for current IIS version, current Software Requirements version, and confirmed IIS version

This model keeps the schema compact while still allowing:

- separate artifact histories
- restore operations
- outdated detection
- source-IIS traceability for Software Requirements

## Frontend behavior model

The browser workbench currently has three main areas:

### Left panel

- Epic context
- retrieval intent
- code evidence
- explicit retrieval re-run action

### Center panel

- `Implementation Intent Specification`
- confirm / reopen IIS controls
- explicit Software Requirements generation
- `Software Requirements` editor

### Right panel

- open questions
- refine workflow
- version history

The refine target changes by mode:

- in IIS mode, refine updates the IIS
- in Software Requirements mode, refine updates the Software Requirements

## Current workflow guarantees

The current implementation explicitly preserves the following product behaviors:

- generation and refinement are separate operations
- retrieval re-run is explicit rather than implicit
- IIS confirmation is a distinct phase change
- Software Requirements generation is explicit and independently triggerable
- reopening the IIS does not destroy existing downstream versions
- manual edits are synced before refinement or downstream generation
- version history restores create new current versions instead of rewriting history

## Extension directions

The current architecture is already shaped so that future work can add:

- richer Jira browsing and filtering
- better Software Requirements few-shot coverage
- trace-pack export APIs
- cloud persistence
- stronger requirements traceability or downstream tool export

without changing the core workflow shape of:

Epic description -> retrieval -> IIS -> confirm/reopen -> Software Requirements
