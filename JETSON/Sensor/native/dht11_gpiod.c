#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <gpiod.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static long long monotonic_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (long long)now.tv_sec * 1000000000LL + now.tv_nsec;
}

static void sleep_ns(long nanoseconds) {
    struct timespec duration = {nanoseconds / 1000000000L,
                                nanoseconds % 1000000000L};
    while (nanosleep(&duration, &duration) < 0 && errno == EINTR) {}
}

static int wait_level(struct gpiod_line *line, int expected, long timeout_us) {
    long long deadline = monotonic_ns() + (long long)timeout_us * 1000LL;
    do {
        int value = gpiod_line_get_value(line);
        if (value < 0)
            return -1;
        if (value == expected)
            return 1;
    } while (monotonic_ns() < deadline);
    return 0;
}

int main(int argc, char **argv) {
    const char *chip_path;
    char *end = NULL;
    long offset;
    struct gpiod_chip *chip = NULL;
    struct gpiod_line *line = NULL;
    unsigned char data[5] = {0, 0, 0, 0, 0};
    int rc = 1;

    if (argc != 3) {
        fprintf(stderr, "usage: %s /dev/gpiochipN line_offset\n", argv[0]);
        return 64;
    }
    chip_path = argv[1];
    errno = 0;
    offset = strtol(argv[2], &end, 10);
    if (errno || !end || *end || offset < 0) {
        fprintf(stderr, "invalid line offset: %s\n", argv[2]);
        return 64;
    }

    chip = gpiod_chip_open(chip_path);
    if (!chip) {
        fprintf(stderr, "open %s failed: %s\n", chip_path, strerror(errno));
        goto cleanup;
    }
    line = gpiod_chip_get_line(chip, (unsigned int)offset);
    if (!line) {
        fprintf(stderr, "get line %ld failed: %s\n", offset, strerror(errno));
        goto cleanup;
    }
    if (gpiod_line_request_output(line, "ningbo-dht11", 0) < 0) {
        fprintf(stderr, "request output failed: %s\n", strerror(errno));
        goto cleanup;
    }
    sleep_ns(20000000L);
    gpiod_line_release(line);
    if (gpiod_line_request_input(line, "ningbo-dht11") < 0) {
        fprintf(stderr, "request input failed: %s\n", strerror(errno));
        goto cleanup;
    }

    /* DHT11 response: low about 80 us, high about 80 us, then data low. */
    if (wait_level(line, 0, 5000) != 1 || wait_level(line, 1, 5000) != 1 ||
        wait_level(line, 0, 5000) != 1) {
        fprintf(stderr, "sensor response timeout\n");
        rc = 2;
        goto cleanup;
    }

    for (int bit = 0; bit < 40; ++bit) {
        long long high_start;
        long long high_end;
        int value;
        if (wait_level(line, 1, 1000) != 1) {
            fprintf(stderr, "data bit %d did not rise\n", bit);
            rc = 2;
            goto cleanup;
        }
        high_start = monotonic_ns();
        if (wait_level(line, 0, 1000) != 1) {
            fprintf(stderr, "data bit %d did not fall\n", bit);
            rc = 2;
            goto cleanup;
        }
        high_end = monotonic_ns();
        value = (high_end - high_start) > 50000LL;
        data[bit / 8] = (unsigned char)((data[bit / 8] << 1) | value);
    }

    if (((data[0] + data[1] + data[2] + data[3]) & 0xFF) != data[4]) {
        fprintf(stderr, "checksum error: %u %u %u %u %u\n",
                data[0], data[1], data[2], data[3], data[4]);
        rc = 3;
        goto cleanup;
    }

    printf("%u.%u %u.%u\n", data[2], data[3], data[0], data[1]);
    rc = 0;

cleanup:
    if (line)
        gpiod_line_release(line);
    if (chip)
        gpiod_chip_close(chip);
    return rc;
}
