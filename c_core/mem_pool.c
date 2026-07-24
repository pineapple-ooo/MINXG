/*
 * mem_pool.c — Implementation: arena, slab, fixed-size ring buffer
 */

#include "mem_pool.h"
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

/* ══════════════════════════════════════════════════════════════════════════
 * Arena allocator
 * ══════════════════════════════════════════════════════════════════════════ */

#define ARENA_DEFAULT_BLOCK 65536
#define ARENA_ALIGN 16

typedef struct arena_block {
    struct arena_block* next;
    char*  base;       /* start of allocatable memory */
    size_t size;       /* total block size */
    size_t offset;     /* current bump pointer */
} arena_block_t;

typedef struct {
    arena_block_t* head;
    arena_block_t* tail;
    size_t         block_default;
    size_t         total_allocated;
    int            block_count;
} arena_impl_t;

agent_harness_arena_t agent_harness_arena_create(size_t block_size) {
    arena_impl_t* a = (arena_impl_t*)calloc(1, sizeof(arena_impl_t));
    if (!a) return NULL;
    a->block_default = block_size ? block_size : ARENA_DEFAULT_BLOCK;
    return a;
}

static arena_block_t* arena_new_block(arena_impl_t* a, size_t min_size) {
    size_t sz = a->block_default;
    while (sz < min_size + sizeof(arena_block_t)) sz *= 2;
    arena_block_t* blk = (arena_block_t*)malloc(sizeof(arena_block_t) + sz);
    if (!blk) return NULL;
    blk->next   = NULL;
    blk->base   = ((char*)blk) + sizeof(arena_block_t);
    blk->size   = sz;
    blk->offset = 0;
    return blk;
}

void* agent_harness_arena_alloc(agent_harness_arena_t arena, size_t size) {
    if (!arena || size == 0) return NULL;
    arena_impl_t* a = (arena_impl_t*)arena;

    if (size % ARENA_ALIGN) size += ARENA_ALIGN - (size % ARENA_ALIGN);

    /* try to fit in existing blocks */
    for (arena_block_t* blk = a->tail; blk; blk = blk->next) {
        if (blk->offset + size <= blk->size) {
            void* ptr = blk->base + blk->offset;
            blk->offset += size;
            a->total_allocated += size;
            return ptr;
        }
    }

    /* need new block */
    arena_block_t* blk = arena_new_block(a, size);
    if (!blk) return NULL;
    a->block_count++;
    if (!a->head) a->head = blk;
    if (a->tail) a->tail->next = blk;
    a->tail = blk;

    blk->offset = size;
    a->total_allocated += size;
    return blk->base;
}

void* agent_harness_arena_realloc(agent_harness_arena_t arena, void* ptr, size_t old_size, size_t new_size) {
    if (!arena || new_size == 0) return NULL;
    if (!ptr) return agent_harness_arena_alloc(arena, new_size);

    /* arena can't shrink; just copy if growing */
    void* new_ptr = agent_harness_arena_alloc(arena, new_size);
    if (new_ptr && old_size > 0) {
        memcpy(new_ptr, ptr, old_size < new_size ? old_size : new_size);
    }
    return new_ptr;
}

void agent_harness_arena_reset(agent_harness_arena_t arena) {
    if (!arena) return;
    arena_impl_t* a = (arena_impl_t*)arena;
    for (arena_block_t* blk = a->head; blk; blk = blk->next) {
        blk->offset = 0;
    }
    a->total_allocated = 0;
}

void agent_harness_arena_destroy(agent_harness_arena_t arena) {
    if (!arena) return;
    arena_impl_t* a = (arena_impl_t*)arena;
    arena_block_t* blk = a->head;
    while (blk) {
        arena_block_t* next = blk->next;
        free(blk);
        blk = next;
    }
    free(a);
}

size_t agent_harness_arena_total(agent_harness_arena_t arena) {
    if (!arena) return 0;
    arena_impl_t* a = (arena_impl_t*)arena;
    size_t total = 0;
    for (arena_block_t* blk = a->head; blk; blk = blk->next) {
        total += blk->size;
    }
    return total;
}

size_t agent_harness_arena_used(agent_harness_arena_t arena) {
    return arena ? ((arena_impl_t*)arena)->total_allocated : 0;
}

int agent_harness_arena_block_count(agent_harness_arena_t arena) {
    return arena ? ((arena_impl_t*)arena)->block_count : 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Slab allocator
 * ══════════════════════════════════════════════════════════════════════════ */

typedef struct slab_node {
    struct slab_node* next;
} slab_node_t;

typedef struct {
    slab_node_t*  freelist;
    void*         memory;      /* underlying slab block */
    size_t        item_size;
    size_t        slab_size;   /* item count per slab */
    size_t        total_items;
    size_t        free_items;
} slab_impl_t;

agent_harness_slab_t agent_harness_slab_create(size_t item_size, size_t slab_size) {
    if (item_size == 0 || slab_size == 0) return NULL;
    if (item_size < sizeof(slab_node_t)) item_size = sizeof(slab_node_t);

    slab_impl_t* s = (slab_impl_t*)calloc(1, sizeof(slab_impl_t));
    if (!s) return NULL;
    s->item_size = item_size;
    s->slab_size = slab_size;

    /* allocate one slab */
    size_t bytes = item_size * slab_size;
    s->memory = calloc(1, bytes);
    if (!s->memory) { free(s); return NULL; }

    /* build freelist */
    char* base = (char*)s->memory;
    for (size_t i = 0; i < slab_size; i++) {
        slab_node_t* node = (slab_node_t*)(base + i * item_size);
        node->next = s->freelist;
        s->freelist = node;
    }

    s->total_items = slab_size;
    s->free_items  = slab_size;
    return s;
}

void* agent_harness_slab_alloc(agent_harness_slab_t slab) {
    if (!slab) return NULL;
    slab_impl_t* s = (slab_impl_t*)slab;
    if (!s->freelist) return NULL;
    slab_node_t* node = s->freelist;
    s->freelist = node->next;
    s->free_items--;
    return node;
}

void agent_harness_slab_free(agent_harness_slab_t slab, void* ptr) {
    if (!slab || !ptr) return;
    slab_impl_t* s = (slab_impl_t*)slab;
    slab_node_t* node = (slab_node_t*)ptr;
    node->next = s->freelist;
    s->freelist = node;
    s->free_items++;
}

void agent_harness_slab_destroy(agent_harness_slab_t slab) {
    if (!slab) return;
    slab_impl_t* s = (slab_impl_t*)slab;
    free(s->memory);
    free(s);
}

size_t agent_harness_slab_total_allocated(agent_harness_slab_t slab) {
    return slab ? ((slab_impl_t*)slab)->total_items : 0;
}

size_t agent_harness_slab_free_count(agent_harness_slab_t slab) {
    return slab ? ((slab_impl_t*)slab)->free_items : 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Fixed-size ring buffer
 * ══════════════════════════════════════════════════════════════════════════ */

typedef struct {
    char*       buf;
    size_t      item_size;
    size_t      capacity;
    atomic_uint head;
    atomic_uint tail;
    atomic_uint count;
} rb_impl_t;

agent_harness_rb_t agent_harness_rb_create(size_t item_size, size_t capacity) {
    if (item_size == 0 || capacity == 0) return NULL;
    rb_impl_t* r = (rb_impl_t*)calloc(1, sizeof(rb_impl_t));
    if (!r) return NULL;
    r->buf       = (char*)calloc(capacity, item_size);
    r->item_size = item_size;
    r->capacity  = (unsigned)capacity;
    if (!r->buf) { free(r); return NULL; }
    return r;
}

agent_harness_err_t agent_harness_rb_push(agent_harness_rb_t rb, const void* item) {
    if (!rb || !item) return AgentHarness_ERR_NULL_PTR;
    rb_impl_t* r = (rb_impl_t*)rb;
    unsigned cnt = atomic_load(&r->count);
    if (cnt >= r->capacity) return AgentHarness_ERR_BUFFER_TOO_SMALL;
    unsigned tail = atomic_load(&r->tail);
    memcpy(r->buf + (tail % r->capacity) * r->item_size, item, r->item_size);
    atomic_store(&r->tail, tail + 1);
    atomic_fetch_add(&r->count, 1);
    return AgentHarness_OK;
}

agent_harness_err_t agent_harness_rb_pop(agent_harness_rb_t rb, void* out_item) {
    if (!rb || !out_item) return AgentHarness_ERR_NULL_PTR;
    rb_impl_t* r = (rb_impl_t*)rb;
    if (atomic_load(&r->count) == 0) return AgentHarness_ERR_BUFFER_TOO_SMALL;
    unsigned head = atomic_load(&r->head);
    memcpy(out_item, r->buf + (head % r->capacity) * r->item_size, r->item_size);
    atomic_store(&r->head, head + 1);
    atomic_fetch_sub(&r->count, 1);
    return AgentHarness_OK;
}

size_t agent_harness_rb_count(agent_harness_rb_t rb) {
    return rb ? (size_t)atomic_load(&((rb_impl_t*)rb)->count) : 0;
}

void agent_harness_rb_destroy(agent_harness_rb_t rb) {
    if (!rb) return;
    rb_impl_t* r = (rb_impl_t*)rb;
    free(r->buf);
    free(r);
}