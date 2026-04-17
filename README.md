# BMWcode Offline MVP

This is a thin offline workbench for validating the `Epic description -> code evidence -> What to Do draft -> refine` loop before wiring in live Jira and production RAG services.

## Requirements

- Python 3.12+
- A directory of Epic JSON files

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

The current `requirements.txt` is intentionally empty because this version uses Python standard library only.

## Single-file configuration

The main integration knobs are centralized in [config.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/config.py).

That file defines:

- Epic data directory
- Code RAG local/remote settings
- Enterprise LLM local/remote settings
- Host and port defaults

You can either:

1. Change defaults in `apps/api/src/config.py`
2. Or override them with environment variables

The adapter implementation points are:

- Code RAG: [code_rag_adapter.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/integrations/code_rag_adapter.py)
- LLM: [llm_adapter.py](/Users/ztcc123/Desktop/BMWcode/apps/api/src/modules/integrations/llm_adapter.py)

By default both use `mode="mock"`. Supported mode names are:

- `mock`
- `local`
- `remote`

To connect real services, keep the business flow unchanged and only replace the logic inside those adapter files.

## Run

From the repository root:

```bash
python run.py
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Cross-platform startup

macOS / Linux:

```bash
python3 run.py
```

Windows PowerShell:

```powershell
py run.py
```

Windows Command Prompt:

```cmd
python run.py
```

## Optional configuration

You can override host, port, and Epic data directory either with CLI flags or environment variables.

CLI flags:

```bash
python run.py --host 0.0.0.0 --port 8010 --data-dir ./data/epics
```

Environment variables:

- `BMWCODE_HOST`
- `BMWCODE_PORT`
- `BMWCODE_EPIC_DATA_DIR`
- `BMWCODE_CODE_RAG_MODE`
- `BMWCODE_CODE_RAG_EMBEDDING_MODEL_PATH`
- `BMWCODE_CODE_RAG_VECTOR_STORE_PATH`
- `BMWCODE_CODE_RAG_METADATA_PATH`
- `BMWCODE_CODE_RAG_DEVICE`
- `BMWCODE_CODE_RAG_ENDPOINT`
- `BMWCODE_CODE_RAG_INDEX`
- `BMWCODE_CODE_RAG_API_KEY`
- `BMWCODE_CODE_RAG_ACCESS_TOKEN`
- `BMWCODE_CODE_RAG_TOP_K`
- `BMWCODE_LLM_MODE`
- `BMWCODE_LLM_LOCAL_MODEL_PATH`
- `BMWCODE_LLM_DEVICE`
- `BMWCODE_LLM_ENDPOINT`
- `BMWCODE_LLM_MODEL`
- `BMWCODE_LLM_API_KEY`
- `BMWCODE_LLM_ACCESS_TOKEN`
- `BMWCODE_LLM_TIMEOUT_SECONDS`

macOS / Linux example:

```bash
BMWCODE_PORT=8010 BMWCODE_EPIC_DATA_DIR=./data/epics python3 run.py
```

Windows PowerShell example:

```powershell
$env:BMWCODE_PORT="8010"
$env:BMWCODE_EPIC_DATA_DIR=".\data\epics"
py run.py
```

Local-first example:

```bash
BMWCODE_CODE_RAG_MODE=local \
BMWCODE_CODE_RAG_EMBEDDING_MODEL_PATH=/path/to/embedding-model \
BMWCODE_CODE_RAG_VECTOR_STORE_PATH=/path/to/vector-store \
BMWCODE_LLM_MODE=local \
BMWCODE_LLM_LOCAL_MODEL_PATH=/path/to/local-llm \
python3 run.py
```

Remote-serving example:

```bash
BMWCODE_CODE_RAG_MODE=remote \
BMWCODE_CODE_RAG_ENDPOINT=https://rag.example/api/search \
BMWCODE_LLM_MODE=remote \
BMWCODE_LLM_ENDPOINT=https://llm.example/api/chat \
BMWCODE_LLM_MODEL=my-model \
python3 run.py
```

## Data format

Each Epic remains a single JSON file. `description` and `whatToDo` can stay as large raw text blocks.

Example:

```json
{
  "id": "EPIC-001",
  "title": "Support conditional processing for feature flow",
  "description": "Raw epic description text...",
  "whatToDo": "Raw historical what-to-do text..."
}
```

## Current scope

- Local Epic JSON files in `data/epics`
- Runtime parsing of raw `whatToDo` text
- Placeholder retrieval intent and evidence
- Placeholder draft generation and refine flow
- Static three-panel workbench UI

## Next steps

1. Replace placeholder retrieval with your real code RAG adapter.
2. Replace placeholder draft/refine logic with the enterprise LLM client.
3. Add evaluation helpers to compare generated output against historical `whatToDo`.
4. Add Jira provider support once the offline flow is stable.
