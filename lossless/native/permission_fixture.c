/* Host fixture only. Native registry calls are substituted; the real port,
 * coordinator and UI/control source execute. Separate ARM suites execute the
 * actual call layer and A2P/property chain. This never supplies UI readiness. */
#include "native_port.h"
#include <string.h>

#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
struct fp_nv_descriptor {
    const char *name;
    uint32_t value, present;
    fp_nv_callback callback;
    void *context;
};
static struct {
    struct fp_nv_descriptor d[3];
    uint32_t fail_register, fail_subscribe, fail_set, echo;
    uint32_t facts_fail, locked, drained, facts_reads, set_calls;
    uint32_t fail_enter, reenter, move_cursor_on_confirm;
    uint32_t log[64], logs, reads[3];
    struct fpl_context facts;
} M;
struct fixture {
    struct fpl_port port;
    struct fpl_state state;
    struct fpl_binding binding;
    struct fpl_ticket ticket;
};
static uint32_t index_of(const char *name) {
    if (!strcmp(name, FPL_PRIVATE_VARIABLE)) return 0;
    if (!strcmp(name, FP_NV_CURSOR_NAME)) return 1;
    if (!strcmp(name, FP_NV_CONFIRM_NAME)) return 2;
    return 3;
}
static uint32_t owns(struct fp_nv_state *s) {
    uint32_t i = index_of(s->name);
    return i < 3 && M.d[i].present && s->owned == &M.d[i] && M.d[i].name == s->name;
}
uint32_t fp_nv_init(struct fp_nv_state *s, void *app, const char *name) {
    if (!s || s->magic || !app || !name || index_of(name) == 3) return FP_NV_INVALID;
    s->magic = 1; s->app = app; s->name = name;
    return FP_NV_OK;
}
uint32_t fp_nv_inspect(struct fp_nv_state *s, struct fp_nv_snapshot *out) {
    uint32_t i = index_of(s->name);
    if (i == 3) return FP_NV_INVALID;
    memset(out, 0, sizeof(*out));
    if (M.d[i].present) {
        out->descriptor = (uint32_t)(uintptr_t)&M.d[i];
        out->value = M.d[i].value;
        out->subscription = M.d[i].callback ? 1 : 0;
    }
    return FP_NV_OK;
}
uint32_t fp_nv_register_off(struct fp_nv_state *s) {
    uint32_t i = index_of(s->name);
    if (i == 3) return FP_NV_INVALID;
    if (M.d[i].present) return FP_NV_COLLISION;
    if (M.fail_register == i + 1) return FP_NV_NATIVE;
    M.d[i].name = s->name; M.d[i].present = 1;
    s->owned = &M.d[i];
    return FP_NV_OK;
}
uint32_t fp_nv_read(struct fp_nv_state *s, uint32_t *out) {
    if (!owns(s)) return FP_NV_STALE;
    ++M.reads[index_of(s->name)];
    *out = s->owned->value;
    return FP_NV_OK;
}
uint32_t fp_nv_subscribe(struct fp_nv_state *s, fp_nv_callback callback, void *context) {
    if (!owns(s) || s->callback) return FP_NV_COLLISION;
    s->callback = callback; s->callback_context = context;
    /* A failed native subscribe may still have published its pair. */
    s->owned->callback = callback; s->owned->context = context;
    s->native_pair = s->owned;
    return M.fail_subscribe == index_of(s->name) + 1 ? FP_NV_NATIVE : FP_NV_OK;
}
uint32_t fp_nv_set_canonical(struct fp_nv_state *s, uint32_t value) {
    uint32_t i = index_of(s->name);
    if (!owns(s) || value > 1) return FP_NV_INVALID;
    ++M.set_calls;
    if (M.logs < 64) M.log[M.logs++] = i * 10 + value;
    if (M.fail_set == i + 1) return FP_NV_NATIVE;
    s->owned->value = value;
    if (M.echo && s->owned->callback)
        s->owned->callback(s->owned, s->owned->context);
    if (i == 2 && value == 1 && M.move_cursor_on_confirm) {
        M.move_cursor_on_confirm = 0;
        M.d[1].value = 1;
        M.d[1].callback(&M.d[1], M.d[1].context);
    }
    return FP_NV_OK;
}
uint32_t fp_nv_unsubscribe_locked(struct fp_nv_state *s) {
    if (!M.locked || !owns(s) || !s->callback || s->owned->callback != s->callback)
        return FP_NV_COLLISION;
    s->owned->callback = 0; s->owned->context = 0;
    s->callback = 0; s->callback_context = 0; s->native_pair = 0;
    return FP_NV_OK;
}
static uint32_t facts(void *context, struct fpl_context *out) {
    (void)context; ++M.facts_reads;
    *out = M.facts;
    if (M.reenter) {
        M.reenter = 0;
        M.d[1].callback(&M.d[1], M.d[1].context);
    }
    return M.facts_fail ? FPL_NOT_READY : FPL_OK;
}
static uint32_t enter(void *context) {
    (void)context;
    if (M.locked || M.fail_enter) return FPL_BUSY;
    M.locked = 1; return FPL_OK;
}
static void leave(void *context) { (void)context; M.locked = 0; }
static uint32_t drain(void *context) { (void)context; return M.drained ? FPL_OK : FPL_BUSY; }
static uint32_t initialize(struct fixture *f) {
    struct fpl_port_facts provider = {facts, 0};
    struct fpl_port_exclusion exclusion = {enter, leave, drain, 0};
    memset(f, 0, sizeof(*f)); memset(&M, 0, sizeof(M));
    M.facts = (struct fpl_context){502, 1, 1, 12, 1936, 1090, 24000, 1001, 1, FPL_READY_ALL};
    fpl_boot(&f->state);
    CHECK(fpl_port_init(&f->port, (void *)(uintptr_t)0x1000, FPL_PRIVATE_VARIABLE,
                        &provider, &exclusion) == FPL_OK);
    CHECK(fpl_binding_init(&f->binding, 71, &f->state, fpl_port_ops(), &f->port) == FPL_OK);
    CHECK(fpl_port_bind(&f->port, &f->binding) == FPL_OK);
    return 0;
}
static uint32_t prepare(struct fixture *f) {
    return fpl_port_prepare_permissions(&f->port, FP_NV_CURSOR_NAME, FP_NV_CONFIRM_NAME);
}
static uint32_t attach(struct fixture *f) {
    return fpl_binding_attach(&f->binding, FPL_PAGE_MAINB2, 0x5500, 1, &f->ticket);
}
static int32_t event(uint32_t which, uint32_t value) {
    M.d[which].value = value;
    return M.d[which].callback ? M.d[which].callback(&M.d[which], M.d[which].context) : -1;
}

unsigned permission_case(unsigned n) {
    struct fixture f;
    struct fpl_view view = {1, 0, 1, 1, FPL_OK};
    unsigned result = initialize(&f);
    uint32_t before;
    if (result) return result;
    if (n == 0) {
        CHECK(fpl_port_prepare_permissions(&f.port, FPL_PRIVATE_VARIABLE, FP_NV_CONFIRM_NAME) == FPL_INVALID);
        CHECK(fpl_port_prepare_permissions(&f.port, FP_NV_CURSOR_NAME, "MV_AudioRecord") == FPL_INVALID);
        CHECK(!M.d[0].present && !M.d[1].present && !M.d[2].present);
        return 0;
    }
    if (n == 1) {
        M.fail_register = 3;
        CHECK(prepare(&f) == FPL_FAULT);
        CHECK(M.d[1].present && !M.d[2].present && f.port.fault);
        CHECK(prepare(&f) == FPL_FAULT);
        CHECK(!f.port.permissions_prepared);
        return 0;
    }
    CHECK(prepare(&f) == FPL_OK);
    CHECK(M.d[1].value == 0 && M.d[2].value == 0 && !M.d[0].present);
    if (n == 2) {
        CHECK(prepare(&f) == FPL_INVALID);
        return 0;
    }
    if (n == 3) {
        M.fail_subscribe = 2;
        CHECK(attach(&f) == FPL_FAULT);
        CHECK(f.port.ticket && f.port.cursor.callback && f.port.subscribe_uncertain);
        CHECK(fpl_binding_begin(&f.binding) != FPL_OK);
        return 0;
    }
    CHECK(attach(&f) == FPL_OK);
    CHECK(M.d[0].callback && M.d[1].callback && !M.d[2].callback);
    CHECK(M.d[0].value == 0 && M.d[1].value == 0 && M.d[2].value == 1);
    if (n == 4) {
        uint32_t saved_reads = M.reads[0];
        before = M.facts_reads;
        CHECK(event(1, 1) == 0);
        CHECK(M.facts_reads > before && M.d[2].value == 0);
        CHECK(M.reads[0] == saved_reads); /* no saved-value selection path */
        CHECK(f.state.requested == 0 && M.d[0].value == 0 && M.d[1].value == 1);
        CHECK(event(1, 0) == 0 && M.d[2].value == 1);
    } else if (n == 5) {
        CHECK(event(0, 1) != 0); /* stale/held confirmation still reaches policy */
        CHECK(f.state.requested == 0 && M.d[0].value == 0);
    } else if (n == 6) {
        CHECK(fpl_binding_begin(&f.binding) == FPL_OK); /* OFF records stock */
        CHECK(M.d[2].value == 0); /* begin itself revokes, before next refresh */
        CHECK(fpl_binding_refresh(&f.binding) == FPL_OK);
        CHECK(M.d[2].value == 0);
        CHECK(event(1, 1) == 0 && M.d[2].value == 0);
        CHECK(event(1, 0) == 0 && M.d[2].value == 0);
    } else if (n == 7) {
        M.facts.cine = 0;
        CHECK(event(1, 0) == 0 && M.d[2].value == 0);
    } else if (n == 8) {
        CHECK(event(1, 2) != 0 && f.port.fault);
        CHECK(M.d[2].value == 0 && fpl_binding_begin(&f.binding) != FPL_OK);
    } else if (n == 9) {
        M.logs = 0;
        CHECK(event(1, 0) == 0);
        CHECK(M.logs == 4 && M.log[0] == 20 && M.log[1] == 20 &&
              M.log[2] == 0 && M.log[3] == 21);
    } else if (n == 10) {
        M.echo = 1;
        CHECK(event(1, 0) == 0 && !f.port.fault && !f.port.callback_depth);
        CHECK(f.port.echoes_suppressed > 0 && M.d[2].value == 1);
    } else if (n == 11) {
        M.fail_set = 1; /* permission closes before canonical write fails */
        CHECK(event(1, 0) != 0 && M.d[2].value == 0);
        CHECK(fpl_binding_begin(&f.binding) != FPL_OK);
    } else if (n == 12) {
        M.fail_set = 3; /* uncertain permission write must block REC */
        CHECK(event(1, 1) != 0 && f.port.fault);
        CHECK(fpl_binding_begin(&f.binding) != FPL_OK);
    } else if (n == 13) {
        M.facts_fail = 1;
        CHECK(event(1, 1) != 0 && fpl_binding_begin(&f.binding) != FPL_OK);
        CHECK(M.d[2].value == 0);
    } else if (n == 14) {
        fp_nv_callback callback = M.d[1].callback;
        CHECK(callback(&M.d[0], &f.port) != 0);
        CHECK(f.state.requested == 0);
    } else if (n == 15) {
        fp_nv_callback late = M.d[1].callback;
        CHECK(fpl_binding_close(&f.binding) == FPL_OK);
        CHECK(!M.d[0].callback && !M.d[1].callback && M.d[2].value == 0);
        CHECK(fpl_binding_reap(&f.binding) == FPL_BUSY);
        before = M.facts_reads;
        CHECK(late(&M.d[1], &f.port) != 0 && M.facts_reads == before);
        CHECK(fpl_port_release(&f.port) == FPL_BUSY);
        M.drained = 1;
        CHECK(fpl_binding_reap(&f.binding) == FPL_OK);
        CHECK(fpl_port_release(&f.port) == FPL_OK);
        memset(&f.ticket, 0, sizeof(f.ticket)); /* storage reuse only after drain */
        CHECK(attach(&f) == FPL_OK && M.d[0].callback && M.d[1].callback);
    } else if (n == 16) {
        f.port.callback_depth = 1;
        CHECK(event(1, 0) != 0 && f.port.fault);
        CHECK(M.d[2].value == 0);
    } else if (n == 17) {
        /* Test publication mapping independently; this is NOT UI readiness. */
        M.d[1].value = 1;
        CHECK(fpl_port_ops()->publish(&f.port, f.port.descriptor, &view) == FPL_OK);
        CHECK(M.d[2].value == 1 && f.state.requested == 0);
        view.on_enabled = 0;
        CHECK(fpl_port_ops()->publish(&f.port, f.port.descriptor, &view) == FPL_OK);
        CHECK(M.d[2].value == 0 && M.d[1].value == 1);
    } else if (n == 18) {
        M.facts_fail = 1;
        CHECK(fpl_binding_begin(&f.binding) != FPL_OK && M.d[2].value == 0);
    } else if (n == 19) {
        M.fail_enter = 1;
        CHECK(fpl_binding_close(&f.binding) == FPL_BUSY && M.d[2].value == 0);
        CHECK(M.d[0].callback && M.d[1].callback && f.port.ticket);
        CHECK(event(1, 1) != 0 && M.d[2].value == 0);
    } else if (n == 20) {
        before = M.facts_reads;
        ++f.ticket.generation;
        CHECK(event(1, 1) != 0 && M.facts_reads == before);
    } else if (n == 21) {
        M.reenter = 1;
        CHECK(event(1, 1) != 0 && f.port.fault && M.d[2].value == 0);
    } else if (n == 22) {
        M.move_cursor_on_confirm = 1;
        CHECK(event(1, 0) != 0 && f.port.fault && M.d[2].value == 0);
        CHECK(fpl_binding_begin(&f.binding) != FPL_OK);
    } else return __LINE__;
    return 0;
}
