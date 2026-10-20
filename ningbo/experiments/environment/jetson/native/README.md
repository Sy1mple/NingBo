# Xavier NX DHT11 sampler

Python owns serialization, retries, cancellation and value range checks. This
small native helper acquires only DHT11 BOARD37 / PY.02 through the kernel GPIO
API, sends the start pulse using open-drain output, then captures the entire
response with a maximum 8 ms polling window. Only a complete 40-bit frame with
the correct checksum is published. Missing edges remain an error.

For this Xavier NX, the GPIO INPUT register is read through a read-only mapping
at 0x02212248. Direction and output changes use the exclusive kernel GPIO handle.
The helper rejects other chip labels or line names. Register layout reference:
https://github.com/torvalds/linux/blob/v5.10/drivers/gpio/gpio-tegra186.c
(tegra194 Y: bank 2, port 1, pin 2; GPIO base 0x02210000, INPUT offset 8).

Only the bounded capture uses SCHED_FIFO and CPU affinity. Address mapping,
clock initialization and buffer pages are warmed before the sensor response.
The main Python service remains an ordinary unprivileged process.

`deploy.sh` compiles the helper, installs it as root-owned mode 0755 and grants
`cap_sys_nice,cap_sys_rawio,cap_dac_read_search=ep`. The capabilities permit the
helper's bounded scheduling and read-only /dev/mem input mapping. Copying a new
binary does not preserve Linux capabilities; use the deployment steps when
reinstalling. No kernel or UART configuration is changed by this sampler.

Decoder regression tests (no GPIO access):

```
gcc -O2 -Wall -Wextra -Werror -std=c11 tests/dht11_cells_test.c -o /tmp/dht11_cells_test
/tmp/dht11_cells_test
python3 -m unittest discover -s tests -p test_dht11_native.py
```
