#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/gpio.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

static long long monotonic_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (long long)now.tv_sec * 1000000000LL + now.tv_nsec;
}

/* Measure whole low+high data cells between falling edges. Measuring only a
 * 26us high pulse amplifies ioctl latency and can shift response detection.
 * Accept only a complete 40-bit transaction with a valid checksum.
 */
static int decode_falls(const long long *falls, size_t count, unsigned char data[5]) {
    if (count != 41 && count != 42) return 0;
    size_t begin = count - 41;
    if (begin && (falls[1] - falls[0] < 120000 || falls[1] - falls[0] > 220000)) return 0;
    memset(data, 0, 5);
    for (size_t bit = 0; bit < 40; bit++) {
        long long period = falls[begin + bit + 1] - falls[begin + bit];
        if (period < 50000 || period > 155000) return 0;
        data[bit / 8] = (data[bit / 8] << 1) | (period > 100000);
    }
    return ((data[0] + data[1] + data[2] + data[3]) & 255) == data[4];
}

static int capture(volatile const uint32_t *input, long long *falls, size_t *count) {
    int previous = -1;
    long long start = monotonic_ns(), changed = start, now = start;
    unsigned int polls = 0;
    while (now - start < 8000000LL) {
        int level = *input & 1;
        if (level != previous) {
            now = monotonic_ns();
            if (previous == 1 && level == 0) {
                if (*count == 44) return 0;
                falls[(*count)++] = now;
            }
            previous = level;
            changed = now;
        } else if ((++polls & 63) == 0) {
            now = monotonic_ns();
            if (*count && level == 1 && now - changed > 250000LL)
                return 1; /* completed frame and released idle-high line */
        }
    }
    return 0;
}

int main(int argc, char **argv) {
    int chip = -1, handle = -1, rc = 1;
    char *end = NULL;
    unsigned char data[5] = {0};
    int realtime = 0;
    long long falls[44];
    size_t fall_count = 0;
    void *mapping = MAP_FAILED;
    long page_size = sysconf(_SC_PAGESIZE);
    if (argc != 3) {
        fprintf(stderr, "usage: %s /dev/gpiochipN line_offset\n", argv[0]);
        return 64;
    }
    errno = 0;
    long offset = strtol(argv[2], &end, 10);
    if (errno || !end || *end || offset < 0 || offset > UINT32_MAX) {
        fprintf(stderr, "invalid line offset\n");
        return 64;
    }
    chip = open(argv[1], O_RDONLY | O_CLOEXEC);
    if (chip < 0) {
        fprintf(stderr, "open chip failed: %s\n", strerror(errno));
        goto cleanup;
    }
    /* Xavier NX PY.02 / BOARD37 only. Register layout is specified by
     * drivers/gpio/gpio-tegra186.c: GPIO base 0x02210000, Y bank=2,
     * port=1, pin=2, INPUT offset=8. Map INPUT read-only; all output/direction
     * changes still go through the exclusive kernel GPIO line handle.
     */
    struct gpiochip_info chip_info;
    struct gpioline_info line_info;
    memset(&chip_info, 0, sizeof(chip_info));
    memset(&line_info, 0, sizeof(line_info));
    line_info.line_offset = (uint32_t)offset;
    if (ioctl(chip, GPIO_GET_CHIPINFO_IOCTL, &chip_info) < 0 ||
        ioctl(chip, GPIO_GET_LINEINFO_IOCTL, &line_info) < 0 ||
        strcmp(chip_info.label, "tegra194-gpio") || strcmp(line_info.name, "PY.02") ||
        offset != 151 || page_size < 4096) {
        fprintf(stderr, "unsupported DHT11 GPIO: expected Xavier NX PY.02 / line151\n");
        goto cleanup;
    }
    int memory = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (memory < 0) {
        fprintf(stderr, "open GPIO input register failed: %s; install helper capabilities\n", strerror(errno));
        goto cleanup;
    }
    const off_t input_address = 0x02212248;
    off_t map_address = input_address & ~((off_t)page_size - 1);
    mapping = mmap(NULL, (size_t)page_size, PROT_READ, MAP_SHARED, memory, map_address);
    close(memory);
    if (mapping == MAP_FAILED) {
        fprintf(stderr, "map GPIO input register failed: %s\n", strerror(errno));
        goto cleanup;
    }
    volatile const uint32_t *input = (volatile const uint32_t *)((char *)mapping + input_address - map_address);
    /* Resolve the device mapping/page faults before the sensor begins its
     * microsecond response. First-touch faults here otherwise lose early bits.
     */
    volatile uint32_t warmup = *input;
    (void)warmup;
    for (size_t i = 0; i < 44; i++) ((volatile long long *)falls)[i] = 0;
    struct gpiohandle_request request;
    memset(&request, 0, sizeof(request));
    request.lineoffsets[0] = (uint32_t)offset;
    request.flags = GPIOHANDLE_REQUEST_OUTPUT | GPIOHANDLE_REQUEST_OPEN_DRAIN;
    request.lines = 1;
    strcpy(request.consumer_label, "ningbo-dht11");
    if (ioctl(chip, GPIO_GET_LINEHANDLE_IOCTL, &request) < 0) {
        fprintf(stderr, "request output failed: %s\n", strerror(errno));
        goto cleanup;
    }
    handle = request.fd;
    /* Resolve the clock function's lazy linker/vDSO setup while the host is
     * still holding LOW, not after releasing the sensor's response window.
     */
    (void)monotonic_ns();
    cpu_set_t allowed, capture_cpu;
    if (sched_getaffinity(0, sizeof(allowed), &allowed) == 0) {
        /* Prefer CPU2, away from CPU0's device IRQs and CPU3's display work. */
        for (int index = 0; index < CPU_SETSIZE; index++) {
            int cpu = index == 0 ? 2 : (index == 2 ? 0 : index);
            if (CPU_ISSET(cpu, &allowed)) {
                CPU_ZERO(&capture_cpu);
                CPU_SET(cpu, &capture_cpu);
                if (sched_setaffinity(0, sizeof(capture_cpu), &capture_cpu) < 0) {
                    fprintf(stderr, "set capture CPU affinity failed: %s\n", strerror(errno));
                    goto cleanup;
                }
                break;
            }
        }
    }
    struct timespec duration = {0, 20000000};
    while (nanosleep(&duration, &duration) < 0 && errno == EINTR) {}

    /* Only the bounded wire transaction uses real-time scheduling. Sleeping
     * for the start pulse, logging and Python/application work stay normal.
     * The installer grants capabilities only to this root-owned helper;
     * never run the whole application FIFO or as root.
     */
    struct sched_param priority = {.sched_priority = 70};
    if (sched_setscheduler(0, SCHED_FIFO, &priority) < 0) {
        fprintf(stderr, "DHT11 real-time capture unavailable: %s; run deployment capability setup\n", strerror(errno));
        goto cleanup;
    }
    realtime = 1;

    /* Open-drain HIGH releases the wire without a full pin reconfiguration.
     * A SET_CONFIG direction change may return after response/data has begun.
     * Never drive this shared sensor line actively high.
     */
    struct gpiohandle_data released = {{1}};
    if (ioctl(handle, GPIOHANDLE_SET_LINE_VALUES_IOCTL, &released) < 0) {
        fprintf(stderr, "release open-drain line failed: %s\n", strerror(errno));
        goto cleanup;
    }
    int complete = capture(input, falls, &fall_count);
    if (!complete || !decode_falls(falls, fall_count, data)) {
        fprintf(stderr, "DHT11 incomplete frame/timing/checksum: falling_edges=%zu\n", fall_count);
        rc = 2;
        goto cleanup;
    }
    struct sched_param normal = {.sched_priority = 0};
    if (sched_setscheduler(0, SCHED_OTHER, &normal) < 0) {
        fprintf(stderr, "restore normal scheduling failed: %s\n", strerror(errno));
        goto cleanup;
    }
    realtime = 0;
    openlog("ningbo-dht11", LOG_PID | LOG_NDELAY, LOG_USER);
    syslog(LOG_INFO, "valid temperature=%u.%u humidity=%u.%u parent=%ld sampler=fastcells-v7",
           data[2], data[3], data[0], data[1], (long)getppid());
    closelog();
    printf("%u.%u %u.%u\n", data[2], data[3], data[0], data[1]);
    rc = 0;
cleanup:
    if (realtime) {
        struct sched_param normal = {.sched_priority = 0};
        sched_setscheduler(0, SCHED_OTHER, &normal);
    }
    if (rc) {
        openlog("ningbo-dht11", LOG_PID | LOG_NDELAY, LOG_USER);
        char timings[512] = {0};
        size_t used = 0;
        for (size_t i = 1; i < fall_count && used + 16 < sizeof(timings); i++)
            used += snprintf(timings + used, sizeof(timings) - used, "%lld,", (falls[i] - falls[i-1]) / 1000);
        syslog(LOG_WARNING, "sample_failed parent=%ld sampler=fastcells-v7 code=%d falls=%zu cells_us=%s", (long)getppid(), rc, fall_count, timings);
        closelog();
    }
    if (handle >= 0) close(handle);
    if (chip >= 0) close(chip);
    if (mapping != MAP_FAILED) munmap(mapping, (size_t)page_size);
    return rc;
}
