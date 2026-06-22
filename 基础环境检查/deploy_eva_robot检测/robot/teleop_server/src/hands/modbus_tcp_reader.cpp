// Copyright 2026 coScene
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "hands/modbus_tcp_reader.hpp"

#include "logging/logger.hpp"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <utility>

namespace teleop_server
{
namespace
{
constexpr int kModbusFunctionReadHoldingRegisters = 0x03;
}  // namespace

ModbusTcpReader::ModbusTcpReader(std::string ip, uint16_t port, uint8_t unit_id)
  : ip_(std::move(ip)),
    port_(port),
    unit_id_(unit_id)
{}

ModbusTcpReader::~ModbusTcpReader()
{
  disconnect();
}

bool ModbusTcpReader::read_holding_registers(
    uint16_t start_address,
    uint16_t register_count,
    std::vector<int16_t> & output)
{
  output.clear();
  if (!ensure_connected()) {
    return false;
  }

  ++transaction_id_;
  const uint16_t protocol_id = 0;
  const uint16_t pdu_length_with_unit_id = 6;
  std::vector<uint8_t> request(12, 0);
  request[0] = static_cast<uint8_t>((transaction_id_ >> 8) & 0xff);
  request[1] = static_cast<uint8_t>(transaction_id_ & 0xff);
  request[2] = static_cast<uint8_t>((protocol_id >> 8) & 0xff);
  request[3] = static_cast<uint8_t>(protocol_id & 0xff);
  request[4] = static_cast<uint8_t>((pdu_length_with_unit_id >> 8) & 0xff);
  request[5] = static_cast<uint8_t>(pdu_length_with_unit_id & 0xff);
  request[6] = unit_id_;
  request[7] = kModbusFunctionReadHoldingRegisters;
  request[8] = static_cast<uint8_t>((start_address >> 8) & 0xff);
  request[9] = static_cast<uint8_t>(start_address & 0xff);
  request[10] = static_cast<uint8_t>((register_count >> 8) & 0xff);
  request[11] = static_cast<uint8_t>(register_count & 0xff);

  if (!send_all(request)) {
    disconnect();
    return false;
  }

  std::vector<uint8_t> header(9, 0);
  if (!recv_exact(header)) {
    disconnect();
    return false;
  }

  const auto function_code = header[7];
  if (function_code != kModbusFunctionReadHoldingRegisters) {
    disconnect();
    return false;
  }
  const auto byte_count = header[8];
  if (byte_count != register_count * 2) {
    disconnect();
    return false;
  }

  std::vector<uint8_t> payload(byte_count, 0);
  if (!recv_exact(payload)) {
    disconnect();
    return false;
  }

  output.reserve(register_count);
  for (uint16_t i = 0; i < register_count; ++i) {
    const auto high = payload[2 * i];
    const auto low = payload[2 * i + 1];
    const uint16_t raw = static_cast<uint16_t>((high << 8) | low);
    output.push_back(static_cast<int16_t>(raw));
  }
  return true;
}

bool ModbusTcpReader::ensure_connected()
{
  if (socket_fd_ >= 0) {
    return true;
  }

  socket_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
  if (socket_fd_ < 0) {
    TELEOP_LOG_ERROR("Failed to create Modbus socket for %s:%u", ip_.c_str(), port_);
    return false;
  }

  timeval timeout{};
  timeout.tv_sec = 0;
  timeout.tv_usec = 50000;
  setsockopt(socket_fd_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(socket_fd_, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

  sockaddr_in server_addr{};
  server_addr.sin_family = AF_INET;
  server_addr.sin_port = htons(port_);
  if (::inet_pton(AF_INET, ip_.c_str(), &server_addr.sin_addr) != 1) {
    TELEOP_LOG_ERROR("Invalid Modbus IP: %s", ip_.c_str());
    disconnect();
    return false;
  }

  if (::connect(socket_fd_, reinterpret_cast<sockaddr *>(&server_addr), sizeof(server_addr)) != 0) {
    TELEOP_LOG_WARN("Failed to connect Modbus server %s:%u, errno=%d",
        ip_.c_str(),
        port_,
        errno);
    disconnect();
    return false;
  }
  return true;
}

bool ModbusTcpReader::send_all(const std::vector<uint8_t> & data)
{
  size_t sent = 0;
  while (sent < data.size()) {
    const auto n = ::send(socket_fd_, data.data() + sent, data.size() - sent, 0);
    if (n <= 0) {
      return false;
    }
    sent += static_cast<size_t>(n);
  }
  return true;
}

bool ModbusTcpReader::recv_exact(std::vector<uint8_t> & data)
{
  size_t read_total = 0;
  while (read_total < data.size()) {
    const auto n = ::recv(socket_fd_, data.data() + read_total, data.size() - read_total, 0);
    if (n <= 0) {
      return false;
    }
    read_total += static_cast<size_t>(n);
  }
  return true;
}

void ModbusTcpReader::disconnect()
{
  if (socket_fd_ >= 0) {
    ::close(socket_fd_);
    socket_fd_ = -1;
  }
}

}  // namespace teleop_server
