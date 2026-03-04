Below is an **implementation-ready, multi-phase plan** that turns the updated BRD into an execution blueprint for a **web app** (React + Vite frontend; Python + FastAPI backend), ending at a **production-ready V1**. It is intentionally **long and ultra prescriptive** so you can slice it into developer assignments with minimal ambiguity.

---

# MindVault Implementation Plan (Phase 0 → Production V1)

**Core product contract (unchanged):**

* Voice-first capture → transcript → minimal indexing
* Retrieval-first “Ask” (**sources-first**, generation optional + constrained)
* Evidence-backed outputs everywhere (tappable citations + highlights)
* “Raw is sacred” (audio + verbatim transcript preserved; generated outputs separate)
* Privacy-first approach = **MindVault stores user data; AI providers are used only for stateless processing** with **zero data retention** enabled/contracted where available.

**Important constraint (cloud processing):**

* OpenAI notes API data is not used for training by default (unless you opt in). ([OpenAI Developers][1])
* OpenAI also notes it may retain API inputs/outputs up to **30 days** for abuse monitoring/service delivery unless an exception applies. ([OpenAI][2])
* OpenAI states “qualifying organizations” can configure **zero data retention**. ([OpenAI][3])
  This plan treats “zero retention” as a **vendor/account gating requirement** (we build the system to support it; production enablement depends on qualifying terms).

---

## 0) Decisions (locked-in)

### 0.1 Architecture

* **Monorepo**: `apps/web` (React), `apps/api` (FastAPI), `infra/` (docker/compose), `docs/`.
* **Backend is a single service** (monolith) with:

  * Postgres for relational + full-text search (FTS)
  * Redis + RQ worker for background jobs (transcription, summarization, exports)
  * Local filesystem for audio blobs (abstracted behind a storage interface)
* **RAG = retrieval-first**:

  * Primary retrieval: Postgres FTS
  * Optional rerank: embeddings (v1) using pgvector + OpenAI embeddings
  * Generation only runs on **retrieved snippets**, never the whole vault

### 0.2 Frontend stack (React + Vite)

* React 18 + TypeScript + Vite
* React Router
* TanStack Query (server state)
* Zustand (UI state)
* UI: Tailwind + shadcn/ui (fast, consistent)
* Audio recording: browser **MediaRecorder** → `webm` (supported for OpenAI transcription) ([OpenAI Developers][4])

### 0.3 Backend stack (Python)

* Python 3.12
* FastAPI + Pydantic v2
* SQLAlchemy 2.0 + Alembic migrations
* Postgres 16
* Redis + RQ
* Auth: email+password (Argon2), JWT access token + refresh token (HttpOnly cookie) + CSRF token for mutating requests

### 0.4 AI provider defaults (baked-in)

* Transcription: OpenAI Audio Transcriptions endpoint using `gpt-4o-mini-transcribe` (default) or `gpt-4o-transcribe` (higher quality) ([OpenAI Developers][4])

  * Input formats supported include `webm`, with 25MB upload limit. ([OpenAI Developers][4])
* Generation (summaries/bullets): `gpt-4o-mini` ([OpenAI Developers][5])
* Embeddings (v1): `text-embedding-3-small` ([OpenAI Developers][6])

---

# 1) User flows (web app version, locked)

### 1.1 Authentication flow

1. User lands on marketing or app root
2. Login / Signup
3. After signup → onboarding checklist (short)
4. App uses refresh cookie to keep session alive

### 1.2 Onboarding flow (v0)

On first login:

* “What MindVault does” (1 screen)
* “Data & AI processing disclosure”

  * We store your entries (audio + transcripts) in MindVault
  * We call transcription/LLM providers only for processing; **we require zero-retention where available**
* Set defaults:

  * Focus: Career / Life / Both (affects prompt chips + brag buckets visibility)
  * Reminder opt-in (v1: browser notification; v0: skip or email reminder optional)
* Finish → “Record first entry” CTA

### 1.3 Core daily loop (v0)

1. Home → “Record”
2. Stop → Upload audio → background transcribe
3. Processing screen shows status
4. When transcript ready → “5-second indexing” (Type + Work/Life + optional Project/Person tags)
5. Timeline shows entry with quote chip
6. User later:

   * searches timeline
   * uses Ask sources-first
   * builds Brag Doc + exports

### 1.4 Data rights flows

* Export:

  * “Export my data” creates job → zip download
* Delete:

  * Delete entry (hard delete + remove blob)
  * Delete account (hard delete all user data + blobs)
* Audit:

  * “Processing log” shows when transcription/LLM calls happened (no content)

---

# 2) Data model (implementation-ready)

### 2.1 Core tables (Postgres)

**users**

* id (uuid)
* email (unique)
* password_hash
* created_at, updated_at
* settings_json (focus, default filters, redaction config, etc.)

**entries**

* id (uuid)
* user_id (fk)
* created_at
* recorded_at (client timestamp)
* duration_seconds
* context (`work|life`)
* entry_type (`win|blocker|decision|people|lesson|other`)
* flags:

  * `work_sensitive` (exclude from LLM by default)
  * `raw_only` (exclude from generation/bullets but still searchable)
* title_user (nullable)
* title_generated (nullable)
* status (`uploaded|transcribing|ready|error`)
* error_code, error_message (nullable)

**audio_assets**

* id (uuid)
* entry_id (fk unique)
* storage_key (path)
* mime_type
* bytes
* checksum_sha256
* created_at

**transcripts**

* id (uuid)
* entry_id (fk)
* version (int; starts at 1)
* text (plaintext)
* created_at
* edited_by_user (bool)
* source (`stt|user_edit`)
* text_tsvector (generated column or materialized)  ← for FTS

**tags**

* id (uuid)
* user_id (fk)
* tag_type (`project|person|theme`)
* name (normalized)
* created_at

**entry_tags**

* entry_id
* tag_id

**snippets** (can be computed on the fly in v0; persisted in v1 if needed)

* id (uuid)
* entry_id
* transcript_id (points to version)
* start_char
* end_char
* snippet_text (stored)
* snippet_hash (for integrity)

**citations**

* id (uuid)
* usage (`ask|brag`)
* created_at
* entry_id
* transcript_id
* start_char
* end_char
* snippet_hash
* metadata_json (query_id, bullet_id, etc.)

**brag_bullets**

* id (uuid)
* user_id
* bucket (`impact|execution|leadership|collaboration|growth`)
* text (the bullet claim)
* created_at
* updated_at
* source_mode (`manual|generated`)
* stale (bool)

**brag_bullet_citations**

* bullet_id
* citation_id

**ask_queries**

* id (uuid)
* user_id
* created_at
* query_text
* date_range_start, date_range_end
* mode (`sources_only|summary`)
* redact_names (bool)
* mask_numbers (bool)
* include_work_sensitive (bool default false)
* status (`done|error`)

**ask_results**

* id (uuid)
* ask_query_id
* answer_text (nullable)
* answer_json (nullable; sentence→citation mapping)
* created_at

**audit_log**

* id (uuid)
* user_id
* created_at
* event_type (`login|logout|entry_created|audio_uploaded|transcription_called|llm_called|export_started|export_downloaded|entry_deleted|account_deleted|settings_changed`)
* metadata_json (STRICT: no raw content)

**export_jobs**

* id (uuid)
* user_id
* status (`queued|running|done|error`)
* storage_key (zip path)
* created_at
* completed_at
* error_message

---

# 3) API surface (FastAPI, locked)

All endpoints are `/api/v1/...`.

## 3.1 Auth

* `POST /auth/signup` {email, password}
* `POST /auth/login` {email, password} → sets refresh cookie + returns access token + CSRF token
* `POST /auth/refresh` (cookie) → new access token
* `POST /auth/logout` → clears cookie
* `GET /me` → profile/settings

## 3.2 Entries

* `POST /entries` → create entry shell (returns entry_id, upload_url or direct upload endpoint)
* `POST /entries/{id}/audio` (multipart) → stores audio, enqueues transcription
* `GET /entries` (filters: date range, type, tags, query)
* `GET /entries/{id}`
* `PATCH /entries/{id}` (title_user, flags, type, context)
* `PATCH /entries/{id}/transcript` (creates new transcript version)
* `DELETE /entries/{id}`

## 3.3 Search

* `GET /search` (q, filters) → returns ranked snippets + entry metadata

## 3.4 Ask (retrieval-first)

* `POST /ask/query` {query_text, date_range, options...} → returns sources list immediately + optional job id for summary
* `POST /ask/{query_id}/summarize` → enqueue summary over selected snippet IDs
* `GET /ask/{query_id}` → results (sources + summary if ready)

## 3.5 Brag Doc

* `GET /brag` (date range) → bullets grouped by bucket
* `POST /brag/bullets` (manual bullet + citations)
* `POST /brag/bullets/generate` (from selected entries/snippets; returns suggested bullets + citations)
* `PATCH /brag/bullets/{id}`
* `DELETE /brag/bullets/{id}`
* `POST /brag/export` → creates export job (text in v0, PDF in v1)

## 3.6 Data rights + audit

* `POST /exports` → export all data zip job
* `GET /exports/{id}` → status
* `GET /exports/{id}/download` → signed link or streamed download
* `GET /audit` → audit events (paged)
* `DELETE /account` → full wipe (requires password confirmation)

---

# 4) RAG implementation rules (enforced)

### 4.1 Retrieval-first invariant

* The system **must be able to answer every query with sources only**.
* Summaries are optional, never default.
* Summaries can only use:

  * the retrieved snippet set
  * plus minimal query metadata (date range, user options)
* No “freeform recall”.

### 4.2 Citation enforcement

* Every claim in any generated text must map to snippet IDs.
* Implementation technique:

  * Call LLM with: `sources[]` (each = {snippet_id, date, entry_title, snippet_text})
  * Request JSON output: list of sentences with `citation_snippet_ids: []`
  * Validate server-side:

    * each sentence has >= 1 snippet id
    * snippet ids exist in sources list
  * Render UI sentence-by-sentence with citations

### 4.3 Work-sensitive and raw-only enforcement

* `work_sensitive` entries are excluded from any LLM calls unless the user explicitly includes them.
* `raw_only` entries are excluded from summary/bullet generation but searchable and playable.

---

# 5) Multi-phase implementation plan

Each phase includes:

* **Purpose**
* **Deliverables**
* **Task list** with:

  * Owner tags: [FE], [BE], [AI], [INF], [QA]
  * Dependency markers:

    * (SEQ) must be completed before subsequent tasks
    * (PAR) can be done in parallel
  * “Done” criteria

---

## Phase 0 — Project setup + engineering baseline (foundation)

### Purpose

Create a stable dev environment and shared conventions so feature work doesn’t thrash.

### Deliverables

* Monorepo scaffolding + code style + CI
* Docker Compose dev stack (api, db, redis, worker, web)
* First deployable skeleton (healthcheck pages)

### Tasks

1. [INF] (SEQ) Create repo structure

   * `/apps/web`, `/apps/api`, `/infra`, `/docs`
2. [INF] (SEQ) Docker Compose dev stack

   * Postgres + Redis + API + Worker + Web
3. [BE] (SEQ) FastAPI skeleton

   * `/health`, `/version`, basic settings loader
4. [FE] (SEQ) Vite + React skeleton

   * routes: `/login`, `/signup`, `/app`
5. [INF] (PAR) CI pipeline

   * lint + tests for FE/BE
6. [BE] (SEQ) Database migration framework (Alembic) wired
7. [FE] (PAR) UI kit setup (Tailwind + shadcn/ui), router, query client
8. [QA] (PAR) Define “definition of done”

   * unit tests required for service layer
   * e2e smoke test plan

**Done when:** `docker compose up` yields a working web shell and API health endpoint; migrations run; CI is green.

---

## Phase 1 — Core backend platform: auth, storage, job system, base schema

### Purpose

Enable secure multi-user usage, persistent storage, and background processing.

### Deliverables

* Auth working end-to-end
* Core DB tables created
* File storage interface implemented
* RQ job queue running (transcription job stubbed)

### Tasks

1. [BE] (SEQ) Implement DB schema v1 (users, entries, transcripts, audio_assets, tags, audit_log)
2. [BE] (SEQ) Auth service

   * signup/login/refresh/logout
   * Argon2 password hashing
   * HttpOnly refresh cookie + CSRF token
3. [FE] (PAR) Auth UI

   * signup/login forms
   * token refresh integration
4. [BE] (SEQ) Authorization middleware

   * all entry endpoints require authenticated user
5. [BE] (SEQ) Storage abstraction

   * `StorageBackend` interface
   * `LocalDiskStorage` implementation
6. [BE] (SEQ) File upload endpoint skeleton

   * `POST /entries/{id}/audio` (stores file + metadata)
7. [BE] (SEQ) Job queue integration

   * Redis connection
   * RQ worker process with job registry
8. [BE] (PAR) Audit logging middleware

   * log auth events + entry creates (no content)
9. [QA] (PAR) Backend unit tests for auth + storage + basic CRUD
10. [INF] (PAR) Add env management

* `.env.example`
* secrets strategy for dev/prod

**Dependencies:** Phase 0 complete.
**Done when:** a user can signup/login and create an empty entry; audio upload stores a blob; a worker can pick up a dummy job.

---

## Phase 2 — Audio capture (web), upload, transcription pipeline (v0 usable)

### Purpose

Ship the core “record → transcript → saved entry” loop.

### Deliverables

* Browser recording UI
* Upload audio to backend
* Background transcription using OpenAI Audio API
* Entry status updates + error handling

### Key implementation notes

* Browser records `webm` via MediaRecorder.
* OpenAI supports `webm` input, and file size limit is 25MB. ([OpenAI Developers][4])
* Transcription model default: `gpt-4o-mini-transcribe`. ([OpenAI Developers][4])

### Tasks

1. [FE] (SEQ) Recording UI (MediaRecorder)

   * start/stop, timer, waveform (optional simple)
   * produce Blob (`audio/webm`)
2. [BE] (SEQ) Entry creation API

   * `POST /entries` returns entry_id
3. [FE] (SEQ) Upload pipeline

   * create entry → upload audio → poll entry status
4. [BE] (SEQ) Audio upload endpoint (multipart)

   * store blob
   * set entry.status = `transcribing`
   * enqueue `TranscribeEntryJob(entry_id)`
5. [AI] (SEQ) Implement transcription worker job

   * read file from storage
   * call OpenAI transcriptions endpoint
   * store transcript version 1
   * set entry.status = `ready`
6. [BE] (SEQ) Error handling contract

   * transient vs fatal errors
   * store error_code/message on entry
7. [FE] (PAR) Processing screen

   * show “uploading/transcribing/ready/error”
   * retry button if safe
8. [BE] (PAR) Audit events

   * `audio_uploaded`, `transcription_called` (store model name + bytes only)
9. [QA] (PAR) Integration test

   * upload small webm, stub OpenAI response in test mode

**Done when:** user can record a 1–2 minute entry, see it transcribed, and open the entry detail page with audio playback + transcript.

---

## Phase 3 — Entry detail, transcript versioning, and “5-second indexing”

### Purpose

Make entries trustworthy, editable, and minimally structured for later retrieval.

### Deliverables

* Entry detail view (audio + transcript + generated section container)
* Transcript edits create new versions
* 5-second indexing UI: type + work/life + tags

### Tasks

1. [BE] (SEQ) Transcript versioning

   * `PATCH /entries/{id}/transcript` creates new transcript row with version+1
   * mark derived artifacts stale (future)
2. [FE] (SEQ) Entry detail page

   * audio player
   * transcript display
   * “Edit transcript” mode
3. [FE] (SEQ) 5-second indexing modal after transcript ready

   * select: Win/Blocker/Decision/People/Lesson/Other
   * select: Work/Life
   * project/person tag entry (autocomplete)
4. [BE] (SEQ) Tags CRUD + autocomplete

   * normalize tags
   * return suggestions
5. [BE] (PAR) Generated title baseline

   * v0: deterministic title (first ~8–12 words) OR optional LLM title job
6. [FE] (PAR) Prompt chips (Career)

   * not required, but quick-start UI
7. [QA] (PAR) Tests for transcript revision + tag linking

**Done when:** user can correct transcript, classify it, add a project/person tag, and reopen it later seeing the persisted state.

---

## Phase 4 — Timeline + search (FTS) + quote chips + highlight navigation (core “trust”)

### Purpose

Enable fast retrieval without AI, and make “receipts” visible everywhere.

### Deliverables

* Timeline view with filters
* Full-text search across transcripts
* Quote chip on each entry card
* Tap quote → open entry + highlight span

### Implementation decisions

* Use Postgres FTS on transcripts (`tsvector`) for v0.
* Snippet extraction occurs server-side:

  * FTS returns match positions; compute a snippet window
  * store snippet offsets + text (either ephemeral or persist as snippet record)

### Tasks

1. [BE] (SEQ) Add FTS column + GIN index

   * `transcripts.text_tsvector`
2. [BE] (SEQ) Implement `/search` endpoint

   * inputs: q, date range, tag filters, type filters
   * output: ranked list of {entry_id, transcript_id, snippet_text, start_char, end_char}
3. [FE] (SEQ) Timeline UI

   * list by day
   * filters: date range, type, context, tags
4. [FE] (SEQ) Search UI

   * query box + results list + “jump to highlight”
5. [FE] (SEQ) Quote chip rendering on timeline cards

   * use most recent snippet or “auto quote” from transcript start
6. [BE] (PAR) “Auto quote” selection rule

   * if no search term: first meaningful sentence excerpt
7. [FE] (SEQ) Highlight navigation

   * open entry detail and scroll to offsets
   * highlight span in transcript view
8. [QA] (PAR) E2E tests

   * create entry, search term, open highlight

**Done when:** search is fast and usable; quote chips create repeated “trust moments” by jumping to exact transcript text.

---

## Phase 5 — Brag Doc v0 (career wedge, exportable)

### Purpose

Ship the career outcome without needing fancy AI.

### Deliverables

* Brag Doc view with buckets
* Manual bullet creation with attached citations
* “Add evidence to Brag” from entries/snippets
* Export: plain text (v0)

### Tasks

1. [FE] (SEQ) Brag Doc UI skeleton

   * buckets: Impact, Execution, Leadership, Collaboration, Growth
   * date range selector
2. [BE] (SEQ) Brag bullet CRUD endpoints
3. [FE] (SEQ) “Add to Brag” action from entry detail

   * user selects transcript highlight/snippet → creates citation + bullet draft
4. [BE] (SEQ) Citation creation endpoint

   * validate offsets against transcript version
5. [FE] (PAR) Bullet editor

   * inline edit bullet text
   * show “(x sources)” expand list
6. [BE] (SEQ) Export text job

   * build a plain text report with citations (dated quotes)
   * store as downloadable file
7. [FE] (SEQ) Export UI

   * “Generate export” → status → download
8. [QA] (PAR) Brag export test (snapshot)

**Done when:** a user can build a brag doc with receipts and export it to share.

At this point you have a **real v0**: record → retrieve → brag → export.

---

# V1 BUILDOUT PHASES (production-ready)

## Phase 6 — Ask v1 (sources-first, optional summary with enforced citations + “what gets sent”)

### Purpose

Deliver the RAG experience while preventing “chat sinkhole”.

### Deliverables

* Ask tab
* Sources-first results always
* Optional “Summarize these sources” → JSON citation mapping enforced
* “What gets sent” preview + redaction/masking controls
* Work-sensitive exclusion enforced by default

### Tasks

1. [FE] (SEQ) Ask UI

   * input box + templates
   * date range selector
   * display sources list (5–12)
2. [BE] (SEQ) Ask retrieval endpoint

   * reuse `/search` logic with query-specific ranking
   * store ask_queries + ask_results records
3. [FE] (SEQ) “What gets sent” preview modal

   * shows count + sample snippets (not everything)
   * toggles: redact names, mask numbers, include work-sensitive
4. [BE] (SEQ) Redaction pipeline (server-side, deterministic)

   * user dictionary + regex transforms
   * apply only to outbound provider payload
   * never mutate stored transcript
5. [AI] (SEQ) Summary job

   * input: query + retrieved snippets (after redaction)
   * output: JSON sentences with snippet_ids
   * validate mapping server-side
6. [FE] (SEQ) Summary renderer

   * sentence list with citations
   * click citation → open entry highlight
7. [BE] (PAR) Audit events

   * `llm_called` stores model name, snippet_count, bytes estimate (no content)
8. [QA] (PAR) Adversarial tests

   * ensure system refuses to output uncited sentences
   * ensure sensitive entries excluded by default

**Done when:** Ask works even with “sources only”; summaries are always citation-bound; user can see exactly what will be sent before generation.

---

## Phase 7 — Privacy-first production controls: export, deletion, audit, retention gating

### Purpose

Make the system safe to run in production with real users.

### Deliverables

* Complete export (zip: JSON + audio + transcripts + brag doc)
* Entry deletion and account deletion (hard delete + blob cleanup)
* Audit log view
* Provider retention gating configuration + operational checklist

### Tasks

1. [BE] (SEQ) Export-all job

   * build zip:

     * entries.json
     * transcripts.json (all versions)
     * tags.json
     * brag_doc.json
     * audio/ (all blobs)
2. [FE] (SEQ) Data export UI

   * “Request export” + status + download
3. [BE] (SEQ) Delete entry

   * cascade delete: transcripts, citations, brag mappings
   * delete audio blob
   * audit event
4. [FE] (SEQ) Delete entry UX

   * confirm modal
5. [BE] (SEQ) Delete account (full wipe)

   * confirm password
   * revoke tokens
   * delete all blobs
6. [FE] (SEQ) Delete account UX

   * explicit warnings + typed confirmation
7. [BE] (PAR) Audit log endpoint + pagination
8. [FE] (PAR) Audit log page
9. [INF] (SEQ) Provider “zero retention” gating

   * config flag: `REQUIRE_ZERO_RETENTION=true`
   * startup check:

     * environment must indicate approved provider tier
     * if not, disable summary features (sources-only still works)
10. [QA] (PAR) Data rights tests

* export contains expected files
* delete truly removes blobs and db rows

**Done when:** you can satisfy user requests for export/wipe and demonstrate a processing log without content leakage. (The “zero retention” commercial enablement is external, but the product behavior is correct.)

---

## Phase 8 — Production readiness V1: observability, security hardening, deployment, QA gates

### Purpose

Make V1 deployable and maintainable for real usage (10 concurrent users).

### Deliverables

* Deployment pipeline
* Monitoring + error reporting
* Rate limiting / abuse basics
* Backups + restore procedure
* Test gates + smoke suite
* Documentation

### Tasks

1. [INF] (SEQ) Production Docker build

   * multi-stage build for web + api
2. [INF] (SEQ) Reverse proxy

   * Nginx (TLS termination, compression, static assets)
3. [BE] (SEQ) API hardening

   * request size limits (audio upload)
   * rate limiting (login, upload, ask)
   * CORS policy (strict)
4. [BE] (SEQ) Security hardening

   * secure cookies
   * CSRF protection
   * password policy
   * dependency scanning
5. [INF] (SEQ) Backups

   * pg_dump schedule
   * blob directory backups
   * restore runbook tested
6. [BE] (PAR) Observability

   * structured JSON logs
   * Sentry (or equivalent) for FE/BE errors
7. [QA] (SEQ) End-to-end test suite

   * auth → record → transcribe → search → ask sources → brag export → delete
8. [QA] (PAR) Load sanity (10 concurrent users)

   * simulate:

     * 10 users uploading 1–2 entries
     * 10 users searching/asking
9. [DOC] (PAR) Operator docs + user docs

   * privacy disclosure
   * data export instructions
   * retention statement (provider dependent)

**Done when:** you can deploy V1, monitor it, back it up, restore it, and confidently run a private beta with real users.

---

# 6) Parallelization plan (how to distribute among devs)

## Workstreams (recommended)

### Workstream A — Backend Platform (BE-heavy)

* Phase 1 core schema + auth + storage + jobs
* Phase 2 transcription job
* Phase 4 search endpoint
* Phase 7 exports/deletion
* Phase 8 security hardening

### Workstream B — Frontend Core (FE-heavy)

* Auth screens + app shell
* Recording UI + processing UI
* Entry detail + indexing
* Timeline + search + highlight UX
* Brag Doc UI + export UI
* Ask UI + preview modal

### Workstream C — AI Integration (AI/BE)

* OpenAI transcription integration
* Ask summarization JSON schema + validation
* (v1) embeddings + rerank (optional)

### Workstream D — QA/Infra

* CI, docker compose, e2e tests
* monitoring + logging + deployment scripts

## Key sequencing constraints

* Auth + schema must exist before “real” app data flows.
* Transcription pipeline must exist before indexing/entry detail feels real.
* Search must exist before Ask/Brag feel valuable.
* Export/deletion must exist before production V1.

Everything else can move in parallel if you stub endpoints early.

---

# 7) Exact “v0 vs v1” cut line (so you don’t overbuild)

### v0 “shippable private beta”

* Auth
* Record/upload/transcribe
* Entry detail + edit transcript + classify + tags
* Timeline + search + highlight
* Brag Doc (manual evidence aggregation) + text export
* Basic deletion (entry) and basic export (brag)

### v1 “production-ready”

* Ask sources-first + optional constrained summary
* What-gets-sent + redaction
* Full export all + delete account
* Audit log
* Deployment + backups + monitoring + test gates

---

# 8) Provider-specific implementation notes (concrete)

### Audio transcription constraints

* Upload limit: 25MB; supported formats include `webm` (good for MediaRecorder). ([OpenAI Developers][4])
* Default model: `gpt-4o-mini-transcribe` (speed/cost), upgrade path to `gpt-4o-transcribe`. ([OpenAI Developers][4])

### Data retention gating

* By default, OpenAI indicates it may retain API inputs/outputs up to 30 days for abuse monitoring/service delivery unless exceptions apply. ([OpenAI][2])
* OpenAI indicates zero data retention is available for qualifying organizations. ([OpenAI][3])
  **Implementation stance:** build a strict runtime gate:
* If zero-retention is not contractually/configured, MindVault still functions in:

  * transcription-only + retrieval-only mode
  * summaries disabled
    …but since you said you want to remove “privacy mode”, we don’t market this as a “mode”—it’s a **capability toggle** controlled by compliance configuration.

---

# 9) What I would do next (practical execution order)

If you want the fastest path to a real beta without rework, run these phases in this order:

1. Phase 0 + Phase 1 (platform)
2. Phase 2 (record + transcribe)
3. Phase 3 (indexing + edits)
4. Phase 4 (timeline + search)
5. Phase 5 (brag doc)
   → You now have a real v0 loop.
6. Phase 6 (Ask v1)
7. Phase 7–8 (production controls)

---

If you want, I can also output this as:

* a **Jira-ready task breakdown** (each task as a ticket with acceptance criteria),
* a **component-by-component frontend spec** (routes, components, state ownership),
* and a **backend module map** (service layer boundaries so FastAPI stays thin).

[1]: https://developers.openai.com/api/docs/guides/your-data?utm_source=chatgpt.com "Data controls in the OpenAI platform"
[2]: https://openai.com/enterprise-privacy/?utm_source=chatgpt.com "Enterprise privacy at OpenAI"
[3]: https://openai.com/business-data/?utm_source=chatgpt.com "Business data privacy, security, and compliance | OpenAI"
[4]: https://developers.openai.com/api/docs/guides/speech-to-text "Speech to text | OpenAI API"
[5]: https://developers.openai.com/api/docs/models/gpt-4o-mini?utm_source=chatgpt.com "GPT-4o mini Model | OpenAI API"
[6]: https://developers.openai.com/api/docs/models/text-embedding-3-small?utm_source=chatgpt.com "text-embedding-3-small Model | OpenAI API"
