/**
 * ESP32 ESP-NOW → USB Serial Relay v2.0
 * =====================================
 * Receives IMU packets from the bar ESP via ESP-NOW unicast and forwards them
 * to the host PC over USB at 921600 baud. The host sees the same byte stream
 * as if the bar ESP were USB-connected.
 *
 *   recv ISR  ──►  ring buffer (8 KB)  ──►  loop() Serial.write
 *
 * Decoupling the recv callback from Serial output is critical: at 988 Hz IMU,
 * batched into 124 Hz packets of 208 bytes, blocking inside the callback
 * stalls the WiFi task and ESP-NOW drops packets.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>

static volatile uint32_t g_recv_pkts   = 0;   // counted in ISR (Serial-independent)
static volatile uint32_t g_recv_bytes  = 0;
static uint32_t g_fwd_bytes            = 0;   // counted in loop after Serial.write
static uint32_t g_last_status_ms       = 0;
static uint32_t g_last_recv_pkts       = 0;
static uint32_t g_overruns             = 0;

// Ring buffer between recv ISR and loop(). 8 KB ≈ 40 batched packets — plenty
// of headroom for short Serial.write stalls.
static constexpr size_t RING_SIZE = 8192;
static uint8_t  g_ring[RING_SIZE];
static volatile size_t g_ring_w = 0;
static volatile size_t g_ring_r = 0;

static inline size_t ring_used() {
    size_t w = g_ring_w, r = g_ring_r;
    return (w >= r) ? (w - r) : (RING_SIZE - (r - w));
}

static void on_recv(const uint8_t* mac, const uint8_t* data, int len) {
    g_recv_pkts++;
    g_recv_bytes += len;
    // Append to ring; if not enough space, drop and count overrun
    size_t free_space = RING_SIZE - ring_used() - 1;
    if ((size_t)len > free_space) { g_overruns++; return; }
    size_t w = g_ring_w;
    for (int i = 0; i < len; i++) {
        g_ring[w] = data[i];
        w = (w + 1) % RING_SIZE;
    }
    g_ring_w = w;
}

void setup() {
    Serial.begin(921600);
    delay(300);
    Serial.println();
    Serial.println("# ESP32 ESP-NOW → Serial Relay v2.0 (ring-buffered)");

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    Serial.printf("# Relay MAC: %s\n", WiFi.macAddress().c_str());

    if (esp_now_init() != ESP_OK) {
        Serial.println("# ERROR: esp_now_init failed");
        while (true) { delay(1000); Serial.println("# ERROR: esp_now_init failed"); }
    }
    esp_now_register_recv_cb(on_recv);
    Serial.println("# Listening for ESP-NOW unicast/broadcast packets");

    g_last_status_ms = millis();
}

void loop() {
    // Drain ring buffer to Serial. Use a local block-copy to amortize syscall.
    static uint8_t tx_buf[512];
    while (ring_used() > 0) {
        size_t n = 0, r = g_ring_r;
        while (n < sizeof(tx_buf) && r != g_ring_w) {
            tx_buf[n++] = g_ring[r];
            r = (r + 1) % RING_SIZE;
        }
        if (n == 0) break;
        size_t written = Serial.write(tx_buf, n);
        g_ring_r = (g_ring_r + written) % RING_SIZE;
        g_fwd_bytes += written;
        if (written < n) break;  // serial buffer full, retry next loop
    }

    uint32_t now = millis();
    if (now - g_last_status_ms >= 5000) {
        uint32_t delta = g_recv_pkts - g_last_recv_pkts;
        float rate = delta / 5.0f;
        Serial.printf("# RELAY: recv=%u (%.1f pkt/s), bytes=%u, fwd=%u, overruns=%u\n",
                      g_recv_pkts, rate, g_recv_bytes, g_fwd_bytes, g_overruns);
        g_last_status_ms = now;
        g_last_recv_pkts = g_recv_pkts;
    }
}
