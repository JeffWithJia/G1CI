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

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace teleop_server
{

class ModbusTcpReader
{
public:
  ModbusTcpReader(std::string ip, uint16_t port, uint8_t unit_id);
  ~ModbusTcpReader();

  bool read_holding_registers(
      uint16_t start_address,
      uint16_t register_count,
      std::vector<int16_t> & output);

private:
  bool ensure_connected();
  bool send_all(const std::vector<uint8_t> & data);
  bool recv_exact(std::vector<uint8_t> & data);
  void disconnect();

  std::string ip_;
  uint16_t port_;
  uint8_t unit_id_;
  int socket_fd_{-1};
  uint16_t transaction_id_{0};
};

}  // namespace teleop_server
