# LocalBachelor — Architecture (v1)

Source of truth for implementation (roadmap rule 1). Based strictly on
**Locked stack decisions (v3)** in `roadmap.md`. This is the last roadmap
iteration: any further change goes through the **Open decisions** section
below, then into this file. Sections marked **(contract)** are normative —
implementation and tests must match them; deviations require an
"Open decisions" entry first.

Docs-sync tasks at the end of each phase keep this file consistent.

## 1. Locked stack (v3)

| Area | Decision |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite (WAL) |
| Vectors / BM25 | sqlite-vec + FTS5 in the same DB file; `VectorStore` interface allows Qdrant later |
| Frontend | Jinja2 + HTMX + Alpine.js + vanilla JS (SSE); single CSS file with theme variables; NO Node build |
| Workers | DB job queue + in-process worker thread (see Job lifecycle) |
| Auth | Session cookie + argon2id; roles admin/user; lean multi-user (admin creates accounts; no RBAC beyond roles) |
| Tenant isolation | Owner-scoped everything (see Tenant isolation, contract) |
| Secrets | Two bootstrap keys: `secret_key` (sessions/CSRF), `settings_key` (Fernet for stored provider keys) |
| Chat | Generic OpenAI-compatible provider FIRST; OpenAI/Anthropic/Google/OpenRouter post-MVP; streaming mandatory |
| Embeddings | OpenAI-compatible HTTP adapter FIRST (covers GGUF via `llama-server --embedding`); llama-cpp-python and sentence-transformers post-MVP; query/passage prefixes, L2-normalize, cosine |
| Search | SearXNG + DuckDuckGo (keyless) FIRST; Serper/Brave/Tavily post-MVP |
| Grounding | Answer only from retrieved sources; cite every claim; never from memory; state absence explicitly |
| Modes | "sources only" / "+web" request-scoped flag |
| Prompt | RAG system prompt template is English; reply in the user's message language |
| Deployment | systemd now; Docker Compose Phase 9 (non-breaking) |
| i18n | en/uk catalogs, per-user preference, header switcher |

## 2. Layout

Intended repository layout (files appear as their roadmap tasks land):

```
LocalBachelor/
├── roadmap.md                 # process; only tasks tick its checkboxes
├── architecture.md            # this file; source of truth
├── README.md                  # (0.2) first-run, keys, recipes, ops
├── pyproject.toml             # (0.2) pinned deps, ruff + pytest config
├── config.example.yaml        # (0.2) secret_key, settings_key, db_path, data_dir, host, port
├── config.yaml                # gitignored; ONLY bootstrap secrets + host/port
├── evals/                     # (0.2) 2 sample docs + QA pairs; fixture + run files
│   ├── docs/
│   └── fixtures/
├── scripts/
│   └── check.sh               # (0.5) quality gate: lint + typecheck + tests
├── deploy/
│   └── local-bachelor.service # (0.4) 4-line ExecStart systemd unit
├── app/
│   ├── main.py                # (0.3) app factory
│   ├── config.py              # (0.3) config loader (bootstrap keys only)
│   ├── core/                  # (0.3) logging, error middleware
│   ├── db/                    # engine (WAL), session, models, alembic/
│   ├── repositories/          # every query owner/project-scoped
│   ├── services/              # chunker, indexer, retrieval, chat pipeline,
│   │                          # citation validator, eval runner, job queue
│   ├── providers/
│   │   ├── chat/              # OpenAI-compatible (+ post-MVP adapters)
│   │   ├── embeddings/        # OpenAI-compatible HTTP (+ post-MVP)
│   │   ├── search/            # SearXNG, DuckDuckGo (+ post-MVP)
│   │   └── vectorstore/       # SQLiteVecStore; Qdrant later
│   ├── api/                   # FastAPI routers (see API contracts)
│   ├── i18n/                  # en/uk catalogs
│   ├── templates/             # Jinja2, single CSS, vanilla JS + HTMX + Alpine
│   └── static/
└── tests/                     # mirrors app/; negative isolation tests mandatory
```

Runtime data (gitignored): `data_dir/` holds the SQLite DB (WAL) and any
extracted artifacts.

## 3. Data model

SQLite, single file in `data_dir`, WAL mode. Vectors in a `vec0` virtual
table (sqlite-vec), BM25 in an FTS5 virtual table; both in the same DB
file, keyed by `chunks.id`. All timestamps ISO-8601 UTC. Rows marked
**[derived]** are implementation details implied by locked decisions, not
locked themselves.

### users
| column | notes |
|---|---|
| id | PK |
| username | UNIQUE NOT NULL |
| password_hash | argon2id |
| role | `admin` / `user` |
| lang | `en` / `uk`, default `en` |
| active | bool, default true |
| created_at | |

### projects
| column | notes |
|---|---|
| id | PK |
| owner_id | → users.id NOT NULL |
| name | NOT NULL |
| created_at | |

### sources
| column | notes |
|---|---|
| id | PK |
| project_id | → projects.id NOT NULL |
| owner_id | → users.id NOT NULL (denormalized for isolation filters) |
| kind | `file` / `url` |
| title | |
| url | nullable |
| retrieved_at | nullable; URL fetch time |
| published_at | nullable; extracted from content |
| file_sha256 | content hash of stored file |
| content_sha256 | URL dedupe key is (url, content_sha256) |
| parser_version | version of the extractor that produced text |
| active_index_version | NULL until first successful build; retrieval reads ONLY this version |
| status | [derived] `queued`/`indexing`/`ready`/`failed`/`cancelled`, driven by jobs |
| created_at / updated_at | |

### chunks
| column | notes |
|---|---|
| id | PK |
| source_id | → sources.id NOT NULL |
| parent_id | → chunks.id NULL; parent = heading-level section (parent expansion) |
| index_version | build version this chunk belongs to |
| file_sha256 | |
| parser_version | |
| chunk_config_version | integer, bumped on any chunking param change => re-index |
| embedding_model_id | |
| embedding_dim | |
| lang | `en` / `uk` / ... |
| page | nullable |
| char_start / char_end | offsets in the parsed document |
| token_count | counted in the **embedding** tokenizer |
| heading_path | [derived] heading hierarchy, e.g. `1.2.3` |
| text | chunk text (serves prompt building + FTS) |

Multiple `index_version` values coexist per source; retrieval joins to
`sources.active_index_version` (see Index lifecycle).

### index_builds
| column | notes |
|---|---|
| id | PK; UNIQUE(source_id, index_version) |
| source_id | |
| index_version | |
| embedding_model_id / embedding_dim | |
| chunk_config_version / parser_version / file_sha256 | the config fingerprint of this build |
| state | `building` / `active` / `retired` / `abandoned` |
| created_at / finished_at | |

`building` → `active` on the atomic swap; → `abandoned` on
failure/interruption (rows garbage-collected); `active` → `retired` when a
newer version takes over.

### jobs
| column | notes |
|---|---|
| id | PK |
| owner_id | NOT NULL; re-checked at execution time |
| kind | e.g. `source.index`, `source.cancel` |
| payload | JSON (source_id, params) |
| state | `queued` → `processing` → `done` / `failed` / `cancelled` |
| attempt_id | set per run (1-based) |
| attempt_token | random token set at claim; completion requires it |
| attempts | count of processing starts |
| max_retries | per kind |
| error | nullable |
| created_at / started_at / finished_at | |

See Job lifecycle (contract).

### conversations
`id`, `owner_id`, `project_id`, `title`, `created_at`. **[derived]** —
grouping model is an Open decision; kept minimal.

### messages
| column | notes |
|---|---|
| id | PK |
| conversation_id | → conversations.id |
| owner_id | NOT NULL |
| role | `user` / `assistant` |
| content | text |
| mode | `sources_only` / `web` (stored per message; UI badge) |
| citations | nullable JSON: **validated** per-marker state (see Citation contract) |
| provenance | nullable JSON: versioned provenance, assistant messages only (see Provenance schema) |
| created_at | |

A message row is written **only after** the citation validator has run
(contract).

### notes
`id`, `owner_id`, `project_id`, `message_id` (nullable; "save answer"),
`title`, `content`, `created_at`. Notes-as-context toggle is a request
flag, not a stored property.

### settings
`id`, `owner_id` (NULL = admin default), `key`, `value` (typed JSON),
`updated_at`. Per-user settings with admin-defaults fallback. Provider
API keys, when storage is opted into, are Fernet-encrypted with
`settings_key` at rest and masked in UI; local providers are keyless.

## 4. Provider interfaces

Python protocols in `app/providers/`; adapters are selected via settings.

### ChatProvider
- `model_id`, `version` (if exposed)
- `stream(messages, params) → AsyncIterator[StreamEvent]` — streaming is
  mandatory; `StreamEvent` = token delta or finish info
- `estimate_tokens(text) → int` — **chat** tokenizer; if the provider
  cannot count, a conservative char-based estimate (see Open decisions)
- `test_connection() → ok/error`

### EmbeddingProvider
See Embedding contract (section 5) — `model_id`, `dim`, `normalize`,
`prefix_query`, `prefix_passage`, batched `embed(texts, kind)`,
`estimate_tokens(text)`, `test_connection()`.

### SearchProvider
- `search(query, max_results) → list[SearchResult]`
- `SearchResult` schema (locked): `provider`, `title`, `url`, `snippet`,
  `published_at?`, `doc_type?`, `error?` (error carries a machine-readable
  reason: timeout, blocked, provider_error, ...)
- **Invariant: SearchResult ≠ Source.** Snippets NEVER enter chat
  context; only fetched + indexed sources do. Discovery results are
  candidates; "Add to sources" is an explicit per-card user action,
  never automatic.

### VectorStore
- `upsert(ids, vectors)`, `delete(ids)`, `knn(vector, k, filters)`, `count()`
- v1 implementation: `SQLiteVecStore` (sqlite-vec, cosine over
  L2-normalized vectors); the interface is the seam for Qdrant later.

### TextParser (extractors)
- `parse(data) → ParsedDoc { text, pages[(page, text)], headings[(path, char range)] }`
- `parser_version: str` — bumped on extractor behavior change ⇒ re-index
- v1: PDF / DOCX / MD / TXT; HTML/URL via trafilatura WITH the URL fetch
  guard (Security model).

### Reranker (stub in v1)
`rerank(query, chunks) → reordered chunks`; v1 = identity stub, interface
reserved for v2.

## 5. Embedding contract (contract)

- `model_id` — stable identifier of the served model (e.g. GGUF filename
  or server-reported name).
- `dim` — fixed for a model; verified at provider setup (dim check).
- `normalize` — **L2** for all vectors; cosine similarity is the metric.
- `prefix_query` / `prefix_passage` — prepended per call kind (adapter
  config; e.g. `Instruct: ...` for passage, `Query: ...` for query, or
  empty).
- Batched `embed()` for indexing; single call for queries.
- **Index incompatibility rules:** an index built with
  `(model_id, dim, normalize, prefix_query, prefix_passage)` cannot be
  reused by a provider whose tuple differs in `model_id`, `dim`, or
  `normalize` ⇒ full re-index from scratch (new `index_version`). Prefix
  changes also force re-embedding (new `index_version`), since passage
  vectors are not comparable across prefix regimes.
- Mismatch between the configured provider and `active_index_version`
  metadata is surfaced in the UI as "re-index required", not silently
  served.

## 6. Chunking / retrieval parameters

### Chunking
| param | default | notes |
|---|---|---|
| target size | 400 tokens | counted in the **embedding** tokenizer |
| overlap | 60 tokens | |
| strategy | recursive, heading-aware | headings split sections before recursion |
| parent expansion | on | top-level chunks = heading sections; small chunks link `parent_id`; retrieval expands hits to their parent for prompt context |
| `chunk_config_version` | int | any change to the above ⇒ full re-index |

### Retrieval
| param | default | notes |
|---|---|---|
| fusion | hybrid BM25 (FTS5) + vector (cosine) → RRF | `k = 60` (Open decision: tune via eval runs) |
| candidates | 10 → 5 | top-10 fused → top-5 final |
| dedupe | by parent | one representative per parent chunk |
| active-version filter | always | join `sources.active_index_version = chunks.index_version` |
| `top_k` | settings-driven | per-user setting, default 5 |
| context budget | chat tokenizer | truncation/reorder to fit; fallback conservative char estimate |
| reranker | identity (v1) | pluggable stub for v2 |
| retrieval cache | **disabled in v1** | if ever enabled: key MUST include `user_id` (no cross-user cache) |

## 7. Index lifecycle (contract)

1. **Active version per source.** `sources.active_index_version` is the
   single read pointer; retrieval always reads the active version.
2. **Build alongside.** A re-index (new file hash, parser_version bump,
   chunk_config_version change, embedding incompatibility) creates
   `index_builds(state='building')` and writes new chunks/vec0/FTS5 rows
   under a new `index_version`. The old version keeps serving reads.
3. **Atomic swap.** Only after ALL stages (parse → chunk → embed →
   vector/FTS write) succeed, one transaction:
   `sources.active_index_version = new`; old build → `retired`;
   new build → `active`.
4. **Failure / interruption.** Old version stays active; the partial
   build → `abandoned` and its rows (chunks, vec0, FTS5) are
   garbage-collected.
5. **Re-index triggers:** `file_sha256` change; `parser_version` bump;
   `chunk_config_version` change; embedding incompatibility (section 5).
6. **Retired versions** are GC-eligible (retention policy: Open
   decisions).

Mandatory test: kill the process mid-indexing → old version still
serves, partial build GC'd, no half-swapped state.

## 8. Citation contract (contract)

- **Markers:** `[S<n>]` = chunk from an indexed source; `[W<n>]` = chunk
  from a web-fetched (discovery) source. `[W<n>]` appears **only** in
  `+web` mode.
- **Marker→chunk map built at prompt time.** Retrieved chunks are assigned
  `n` when the prompt is composed; the map (marker → chunk_id, source_id)
  is the request context the validator checks against.
- **Stream-then-validate.**
  1. SSE streams assistant tokens; markers are provisional (UI renders
     them live).
  2. After stream completion, the validator checks every emitted marker:
     `n` exists in the request map; the referenced chunk was in the
     request context; mode permits the marker kind.
  3. A **final SSE event `citations`** delivers per-marker status:
     `valid` / `invalid`.
- **Persistence.** The message row + validated citation state are stored
  **only after** validation. Invalid markers are stored as flagged.
- **UI.** Clickable `[n]` → source viewer span highlight; used-source
  chips; per-message mode badge; invalid markers re-rendered in flagged
  (invalid) style.
- **Grounding rules** (English system prompt template, admin-editable
  with placeholder validation): answer only from retrieved sources; cite
  every claim with markers; never from model memory; when the sources do
  not contain the answer, state the absence explicitly; mirror the
  requested answer structure; reply in the user's message language.
- **Support scoring** (NLI / LLM-judge): v2 backlog, not in v1.

SSE event types for chat: `token`, `citations` (final, before `done`),
`done`, `error`.

## 9. Provenance schema (contract, versioned)

Every stored assistant answer carries versioned provenance — a structured
field with a `provenance_schema_version` integer, **not** free JSON.
Schema change ⇒ version bump; old records stay verbatim and readers must
handle all older versions.

**v1 fields:**
```json
{
  "provenance_schema_version": 1,
  "chat_model": {"id": "...", "version": "..."},
  "generation_params": {"temperature": 0.2, "max_tokens": null},
  "prompt_template_version": 1,
  "mode": "sources_only | web",
  "retrieved": [
    {"chunk_id": "...", "source_id": "...", "index_version": 3,
     "score": 0.41, "rank": 1, "marker": "S1"}
  ],
  "index_versions": {"<source_id>": 3},
  "citations": [
    {"marker": "S1", "chunk_id": "...", "status": "valid"}
  ]
}
```
`index_versions` records the active index_version per source at
generation time, so a later re-index never rewrites what an answer was
grounded on.

## 10. Job lifecycle (contract)

- **Queue in DB, in-process worker thread** (single worker loop in the
  app process; SQLite-backed, survives restarts).
- **States:** `queued → processing → done | failed | cancelled`
  (terminal: done/failed/cancelled).
- **Claim via attempt token (no double processing):**
  claim is an atomic conditional update
  `UPDATE jobs SET state='processing', attempt_id=attempts+1,
   attempt_token=<random>, started_at=... WHERE id=... AND state='queued'`
  — exactly one claimer wins. Completion/failure updates are conditioned
  on the same `attempt_token`; a stale token is a no-op.
- **attempt_id** increments per run; `attempts` counts runs.
- **max_retries** per job kind: on failure, if `attempts < max_retries`
  the job is re-queued, otherwise it becomes terminal `failed` (no
  infinite retry).
- **Cancel:** `queued → cancelled` immediately; `processing →
  cancelled` cooperatively at the next checkpoint.
- **Owner re-check:** jobs carry `owner_id`; at execution time the worker
  re-verifies the owner and that the target entity still exists in that
  tenant; mismatch ⇒ controlled failure (see Tenant isolation).
- **Startup reclaim:** on boot, jobs left in `processing` (crashed
  process) are re-queued with a new attempt or moved to controlled
  failure if retries are exhausted.
- **Mandatory test:** kill the process mid-indexing, restart, verify:
  correct recovery, no double processing, no infinite retry.

## 11. Tenant isolation (contract)

- **Every repository query filters by owner or project membership.** No
  repository method returns a row without the tenant filter; the filter
  is applied inside the repository, never left to callers.
- **Jobs carry `owner_id` and re-check at execution time** (worker
  re-verification per Job lifecycle).
- **No cross-user retrieval cache.** v1: retrieval caching disabled.
  If introduced later, the cache key MUST include `user_id`.
- **Admin scope is administrative only** (create/deactivate users, reset
  passwords, admin-default settings). Admins do NOT bypass tenant
  isolation to read other users' data.
- **Negative tests (mandatory, one per entity):** user A cannot
  read / update / delete any entity of user B — projects, sources,
  chunks, notes, jobs, settings.

## 12. API contracts

REST + SSE under `/api`; session-cookie auth (all endpoints except
`/healthz`, login, first-run wizard). Requests/responses JSON; mutating
HTMX calls pass the CSRF token.

| Method + path | Purpose |
|---|---|
| `GET /healthz` | liveness (public) |
| `POST /api/auth/login` / `POST /api/auth/logout` | session cookie |
| `GET /api/auth/first-run` / `POST /api/auth/first-run` | wizard: admin account + UI language (pre-setup only) |
| `GET /api/projects` / `POST /api/projects` | list / create |
| `GET /api/projects/{id}` / `PATCH` / `DELETE` | project CRUD (owner-scoped) |
| `GET /api/projects/{id}/sources` | list with status badges |
| `POST /api/projects/{id}/sources/upload` | multipart file → extract + index job |
| `POST /api/projects/{id}/sources/url` | url + fetch guard + index job |
| `DELETE /api/sources/{id}` / `POST /api/sources/{id}/reindex` | delete / new index_version |
| `GET /api/sources/{id}/chunks` | chunk list (page, offsets, status) |
| `GET /api/sources/{id}/chunks/{cid}` | chunk detail for viewer highlight |
| `POST /api/chat` | `{project_id, conversation_id?, message, mode}` → **SSE**: `token`, `citations` (final), `done`, `error` |
| `POST /api/retrieval` | debug: scores + RRF fusion trace (active version only) |
| `POST /api/discovery` | `{query}` → `list[SearchResult]` candidate cards |
| `POST /api/discovery/add` | explicit per-card add → fetch+index job (never auto) |
| `GET /api/notes` / `POST /api/notes` / `PATCH /api/notes/{id}` / `DELETE` | notes CRUD (owner-scoped) |
| `GET /api/settings` / `PUT /api/settings` | per-user settings; masked keys; test buttons |
| `GET /api/admin/users` / `POST /api/admin/users` / `PATCH` | admin only: create, deactivate, reset password |
| `GET /api/jobs` / `POST /api/jobs/{id}/cancel` | own jobs |
| `GET /api/messages/{id}/export.md` / `GET /api/projects/{id}/export.bib` | export (Phase 7) |

All list/detail endpoints return only the caller's tenant rows.

## 13. Security model

**Baseline (Phase 1):**
- Cookies: `httpOnly` + `SameSite=Lax` (Secure when behind TLS).
- CSRF middleware protecting mutating HTMX requests (double-submit token).
- Bind: **default `127.0.0.1`**; LAN access is an explicit opt-in with a
  UI warning.
- argon2id password hashing; session-based auth.

**Secrets (bootstrap only, from `config.yaml`):**
- `secret_key` — session signing / CSRF.
- `settings_key` — Fernet encryption for stored provider keys.
- Key storage is optional (local providers are keyless). README documents:
  storage location, loss consequences (keys unrecoverable → re-enter
  flow), rotation and re-encryption procedure. Backup/restore
  **including `settings_key`** is a Phase 8 gate.
- All other settings live in the DB, edited via UI (roadmap rule 7).

**URL fetch guard (locked):**
- `http`/`https` schemes only.
- Block private / loopback / link-local addresses **after DNS resolution
  and on every redirect hop** (SSRF).
- Limits: max redirects, timeout, max body size.
- Dedupe by URL + content hash; store `url`, `retrieved_at`,
  `published_at`.

**Phase 8 full pass (public readiness):** per-user quotas + chat rate
limiting; keys; SSRF re-audit; uploads; CSRF; cookies; session fixation;
Caddy HTTPS; log rotation.

## 14. i18n

- Catalogs: **en** and **uk**, from day one; every user-visible string
  goes through i18n (roadmap rule 8).
- `_()` in Jinja templates and a matching helper for JS strings.
- Per-user preference (`users.lang`); header language switcher.
- Default locale: `en`.
- The RAG system prompt template is **English** and is NOT localized
  (roadmap rule 9); the model replies in the user's message language.

## 15. Deployment topology

- **Single process:** FastAPI app + in-process worker thread; SQLite (WAL)
  in `data_dir`. No separate worker service in v1.
- **systemd (now):** `deploy/local-bachelor.service`, 4-line `ExecStart`;
  user-level or system unit per host.
- **Sidecars (user-managed, outside our deploy):**
  - `llama-server` for OpenAI-compatible chat and/or `--embedding` GGUF
    models (one-command recipe in README; covers the whole MVP on a local
    GPU box).
  - SearXNG (self-hosted or any public instance) or nothing (DuckDuckGo is
    keyless and needs no server).
- **Config split:** `config.yaml` = bootstrap only (`secret_key`,
  `settings_key`, `db_path`, `data_dir`, `host`, `port`); everything else
  in DB settings via UI.
- **Docker Compose (Phase 9, non-breaking):** `python-slim` image;
  optional caddy/searxng services; volumes mirror `data_dir`; behavioral
  parity with the systemd deployment.
- **Backup/restore (Phase 8 gate):** DB file + `data_dir` +
  `settings_key`.

## 16. Evaluation protocol (contract)

**Three tiers:**
1. **Retrieval:** `recall@5` and `recall@10` against fixture expectations.
2. **Grounding (v1 — no NLI / LLM-judge; that is v2 backlog):** for
   answerable fixtures, the generated answer must contain at least one
   `valid` citation marker and **zero** `invalid` markers.
   `grounded_rate` = fraction of answerable fixtures passing.
3. **Abstention:** for `unanswerable` fixtures, the answer must state the
   absence of evidence explicitly and carry **zero** citation markers.
   `abstention_rate` = fraction of unanswerable fixtures passing.

**Fixtures:** UA + EN, four types: `exact_name`, `paraphrase`,
`multi_source`, `unanswerable`. Format:
```json
{
  "schema_version": 1,
  "fixtures": [
    {
      "id": "ua-001",
      "lang": "uk",
      "type": "paraphrase",
      "question": "...",
      "expected": {
        "chunk_ids": ["..."],
        "source_ids": ["..."]
      }
    }
  ]
}
```
`recall@k` = |retrieved_top_k ∩ expected.chunk_ids| /
|expected.chunk_ids| (per fixture, then averaged). `unanswerable`
fixtures have an empty `expected` and are excluded from recall.

**Runs are saved as versioned JSON** for parameter comparison:
```json
{
  "schema_version": 1,
  "run_id": "...",
  "created_at": "...",
  "params": {
    "chunk_config_version": 1,
    "chunk_target_tokens": 400,
    "chunk_overlap_tokens": 60,
    "embedding_model_id": "...",
    "embedding_dim": 896,
    "retrieval": {"candidates": 10, "top_k": 5, "rrf_k": 60},
    "prompt_template_version": 1,
    "chat_model": "..."
  },
  "results": [
    {"fixture_id": "ua-001", "retrieved": ["..."],
     "recall": 1.0, "grounded": true, "abstained": null}
  ],
  "aggregates": {
    "recall@5": 0.9, "recall@10": 0.95,
    "grounded_rate": 0.97, "abstention_rate": 1.0
  }
}
```
Purpose: compare chunking / retrieval / model parameters across runs
before and after any parameter change.

## 17. Open decisions

Items not covered by Locked decisions v3. Each gets resolved here before
its implementing task; the resolution is then promoted into the relevant
section above.

1. **Default prompt context budget** (chat tokens) — pick once the
   target chat model's context window is known (before Phase 5).
2. **Conservative token-estimation fallback** (chars-per-token factor
   for chat and embedding tokenizers when the provider cannot count) —
   pick in Phase 2.
3. **FTS5 tokenizer for uk/en text** — `unicode61` by default; ICU
   variant if the server SQLite build supports it; decide via the 0.6
   probe and Phase 4 eval runs.
4. **Conversation grouping** — flat per-project messages vs named
   conversations; resolve before Phase 5.
5. **Retired index version retention** — immediate GC after swap vs
   deferred cleanup; resolve in Phase 3.
6. **RRF constant `k`** — default 60; tune via Phase 4 eval runs.
7. **sqlite-vec + SQLite versions on the target Linux server** — to be
   recorded here by task 0.6.
