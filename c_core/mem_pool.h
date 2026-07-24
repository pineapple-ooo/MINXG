/*
 * mem_pool.h — C arena allocator + slab allocator for hot-path memory management
 *
 * Arena: bump-pointer allocator for short-lived allocations (one request lifetime).
 *        Allocates once, frees all at once. No fragmentation.
 * Slab:  per-size-class pre-allocated pools, no locks for owner-thread,
 *        ideal for fixed-size objects (tokens, nodes, events).
 */

#ifndef AgentHarness_MEM_POOL_H
#define AgentHarness_MEM_POOL_H

#include "agent_harness_arch.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ═══════════════════════════════════════════════════════════════════════════
 * Arena allocator (linear bump-pointer)
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef void* agent_harness_arena_t;

/*
 * Create an arena with initial block size (grows in 2x increments).
 * Recommended: agent_harness_arena_create(65536) for 64KB initial block.
 */
agent_harness_arena_t agent_harness_arena_create(size_t block_size);

/*
 * Allocate aligned memory from arena. Freeing individual allocations
 * is a no-op — the arena frees everything at once on destroy.
 */
void* agent_harness_arena_alloc(agent_harness_arena_t arena, size_t size);
void* agent_harness_arena_realloc(agent_harness_arena_t arena, void* ptr, size_t old_size, size_t new_size);

/*
 * Reset: keeps all blocks but resets bump pointer (reuse memory).
 */
void agent_harness_arena_reset(agent_harness_arena_t arena);
void agent_harness_arena_destroy(agent_harness_arena_t arena);

/* Stats: total allocated, used, blocks */
size_t agent_harness_arena_total(agent_harness_arena_t arena);
size_t agent_harness_arena_used(agent_harness_arena_t arena);
int    agent_harness_arena_block_count(agent_harness_arena_t arena);

/* ═══════════════════════════════════════════════════════════════════════════
 * Slab allocator — fixed-size object pool per size class
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef void* agent_harness_slab_t;

/* item_size: what the user allocates. slab_size: total objects per slab. */
agent_harness_slab_t agent_harness_slab_create(size_t item_size, size_t slab_size);
void*        agent_harness_slab_alloc(agent_harness_slab_t slab);
void         agent_harness_slab_free(agent_harness_slab_t slab, void* ptr);
void         agent_harness_slab_destroy(agent_harness_slab_t slab);

/* Stats */
size_t agent_harness_slab_total_allocated(agent_harness_slab_t slab);
size_t agent_harness_slab_free_count(agent_harness_slab_t slab);

/* ═══════════════════════════════════════════════════════════════════════════
 * Fixed-size ring buffer (fast allocation for messages, events, packets)
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef void* agent_harness_rb_t;

agent_harness_rb_t agent_harness_rb_create(size_t item_size, size_t capacity);
agent_harness_err_t agent_harness_rb_push(agent_harness_rb_t rb, const void* item);
agent_harness_err_t agent_harness_rb_pop(agent_harness_rb_t rb, void* out_item);
size_t      agent_harness_rb_count(agent_harness_rb_t rb);
void        agent_harness_rb_destroy(agent_harness_rb_t rb);

#ifdef __cplusplus
}
#endif

#endif /* AgentHarness_MEM_POOL_H */