# workflow

This repository contains an Epic analysis workbench. Local Epic JSON files are read, retrieval intent is produced from Epic descriptions, code evidence is retrieved from a local vector store, and a `What to Do` draft with open questions is generated and refined in a web UI.

The current default pipeline is:

1. Local Epic data is loaded.
2. Retrieval intent is generated from the Epic description.
3. Code evidence is retrieved from a local embedding model and local vector store.
4. A `What to Do` draft is generated through the configured LLM path.
5. Refinement is performed in the browser UI with historical `whatToDo` shown as reference.

## Repository layout

### Project entry points

- [run.py](/Users/ztcc123/Desktop/BMWcode/run.py)
  Cross-platform startup entry point.

- [requirements.txt](/Users/ztcc123/Desktop/BMWcode/requirements.txt)
  Python dependency manifest.

- [.env](/Users/ztcc123/Desktop/BMWcode/.env)
  Active runtime configuration file.

- [.env.example](/Users/ztcc123/Desktop/BMWcode/.env.example)
  Configuration template.

### Backend

- [apps/api/src/server.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/server.py)
  HTTP server entry point. API routes, session orchestration, and end-to-end flow wiring are defined here.

- [apps/api/src/config.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/config.py)
  Central non-sensitive configuration file. Default runtime behavior, local RAG behavior, and LLM defaults are defined here.

- [apps/api/src/prompt_loader.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompt_loader.py)
  Prompt and few-shot loading utilities are defined here.

### Epic loading

- [apps/api/src/modules/epics/repository.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/epics/repository.py)
  Local Epic JSON files are normalized here. `id`, `title`, `description`, and `whatToDo` are extracted here.

- [apps/api/src/modules/epics/what_to_do_parser.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/epics/what_to_do_parser.py)
  Raw `whatToDo` text is parsed into structured steps and files-to-change data here.

### Integration layer

- [apps/api/src/modules/integrations/code_rag_adapter.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/integrations/code_rag_adapter.py)
  Code retrieval modes are implemented here. Support is structured for:
  - `mock`
  - `local`
  - `remote`

  The current default is `local`, where:
  - a local embedding model is loaded,
  - a local vector store is loaded,
  - a query is derived from retrieval intent,
  - vector retrieval is executed locally.

- [apps/api/src/modules/integrations/llm_adapter.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/integrations/llm_adapter.py)
  LLM execution modes are implemented here. Support is structured for:
  - `mock`
  - `local`
  - `remote`

  The current default is `remote`, where:
  - a token may be acquired through M2M credentials,
  - `Bearer` authentication and `x-apikey` are attached,
  - retrieval intent, draft generation, and refinement are driven by prompt files.

### Session and shared models

- [apps/api/src/modules/sessions/store.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/sessions/store.py)
  In-memory session state is stored here.

- [apps/api/src/modules/shared/models.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/shared/models.py)
  Shared internal data models are defined here.

### Prompt and few-shot assets

Directory:
[apps/api/src/prompts](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompts)

Key files:

- [retrieval_intent_system.txt](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompts/retrieval_intent_system.txt)
  The retrieval-intent system prompt is defined here.

- [retrieval_intent_fewshot.json](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompts/retrieval_intent_fewshot.json)
  Few-shot retrieval-intent references are stored here.

- [draft_generation_system.txt](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompts/draft_generation_system.txt)
  The `What to Do` generation prompt is defined here.

- [draft_generation_fewshot.json](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompts/draft_generation_fewshot.json)
  Few-shot draft-generation references are stored here.

- [refine_open_questions_system.txt](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompts/refine_open_questions_system.txt)
  The refinement and open-question prompt is defined here.

- [refine_open_questions_fewshot.json](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompts/refine_open_questions_fewshot.json)
  Few-shot refinement references are stored here.

### Frontend

- [apps/web/index.html](/Users/ztcc123/Desktop/BMWcode/apps/web/index.html)
  The page structure is defined here.

- [apps/web/styles.css](/Users/ztcc123/Desktop/BMWcode/apps/web/styles.css)
  Layout and styling are defined here, including the resizable three-panel layout.

- [apps/web/app.js](/Users/ztcc123/Desktop/BMWcode/apps/web/app.js)
  Browser-side interaction logic is implemented here. Epic loading, generate/refine/confirm actions, and panel resizing are handled here.

### Data and documentation

- [data/epics](/Users/ztcc123/Desktop/BMWcode/data/epics)
  Local Epic JSON samples are stored here.

- [docs/architecture.md](/Users/ztcc123/Desktop/BMWcode/docs/architecture.md)
  Supplemental architecture notes are stored here.

## Current default runtime shape

The current default shape is:

- Epic source: local
- Code RAG: local
- LLM: remote

This is driven by:

- `DEFAULT_CODE_RAG["mode"] = "local"`
- `DEFAULT_LLM["mode"] = "remote"`

## Configuration structure

### `.env`

Runtime-sensitive and machine-specific values are intended to be placed in:
[.env](/Users/ztcc123/Desktop/BMWcode/.env)

Typical values placed there are:

- local absolute paths,
- remote endpoints,
- certificate paths,
- API keys,
- client IDs,
- client secrets,
- temporary overrides.

### `config.py`

Non-sensitive defaults are intended to be placed in:
[config.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/config.py)

The following configuration blocks are defined there:

- `USER_SERVER`
  Default host and port.

- `USER_PATHS`
  Default Epic-data and prompt-directory paths.

- `DEFAULT_CODE_RAG`
  Default local retrieval behavior, including:
  - mode,
  - embedding model path,
  - vector store path,
  - filtering rules,
  - ranking mode,
  - retrieval depth.

- `DEFAULT_LLM`
  Default LLM behavior, including:
  - mode,
  - model name,
  - timeout,
  - temperature,
  - max tokens,
  - remote request defaults.

## What is edited where

### Runtime values

The following file is intended to be edited first:
[.env](/Users/ztcc123/Desktop/BMWcode/.env)

The most commonly edited fields are:

- `BMWCODE_CODE_RAG_EMBEDDING_MODEL_PATH`
- `BMWCODE_CODE_RAG_VECTOR_STORE_PATH`
- `BMWCODE_LLM_ENDPOINT`
- `BMWCODE_LLM_API_PATH`
- `BMWCODE_LLM_CERT_PATH`
- `BMWCODE_LLM_AUTH_URL`
- `BMWCODE_LLM_API_KEY`
- `BMWCODE_LLM_CLIENT_ID`
- `BMWCODE_LLM_CLIENT_SECRET`

If the Epic directory differs from the default, this field is also edited:

- `BMWCODE_EPIC_DATA_DIR`

### Default behavior

The following file is intended to be edited when non-sensitive defaults should change:
[config.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/config.py)

Typical edits include:

- local retrieval mode,
- local retrieval filters,
- `top_k`,
- allowed extensions,
- LLM mode,
- model name,
- temperature,
- `max_tokens`,
- default timeout.

### Prompt behavior

The following directory is intended to be edited when model behavior should change:
[apps/api/src/prompts](/Users/ztcc123/Desktop/BMWcode/apps/api/src/prompts)

Typical edits include:

- retrieval-query shaping,
- retrieval-intent structure,
- `What to Do` output format,
- historical style alignment,
- open-question behavior,
- refinement behavior.

### Adapter logic

The following files are intended to be edited when low-level integration behavior should change:

- [code_rag_adapter.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/integrations/code_rag_adapter.py)
- [llm_adapter.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/integrations/llm_adapter.py)

## Configuration precedence

The current precedence is:

1. Shell environment variables exported before startup
2. Values loaded from `.env`
3. Defaults defined in `config.py`

Because `.env` is loaded with `override=False`, explicit shell exports take precedence over `.env`.

## Path compatibility

The current path resolver supports:

- relative paths,
- macOS/Linux absolute paths,
- Windows absolute paths such as `C:\work\...` or `C:/work/...`,
- UNC paths such as `\\server\share\...`

This allows the same repository to be moved between macOS and Windows with only configuration-path changes.

## Run

Dependencies are installed with:

```bash
python -m pip install -r requirements.txt
```

The application is started with:

```bash
python run.py
```

The workbench is then available at:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## Current implementation status

The following parts are already wired into the flow:

- local Epic loading,
- raw `whatToDo` parsing,
- prompt and few-shot loading,
- remote BMW-style LLM calls,
- local code-RAG retrieval flow,
- browser workbench UI.

The following extension points remain available:

- `local` LLM mode,
- `remote` code-RAG mode,
- richer evidence rendering,
- live Jira integration.

## Recommended first validation order

1. Values are filled in [`.env`](/Users/ztcc123/Desktop/BMWcode/.env).
2. Default modes are reviewed in [config.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/config.py).
3. The server is started with `python run.py`.
4. An Epic is selected in the UI.
5. Retrieval intent is inspected.
6. Retrieved evidence is inspected.
7. Generated draft quality is compared against historical `whatToDo`.
