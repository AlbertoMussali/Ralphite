---
task: "VoiceVault (aka MindVault) — Web MVP (v0) → Production V1"
test_command: "bash -lc 'test -f Makefile && make test'"
---

# Task: VoiceVault (aka MindVault) — Implementation Plan to V1

This task file is designed for **Ralphex** (`.cursor/ralphex-scripts/*`).  
Ralphex will:
- pick the **next unchecked** checklist line (`[ ]`) in this file
- work only on that item
- mark it `[x]` when done
- run `test_command` after each iteration (when configured)

## Product contract (non-negotiables)

These are acceptance constraints, not suggestions:

- Voice-first capture → transcript → minimal indexing
- Retrieval-first “Ask”:
  - must always work as **sources-only**
  - summaries are optional and must be **citation-bound**
- Evidence-backed UX everywhere (tappable citations + highlight jumps)
- “Raw is sacred”:
  - audio + verbatim transcript preserved
  - generated artifacts are separate and can be regenerated
- Privacy-first vendor posture:
  - providers used only for stateless processing
  - implement **runtime gating** for “zero retention” capability (summaries disabled if not satisfied)
- Sensitive controls:
  - `work_sensitive` excluded from LLM by default
  - `raw_only` excluded from generation but still searchable/playable

## Scope boundary (so we don’t overbuild)

- v0 (private beta): Phases 0–5
- v1 (production-ready): Phases 6–8

## Architecture / implementation defaults (locked)

- Monorepo layout:
  - `apps/web` (React 18 + Vite + TS)
  - `apps/api` (Python 3.12 + FastAPI + Pydantic v2)
  - `infra/` (Docker/compose, production Docker builds, Nginx)
  - `docs/`
- Backend:
  - Postgres 16 (relational + FTS; pgvector optional later)
  - Redis + RQ for background jobs
  - Storage abstraction with local disk implementation (swappable later)
- Frontend:
  - React Router
  - TanStack Query
  - Zustand
  - Tailwind + shadcn/ui
  - MediaRecorder → `webm` recording
- AI provider:
  - OpenAI STT: `gpt-4o-mini-transcribe` default (upgrade path later)
  - OpenAI generation: `gpt-4o-mini` for summaries
  - Embeddings (optional later): `text-embedding-3-small`

## Public API surface (target)

All endpoints under `/api/v1/...`:
- Auth: `/auth/signup`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/me`
- Entries: `/entries` CRUD-ish + `/entries/{id}/audio` + transcript patching
- Search: `/search`
- Ask: `/ask/query`, `/ask/{query_id}/summarize`, `/ask/{query_id}`
- Brag: `/brag` + bullets CRUD + `/brag/export`
- Data rights: `/exports` + status/download, `/audit`, `/account` deletion

## Data model (target tables)

`users`, `entries`, `audio_assets`, `transcripts` (+ versioning), `tags`, `entry_tags`, `citations`, `brag_bullets`, `brag_bullet_citations`, `ask_queries`, `ask_results`, `audit_log`, `export_jobs`.

---

# Execution Checklist (Ralphex Tasks)

Notes:
- Each checklist line is a Ralphex task item.
- Group numbers are phase-based so `.cursor/ralphex-scripts/ralphex-parallel.sh` can parallelize safely.

## Phase 0 — Project setup + engineering baseline (foundation)

- [x] [INF] Create monorepo folder structure (`apps/web`, `apps/api`, `infra`, `docs`). <!-- group: 100 -->
  - Done when: tree exists; minimal READMEs in each folder explain purpose.
- [x] [INF] Add dev Docker Compose stack (api, worker, db, redis, web). <!-- group: 100 -->
  - Done when: `docker compose up` boots all services without manual steps.
- [x] [BE] Implement FastAPI skeleton (`/health`, `/version`, settings loader). <!-- group: 100 -->
  - Done when: API container serves health/version endpoints.
- [x] [FE] Implement Vite + React skeleton with routes (`/login`, `/signup`, `/app`). <!-- group: 100 -->
  - Done when: web container serves UI and routes render.
- [x] [INF] Add CI pipeline (lint + tests for FE/BE). <!-- group: 100 -->
  - Done when: CI runs on PR/commit and is green on main branch.
- [x] [BE] Wire Alembic migrations and DB connectivity end-to-end. <!-- group: 100 -->
  - Done when: `alembic upgrade head` runs in container against compose Postgres.
- [x] [FE] Set up Tailwind + shadcn/ui, router, TanStack Query client. <!-- group: 100 -->
  - Done when: basic styled UI components render, no build warnings.
- [x] [QA] Define project “Definition of Done” and minimal smoke plan in `docs/`. <!-- group: 100 -->
  - Done when: includes required tests per layer and “how to run locally”.
- [x] [INF] Add `Makefile` targets (`make dev-up`, `make dev-down`, `make lint`, `make test`). <!-- group: 100 -->
  - Done when: `make test` exists (even if minimal at first) and is used by Ralphex.

## Phase 1 — Core backend platform: auth, storage, jobs, base schema

- [x] [BE] Implement DB schema v1 (users, entries, transcripts, audio_assets, tags, audit_log). <!-- group: 200 -->
  - Done when: migrations create tables; app can connect and query.
- [x] [BE] Implement auth service: signup/login/refresh/logout with Argon2. <!-- group: 200 -->
  - Done when: accounts can be created and sessions refreshed.
- [x] [FE] Implement auth UI (signup/login) + token refresh integration. <!-- group: 200 -->
  - Done when: user can login and reach `/app`.
- [x] [BE] Add authorization middleware (all entry routes require auth). <!-- group: 200 -->
  - Done when: unauthenticated calls are rejected consistently.
- [x] [BE] Implement storage abstraction + local disk backend. <!-- group: 200 -->
  - Done when: audio can be stored/retrieved by a storage key.
- [x] [BE] Add file upload skeleton for `POST /entries/{id}/audio` (store blob + metadata). <!-- group: 200 -->
  - Done when: blob persists; metadata recorded in DB; endpoint validated.
- [x] [BE] Integrate RQ worker process + job registry in compose. <!-- group: 200 -->
  - Done when: worker runs and can execute a stub job.
- [x] [BE] Add audit logging middleware for auth + entry events (no content). <!-- group: 200 -->
  - Done when: DB audit rows exist for key actions.
- [x] [QA] Add backend unit tests for auth + storage + basic CRUD. <!-- group: 200 -->
  - Done when: `make test` runs these and passes.
- [x] [INF] Add env management: `.env.example` + documented secrets strategy (dev/prod). <!-- group: 200 -->
  - Done when: new dev can boot from docs + `.env.example`.

## Phase 2 — Audio capture (web), upload, transcription pipeline (v0 usable)

- [x] [FE] Build recording UI with MediaRecorder (webm blob, timer). <!-- group: 300 -->
  - Done when: user can record/stop and obtain a playable blob.
- [x] [BE] Implement `POST /entries` to create an entry shell. <!-- group: 300 -->
  - Done when: returns `entry_id`; entry stored with correct initial status.
- [x] [FE] Implement upload pipeline: create entry → upload audio → poll status. <!-- group: 300 -->
  - Done when: UI shows uploading/transcribing/ready/error states.
- [x] [BE] Implement multipart audio upload fully: store blob, set status=transcribing, enqueue job. <!-- group: 300 -->
  - Done when: upload triggers job and persists audio asset record.
- [ ] [AI] Implement transcription worker job using OpenAI STT and store transcript v1. <!-- group: 300 -->
  - Done when: transcript saved; entry status flips to ready.
- [ ] [BE] Define and enforce error contract (transient vs fatal; error_code/message). <!-- group: 300 -->
  - Done when: failures are visible in UI and safe retries are possible.
- [ ] [FE] Implement processing screen with retry path where safe. <!-- group: 300 -->
  - Done when: user can recover from common failures without data loss.
- [ ] [BE] Add audit events: `audio_uploaded`, `transcription_called` (no content; include model/bytes). <!-- group: 300 -->
  - Done when: audit log shows processing timeline.
- [ ] [QA] Add integration test path: upload small file + stub OpenAI in test mode. <!-- group: 300 -->
  - Done when: test suite validates pipeline deterministically.

## Phase 3 — Entry detail, transcript versioning, and “5-second indexing”

- [ ] [BE] Implement transcript versioning: `PATCH /entries/{id}/transcript` creates version+1. <!-- group: 400 -->
  - Done when: old transcript preserved; new version becomes active.
- [ ] [FE] Implement entry detail page: audio player + transcript display. <!-- group: 400 -->
  - Done when: entry renders reliably and is navigable from timeline/search.
- [ ] [FE] Implement “Edit transcript” mode that creates a new transcript version. <!-- group: 400 -->
  - Done when: edits persist; version increments; UI indicates edited status.
- [ ] [FE] Implement 5-second indexing modal post-transcription (type, context, tags). <!-- group: 400 -->
  - Done when: user can classify Win/Blocker/etc + Work/Life + tags.
- [ ] [BE] Implement tags CRUD + autocomplete (normalized). <!-- group: 400 -->
  - Done when: tag suggestions work; entry-tag links persist.
- [ ] [BE] Implement baseline title behavior (deterministic or optional job). <!-- group: 400 -->
  - Done when: entries show a stable title without needing generation.
- [ ] [QA] Add tests for transcript revision + tag linking behavior. <!-- group: 400 -->
  - Done when: edits don’t corrupt offsets; tag relations correct.

## Phase 4 — Timeline + search (FTS) + quote chips + highlight navigation

- [ ] [BE] Add Postgres FTS column/index for transcripts (`tsvector` + GIN). <!-- group: 500 -->
  - Done when: queries are fast on realistic data volumes.
- [ ] [BE] Implement `/search` endpoint returning ranked snippets + offsets. <!-- group: 500 -->
  - Done when: response includes `{entry_id, transcript_id, snippet_text, start_char, end_char}`.
- [ ] [FE] Implement timeline UI with filters (date range, type, context, tags). <!-- group: 500 -->
  - Done when: browsing feels responsive and predictable.
- [ ] [FE] Implement search UI: query box + results + jump-to-highlight. <!-- group: 500 -->
  - Done when: click result opens entry and highlights matching span.
- [ ] [FE] Implement quote chip on timeline cards. <!-- group: 500 -->
  - Done when: cards show a credible “receipt” snippet consistently.
- [ ] [BE] Implement auto-quote selection rule when no explicit snippet. <!-- group: 500 -->
  - Done when: quote chip is meaningful (not empty/garbage).
- [ ] [FE] Implement transcript highlight navigation (scroll + span highlight). <!-- group: 500 -->
  - Done when: highlight lands accurately by offsets.
- [ ] [QA] Add E2E test: create entry → search term → open highlight. <!-- group: 500 -->
  - Done when: runs in CI reliably.

## Phase 5 — Brag Doc v0 (career wedge, exportable)

- [ ] [FE] Implement Brag Doc UI skeleton (buckets + date range selector). <!-- group: 600 -->
  - Done when: buckets render: Impact/Execution/Leadership/Collaboration/Growth.
- [ ] [BE] Implement brag bullet CRUD endpoints. <!-- group: 600 -->
  - Done when: bullets persist, update, delete, list by bucket.
- [ ] [FE] Implement “Add to Brag” from entry detail using selected highlight/snippet. <!-- group: 600 -->
  - Done when: creates bullet draft linked to evidence.
- [ ] [BE] Implement citation creation endpoint validating offsets vs transcript version. <!-- group: 600 -->
  - Done when: invalid offsets rejected; citations immutable to source version.
- [ ] [FE] Implement bullet editor showing sources count + expandable evidence list. <!-- group: 600 -->
  - Done when: user can edit claim text without losing citations.
- [ ] [BE] Implement text export job producing downloadable report with dated quotes. <!-- group: 600 -->
  - Done when: export is reproducible and stored as a job artifact.
- [ ] [FE] Implement export UI (request → status → download). <!-- group: 600 -->
  - Done when: user can generate and download export without manual ops.
- [ ] [QA] Add brag export test (snapshot or deterministic fixtures). <!-- group: 600 -->
  - Done when: export format remains stable.

## Phase 6 — Ask v1 (sources-first + optional summary with enforced citations)

- [ ] [FE] Implement Ask UI (query + templates + date range + sources list). <!-- group: 700 -->
  - Done when: sources-only results are always available.
- [ ] [BE] Implement Ask retrieval endpoint (store ask_queries/results; reuse search ranking). <!-- group: 700 -->
  - Done when: returns a bounded list of sources (5–12) quickly.
- [ ] [FE] Implement “What gets sent” preview modal + toggles (redact, mask, include sensitive). <!-- group: 700 -->
  - Done when: user can see outbound payload shape before summarization.
- [ ] [BE] Implement deterministic outbound redaction/masking pipeline (never mutates stored data). <!-- group: 700 -->
  - Done when: transformations apply only to provider-bound payload.
- [ ] [AI] Implement summary job: JSON sentences with snippet_ids + server-side validation. <!-- group: 700 -->
  - Done when: uncited sentences are rejected; only provided snippet_ids allowed.
- [ ] [FE] Implement summary renderer with per-sentence citations + jump to highlight. <!-- group: 700 -->
  - Done when: every sentence has clickable evidence.
- [ ] [BE] Add audit event `llm_called` storing metadata only (model, snippet_count, byte estimate). <!-- group: 700 -->
  - Done when: no content stored in audit trail.
- [ ] [QA] Add adversarial tests for citation enforcement + sensitive exclusion default. <!-- group: 700 -->
  - Done when: regression prevents “freeform” output.

## Phase 7 — Production controls: export, deletion, audit, retention gating

- [ ] [BE] Implement export-all job (zip: JSON + transcripts all versions + tags + brag + audio blobs). <!-- group: 800 -->
  - Done when: zip contents match spec and restore is feasible.
- [ ] [FE] Implement data export UI (request + status + download). <!-- group: 800 -->
  - Done when: export flows without admin intervention.
- [ ] [BE] Implement entry deletion with cascade + blob cleanup + audit event. <!-- group: 800 -->
  - Done when: no orphan blobs/rows remain.
- [ ] [FE] Implement delete-entry UX (explicit confirmation). <!-- group: 800 -->
  - Done when: prevents accidental deletion; communicates finality.
- [ ] [BE] Implement delete-account full wipe (password confirm, revoke tokens, delete blobs). <!-- group: 800 -->
  - Done when: user data is actually removed; audit records are content-free.
- [ ] [FE] Implement delete-account UX with typed confirmation + warnings. <!-- group: 800 -->
  - Done when: user understands irreversible action.
- [ ] [BE] Implement audit endpoint with pagination. <!-- group: 800 -->
  - Done when: supports viewing processing events without leaking content.
- [ ] [FE] Implement audit log page. <!-- group: 800 -->
  - Done when: user can review processing history.
- [ ] [INF] Implement “zero retention required” runtime gate for summary features. <!-- group: 800 -->
  - Done when: summaries disable cleanly if requirement not met; sources-only still works.
- [ ] [QA] Add data rights tests (export contents; delete removes blobs/rows). <!-- group: 800 -->
  - Done when: deterministic tests prove compliance behaviors.

## Phase 8 — Production readiness V1: observability, security, deployment, QA gates

- [ ] [INF] Implement production Docker builds (multi-stage for web/api). <!-- group: 900 -->
  - Done when: images are small, reproducible, tagged.
- [ ] [INF] Add Nginx reverse proxy (TLS termination, compression, static assets). <!-- group: 900 -->
  - Done when: single VM deploy is documented and repeatable.
- [ ] [BE] Implement API hardening (request size limits, rate limiting, strict CORS). <!-- group: 900 -->
  - Done when: abuse surfaces are reduced; limits documented.
- [ ] [BE] Implement security hardening (secure cookies, CSRF, password policy, dependency scanning). <!-- group: 900 -->
  - Done when: baseline security checklist is met and automated where possible.
- [ ] [INF] Implement backups (pg_dump schedule + blob backup) + restore runbook tested. <!-- group: 900 -->
  - Done when: restore procedure is executable and verified.
- [ ] [BE] Add observability (structured JSON logs + error reporting for FE/BE). <!-- group: 900 -->
  - Done when: operator can diagnose issues quickly.
- [ ] [QA] Build end-to-end test suite covering full critical loop. <!-- group: 900 -->
  - Done when: CI runs auth → record → transcribe → search → ask sources → brag export → delete.
- [ ] [QA] Run load sanity for 10 concurrent users (upload/search/ask). <!-- group: 900 -->
  - Done when: no catastrophic bottlenecks; results recorded in `docs/`.
- [ ] [DOC] Write operator + user docs (privacy disclosure, data export, retention statements). <!-- group: 900 -->
  - Done when: a new operator can deploy + run + support users from docs.

---

## Ralphex Instructions (operational)

1. Only work on the next unchecked task item (`[ ]`) in “Execution Checklist”.
2. When complete, change it to `[x]` in `RALPHEX_TASK.md`.
3. Keep commits small and checkpoint before risky changes.
4. Run `make test` (or the best available target) after changes; keep it green.
5. Update `.ralphex/progress.md` with concise progress notes after significant milestones.
6. When ALL items are `[x]`, output exactly: `<ralphex>COMPLETE</ralphex>`
7. If stuck on the same blocker 3+ attempts, output exactly: `<ralphex>GUTTER</ralphex>`

## Acceptance test scenarios (minimum bar for “ready for implementation”)

These scenarios must be achievable by the end of the corresponding phases:

- v0 end-to-end: Signup/login → record audio → upload → transcribe → classify/tag → search → jump-to-highlight → add evidence to brag → export brag.
- v1 end-to-end: Ask returns sources-only reliably; optional summaries are always citation-bound; exports/deletions work; audit log shows processing events without content; production deploy is repeatable with backups/restore.

## Assumptions and defaults

- The repo currently contains planning/docs and Ralphex scripts, but not an existing FE/BE codebase; tasks assume greenfield implementation under the monorepo structure above.
- OpenAI usage is limited to transcription + constrained summarization; stored data remains in VoiceVault.
- Single-VM Docker deployment is the default target for production readiness (Phase 8).
