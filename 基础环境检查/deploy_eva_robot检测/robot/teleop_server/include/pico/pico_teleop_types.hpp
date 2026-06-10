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

#include <array>
#include <cstdint>
#include <vector>

namespace teleop_server
{

struct PicoControllerInput
{
  double trigger_l{0.0};
  double trigger_r{0.0};
  double grip_l{0.0};
  double grip_r{0.0};
  std::array<double, 2> axis_l{0.0, 0.0};
  std::array<double, 2> axis_r{0.0, 0.0};
  bool A{false};
  bool B{false};
  bool X{false};
  bool Y{false};
};

struct PicoTeleopPacket
{
  std::vector<double> pose_82d;
  PicoControllerInput input;
  int64_t body_timestamp_ns{0};
  int64_t input_timestamp_ns{0};
  int64_t pose_sequence{0};
  bool has_controller_input{false};
  bool has_pose_82d{false};
};

}  // namespace teleop_server
