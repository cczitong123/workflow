# Agentic-Workflow Architecture

This repository contains a local-first workflow for generating `What to Do` drafts from Epic descriptions.

## Current implementation shape

- `data/epics/*.json`
  Each Epic remains in one JSON file. `description` is kept as raw text. Historical `whatToDo` content is preserved and parsed at runtime.

- `apps/api/src/`
  Backend logic, configuration, prompt loading, integration adapters, Epic parsing, and session state are implemented here.

- `apps/web/`
  Static browser workbench UI is implemented here.

- `run.py`
  Cross-platform startup entry point.

## Main flow

1. A local Epic is selected in the UI.
2. A session is created.
3. Retrieval intent is generated from the Epic description.
4. Code evidence is retrieved from the configured Code RAG mode.
5. Historical `whatToDo` samples are loaded as references.
6. A first draft is generated.
7. Refinement and open-question handling are performed in the workbench.
8. The latest draft is confirmed and exported.

## Integration boundaries

### LLM integration

LLM behavior is defined in:

- `apps/api/src/modules/integrations/llm_adapter.py`

Supported runtime modes:

- `mock`
- `local`
- `remote`

### Code RAG integration

Code retrieval behavior is defined in:

- `apps/api/src/modules/integrations/code_rag_adapter.py`

Supported runtime modes:

- `mock`
- `local`
- `remote`

## Runtime configuration

Configuration is split into two layers:

- `apps/api/src/config.py`
  Non-sensitive defaults and mode selection.

- `.env`
  Machine-specific values, endpoints, credentials, and path overrides.

The environment-variable prefix is:

- `AGENTIC_WORKFLOW_*`
