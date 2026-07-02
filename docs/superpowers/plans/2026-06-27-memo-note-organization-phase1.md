# Memo Note Organization Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight organization layer to Memo notes with flat categories, tag filtering, favorite, pinning, and backward-compatible OSS note payload fields.

**Architecture:** Keep notes as the primary local-first object. Add `NoteCategory` as a small adjacent model, append organization fields to `Note`, and expose category/tag metadata through focused APIs. OSS compatibility is additive: old note JSON payloads remain readable, new payloads include optional organization fields.

**Tech Stack:** FastAPI, SQLModel, SQLite lightweight schema migration, pytest, React, TypeScript, TanStack Query.

---

### Task 1: Backend Note Organization Model And Service

**Files:**
- Modify: `backend/app/models/note.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/schemas/note.py`
- Modify: `backend/app/services/note_service.py`
- Modify: `backend/app/api/notes.py`
- Modify: `backend/app/core/database.py`
- Test: `backend/tests/test_note_service_jobs.py`

- [ ] **Step 1: Write failing tests**

Add tests proving organization-only updates do not rebuild content jobs, category filters work, and pin/favorite fields round-trip.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest backend/tests/test_note_service_jobs.py -q`

Expected: tests fail because `NoteCategory`, `category_id`, `is_favorite`, `pinned_at`, `tags` update support, and note filters do not exist yet.

- [ ] **Step 3: Implement model, schema, and service changes**

Add `NoteCategory`; append `category_id`, `is_favorite`, `pinned_at`; extend `NoteCreate`, `NoteUpdate`, `NoteRead`, `NoteListItem`; add service helpers for category CRUD, tag listing, and organization-only note updates.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest backend/tests/test_note_service_jobs.py -q`

Expected: all note service tests pass.

### Task 2: Backend Category And Tag APIs

**Files:**
- Create: `backend/app/api/note_categories.py`
- Create: `backend/app/api/note_tags.py`
- Modify: `backend/app/api/__init__.py` or app router registration file
- Test: `backend/tests/test_app_routes.py`
- Test: `backend/tests/test_note_service_jobs.py`

- [ ] **Step 1: Write failing API route tests**

Assert routes exist for `/api/note-categories` and `/api/note-tags`, and service tests cover rename/merge/delete tag behavior.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest backend/tests/test_app_routes.py backend/tests/test_note_service_jobs.py -q`

Expected: route assertions fail before routers are registered.

- [ ] **Step 3: Implement routers**

Expose category CRUD and lightweight tag management APIs backed by note service helpers.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest backend/tests/test_app_routes.py backend/tests/test_note_service_jobs.py -q`

Expected: all route and service tests pass.

### Task 3: OSS Sync Compatibility

**Files:**
- Modify: `backend/app/services/cloud_sync_service.py`
- Test: `backend/tests/test_cloud_sync_service.py`

- [ ] **Step 1: Write failing sync tests**

Add tests for pushing new organization fields and pulling legacy payloads without organization fields.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest backend/tests/test_cloud_sync_service.py -q`

Expected: new assertions fail before payload fields are implemented.

- [ ] **Step 3: Implement additive payload fields**

Update `_note_payload` and `_apply_note_payload` to handle `category_id`, `category_name`, `is_favorite`, `pinned_at`, and `organization_schema_version` with safe defaults.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest backend/tests/test_cloud_sync_service.py -q`

Expected: all cloud sync tests pass.

### Task 4: Frontend Memo Organization UI

**Files:**
- Modify: `frontend/src/types/note.ts`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/pages/memo/MemoPage.tsx`
- Modify: `frontend/src/features/notes/NoteSidebar.tsx`
- Modify: `frontend/src/features/notes/NoteDetail.tsx`
- Possibly create: `frontend/src/features/notes/noteOrganization.ts`

- [ ] **Step 1: Type-check expected frontend API**

Run: `npm --prefix frontend run build`

Expected before implementation: TypeScript still passes; after adding fields, use this as regression verification.

- [ ] **Step 2: Implement API types and client functions**

Add `NoteCategory`, organization fields, category list/create/update/delete clients, tag list/manage clients, and note update fields.

- [ ] **Step 3: Implement UI state and controls**

Add quick filters, category list, tag list, favorite/pin actions, and category/tag editing in note detail while keeping current compose/read flows intact.

- [ ] **Step 4: Run frontend build**

Run: `npm --prefix frontend run build`

Expected: build completes with no TypeScript errors.

### Task 5: Final Verification

**Files:**
- All changed files

- [ ] **Step 1: Run focused backend tests**

Run: `pytest backend/tests/test_note_service_jobs.py backend/tests/test_cloud_sync_service.py backend/tests/test_app_routes.py -q`

Expected: all pass.

- [ ] **Step 2: Run frontend build**

Run: `npm --prefix frontend run build`

Expected: build passes.

- [ ] **Step 3: Review git diff**

Run: `git diff --stat` and `git diff --check`

Expected: no whitespace errors; diff only includes Phase 1 implementation plus already-existing user-approved docs.
