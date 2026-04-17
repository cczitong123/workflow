# Offline MVP Architecture

This repository now contains a thin offline MVP for generating `What to Do` drafts from local Epic JSON data.

## Current shape

- `data/epics/*.json`
  - Each Epic stays in a single JSON file.
  - `description` remains a raw text block.
  - `whatToDo` remains a raw text block.
- `apps/api/src`
  - Python standard-library API server.
  - Local Epic repository.
  - `whatToDo` runtime parser.
  - Placeholder retrieval and draft services.
- `apps/web`
  - Static three-panel workbench UI.
- `run.py`
  - Cross-platform entry point.
- `requirements.txt`
  - Dependency manifest, currently standard-library only.

## Why this shape

- No destructive migration of the existing Epic corpus.
- `description` can stay free-form for LLM summarization.
- `whatToDo` is parsed at read time so we can:
  - reuse historical samples as style references,
  - compare generated output to historical answers,
  - keep the original text untouched if parsing rules evolve later.

## Main flow

1. User selects a local Epic.
2. API creates a session.
3. API builds a retrieval intent from the Epic description.
4. API fetches placeholder evidence.
5. API loads historical `whatToDo` samples from other Epic JSON files.
6. API generates a first `What to Do` draft.
7. User refines the draft in the workbench.
8. API stores draft versions and exports the final text on confirm.

## Intended integration points

- Replace the logic in `modules/integrations/code_rag_adapter.py`
  - `mode="local"` for local embedding model + local vector store.
  - `mode="remote"` for an HTTP-backed RAG service.
- Replace the logic in `modules/integrations/llm_adapter.py`
  - `mode="local"` for local draft/refine testing.
  - `mode="remote"` for endpoint-based generation and refinement.
- Extend `modules/epics/repository.py`
  - Add a future Jira provider while keeping the same internal models.

## Runtime configuration

The app can be configured through either CLI flags or environment variables:

- `--host` / `BMWCODE_HOST`
- `--port` / `BMWCODE_PORT`
- `--data-dir` / `BMWCODE_EPIC_DATA_DIR`
- `BMWCODE_CODE_RAG_*`
- `BMWCODE_LLM_*`

This keeps the same code runnable on macOS, Windows, and Linux without hard-coded machine paths.
