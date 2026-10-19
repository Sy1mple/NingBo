#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <linux/gpio.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

/* Ubuntu 20.04 headers predate this ioctl; Jetson's Linux 5.10 implements it. */
#ifndef GPIOHANDLE_SET_CONFIG_IOCTL
struct gpiohandle_config {
    uint32_t flags;
    uint8_t default_values[64];
    uint32_t padding[4];
};
#define GPIOHANDLE_SET_CONFIG_IOCTL _IOWR(0xB4, 0x0A, struct gpiohandle_config)
#endif

static long long monotonic_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (long long)now.tv_sec * 1000000000LL + now.tv_nsec;
}

static int wait_level(int handle, int expected, long timeout_us) {
    const long long deadline = monotonic_ns() + timeout_us * 1000LL;
    struct gpiohandle_data values = {{0}};
    do {
        if (ioctl(handle, GPIOHANDLE_GET_LINE_VALUES_IOCTL, &values) < 0) return -1;
        if (values.values[0] == expected) return 1;
    } while (monotonic_ns() < deadline);
    return 0;
}

int main(int argc, char **argv) {
    int chip = -1, handle = -1, rc = 1;
    char *end = NULL;
    unsigned char data[5] = {0};
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
    struct gpiohandle_request request;
    memset(&request, 0, sizeof(request));
    request.lineoffsets[0] = (uint32_t)offset;
    request.flags = GPIOHANDLE_REQUEST_OUTPUT;
    request.lines = 1;
    strcpy(request.consumer_label, "ningbo-dht11");
    if (ioctl(chip, GPIO_GET_LINEHANDLE_IOCTL, &request) < 0) {
        fprintf(stderr, "request output failed: %s\n", strerror(errno));
        goto cleanup;
    }
    handle = request.fd;
    struct timespec duration = {0, 20000000};
    while (nanosleep(&duration, &duration) < 0 && errno == EINTR) {}

    /* Keep the line reserved: closing/re-requesting can miss the response. */
    struct gpiohandle_config config;
    memset(&config, 0, sizeof(config));
    config.flags = GPIOHANDLE_REQUEST_INPUT;
    if (ioctl(handle, GPIOHANDLE_SET_CONFIG_IOCTL, &config) < 0) {
        fprintf(stderr, "switch input in place failed: %s\n", strerror(errno));
        goto cleanup;
    }
    /* Ignore a short host-release pulse; synchronize to the sensor response. */
    int response = 0;
    const long long response_deadline = monotonic_ns() + 5000000LL;
    while (monotonic_ns() < response_deadline) {
        if (wait_level(handle, 0, 1000) != 1 || wait_level(handle, 1, 1000) != 1) break;
        long long response_high_start = monotonic_ns();
        if (wait_level(handle, 0, 1000) != 1) break;
        long long response_high_us = (monotonic_ns() - response_high_start) / 1000;
        if (response_high_us >= 55 && response_high_us <= 130) {response = 1; break;}
    }
    if (!response) {
        fprintf(stderr, "sensor response timing invalid\n");
        rc = 2;
        goto cleanup;
    }
    for (int bit = 0; bit < 40; bit++) {
        if (wait_level(handle, 1, 1000) != 1) {
            fprintf(stderr, "data bit %d did not rise\n", bit);
            rc = 2;
            goto cleanup;
        }
        const long long start = monotonic_ns();
        if (wait_level(handle, 0, 1000) != 1) {
            fprintf(stderr, "data bit %d did not fall\n", bit);
            rc = 2;
            goto cleanup;
        }
        const int value = monotonic_ns() - start > 50000LL;
        data[bit / 8] = (data[bit / 8] << 1) | value;
    }
    if (((data[0] + data[1] + data[2] + data[3]) & 255) != data[4]) {
        fprintf(stderr, "checksum error\n");
        rc = 3;
        goto cleanup;
    }
    openlog("ningbo-dht11", LOG_PID | LOG_NDELAY, LOG_USER);
    syslog(LOG_INFO, "valid temperature=%u.%u humidity=%u.%u parent=%ld sampler=inplace-sync",
           data[2], data[3], data[0], data[1], (long)getppid());
    closelog();
    printf("%u.%u %u.%u\n", data[2], data[3], data[0], data[1]);
    rc = 0;
cleanup:
    if (handle >= 0) close(handle);
    if (chip >= 0) close(chip);
    return rc;
}
