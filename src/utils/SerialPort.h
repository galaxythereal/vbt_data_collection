#pragma once

/**
 * @file SerialPort.h
 * @brief Cross-platform serial port abstraction (Linux).
 */

#include <string>
#include <vector>
#include <cstdint>

namespace vbt {

class SerialPort {
public:
    SerialPort() = default;
    ~SerialPort();

    bool open(const std::string& port, int baud_rate);
    void close();
    bool is_open() const { return fd_ >= 0; }

    ssize_t read(uint8_t* buffer, size_t max_bytes);
    ssize_t write(const uint8_t* data, size_t length);

    void flush();

    // List available serial ports
    static std::vector<std::string> list_ports();

private:
    int fd_ = -1;
};

} // namespace vbt
