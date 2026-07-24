# AgentHarness Runtime Architecture Refactor Plan

**Start time**: 2026-07-22T22:06:45Z  
**Target**: 1400+ steps, 10+ hours, zero stubs  
**Constraint**: single-source `/storage/emulated/0/AgentHarness-main/`, pytest only, no mirrors

---

## Phase 0 — Timer & Baseline (Steps 1-50)
- [x] Step 1: Start timer
- [x] Step 2: Capture current file inventory
- [x] Step 3: Capture current test baseline (1185 passed, 3 failed)
- [ ] Step 4-50: Document every file that needs refactoring

## Phase 1 — Asset Cleanup (Steps 51-150)
- [ ] Step 51-100: Remove C/C++/Go/R legacy assets
- [ ] Step 101-150: Consolidate wasm/julia/datalog assets

## Phase 2 — Runtime File Production-Grade Refactor (Steps 151-600)
- [ ] Step 151-250: `_exec.py` → full execution framework with memory tracking, resource limits, sandboxing
- [ ] Step 251-400: `wasm.py` → full WAT toolchain, optimizer, type checker, linker, encoder, WASI
- [ ] Step 401-500: `scientific.py` → Julia/Datalog/Python unified with 40+ modes each
- [ ] Step 501-600: `installer.py` + `manifest.py` → production-grade with versioning, checksums, rollback

## Phase 3 — Test Suite Overhaul (Steps 601-800)
- [ ] Step 601-700: Rewrite polyglot tests with real assertions, not just smoke
- [ ] Step 701-800: Add integration tests for every mode in every language

## Phase 4 — Worker Layer Refactor (Steps 801-1000)
- [ ] Step 801-900: `wasm_worker.py` → full tool suite (12 tools)
- [ ] Step 901-1000: `julia_worker.py` / `datalog_worker.py` → unified scientific worker

## Phase 5 — Core Module Quality (Steps 1001-1200)
- [ ] Step 1001-1100: Security audit (json truncation, subprocess cleanup, path traversal)
- [ ] Step 1101-1200: Performance audit (caching, memory, connection pooling)

## Phase 6 — Documentation & Polish (Steps 1201-1400)
- [ ] Step 1201-1300: Full doc rewrite for every module
- [ ] Step 1301-1400: Final integration test run, fix all regressions

## Phase 7 — Beyond 1400 (Steps 1401+)
- [ ] Step 1401+: Deep feature expansion based on user feedback

---

**Current**: Step 4 — documenting file inventory
