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

#endif
