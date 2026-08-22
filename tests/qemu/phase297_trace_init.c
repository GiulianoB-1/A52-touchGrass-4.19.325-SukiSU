#define _GNU_SOURCE
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/reboot.h>
#include <sched.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

static void say(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    fflush(stdout);
}

static void ensure_dir(const char *path)
{
    if (mkdir(path, 0755) && errno != EEXIST)
        say("PHASE297_WARN mkdir path=%s errno=%d\n", path, errno);
}

static int write_text(const char *path, const char *text, int append)
{
    int flags = O_WRONLY | O_CLOEXEC | (append ? O_APPEND : O_TRUNC);
    int fd = open(path, flags);
    ssize_t len = (ssize_t)strlen(text);
    ssize_t n;

    if (fd < 0)
        return -errno;
    n = write(fd, text, (size_t)len);
    if (n != len) {
        int saved = errno ? errno : EIO;
        close(fd);
        return -saved;
    }
    close(fd);
    return 0;
}

static char *read_all(const char *path, size_t limit, size_t *out_len)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    char *buf;
    size_t used = 0;

    if (fd < 0)
        return NULL;
    buf = calloc(1, limit + 1);
    if (!buf) {
        close(fd);
        return NULL;
    }
    while (used < limit) {
        ssize_t n = read(fd, buf + used, limit - used);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            free(buf);
            close(fd);
            return NULL;
        }
        if (n == 0)
            break;
        used += (size_t)n;
    }
    close(fd);
    buf[used] = '\0';
    if (out_len)
        *out_len = used;
    return buf;
}

static int has_function(const char *available, const char *name)
{
    const char *p = available;
    size_t nlen = strlen(name);

    if (!available)
        return 0;
    while (*p) {
        const char *e = strchr(p, '\n');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        if (len >= nlen && !strncmp(p, name, nlen) &&
            (len == nlen || p[nlen] == ' ' || p[nlen] == '\t'))
            return 1;
        if (!e)
            break;
        p = e + 1;
    }
    return 0;
}

static const char *find_trace_dir(void)
{
    static const char *paths[] = {
        "/sys/kernel/tracing",
        "/sys/kernel/debug/tracing",
    };
    size_t i;
    for (i = 0; i < sizeof(paths) / sizeof(paths[0]); ++i) {
        char probe[256];
        snprintf(probe, sizeof(probe), "%s/tracing_on", paths[i]);
        if (access(probe, W_OK) == 0)
            return paths[i];
    }
    return NULL;
}

static int setup_trace(const char *trace_dir)
{
    static const char *functions[] = {
        "drm_open",
        "drm_stub_open",
        "drm_open_helper",
        "drm_file_alloc",
        "drm_ioctl",
        "drm_ioctl_kernel",
        "drm_setclientcap",
        "drm_getcap",
        "drm_mode_getresources",
        "drm_mode_atomic_ioctl",
        "drm_atomic_helper_check",
        "drm_atomic_helper_commit",
        "drm_release",
        "virtio_gpu_probe",
        "virtio_gpu_driver_open",
        "virtio_gpu_driver_postclose",
        "queue_delayed_work_on",
        "__queue_delayed_work",
        "process_one_work",
        "worker_thread",
    };
    char path[256];
    char *available;
    size_t available_len = 0;
    unsigned int enabled = 0;
    size_t i;

    snprintf(path, sizeof(path), "%s/tracing_on", trace_dir);
    write_text(path, "0\n", 0);
    snprintf(path, sizeof(path), "%s/current_tracer", trace_dir);
    if (write_text(path, "function_graph\n", 0))
        return -1;

    snprintf(path, sizeof(path), "%s/set_ftrace_filter", trace_dir);
    {
        int fd = open(path, O_WRONLY | O_TRUNC | O_CLOEXEC);
        if (fd >= 0)
            close(fd);
    }

    snprintf(path, sizeof(path), "%s/available_filter_functions", trace_dir);
    available = read_all(path, 4U * 1024U * 1024U, &available_len);
    if (!available || !available_len) {
        free(available);
        return -2;
    }

    snprintf(path, sizeof(path), "%s/set_ftrace_filter", trace_dir);
    for (i = 0; i < sizeof(functions) / sizeof(functions[0]); ++i) {
        char line[128];
        if (!has_function(available, functions[i]))
            continue;
        snprintf(line, sizeof(line), "%s\n", functions[i]);
        if (!write_text(path, line, 1)) {
            say("PHASE297_TRACE_FILTER fn=%s\n", functions[i]);
            ++enabled;
        }
    }
    free(available);

    snprintf(path, sizeof(path), "%s/trace", trace_dir);
    write_text(path, "", 0);
    snprintf(path, sizeof(path), "%s/tracing_on", trace_dir);
    if (write_text(path, "1\n", 0))
        return -3;
    say("PHASE297_TRACE_READY filters=%u\n", enabled);
    return enabled ? 0 : -4;
}

static int wait_open_card0(int *saved_errno)
{
    int attempt;
    for (attempt = 0; attempt < 100; ++attempt) {
        int fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
        if (fd >= 0)
            return fd;
        if (saved_errno)
            *saved_errno = errno;
        usleep(100000);
    }
    return -1;
}

static void run_drm_probe(void)
{
    int open_errno = 0;
    int fd = wait_open_card0(&open_errno);
    int version_rc = -1, version_errno = 0;
    int cap_rc = -1, cap_errno = 0;
    int universal_rc = -1, universal_errno = 0;
    int atomic_cap_rc = -1, atomic_cap_errno = 0;
    int resources_rc = -1, resources_errno = 0;
    int atomic_rc = -1, atomic_errno = 0;
    unsigned int crtcs = 0, conns = 0, encoders = 0, fbs = 0;
    char name[128] = {0}, date[128] = {0}, desc[256] = {0};

    if (fd < 0) {
        say("PHASE297_DRM card_open=-1 errno=%d\n", open_errno);
        return;
    }
    say("PHASE297_DRM card_open=0 fd=%d\n", fd);

    {
        struct drm_version v;
        memset(&v, 0, sizeof(v));
        v.name_len = sizeof(name) - 1;
        v.name = name;
        v.date_len = sizeof(date) - 1;
        v.date = date;
        v.desc_len = sizeof(desc) - 1;
        v.desc = desc;
        version_rc = ioctl(fd, DRM_IOCTL_VERSION, &v);
        version_errno = version_rc ? errno : 0;
        say("PHASE297_DRM version_rc=%d errno=%d driver=%s version=%d.%d.%d\n",
            version_rc, version_errno, name,
            v.version_major, v.version_minor, v.version_patchlevel);
    }
    {
        struct drm_get_cap cap;
        memset(&cap, 0, sizeof(cap));
        cap.capability = DRM_CAP_DUMB_BUFFER;
        cap_rc = ioctl(fd, DRM_IOCTL_GET_CAP, &cap);
        cap_errno = cap_rc ? errno : 0;
        say("PHASE297_DRM getcap_rc=%d errno=%d dumb=%llu\n",
            cap_rc, cap_errno, (unsigned long long)cap.value);
    }
    {
        struct drm_set_client_cap cap;
        memset(&cap, 0, sizeof(cap));
        cap.capability = DRM_CLIENT_CAP_UNIVERSAL_PLANES;
        cap.value = 1;
        universal_rc = ioctl(fd, DRM_IOCTL_SET_CLIENT_CAP, &cap);
        universal_errno = universal_rc ? errno : 0;
        cap.capability = DRM_CLIENT_CAP_ATOMIC;
        cap.value = 1;
        atomic_cap_rc = ioctl(fd, DRM_IOCTL_SET_CLIENT_CAP, &cap);
        atomic_cap_errno = atomic_cap_rc ? errno : 0;
        say("PHASE297_DRM client_caps universal=%d/%d atomic=%d/%d\n",
            universal_rc, universal_errno, atomic_cap_rc, atomic_cap_errno);
    }
    {
        struct drm_mode_card_res res;
        memset(&res, 0, sizeof(res));
        resources_rc = ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res);
        resources_errno = resources_rc ? errno : 0;
        crtcs = res.count_crtcs;
        conns = res.count_connectors;
        encoders = res.count_encoders;
        fbs = res.count_fbs;
        say("PHASE297_DRM resources_rc=%d errno=%d crtcs=%u connectors=%u encoders=%u fbs=%u\n",
            resources_rc, resources_errno, crtcs, conns, encoders, fbs);
    }
    {
        struct drm_mode_atomic req;
        memset(&req, 0, sizeof(req));
        req.flags = DRM_MODE_ATOMIC_ALLOW_MODESET;
        atomic_rc = ioctl(fd, DRM_IOCTL_MODE_ATOMIC, &req);
        atomic_errno = atomic_rc ? errno : 0;
        say("PHASE297_DRM empty_atomic_rc=%d errno=%d\n", atomic_rc, atomic_errno);
    }

    close(fd);
    say("PHASE297_DRM_COMPLETE version=%d cap=%d universal=%d atomic_cap=%d resources=%d empty_atomic=%d\n",
        version_rc, cap_rc, universal_rc, atomic_cap_rc, resources_rc, atomic_rc);
}

static void dump_trace(const char *trace_dir)
{
    char path[256];
    char *trace;
    size_t len = 0;

    snprintf(path, sizeof(path), "%s/tracing_on", trace_dir);
    write_text(path, "0\n", 0);
    snprintf(path, sizeof(path), "%s/trace", trace_dir);
    trace = read_all(path, 1024U * 1024U, &len);
    say("PHASE297_FTRACE_BEGIN bytes=%zu\n", len);
    if (trace && len)
        fwrite(trace, 1, len, stdout);
    say("\nPHASE297_FTRACE_END\n");
    fflush(stdout);
    free(trace);
}

static void dump_small_file(const char *tag, const char *path)
{
    size_t len = 0;
    char *buf = read_all(path, 64U * 1024U, &len);
    if (!buf)
        return;
    say("PHASE297_FILE_BEGIN tag=%s path=%s bytes=%zu\n", tag, path, len);
    fwrite(buf, 1, len, stdout);
    if (!len || buf[len - 1] != '\n')
        putchar('\n');
    say("PHASE297_FILE_END tag=%s\n", tag);
    fflush(stdout);
    free(buf);
}

int main(void)
{
    struct utsname uts;
    const char *trace_dir;
    int trace_rc;

    setvbuf(stdout, NULL, _IONBF, 0);
    ensure_dir("/dev");
    ensure_dir("/proc");
    ensure_dir("/sys");
    ensure_dir("/sys/kernel");
    ensure_dir("/sys/kernel/debug");
    mount("devtmpfs", "/dev", "devtmpfs", MS_NOSUID, "mode=0755");
    mount("proc", "/proc", "proc", 0, NULL);
    mount("sysfs", "/sys", "sysfs", 0, NULL);
    ensure_dir("/sys/kernel/debug");
    mount("debugfs", "/sys/kernel/debug", "debugfs", 0, NULL);
    ensure_dir("/sys/kernel/tracing");
    mount("tracefs", "/sys/kernel/tracing", "tracefs", 0, NULL);

    if (!uname(&uts))
        say("PHASE297_BOOT_OK release=%s machine=%s\n", uts.release, uts.machine);
    else
        say("PHASE297_BOOT_OK uname_errno=%d\n", errno);
    dump_small_file("cmdline", "/proc/cmdline");
    dump_small_file("version", "/proc/version");

    trace_dir = find_trace_dir();
    if (!trace_dir) {
        say("PHASE297_TRACE_UNAVAILABLE\n");
        trace_rc = -1;
    } else {
        say("PHASE297_TRACE_DIR path=%s\n", trace_dir);
        trace_rc = setup_trace(trace_dir);
    }

    run_drm_probe();
    sleep(1);
    if (trace_dir)
        dump_trace(trace_dir);
    dump_small_file("interrupts", "/proc/interrupts");
    dump_small_file("modules", "/proc/modules");
    say("PHASE297_TRACE_COMPLETE trace_rc=%d\n", trace_rc);
    sync();
    reboot(RB_AUTOBOOT);
    for (;;)
        pause();
    return 0;
}
