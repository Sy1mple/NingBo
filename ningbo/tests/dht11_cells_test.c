/* Compile with gcc; these tests never access GPIO. */
#define main dht11_helper_main
#include "../experiments/environment/jetson/native/dht11_gpiod.c"
#undef main
#include <assert.h>

static size_t frame(long long *falls, int header) {
    const unsigned char bytes[5] = {55, 0, 26, 1, 82};
    size_t n = 0;
    long long t = 1000000;
    falls[n++] = t;
    if (header) { t += 160000; falls[n++] = t; }
    for (int bit = 0; bit < 40; bit++) {
        t += (bytes[bit / 8] & (1 << (7 - bit % 8))) ? 120000 : 77000;
        falls[n++] = t;
    }
    return n;
}

int main(void) {
    long long falls[44];
    unsigned char bytes[5];
    for (int header = 0; header <= 1; header++) {
        size_t n = frame(falls, header);
        assert(decode_falls(falls, n, bytes));
        assert(bytes[0] == 55 && bytes[2] == 26 && bytes[3] == 1);
        assert(!decode_falls(falls, n - 1, bytes)); /* missing last bit */
        for (size_t i = 0; i < n; i++) falls[i] += (i % 2) ? 4000 : -4000;
        assert(decode_falls(falls, n, bytes)); /* observation jitter */
    }
    size_t n = frame(falls, 1);
    falls[n - 1] += 43000;
    assert(!decode_falls(falls, n, bytes)); /* checksum bit changed */
    n = frame(falls, 1);
    falls[8] = falls[7];
    assert(!decode_falls(falls, n, bytes)); /* collapsed/missing transition */
    puts("DHT11 cell decoder: 8 checks passed");
    return 0;
}
