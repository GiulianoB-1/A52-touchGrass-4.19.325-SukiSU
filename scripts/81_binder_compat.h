/* Linux 4.19 compatibility glue for the Android 17 Binder core. */
#ifndef _A52_BINDER_COMPAT_H
#define _A52_BINDER_COMPAT_H

#include <linux/fdtable.h>
#include <linux/mutex.h>
#include <linux/rcupdate.h>
#include <linux/sched.h>
#include <linux/security.h>
#include <linux/spinlock.h>

/* Android GKI vendor/OEM reservation macros do not exist in this 4.19 tree.
 * They reserve opaque padding only and carry no Binder semantics. */
#ifndef ANDROID_VENDOR_DATA
#define ANDROID_VENDOR_DATA(_n)
#endif
#ifndef ANDROID_VENDOR_DATA_ARRAY
#define ANDROID_VENDOR_DATA_ARRAY(_n, _s)
#endif
#ifndef ANDROID_OEM_DATA
#define ANDROID_OEM_DATA(_n)
#endif
#ifndef ANDROID_OEM_DATA_ARRAY
#define ANDROID_OEM_DATA_ARRAY(_n, _s)
#endif

#ifndef VISIBLE_IF_KUNIT
#define VISIBLE_IF_KUNIT
#endif
#ifndef EXPORT_SYMBOL_IF_KUNIT
#define EXPORT_SYMBOL_IF_KUNIT(sym)
#endif

#define __BINDER_JOIN2(a, b) a##b

#ifndef TWA_RESUME
#define TWA_RESUME true
#endif

/*
 * Backport of file_close_fd() semantics using the 4.19 fdtable layout.
 * It detaches the descriptor without dropping the file reference; Binder
 * then pins and defers the final fput until it has returned from ioctl.
 */
static inline struct file *__binder_file_close_fd(unsigned int fd)
{
	struct files_struct *files = current->files;
	struct fdtable *fdt;
	struct file *file = NULL;

	spin_lock(&files->file_lock);
	fdt = files_fdtable(files);
	if (fd < fdt->max_fds) {
		fd = array_index_nospec(fd, fdt->max_fds);
		file = rcu_dereference_raw(fdt->fd[fd]);
		if (file) {
			rcu_assign_pointer(fdt->fd[fd], NULL);
			__clear_bit(fd, fdt->open_fds);
			__clear_bit(fd, fdt->close_on_exec);
			__clear_bit(fd / BITS_PER_LONG, fdt->full_fds_bits);
			if (fd < files->next_fd)
				files->next_fd = fd;
		}
	}
	spin_unlock(&files->file_lock);
	return file;
}

#define file_close_fd(_fd) __binder_file_close_fd((_fd))


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
