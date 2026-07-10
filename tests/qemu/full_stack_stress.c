#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/bpf.h>
#include <linux/reboot.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef __NR_bpf
#define __NR_bpf 280
#endif

#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define KSU_INSTALL_MAGIC1 0xDEADBEEFU
#define KSU_INSTALL_MAGIC2 0xCAFEBABEU
#define KSU_SUSFS_MAGIC    0xFAFAFAFAU
#define KERNEL_SU_OPTION   ((int)0xDEADBEEFU)

#define KSU_IOCTL_SET_SEPOLICY _IOC(_IOC_READ | _IOC_WRITE, 'K', 4, 0)

struct ksu_get_info_cmd {
    uint32_t version;
    uint32_t flags;
    uint32_t features;
    uint32_t uapi_version;
};

struct ksu_get_feature_cmd {
    uint32_t feature_id;
    uint64_t value;
    uint8_t supported;
};

struct ksu_set_feature_cmd {
    uint32_t feature_id;
    uint64_t value;
};

struct ksu_set_sepolicy_cmd {
    uint64_t data_len;
    uint64_t data;
};

#define KSU_IOCTL_GET_INFO    _IOR('K', 2, struct ksu_get_info_cmd)
#define KSU_IOCTL_GET_FEATURE _IOWR('K', 13, struct ksu_get_feature_cmd)
#define KSU_IOCTL_SET_FEATURE _IOW('K', 14, struct ksu_set_feature_cmd)

struct linux_dirent64_local {
    uint64_t d_ino;
    int64_t d_off;
    unsigned short d_reclen;
    unsigned char d_type;
    char d_name[];
};

static volatile sig_atomic_t stop_requested;
static int duration_seconds = 600;
static const char *self_path = "/init";

static long monotonic_seconds(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return 0;
    return ts.tv_sec;
}

static void log_line(const char *tag, const char *message)
{
    dprintf(STDOUT_FILENO, "A52_QEMU_STRESS %ld %s %s\n",
            monotonic_seconds(), tag, message);
    fsync(STDOUT_FILENO);
}

static void request_stop(int sig)
{
    (void)sig;
    stop_requested = 1;
}

static int mkdir_if_needed(const char *path, mode_t mode)
{
    if (mkdir(path, mode) == 0 || errno == EEXIST)
        return 0;
    return -1;
}

static void read_file_repeatedly(const char *path)
{
    char buffer[4096];
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return;
    while (read(fd, buffer, sizeof(buffer)) > 0) {
    }
    close(fd);
}

static int install_ksu_fd(void)
{
    int fd = -1;
    errno = 0;
    (void)syscall(SYS_reboot, KSU_INSTALL_MAGIC1, KSU_INSTALL_MAGIC2, 0, &fd);
    return fd;
}

static int load_bpf_program(int intentionally_invalid)
{
    static const char license[] = "GPL";
    char log_buffer[8192];
    struct bpf_insn valid_program[] = {
        {
            .code = BPF_ALU64 | BPF_MOV | BPF_K,
            .dst_reg = BPF_REG_0,
            .src_reg = 0,
            .off = 0,
            .imm = 0,
        },
        {
            .code = BPF_JMP | BPF_EXIT,
            .dst_reg = 0,
            .src_reg = 0,
            .off = 0,
            .imm = 0,
        },
    };
    struct bpf_insn invalid_program[] = {
        {
            .code = BPF_LDX | BPF_MEM | BPF_DW,
            .dst_reg = BPF_REG_0,
            .src_reg = BPF_REG_1,
            .off = -4096,
            .imm = 0,
        },
        {
            .code = BPF_JMP | BPF_EXIT,
            .dst_reg = 0,
            .src_reg = 0,
            .off = 0,
            .imm = 0,
        },
    };
    union bpf_attr attr;
    struct bpf_insn *program = intentionally_invalid ? invalid_program : valid_program;

    memset(&attr, 0, sizeof(attr));
    memset(log_buffer, 0, sizeof(log_buffer));
    attr.prog_type = BPF_PROG_TYPE_SOCKET_FILTER;
    attr.insn_cnt = 2;
    attr.insns = (uint64_t)(uintptr_t)program;
    attr.license = (uint64_t)(uintptr_t)license;
    attr.log_buf = (uint64_t)(uintptr_t)log_buffer;
    attr.log_size = sizeof(log_buffer);
    attr.log_level = intentionally_invalid ? 1 : 0;

    return (int)syscall(__NR_bpf, BPF_PROG_LOAD, &attr, sizeof(attr));
}

static int worker_bpf(int id, long deadline)
{
    unsigned long iterations = 0;
    (void)id;
    while (!stop_requested && monotonic_seconds() < deadline) {
        int fd = load_bpf_program(0);
        if (fd >= 0)
            close(fd);
        fd = load_bpf_program(1);
        if (fd >= 0)
            close(fd);
        iterations++;
        if ((iterations & 0x3ffUL) == 0)
            sched_yield();
    }
    dprintf(STDOUT_FILENO, "A52_QEMU_STRESS BPF iterations=%lu\n", iterations);
    return 0;
}

static int worker_ksu(int id, long deadline)
{
    unsigned long iterations = 0;
    unsigned char malformed_policy[64];
    (void)id;
    memset(malformed_policy, 0xA5, sizeof(malformed_policy));

    while (!stop_requested && monotonic_seconds() < deadline) {
        int fd = install_ksu_fd();
        if (fd >= 0) {
            struct ksu_get_info_cmd info;
            unsigned int feature;
            memset(&info, 0, sizeof(info));
            (void)ioctl(fd, KSU_IOCTL_GET_INFO, &info);

            for (feature = 0; feature < 32; feature++) {
                struct ksu_get_feature_cmd get_feature;
                struct ksu_set_feature_cmd set_feature;
                memset(&get_feature, 0, sizeof(get_feature));
                get_feature.feature_id = feature;
                (void)ioctl(fd, KSU_IOCTL_GET_FEATURE, &get_feature);

                memset(&set_feature, 0, sizeof(set_feature));
                set_feature.feature_id = feature;
                set_feature.value = iterations & 1UL;
                (void)ioctl(fd, KSU_IOCTL_SET_FEATURE, &set_feature);
            }

            {
                struct ksu_set_sepolicy_cmd policy;
                policy.data_len = iterations & 1UL ? sizeof(malformed_policy) : 0;
                policy.data = (uint64_t)(uintptr_t)malformed_policy;
                (void)ioctl(fd, KSU_IOCTL_SET_SEPOLICY, &policy);
            }
            close(fd);
        }
        iterations++;
        if ((iterations & 0xffUL) == 0)
            sched_yield();
    }
    dprintf(STDOUT_FILENO, "A52_QEMU_STRESS KSU iterations=%lu\n", iterations);
    return 0;
}

static int worker_susfs_abi(int id, long deadline)
{
    unsigned long iterations = 0;
    unsigned char buffer[8192 + 256];
    int err = 0;
    (void)id;

    while (!stop_requested && monotonic_seconds() < deadline) {
        unsigned long command;
        struct utsname uts;
        memset(buffer, 0, sizeof(buffer));

        for (command = 0; command < 128; command++) {
            (void)prctl(KERNEL_SU_OPTION, command,
                        (unsigned long)(uintptr_t)buffer,
                        sizeof(buffer),
                        (unsigned long)(uintptr_t)&err);
            (void)syscall(SYS_reboot, KSU_INSTALL_MAGIC1, KSU_SUSFS_MAGIC,
                          command, buffer);
        }

        (void)prctl(KERNEL_SU_OPTION, 0x55561UL,
                    (unsigned long)(uintptr_t)buffer,
                    sizeof(buffer),
                    (unsigned long)(uintptr_t)&err);
        (void)syscall(SYS_reboot, KSU_INSTALL_MAGIC1, KSU_SUSFS_MAGIC,
                      0x55561UL, buffer);

        (void)uname(&uts);
        read_file_repeatedly("/proc/self/mountinfo");
        read_file_repeatedly("/proc/mounts");
        read_file_repeatedly("/proc/cmdline");
        iterations++;
    }
    dprintf(STDOUT_FILENO, "A52_QEMU_STRESS SUSFS_ABI iterations=%lu\n", iterations);
    return 0;
}

static int exercise_directory(const char *path)
{
    char file_path[256];
    char buffer[4096];
    struct stat st;
    struct statfs sfs;
    int fd;

    snprintf(file_path, sizeof(file_path), "%s/file", path);
    fd = open(file_path, O_CREAT | O_RDWR | O_TRUNC | O_CLOEXEC, 0600);
    if (fd >= 0) {
        (void)write(fd, "stress", 6);
        (void)fsync(fd);
        close(fd);
    }
    (void)stat(file_path, &st);
    (void)lstat(file_path, &st);
    (void)statfs(path, &sfs);

    fd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd >= 0) {
        (void)syscall(SYS_getdents64, fd, buffer, sizeof(buffer));
        close(fd);
    }
    return 0;
}

static int worker_mount(int id, long deadline)
{
    char base[128];
    char root[160];
    char bind[160];
    unsigned long iterations = 0;

    snprintf(base, sizeof(base), "/mnt/mount-worker-%d", id);
    snprintf(root, sizeof(root), "%s/root", base);
    snprintf(bind, sizeof(bind), "%s/bind", base);
    (void)mkdir_if_needed(base, 0700);

    if (unshare(CLONE_NEWNS) == 0)
        (void)mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL);

    while (!stop_requested && monotonic_seconds() < deadline) {
        (void)umount2(bind, MNT_DETACH);
        (void)umount2(base, MNT_DETACH);
        (void)mkdir_if_needed(base, 0700);

        if (mount("tmpfs", base, "tmpfs", MS_NOSUID | MS_NODEV,
                  "size=8m,mode=0700") == 0) {
            (void)mkdir_if_needed(root, 0700);
            (void)mkdir_if_needed(bind, 0700);
            (void)exercise_directory(root);
            if (mount(root, bind, NULL, MS_BIND | MS_REC, NULL) == 0) {
                (void)exercise_directory(bind);
                (void)umount2(bind, MNT_DETACH);
            }
            read_file_repeatedly("/proc/self/mountinfo");
            (void)umount2(base, MNT_DETACH);
        }
        iterations++;
        if ((iterations & 0x3fUL) == 0)
            sched_yield();
    }
    (void)umount2(bind, MNT_DETACH);
    (void)umount2(base, MNT_DETACH);
    dprintf(STDOUT_FILENO, "A52_QEMU_STRESS MOUNT id=%d iterations=%lu\n",
            id, iterations);
    return 0;
}

static int namespace_child(int id, unsigned long iteration)
{
    char base[160];
    snprintf(base, sizeof(base), "/mnt/ns-%d-%lu", id, iteration);
    if (unshare(CLONE_NEWNS) != 0)
        _exit(0);
    (void)mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL);
    (void)mkdir_if_needed(base, 0700);
    if (mount("tmpfs", base, "tmpfs", MS_NOSUID | MS_NODEV,
              "size=2m,mode=0700") == 0) {
        (void)exercise_directory(base);
        (void)umount2(base, MNT_DETACH);
    }
    _exit(0);
}

static int worker_namespace(int id, long deadline)
{
    unsigned long iterations = 0;
    while (!stop_requested && monotonic_seconds() < deadline) {
        pid_t pid = fork();
        if (pid == 0)
            namespace_child(id, iterations);
        if (pid > 0) {
            int status;
            if (waitpid(pid, &status, 0) < 0)
                return 1;
            if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
                return 1;
        }
        iterations++;
    }
    dprintf(STDOUT_FILENO, "A52_QEMU_STRESS NAMESPACE iterations=%lu\n",
            iterations);
    return 0;
}

static int leaf_process(void)
{
    struct stat st;
    char *const argv[] = { (char *)self_path, (char *)"--leaf", NULL };
    (void)argv;
    (void)prctl(PR_SET_NAME, "zygote64", 0, 0, 0);
    (void)open("/proc/self/status", O_RDONLY | O_CLOEXEC);
    (void)stat("/proc/self/exe", &st);
    (void)setresgid(0, 10000, 0);
    (void)setresuid(0, 10000, 0);
    (void)setresuid(0, 0, 0);
    (void)setresgid(0, 0, 0);
    return 0;
}

static int worker_exec(int id, long deadline)
{
    unsigned long iterations = 0;
    (void)id;
    while (!stop_requested && monotonic_seconds() < deadline) {
        pid_t pid = fork();
        if (pid == 0) {
            char *const argv[] = { (char *)self_path, (char *)"--leaf", NULL };
            char *const envp[] = { NULL };
            execve(self_path, argv, envp);
            _exit(127);
        }
        if (pid > 0) {
            int status;
            if (waitpid(pid, &status, 0) < 0)
                return 1;
            if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
                return 1;
        }
        iterations++;
    }
    dprintf(STDOUT_FILENO, "A52_QEMU_STRESS EXEC iterations=%lu\n", iterations);
    return 0;
}

struct worker_spec {
    const char *name;
    int (*fn)(int id, long deadline);
    int id;
};

static int parse_duration(void)
{
    char command_line[4096];
    int fd = open("/proc/cmdline", O_RDONLY | O_CLOEXEC);
    ssize_t count;
    char *match;
    long value;

    if (fd < 0)
        return duration_seconds;
    count = read(fd, command_line, sizeof(command_line) - 1);
    close(fd);
    if (count <= 0)
        return duration_seconds;
    command_line[count] = '\0';
    match = strstr(command_line, "stress_seconds=");
    if (!match)
        return duration_seconds;
    value = strtol(match + strlen("stress_seconds="), NULL, 10);
    if (value < 30 || value > 7200)
        return duration_seconds;
    return (int)value;
}

static void setup_guest(void)
{
    (void)mkdir_if_needed("/proc", 0555);
    (void)mkdir_if_needed("/sys", 0555);
    (void)mkdir_if_needed("/sys/kernel", 0555);
    (void)mkdir_if_needed("/sys/kernel/debug", 0555);
    (void)mkdir_if_needed("/dev", 0755);
    (void)mkdir_if_needed("/tmp", 01777);
    (void)mkdir_if_needed("/mnt", 0755);

    (void)mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL);
    (void)mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL);
    (void)mount("debugfs", "/sys/kernel/debug", "debugfs", 0, NULL);
    (void)mount("tmpfs", "/tmp", "tmpfs", MS_NOSUID | MS_NODEV, "size=32m");
}

static void power_off_guest(void)
{
    sync();
    (void)syscall(SYS_reboot,
                  LINUX_REBOOT_MAGIC1,
                  LINUX_REBOOT_MAGIC2,
                  LINUX_REBOOT_CMD_POWER_OFF,
                  NULL);
    for (;;)
        pause();
}

int main(int argc, char **argv)
{
    struct worker_spec workers[] = {
        { "bpf", worker_bpf, 0 },
        { "ksu", worker_ksu, 0 },
        { "susfs-abi", worker_susfs_abi, 0 },
        { "mount-0", worker_mount, 0 },
        { "mount-1", worker_mount, 1 },
        { "namespace", worker_namespace, 0 },
        { "exec", worker_exec, 0 },
    };
    pid_t children[ARRAY_SIZE(workers)];
    long deadline;
    size_t index;
    int failures = 0;

    if (argc > 1 && strcmp(argv[1], "--leaf") == 0)
        return leaf_process();

    if (getpid() != 1) {
        fprintf(stderr, "stress guest must run as PID 1\n");
        return 2;
    }

    signal(SIGTERM, request_stop);
    signal(SIGINT, request_stop);
    signal(SIGALRM, request_stop);
    setup_guest();
    duration_seconds = parse_duration();
    deadline = monotonic_seconds() + duration_seconds;
    alarm((unsigned int)duration_seconds + 120U);

    dprintf(STDOUT_FILENO,
            "A52_QEMU_STRESS_START seconds=%d workers=%zu uid=%ld\n",
            duration_seconds, ARRAY_SIZE(workers), (long)getuid());
    read_file_repeatedly("/proc/version");

    for (index = 0; index < ARRAY_SIZE(workers); index++) {
        pid_t pid = fork();
        if (pid == 0) {
            int result;
            (void)prctl(PR_SET_NAME, workers[index].name, 0, 0, 0);
            result = workers[index].fn(workers[index].id, deadline);
            _exit(result == 0 ? 0 : 1);
        }
        if (pid < 0) {
            children[index] = -1;
            failures++;
        } else {
            children[index] = pid;
        }
    }

    for (index = 0; index < ARRAY_SIZE(workers); index++) {
        int status;
        if (children[index] <= 0)
            continue;
        if (waitpid(children[index], &status, 0) < 0) {
            failures++;
            continue;
        }
        if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            dprintf(STDOUT_FILENO,
                    "A52_QEMU_STRESS_WORKER_FAIL name=%s status=%d\n",
                    workers[index].name, status);
            failures++;
        }
    }

    if (stop_requested && monotonic_seconds() < deadline)
        failures++;

    if (failures == 0)
        log_line("PASS", "A52_QEMU_FULL_STACK_STRESS_PASS");
    else
        dprintf(STDOUT_FILENO, "A52_QEMU_FULL_STACK_STRESS_FAIL failures=%d\n",
                failures);

    power_off_guest();
    return failures ? 1 : 0;
}
