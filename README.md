# Agentic-Workflow

Agentic-Workflow is a local-first workbench for turning Epic descriptions into a structured `What to Do` draft.

The current pipeline is:

1. Epic JSON data is loaded from `data/epics/`.
2. Retrieval intent is generated from the Epic description.
3. Code evidence is retrieved from a local embedding model and local vector store.
4. A `What to Do` draft is generated through the configured LLM mode.
5. Refinement and open-question handling are performed in the browser UI.

## Repository structure

### Entry points

- `run.py`
  Cross-platform startup entry point.

- `requirements.txt`
  Python dependency manifest.

- `.env`
  Active runtime configuration file.

- `.env.example`
  Runtime configuration template.

## Backend structure

### Core runtime

- `apps/api/src/server.py`
  HTTP server entry point. Route handling, session orchestration, and end-to-end workflow wiring are defined here.

- `apps/api/src/config.py`
  Central runtime configuration. Non-sensitive defaults are grouped here. Environment-variable overrides are resolved here.

- `apps/api/src/prompt_loader.py`
  Prompt text and few-shot assets are loaded here.

### Epic ingestion

- `apps/api/src/modules/epics/repository.py`
  Epic JSON files are read and normalized here.

- `apps/api/src/modules/epics/what_to_do_parser.py`
  Historical `whatToDo` text is parsed into structured steps and file-change entries here.

### Integration layer

- `apps/api/src/modules/integrations/llm_adapter.py`
  Retrieval-intent generation, draft generation, and refinement are dispatched here. `mock`, `local`, and `remote` modes are defined here.

- `apps/api/src/modules/integrations/code_rag_adapter.py`
  Code-evidence retrieval is dispatched here. `mock`, `local`, and `remote` modes are defined here.

### Session and shared models

- `apps/api/src/modules/sessions/store.py`
  In-memory session state is stored here.

- `apps/api/src/modules/shared/models.py`
  Shared internal models are defined here.

## Prompt assets

Prompt files are stored in:

- `apps/api/src/prompts/`

Key prompt files:

- `retrieval_intent_system.txt`
  System prompt for summarizing the Epic and producing retrieval intent.

- `retrieval_intent_fewshot.json`
  Few-shot examples for retrieval-intent output shape and style.

- `draft_generation_system.txt`
  System prompt for generating the `What to Do` draft.

- `draft_generation_fewshot.json`
  Few-shot examples for draft output shape and style.

- `refine_open_questions_system.txt`
  System prompt for refinement and open-question generation.

- `refine_open_questions_fewshot.json`
  Few-shot examples for refinement behavior.

## Frontend structure

- `apps/web/index.html`
  Static page structure.

- `apps/web/styles.css`
  Layout and styling, including the resizable multi-panel workbench layout.

- `apps/web/app.js`
  Browser-side interaction logic, including Epic loading, draft generation, refinement, confirmation, and panel resizing.

## Data and supporting documentation

- `data/epics/`
  Local Epic JSON files.

- `docs/architecture.md`
  Supplemental implementation notes.

## Runtime modes

The current default runtime shape is:

- Epic source: local
- Code RAG: local
- LLM: remote

The runtime modes are controlled in `apps/api/src/config.py` and can be overridden through `.env`.

## What is edited where

### Runtime values and secrets

The following file is edited for machine-specific paths, endpoints, and secrets:

- `.env`

Typical values edited there:

- `AGENTIC_WORKFLOW_EPIC_DATA_DIR`
- `AGENTIC_WORKFLOW_PROMPT_DIR`
- `AGENTIC_WORKFLOW_CODE_RAG_EMBEDDING_MODEL_PATH`
- `AGENTIC_WORKFLOW_CODE_RAG_VECTOR_STORE_PATH`
- `AGENTIC_WORKFLOW_LLM_ENDPOINT`
- `AGENTIC_WORKFLOW_LLM_API_PATH`
- `AGENTIC_WORKFLOW_LLM_CERT_PATH`
- `AGENTIC_WORKFLOW_LLM_AUTH_URL`
- `AGENTIC_WORKFLOW_LLM_API_KEY`
- `AGENTIC_WORKFLOW_LLM_CLIENT_ID`
- `AGENTIC_WORKFLOW_LLM_CLIENT_SECRET`

### Non-sensitive defaults

The following file is edited for default runtime behavior:

- `apps/api/src/config.py`

Typical edits include:

- Code RAG mode
- local retrieval filters
- `top_k`
- allowed extensions
- LLM mode
- model name
- timeout
- temperature
- `max_tokens`

### Prompt behavior

The following directory is edited when model behavior should change:

- `apps/api/src/prompts/`

Typical prompt edits include:

- retrieval-intent format
- retrieval-query guidance
- `What to Do` structure
- open-question style
- refinement behavior

### Integration behavior

The following files are edited when low-level integration logic should change:

- `apps/api/src/modules/integrations/code_rag_adapter.py`
- `apps/api/src/modules/integrations/llm_adapter.py`

## Configuration precedence

The current precedence is:

1. Environment variables exported before startup
2. Values loaded from `.env`
3. Defaults defined in `apps/api/src/config.py`

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

- `http://127.0.0.1:8000`
