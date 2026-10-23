# LocalBachelor

Local-first, multi-user RAG research assistant. You ask questions about your
own materials (and optionally the web) and get answers that are **grounded
only in retrieved sources**, cite every claim with validated markers, and are
refused explicitly when the sources do not contain the answer. Runs as a
single Python process (app + in-process job worker) on your machine or a
small server, with fully local, keyless providers when you want it.

Source of truth: `architecture.md`. Process: `roadmap.md`.

## Requirements

- Python 3.12
- Optional sidecars for local providers: `llama-server` (chat and/or
  embeddings) and/or SearXNG — see [Sidecar recipes](#sidecar-recipes).
  DuckDuckGo search needs no server at all.

## First run

1. Create a virtualenv and install:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   ```

   Add the `gguf` extra (`pip install -e ".[dev,gguf]"`) only if you want the
   post-MVP in-process GGUF embedding loader (task 2.10).

2. Create the bootstrap config (gitignored):

   ```sh
   cp config.example.yaml config.yaml
   ```

   Then generate the two keys and paste them into `config.yaml`, replacing
   the `CHANGE_ME_...` placeholders:

   ```sh
   # secret_key — signs the session cookie + CSRF token
   python -c "import secrets; print(secrets.token_urlsafe(32))"

   # settings_key — Fernet key encrypting stored provider API keys
   pip install cryptography
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Semantics, loss consequences, and the rotation procedure:
   [Bootstrap keys](#bootstrap-keys).

3. Start the app from the repo root:

   ```sh
   python -m app.main --config config.yaml
   ```

   `host`/`port` are read from `config.yaml` (default `127.0.0.1:8000`).
   On a Linux server, run it as a service instead: `deploy/local-bachelor.service`
   (user-level or system-level install; both shown in the unit's comments).

4. Smoke check — the Phase 0 skeleton serves exactly one endpoint:

   ```sh
   curl http://127.0.0.1:8000/healthz
   # → {"status":"ok"}
   ```

   Stop the server with Ctrl+C (or `systemctl stop local-bachelor`).

5. The admin account will be created by the first-run wizard (task 1.2,
   Phase 1), which also picks the UI language (en/uk). Until Phase 1 lands
   there is no login page, no UI, and no user data.

## Bootstrap keys

`config.yaml` holds exactly two secrets plus `db_path`, `data_dir`, `host`,
`port`. Everything else is in the DB settings store, edited via the UI.

| Key | Purpose | Generate | Losing it |
|---|---|---|---|
| `secret_key` | Signs the auth session cookie and the CSRF token | `python -c "import secrets; print(secrets.token_urlsafe(32))"` | All active sessions are invalidated → users re-login. No data is lost. |
| `settings_key` | Fernet key encrypting stored provider API keys at rest in the DB | `pip install cryptography` then `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | Stored provider keys are **unrecoverable** → re-enter them via the Settings UI. Local/keyless deployments are unaffected. |

Storage: the keys live only in `config.yaml` on the host (gitignored).
Provider keys, when key storage is opted into, are Fernet-encrypted in the
database. Key storage is optional — local providers are keyless.
Backup/restore **including `settings_key`** is a Phase 8 gate for public
deploys.

### Rotation and re-encryption procedure (settings_key)

Implemented with task 2.2. Until then the procedure is the documented target:

1. Back up `config.yaml` (both keys) and the data directory.
2. Generate a new `settings_key` (Fernet).
3. Update `config.yaml` with the new key; keep the old key available.
4. Restart the app, then run the re-encryption routine: it decrypts stored
   provider keys with the old key and re-encrypts them with the new key.
5. Verify each provider with its Test button; keep the backup until
   confirmed.

Rotating `secret_key` needs no re-encryption — it only invalidates active
sessions.

## Sidecar recipes

Local providers run outside LocalBachelor; you manage them. The Settings UI
(Phase 2) connects to them.

### Chat — serve any GGUF model with llama-server (one command)

```sh
llama-server -m /path/to/Qwen3.8-27B-Q4_0.gguf --port 8080
```

Then in Settings → chat: pick **OpenAI-compatible**, base URL
`http://127.0.0.1:8080/v1`, model id = the name the server reports (usually
the GGUF filename).

### Embeddings — serve any GGUF embedding model with llama-server (one command)

```sh
llama-server -m /path/to/embedding-model.gguf --embedding --port 8081
```

Then in Settings → embeddings: **OpenAI-compatible**, base URL
`http://127.0.0.1:8081/v1`, model id as reported. Changing the embedding
model (id / dim / prefixes) makes the old index incompatible and requires a
full re-index (architecture.md §5).

### Web search

- **DuckDuckGo** — keyless, no server needed; the default.
- **SearXNG** — self-host (e.g. the official `searxng/searxng` Docker image)
  or point at any public instance; enter the instance URL in Settings →
  search.

Search results are candidate cards only: snippets never enter the chat
context, and "Add to sources" is an explicit per-card action.

## Configuration

`config.yaml` (gitignored) = bootstrap only: `secret_key`, `settings_key`,
`db_path`, `data_dir`, `host` (default `127.0.0.1`; `0.0.0.0` for LAN opt-in
with UI warning), `port`. All other settings live in the DB and are edited
via the UI.

## Evaluations

- `evals/docs/` — two sample documents, ~500 words each, with numbered
  sections: `en_bear_monitoring.md` (EN) and `uk_bear_population.md` (UK).
- `evals/fixtures/sample_qa.json` — 8 QA fixtures in the
  architecture.md §16 format, four per document, covering all four fixture
  types (`exact_name`, `paraphrase`, `multi_source`, `unanswerable`).
  `unanswerable` fixtures have empty `expected` and feed the abstention
  tier; they are excluded from recall.

Convention: `expected.chunk_ids` / `expected.source_ids` use stable document
anchors (`<doc-stem>#<section>`, e.g. `en_bear_monitoring#2`) because the
sample documents are not ingested into a live DB yet. The eval runner
(task 4.4) resolves the anchors to real chunk/source ids after ingestion.

## Status

Phase 0 (bootstrap) in progress — see `roadmap.md` for the task list and
`architecture.md` for all normative contracts.
