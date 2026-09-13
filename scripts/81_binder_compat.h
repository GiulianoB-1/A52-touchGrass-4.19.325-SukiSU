/* Linux 4.19 compatibility glue for the Android 17 Binder core. */
#ifndef _A52_BINDER_COMPAT_H
#define _A52_BINDER_COMPAT_H

#ifndef VISIBLE_IF_KUNIT
#define VISIBLE_IF_KUNIT
#endif
#ifndef EXPORT_SYMBOL_IF_KUNIT
#define EXPORT_SYMBOL_IF_KUNIT(sym)
#endif

#define __BINDER_JOIN2(a, b) a##b

/* Linux 6.x wraps LSM security contexts in struct lsm_context. Linux 4.19
 * uses the older char ** + u32 length ABI. */
struct lsm_context {
	char *context;
	size_t len;
};

static inline int __binder_security_secid_to_secctx(u32 secid,
						     struct lsm_context *ctx)
{
	char *context = NULL;
	u32 len = 0;
	int ret = security_secid_to_secctx(secid, &context, &len);

	if (!ret) {
		ctx->context = context;
		ctx->len = len;
	}
	return ret;
}

static inline void __binder_security_release_secctx(struct lsm_context *ctx)
{
	if (ctx->context)
		security_release_secctx(ctx->context, (u32)ctx->len);
	ctx->context = NULL;
	ctx->len = 0;
}

#define security_secid_to_secctx(_secid, _ctx) \
	__binder_security_secid_to_secctx((_secid), (_ctx))
#define security_release_secctx(_ctx) \
	__binder_security_release_secctx((_ctx))

#define __BINDER_JOIN(a, b) __BINDER_JOIN2(a, b)

static inline void __binder_mutex_cleanup(struct mutex **lock)
{
	if (*lock)
		mutex_unlock(*lock);
}

static inline void __binder_spin_cleanup(spinlock_t **lock)
{
	if (*lock)
		spin_unlock(*lock);
}

#define __binder_guard_mutex(_lock) \
	struct mutex *__attribute__((cleanup(__binder_mutex_cleanup))) \
	__BINDER_JOIN(__binder_mutex_guard_, __COUNTER__) = \
		({ mutex_lock((_lock)); (_lock); })

#define __binder_guard_spinlock(_lock) \
	spinlock_t *__attribute__((cleanup(__binder_spin_cleanup))) \
	__BINDER_JOIN(__binder_spin_guard_, __COUNTER__) = \
		({ spin_lock((_lock)); (_lock); })

#ifndef guard
#define guard(_name) __BINDER_JOIN(__binder_guard_, _name)
#endif

/* Android 17 Binder netlink reporting is diagnostics-only. The generated
 * 6.18 generic-netlink family is intentionally deferred on the 4.19 probe. */
#ifndef genl_register_family
#define genl_register_family(...) (0)
#endif
#ifndef genl_unregister_family
#define genl_unregister_family(...) do { } while (0)
#endif

#endif
