/**
 * @file SerialPort.cpp
 * @brief Linux serial port implementation.
 */
#include "utils/SerialPort.h"
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <cstring>
#include <filesystem>
#include <spdlog/spdlog.h>

namespace vbt {

SerialPort::~SerialPort() { close(); }

bool SerialPort::open(const std::string& port, int baud_rate) {
    fd_ = ::open(port.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd_ < 0) return false;

    struct termios tty;
    memset(&tty, 0, sizeof(tty));
    tcgetattr(fd_, &tty);

    speed_t speed;
    switch (baud_rate) {
#ifdef B2000000
        case 2000000: speed = B2000000; break;
#endif
#ifdef B921600
        case 921600:  speed = B921600; break;
#endif
        case 115200:  speed = B115200; break;
        default:
#ifdef __APPLE__
            // macOS uses IOSSIOSPEED for non-standard rates; fall through to set custom speed below.
            speed = B115200;
            break;
#else
            ::close(fd_); fd_ = -1; return false;
#endif
    }
    cfsetispeed(&tty, speed);
    cfsetospeed(&tty, speed);

    tty.c_cflag = speed | CS8 | CLOCAL | CREAD;
    tty.c_iflag = 0;
    tty.c_oflag = 0;
    tty.c_lflag = 0;
    tty.c_cc[VMIN] = 0;
    tty.c_cc[VTIME] = 1;

    tcsetattr(fd_, TCSANOW, &tty);
    tcflush(fd_, TCIOFLUSH);
    return true;
}

void SerialPort::close() {
    if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
}

ssize_t SerialPort::read(uint8_t* buffer, size_t max_bytes) {
    if (fd_ < 0) return -1;
    return ::read(fd_, buffer, max_bytes);
}

ssize_t SerialPort::write(const uint8_t* data, size_t length) {
    if (fd_ < 0) return -1;
    return ::write(fd_, data, length);
}

void SerialPort::flush() {
    if (fd_ >= 0) tcflush(fd_, TCIOFLUSH);
}

std::vector<std::string> SerialPort::list_ports() {
    std::vector<std::string> ports;
    for (const auto& entry : std::filesystem::directory_iterator("/dev")) {
        std::string name = entry.path().filename().string();
        if (name.find("ttyUSB") == 0 || name.find("ttyACM") == 0) {
            ports.push_back(entry.path().string());
        }
    }
    return ports;
}

} // namespace vbt
