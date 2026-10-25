# LocalBachelor — Development Roadmap (v3, final pre-implementation)

Working model: local Qwen 3.8 27B (xhigh) via Kilo Code; delegation per
model-allocation.md. One task = one session = one commit.
This is the LAST roadmap iteration. Further changes only via
architecture.md → "Open decisions".

## How to use this roadmap (instructions for the coding model)

1. Before any task, read `architecture.md` (task 0.1). Source of truth.
2. ONE unchecked task per session. No unrelated refactors.
3. Tests for everything touched; `scripts/check.sh` green before done.
4. On completion: mark `[x]`, commit `task <id>: <summary>`.
5. End of phase: Docs sync — update architecture.md and README.
6. Task exceeds ~6 open files or one session → STOP, propose split in
   architecture.md → "Open decisions", leave unchecked.
7. Secrets ONLY from config.yaml; other settings in DB, edited via UI.
8. All user-visible strings via i18n (en/uk) from day one.
9. RAG system prompt template is English; reply in user's message language.
10. Grounding core: answer only from retrieved sources, cite every claim,
    never from memory; absence of evidence stated explicitly.
11. Every stored answer carries versioned provenance (see Locked decisions).

## Locked stack decisions (v3)

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite (WAL).
- Vectors/BM25: sqlite-vec + FTS5 in same DB; `VectorStore` interface allows
  Qdrant later.
- Frontend: Jinja2 + HTMX + Alpine.js + vanilla JS (SSE); single CSS with
  theme variables; NO Node build.
- Workers: DB job queue + in-process worker thread. Job lifecycle:
  states queued→processing→done/failed/cancelled; claim via attempt token
  (no double processing); attempt_id per run; max_retries; on startup
  reclaim stale `processing` jobs (re-queue with new attempt or controlled
  failure). Mandatory test: kill process mid-indexing, verify recovery.
- Auth: session cookie + argon2id; roles admin/user; lean multi-user
  (admin creates accounts; no RBAC beyond roles).
- Tenant isolation: EVERY repository query filters by owner or project
  membership; jobs carry owner_id and re-check at execution time; NO
  cross-user retrieval cache (cache key includes user_id, or caching
  disabled in v1). Negative tests: user A cannot read/update/delete any
  entity of user B (projects, sources, chunks, notes, jobs, settings).
- Security baseline (Phase 1): httpOnly + SameSite cookies, CSRF middleware
  for HTMX, default bind 127.0.0.1, LAN opt-in with UI warning. Full pass
  in Phase 8.
- Secrets: TWO bootstrap keys — `secret_key` (sessions/CSRF), `settings_key`
  (Fernet for stored provider keys). Key storage optional (local providers
  keyless). README documents storage, loss consequences (keys unrecoverable
  → re-enter flow), rotation and re-encryption procedure. Backup/restore
  including settings_key is a Phase 8 gate.
- Chat providers: generic OpenAI-compatible FIRST; OpenAI/Anthropic/Google/
  OpenRouter post-MVP. Streaming mandatory.
- Embeddings: OpenAI-compatible HTTP adapter FIRST — this covers GGUF models
  served by `llama-server --embedding` (docs include a one-command recipe).
  In-process llama-cpp-python GGUF loader = post-MVP (autonomous deploys).
  sentence-transformers = post-MVP optional. Query/passage prefixes,
  L2-normalize, cosine. Embedding contract: dim, normalization, prefixes,
  model id, index incompatibility rules.
- Search: SearXNG + DuckDuckGo (keyless) FIRST; Serper/Brave/Tavily
  post-MVP. SearchResult schema: provider, title, url, snippet,
  published_at?, doc_type?, error?. SearchResult ≠ Source: snippets NEVER
  enter chat context; only fetched+indexed sources do.
- URL fetch guard: http/https only; block private/loopback/link-local AFTER
  DNS resolution AND per redirect; max redirects/timeout/size; dedupe by
  URL + content hash; store url, retrieved_at, published_at.
- Chunking: 400 tokens / overlap 60, recursive + heading-aware, parent
  expansion. Chunk size in EMBEDDING tokenizer; prompt context budget in
  CHAT tokenizer (fallback conservative char estimate). Change ⇒ re-index.
- Index lifecycle: active index_version stored PER SOURCE; new version
  built alongside; switched atomically only after ALL stages succeed;
  retrieval always reads the active version; failure/interruption keeps
  old version active and garbage-collects the partial build. Chunks carry
  file_sha256, parser_version, chunk_config_version, embedding_model_id
  (+dim), index_version, lang, page, char offsets.
- Retrieval: hybrid BM25+vector, RRF; candidates 10 → 5; dedupe by parent;
  reranker stub (v2).
- Citation contract: markers [S<n>]/[W<n>]; marker→chunk map built at
  prompt time; STREAM-THEN-VALIDATE: markers stream provisionally; after
  completion the validator checks each marker against the request context;
  a final SSE event `citations` delivers per-marker status (valid/invalid);
  DB stores message + validated citation state ONLY after validation; UI
  re-renders invalid markers as flagged. Support-scoring = v2 backlog.
- Provenance: VERSIONED schema (provenance_schema_version field), not free
  JSON: chat model id+version, generation params, prompt template version,
  retrieved chunk ids+scores, mode flag, index_version per source.
- Modes: "sources only" / "+web" request-scoped flag, stored with message,
  per-message UI badge; [W<n>] only in +web.
- Eval protocol: three tiers (retrieval recall@5/10, grounding, abstention);
  fixtures UA+EN (exact names, paraphrase, multi-source, unanswerable);
  runs saved as versioned JSON for parameter comparison; fixture format
  defined in architecture.md.
- Deployment: systemd now; Docker Compose Phase 9, non-breaking.
- i18n: en/uk catalogs, per-user preference, header switcher.

## MVP checkpoint (end of Phase 5)

Vertical slice end-to-end with LOCAL providers: OpenAI-compatible chat +
GGUF embeddings via llama-server HTTP + one keyless/self-hosted search;
grounded answers with validated citations from owner's own PDFs.
Tasks marked (post-MVP) may land after.

## Phase 0 — Bootstrap

- [x] 0.1 Create `architecture.md` with mandatory contract sections:
      Citation contract; Index lifecycle; Provenance schema (versioned);
      Tenant isolation; Job lifecycle; Embedding contract; Evaluation
      protocol; plus layout, data model, API contracts, provider
      interfaces, chunking/retrieval params, security model, i18n,
      deployment. Based strictly on Locked decisions v3.
- [x] 0.2 Repo bootstrap: git, .gitignore, README, pyproject (pinned),
      ruff+pytest config, config.example.yaml (secret_key, settings_key,
      db_path, data_dir, host, port), evals/ with 2 sample docs + QA pairs.
- [x] 0.3 App skeleton: factory, config loader, logging, /healthz, error
      middleware.
- [x] 0.4 systemd unit (4-line ExecStart) + README first-run (wizard).
- [x] 0.5 scripts/check.sh quality gate.
- [x] 0.6 Target-env probe ON THE LINUX SERVER: sqlite-vec + FTS5 load on
      server Python/SQLite; record versions in architecture.md.
- [x] 0.7 Docs sync.

## Phase 1 — Users, auth, i18n, security baseline, isolation

- [x] 1.1 Alembic + users table; argon2id; login/logout; session middleware;
      roles.
- [x] 1.2 First-run wizard: admin account + UI language.
- [x] 1.3 i18n engine: en/uk catalogs, `_()` in Jinja, per-user preference,
      switcher.
- [ ] 1.4 Three-pane layout per mockup; light/dark themes.
- [ ] 1.5 Admin page: create/deactivate users, reset passwords.
- [ ] 1.6 Security baseline: cookie flags, CSRF middleware, bind default
      127.0.0.1, LAN opt-in warning.
- [ ] 1.7 Tenant isolation: ownership filters in all repositories; job
      owner re-check; negative tests A≠B across all entities.
- [ ] 1.8 Docs sync.

## Phase 2 — Settings & local-first providers (BYOK core)

- [ ] 2.1 DB settings store (typed, defaults) + Settings page skeleton.
- [ ] 2.2 Secrets: Fernet on settings_key; masked inputs; optional key
      storage; README: storage/loss/rotation procedure.
- [ ] 2.3 Chat provider interface + generic OpenAI-compatible adapter
      (streaming, test_connection).
- [ ] 2.4 Embedding provider interface + OpenAI-compatible HTTP adapter
      (prefixes, normalize, dim check) + README recipe: serve any GGUF
      embedding via `llama-server --embedding`.
- [ ] 2.5 Search provider interface + SearXNG + DuckDuckGo adapters with
      full SearchResult schema incl. error reasons.
- [ ] 2.6 Settings UI: pickers, keys, endpoints, Test buttons; per-user
      with admin defaults fallback.
- [ ] 2.7 Docs sync.
- [ ] 2.8 (post-MVP) Chat adapters: OpenAI, Anthropic, Google, OpenRouter.
- [ ] 2.9 (post-MVP) Search adapters: Serper, Brave, Tavily.
- [ ] 2.10 (post-MVP) In-process llama-cpp-python GGUF embedding loader.
- [ ] 2.11 (post-MVP) sentence-transformers embedding adapter.

## Phase 3 — Sources & ingestion

- [ ] 3.1 Schema: projects, sources, chunks (parent_id, offsets, page,
      lang), full index metadata + index_version, vec0, FTS5, jobs
      (attempt_id, state machine columns).
- [ ] 3.2 Project CRUD API + left pane UI.
- [ ] 3.3 Extractors PDF/DOCX/MD/TXT + HTML/URL via trafilatura WITH fetch
      guard (locked rules).
- [ ] 3.4 Chunker: token recursive + heading-aware; settings-driven;
      parent links + char/token offsets.
- [ ] 3.5 Ingestion worker: queue, claim tokens, attempt_id, max_retries,
      statuses, cancel, startup reclaim; test: kill mid-indexing → correct
      recovery, no double processing, no infinite retry.
- [ ] 3.6 Embedding step: batch embed, write vec0+FTS5 with index_version,
      normalized vectors.
- [ ] 3.7 Sources pane UI: icons, pages, chunks, status badges, viewer with
      chunk highlights, delete/re-index.
- [ ] 3.8 Index lifecycle impl: build new version alongside; atomic swap
      per source after full success; old version serves reads meanwhile;
      partial-build garbage collection.
- [ ] 3.9 Docs sync.

## Phase 4 — Retrieval & retrieval eval

- [ ] 4.1 Hybrid KNN+BM25 → RRF; parent expansion + dedupe; active
      index_version filter; top_k settings.
- [ ] 4.2 Retrieval debug API: scores + fusion trace.
- [ ] 4.3 Eval fixtures UA+EN (exact names, paraphrase, multi-source,
      unanswerable) + recall@5/10 assertions.
- [ ] 4.4 Eval runner: versioned JSON runs for chunking/retrieval
      comparison.
- [ ] 4.5 Reranker interface stub.
- [ ] 4.6 Docs sync.

## Phase 5 — Grounded chat, citations, provenance

- [ ] 5.1 English system prompt template with protected placeholders and
      grounding rules (cite [S<n>]; state absence; no paramemory; mirror
      requested structure; reply in user's language).
- [ ] 5.2 Chat pipeline: retrieve → budget (chat tokenizer) → compose →
      SSE; marker→chunk map at prompt time; persist message + versioned
      provenance AFTER validation.
- [ ] 5.3 Citation validator, stream-then-validate: provisional markers
      during SSE; post-completion validation; final SSE `citations` event
      with per-marker status; DB stores validated state; UI flags invalid.
- [ ] 5.4 Chat UI: streaming markdown; clickable [n] → viewer span
      highlight; used-source chips; per-message mode badge; mode toggle;
      invalid-citation styling.
- [ ] 5.5 Notes: save answers with citations; Notes tab; notes-as-context
      toggle.
- [ ] 5.6 System prompt editor (admin) with placeholder validation.
- [ ] 5.7 Grounding + abstention eval tiers wired into eval runner.
- [ ] 5.8 Docs sync.

>>> MVP CHECKPOINT: validate vertical slice with own PDFs here. <<<

## Phase 6 — Web discovery

- [ ] 6.1 Discovery API via selected search provider → candidate cards
      (full SearchResult schema).
- [ ] 6.2 UI: candidates panel + inline in +web chat; per-card "Add to
      sources" → fetch+index job; NEVER auto-add; snippets never used as
      evidence directly.
- [ ] 6.3 URL ingestion: dedupe by URL+content hash; store url,
      retrieved_at, published_at; reuse 3.3 guard.
- [ ] 6.4 Docs sync.

## Phase 7 — Export & polish

- [ ] 7.1 Export: answer → Markdown with citations; project → BibTeX;
      per-source metadata editor (author/year/title).
- [ ] 7.2 [OWNER, human] Curate 30–100 real questions over own materials
      with expected sources into evals/; wire into eval runner.
- [ ] 7.3 Settings UX: provider wizard, tooltips, safe defaults.
- [ ] 7.4 Theme polish per mockup; basic responsive.
- [ ] 7.5 Empty/loading/error states (i18n).
- [ ] 7.6 Docs sync.

## Phase 8 — Public readiness (only if going public)

- [ ] 8.1 Per-user quotas + chat rate limiting.
- [ ] 8.2 Security pass ON TOP of 1.6/1.7: keys, SSRF re-audit, uploads,
      CSRF, cookies, fixation.
- [ ] 8.3 Ops guide: Caddy HTTPS, backup/restore (db + data_dir +
      settings_key) — GATE for public deploy; log rotation.
- [ ] 8.4 Read-only share links.
- [ ] 8.5 Docs sync.

## Phase 9 — Docker Compose (additive)

- [ ] 9.1 Dockerfile (python-slim) + compose (app, optional caddy/searxng);
      volumes mirror data_dir; parity with systemd.
- [ ] 9.2 Deployment guides.
- [ ] 9.3 Docs sync.

## Phase 10 — OCR (last)

- [ ] 10.1 OCR adapter (tesseract/ocrmypdf, uk+eng) for PDFs without text
      layer; settings toggle; hooks re-ingestion.
- [ ] 10.2 Docs sync.

## Backlog — NOT now

Voice input; audio/video overviews; mind maps; flashcards; graph RAG;
YouTube; realtime collab; mobile; citation support-scoring (NLI/LLM-judge);
in-chat use of raw search snippets; any model training.

## Definition of done

check.sh green; checkbox ticked; single commit; i18n complete; no secrets
in repo; architecture.md consistent.