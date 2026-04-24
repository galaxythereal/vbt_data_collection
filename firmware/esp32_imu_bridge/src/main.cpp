/**
 * ESP32 IMU Bridge — PhD-Grade Binary Firmware v3.0
 * ==================================================
 * ICM42688-P @ 1kHz, interrupt-driven, ±16g/±2000dps
 * 26-byte CRC-protected binary packets @ 921600 baud
 *
 * Features:
 *   - INT1 data-ready interrupt for sub-µs jitter timing
 *   - Polling fallback if INT1 not wired
 *   - 64-bit µs timestamps via esp_timer_get_time()
 *   - Manual byte packing (no struct alignment issues)
 *   - 3rd-order anti-aliasing, BW = ODR/4 for clean signal
 *   - FIFO disabled for minimum latency
 *   - Periodic ASCII status reports (prefixed with '#')
 *
 * Packet Format (26 bytes, little-endian):
 *   [0-1]   Sync: 0x55 0xAA
 *   [2-9]   Timestamp µs (uint64_t LE)
 *   [10-11] Accel X raw (int16_t LE)
 *   [12-13] Accel Y raw
 *   [14-15] Accel Z raw
 *   [16-17] Gyro X raw
 *   [18-19] Gyro Y raw
 *   [20-21] Gyro Z raw
 *   [22-23] Temperature raw
 *   [24-25] CRC16-CCITT
 *
 * Scale factors (apply on host):
 *   accel: raw * 16.0 / 32768.0  → g
 *   gyro:  raw * 2000.0 / 32768.0 → dps
 *   temp:  raw / 132.48 + 25.0 → °C
 *
 * Wiring:
 *   GPIO 18 → SCK     GPIO 5  → CS
 *   GPIO 19 → SDO     GPIO 4  → INT1 (optional)
 *   GPIO 23 → SDI     3V3     → VCC
 *                      GND     → GND
 */

#include <Arduino.h>
#include <SPI.h>

// ============================================================================
// Pin Config
// ============================================================================
static constexpr int PIN_SCK  = 18;
static constexpr int PIN_MISO = 19;
static constexpr int PIN_MOSI = 23;
static constexpr int PIN_CS   = 5;
static constexpr int PIN_INT1 = 4;

// ============================================================================
// ICM42688-P Registers (Bank 0)
// ============================================================================
static constexpr uint8_t REG_DEVICE_CONFIG      = 0x11;
static constexpr uint8_t REG_INT_CONFIG          = 0x14;
static constexpr uint8_t REG_FIFO_CONFIG         = 0x16;
static constexpr uint8_t REG_TEMP_DATA1          = 0x1D;
static constexpr uint8_t REG_INT_STATUS          = 0x2D;
static constexpr uint8_t REG_PWR_MGMT0           = 0x4E;
static constexpr uint8_t REG_GYRO_CONFIG0        = 0x4F;
static constexpr uint8_t REG_ACCEL_CONFIG0       = 0x50;
static constexpr uint8_t REG_GYRO_CONFIG1        = 0x51;
static constexpr uint8_t REG_GYRO_ACCEL_CONFIG0  = 0x52;
static constexpr uint8_t REG_ACCEL_CONFIG1       = 0x53;
static constexpr uint8_t REG_INT_CONFIG0         = 0x63;
static constexpr uint8_t REG_INT_CONFIG1         = 0x64;
static constexpr uint8_t REG_INT_SOURCE0         = 0x65;
static constexpr uint8_t REG_WHO_AM_I            = 0x75;
static constexpr uint8_t REG_BANK_SEL            = 0x76;

// ICM42688-P constants
static constexpr uint8_t WHO_AM_I_EXPECTED  = 0x47;
static constexpr uint8_t PWR_ACCEL_GYRO_LN  = 0x0F;  // Accel LN + Gyro LN

// Full-scale: FS_SEL[6:5] → 00=±16g/±2000dps (maximum range for VBT)
static constexpr uint8_t ACCEL_FS_16G       = 0x00;
static constexpr uint8_t GYRO_FS_2000DPS    = 0x00;
static constexpr uint8_t ODR_1KHZ           = 0x06;

// ============================================================================
// SPI (use default bus — proven reliable)
// ============================================================================
static SPISettings spiCfg(8000000, MSBFIRST, SPI_MODE0);

void writeReg(uint8_t reg, uint8_t val) {
    SPI.beginTransaction(spiCfg);
    digitalWrite(PIN_CS, LOW);
    SPI.transfer(reg & 0x7F);
    SPI.transfer(val);
    digitalWrite(PIN_CS, HIGH);
    SPI.endTransaction();
}

uint8_t readReg(uint8_t reg) {
    SPI.beginTransaction(spiCfg);
    digitalWrite(PIN_CS, LOW);
    SPI.transfer(reg | 0x80);
    uint8_t val = SPI.transfer(0x00);
    digitalWrite(PIN_CS, HIGH);
    SPI.endTransaction();
    return val;
}

void readRegs(uint8_t reg, uint8_t* buf, size_t len) {
    SPI.beginTransaction(spiCfg);
    digitalWrite(PIN_CS, LOW);
    SPI.transfer(reg | 0x80);
    for (size_t i = 0; i < len; i++) buf[i] = SPI.transfer(0x00);
    digitalWrite(PIN_CS, HIGH);
    SPI.endTransaction();
}

// ============================================================================
// CRC16-CCITT (matches host-side C++ and Python implementations)
// ============================================================================
static uint16_t crc16_ccitt(const uint8_t* data, size_t length) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < length; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; j++) {
            if (crc & 0x8000) crc = (crc << 1) ^ 0x1021;
            else              crc <<= 1;
        }
    }
    return crc;
}

// ============================================================================
// Globals
// ============================================================================
static volatile bool g_data_ready = false;
static bool g_use_interrupt = false;
static uint32_t g_sample_count = 0;
static uint32_t g_error_count = 0;
static uint32_t g_last_status_ms = 0;
static uint32_t g_last_status_count = 0;
static uint32_t g_next_sample_us = 0;  // For polling fallback

// ============================================================================
// Interrupt Handler
// ============================================================================
static void IRAM_ATTR isr_data_ready() {
    g_data_ready = true;
}

// ============================================================================
// IMU Initialization
// ============================================================================
static bool imu_init() {
    // Bank 0
    writeReg(REG_BANK_SEL, 0x00);
    delay(1);

    // Soft reset
    writeReg(REG_DEVICE_CONFIG, 0x01);
    delay(2);  // 1ms typ per datasheet, 2ms for margin

    // Re-select Bank 0 after reset
    writeReg(REG_BANK_SEL, 0x00);
    delay(1);

    // Verify WHO_AM_I
    uint8_t who = readReg(REG_WHO_AM_I);
    Serial.print("# WHO_AM_I=0x");
    Serial.println(who, HEX);
    if (who != WHO_AM_I_EXPECTED) {
        return false;
    }

    // ── Sensor Configuration ──

    // Gyro: ±2000dps, 1kHz ODR
    writeReg(REG_GYRO_CONFIG0, (GYRO_FS_2000DPS << 5) | ODR_1KHZ);

    // Accel: ±16g, 1kHz ODR
    writeReg(REG_ACCEL_CONFIG0, (ACCEL_FS_16G << 5) | ODR_1KHZ);

    // ── Anti-Aliasing Filters (3rd order, BW = ODR/4 for clean signal) ──

    // GYRO_CONFIG1: FILT_ORD[3:2] = 10 (3rd order)
    writeReg(REG_GYRO_CONFIG1, 0x02 << 2);

    // ACCEL_CONFIG1: FILT_ORD[3:2] = 10 (3rd order)
    writeReg(REG_ACCEL_CONFIG1, 0x02 << 2);

    // GYRO_ACCEL_CONFIG0: BW_SEL = 01 (ODR/4) for both
    // bits [3:0] = accel BW, bits [7:4] = gyro BW
    writeReg(REG_GYRO_ACCEL_CONFIG0, 0x11);  // Both = ODR/4

    // ── FIFO: Bypass (minimum latency) ──
    writeReg(REG_FIFO_CONFIG, 0x00);

    // ── Interrupt Configuration ──

    // INT_CONFIG: INT1 push-pull, active HIGH, pulsed (50µs)
    writeReg(REG_INT_CONFIG, 0x03);

    // INT_CONFIG1: async reset mode (clear on any read)
    writeReg(REG_INT_CONFIG1, 0x00);

    // INT_SOURCE0: Data Ready → INT1
    writeReg(REG_INT_SOURCE0, 0x08);

    // ── Power On ──

    // PWR_MGMT0: Accel Low Noise + Gyro Low Noise
    writeReg(REG_PWR_MGMT0, PWR_ACCEL_GYRO_LN);
    delay(50);  // Wait for gyro startup (per datasheet: 45ms typ)

    return true;
}

// ============================================================================
// Read sensor and send packet
// ============================================================================
static void read_and_send() {
    // Timestamp immediately (64-bit µs, monotonic)
    uint64_t ts = (uint64_t)esp_timer_get_time();

    // Burst read: TEMP(2) + ACCEL(6) + GYRO(6) = 14 bytes from 0x1D
    uint8_t raw[14];
    readRegs(REG_TEMP_DATA1, raw, 14);

    // Parse big-endian sensor registers
    int16_t temp = (int16_t)((uint16_t)raw[0]  << 8 | raw[1]);
    int16_t ax   = (int16_t)((uint16_t)raw[2]  << 8 | raw[3]);
    int16_t ay   = (int16_t)((uint16_t)raw[4]  << 8 | raw[5]);
    int16_t az   = (int16_t)((uint16_t)raw[6]  << 8 | raw[7]);
    int16_t gx   = (int16_t)((uint16_t)raw[8]  << 8 | raw[9]);
    int16_t gy   = (int16_t)((uint16_t)raw[10] << 8 | raw[11]);
    int16_t gz   = (int16_t)((uint16_t)raw[12] << 8 | raw[13]);

    // Manual 26-byte packet assembly (LE, no alignment issues)
    uint8_t buf[26];
    buf[0] = 0x55; buf[1] = 0xAA;          // Sync word
    memcpy(&buf[2],  &ts,   8);             // Timestamp
    memcpy(&buf[10], &ax,   2);             // Accel X
    memcpy(&buf[12], &ay,   2);             // Accel Y
    memcpy(&buf[14], &az,   2);             // Accel Z
    memcpy(&buf[16], &gx,   2);             // Gyro X
    memcpy(&buf[18], &gy,   2);             // Gyro Y
    memcpy(&buf[20], &gz,   2);             // Gyro Z
    memcpy(&buf[22], &temp, 2);             // Temperature

    uint16_t crc = crc16_ccitt(buf, 24);
    memcpy(&buf[24], &crc,  2);             // CRC16

    Serial.write(buf, 26);
    g_sample_count++;
}

// ============================================================================
// Setup
// ============================================================================
void setup() {
    Serial.begin(921600);
    delay(500);
    Serial.println();
    Serial.println("# ESP32 IMU Bridge v3.0 (binary, interrupt-driven)");
    Serial.println("# PhD-Grade VBT Data Collection System");

    // SPI init
    pinMode(PIN_CS, OUTPUT);
    digitalWrite(PIN_CS, HIGH);
    SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);
    delay(20);

    // IMU init
    if (!imu_init()) {
        Serial.println("# ERROR: ICM42688-P not found! Check wiring.");
        while (true) {
            delay(1000);
            Serial.println("# ERROR: IMU init failed — verify SPI wiring");
        }
    }

    Serial.println("# ICM42688-P initialized OK");
    Serial.println("# Config: +-16g accel, +-2000dps gyro, 1kHz ODR");
    Serial.println("# Filter: 3rd-order, BW=ODR/4 (250Hz)");

    // Attempt to attach INT1 interrupt
    pinMode(PIN_INT1, INPUT);
    delay(10);

    // Check if INT1 pin shows activity (data-ready should pulse)
    bool int_detected = false;
    uint32_t t0 = millis();
    while (millis() - t0 < 50) {
        if (digitalRead(PIN_INT1) == HIGH) {
            int_detected = true;
            break;
        }
    }

    if (int_detected) {
        attachInterrupt(digitalPinToInterrupt(PIN_INT1), isr_data_ready, RISING);
        g_use_interrupt = true;
        Serial.println("# INT1: attached (interrupt-driven, <1us jitter)");
    } else {
        g_use_interrupt = false;
        Serial.println("# INT1: not detected — using polling (timer-driven)");
    }

    Serial.println("# Streaming: 26-byte binary packets [55AA|ts:8|ax:2|ay:2|az:2|gx:2|gy:2|gz:2|t:2|crc:2]");
    Serial.println("# Baud: 921600 | Bandwidth: 26*1000 = 26KB/s (3% of link capacity)");

    g_last_status_ms = millis();
}

// ============================================================================
// Loop
// ============================================================================
void loop() {
    bool should_read = false;

    if (g_use_interrupt) {
        // Interrupt-driven: wait for data-ready flag
        if (g_data_ready) {
            g_data_ready = false;
            should_read = true;
        }
    } else {
        // Polling fallback: software 1kHz timer
        uint32_t now_us = micros();
        if (g_next_sample_us == 0) g_next_sample_us = now_us;
        if ((int32_t)(now_us - g_next_sample_us) >= 0) {
            g_next_sample_us += 1000;
            // Catch up if behind
            if ((int32_t)(now_us - g_next_sample_us) > 5000)
                g_next_sample_us = now_us + 1000;
            should_read = true;
        }
    }

    if (should_read) {
        read_and_send();
    }

    // Periodic status (every 5 seconds, ASCII)
    uint32_t now_ms = millis();
    if (now_ms - g_last_status_ms >= 5000) {
        uint32_t delta = g_sample_count - g_last_status_count;
        float rate = delta / 5.0f;
        Serial.printf("# STATUS: rate=%.1f Hz, total=%u, mode=%s\n",
                       rate, g_sample_count,
                       g_use_interrupt ? "INT" : "POLL");
        g_last_status_ms = now_ms;
        g_last_status_count = g_sample_count;
    }
}
