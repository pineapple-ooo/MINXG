/*
 * agent_harness_arch.h — Unified C header for AgentHarness polyglot architecture
 *
 * Language role assignments:
 *   C    — Hot-path data crunching, OS syscall wrappers, stable FFI contract,
 *          lock-free ring buffers, SIMD-accelerated text processing, raw memory pooling.
 *          C is the universal FFI lingua franca; every other language calls into C directly.
 *          This file: no C++ features, C11 only, zero dependencies beyond libc.
 *
 *   C++  — High-level RAII wrappers over C primitives, OpenSSL crypto pipeline,
 *          polymorphic plugin system, JSON/text parser combinators, template-based
 *          containers (concurrent hash maps, LRU caches). Depends on C layer + OpenSSL.
 *
 *   Go   — Network services: gRPC gateway, WebSocket fan-out hub, rate-limit service,
 *          distributed cron scheduler, health-check daemon. Go's goroutine model
 *          replaces Python's asyncio for all server-side concurrency. Talks to C++
 *          via CGo and to Python via Unix sockets/protobuf.
 *
 *   Python — User-facing CLI/TUI shell, AI prompt orchestration, extension ecosystem,
 *            configuration management, documentation generation. Delegates all
 *            performance-critical work to C/C++/Go via ctypes/CGo/sockets.
 *
 * Call chain:  CLI (Python) → ctypes → C wrapper → C++ core → OpenSSL/kernel
 *              Gateway (Go) → CGo → C wrapper → C++ core → ...
 */

#ifndef AgentHarness_ARCH_H
#define AgentHarness_ARCH_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ═══════════════════════════════════════════════════════════════════════════
 * Versioning — single source of truth for the entire polyglot stack
 * ═══════════════════════════════════════════════════════════════════════════ */

#define AgentHarness_VERSION_MAJOR 2
#define AgentHarness_VERSION_MINOR 0
#define AgentHarness_VERSION_PATCH 0
#define AgentHarness_VERSION_STRING "2.0.0-polyglot"

/* ═══════════════════════════════════════════════════════════════════════════
 * Error codes — shared between C, C++, Go (CGo), Python (ctypes)
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef enum {
    AgentHarness_OK                    =  0,
    AgentHarness_ERR_NULL_PTR          = -1,
    AgentHarness_ERR_BUFFER_TOO_SMALL  = -2,
    AgentHarness_ERR_IO                = -3,
    AgentHarness_ERR_CRYPTO            = -4,
    AgentHarness_ERR_ENCODING          = -5,
    AgentHarness_ERR_PARSE             = -6,
    AgentHarness_ERR_BOUNDS            = -7,
    AgentHarness_ERR_TIMEOUT           = -8,
    AgentHarness_ERR_PERMISSION        = -9,
    AgentHarness_ERR_THREAD            = -10,
    AgentHarness_ERR_MEMORY            = -11,
    AgentHarness_ERR_NOT_FOUND         = -12,
    AgentHarness_ERR_ALREADY_EXISTS    = -13,
    AgentHarness_ERR_INVALID_STATE     = -14,
    AgentHarness_ERR_NOT_IMPLEMENTED   = -15,
} agent_harness_err_t;

const char* agent_harness_strerror(agent_harness_err_t err);

/* ═══════════════════════════════════════════════════════════════════════════
 * Byte buffer — the universal data carrier across all FFI boundaries
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef struct {
    uint8_t*  data;
    size_t    len;
    size_t    cap;
    void*     _internal;  /* opaque: arena or malloc tag */
} agent_harness_buf_t;

agent_harness_buf_t agent_harness_buf_new(size_t cap);
agent_harness_buf_t agent_harness_buf_from_bytes(const uint8_t* src, size_t len);
agent_harness_buf_t agent_harness_buf_from_cstr(const char* str);
void        agent_harness_buf_free(agent_harness_buf_t* buf);
agent_harness_err_t agent_harness_buf_reserve(agent_harness_buf_t* buf, size_t new_cap);
agent_harness_err_t agent_harness_buf_append(agent_harness_buf_t* buf, const uint8_t* src, size_t len);
void        agent_harness_buf_clear(agent_harness_buf_t* buf);
bool        agent_harness_buf_eq(const agent_harness_buf_t* a, const agent_harness_buf_t* b);

/* ═══════════════════════════════════════════════════════════════════════════
 * Thread pool (lock-free work-stealing) — C owns the concurrency primitives
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef void* agent_harness_thread_pool_t;
typedef void (*agent_harness_work_fn)(void* arg);

agent_harness_thread_pool_t agent_harness_thread_pool_create(int num_threads);
agent_harness_err_t         agent_harness_thread_pool_submit(agent_harness_thread_pool_t pool,
                                             agent_harness_work_fn fn, void* arg);
agent_harness_err_t         agent_harness_thread_pool_wait(agent_harness_thread_pool_t pool);
void                agent_harness_thread_pool_destroy(agent_harness_thread_pool_t pool);
int                 agent_harness_thread_pool_pending(agent_harness_thread_pool_t pool);

/* ═══════════════════════════════════════════════════════════════════════════
 * Lock-free ring buffer — single-producer single-consumer (SPSC)
 * Useful for Python ↔ C++ streaming, Go ↔ C++ event pipes
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef void* agent_harness_ring_t;

agent_harness_ring_t agent_harness_ring_create(size_t capacity);
agent_harness_err_t  agent_harness_ring_push(agent_harness_ring_t ring, const uint8_t* data, size_t len);
agent_harness_err_t  agent_harness_ring_pop(agent_harness_ring_t ring, uint8_t* out, size_t out_cap, size_t* out_len);
size_t       agent_harness_ring_readable(agent_harness_ring_t ring);
size_t       agent_harness_ring_writable(agent_harness_ring_t ring);
void         agent_harness_ring_destroy(agent_harness_ring_t ring);

#ifdef __cplusplus
}
#endif

#endif /* AgentHarness_ARCH_H */