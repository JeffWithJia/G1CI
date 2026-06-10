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

#include <cstddef>
#include <memory>
#include <string>
#include <vector>

#include <rclcpp/rclcpp.hpp>

namespace teleop_server
{

enum class HandType
{
  NONE,
  INSPIRE,
  DEX1,
  BRAINCO,
};

struct HandStateData
{
  std::vector<double> position;
  std::vector<double> velocity;
  std::vector<double> effort;
};

struct InspireHandConfig
{
  std::string right_ip;
  std::string left_ip;
  std::string left_cmd_topic;
  std::string right_cmd_topic;
  int port{6000};
  int device_id{1};
  int angle_start_address{1546};
  int angle_count{6};
};

struct TopicHandConfig
{
  std::string left_cmd_topic;
  std::string right_cmd_topic;
  std::string left_state_topic;
  std::string right_state_topic;
};

struct HandProviderConfig
{
  HandType type{HandType::NONE};
  InspireHandConfig inspire;
  TopicHandConfig dex1;
  TopicHandConfig brainco;
};

class HandStateProvider
{
public:
  virtual ~HandStateProvider() = default;

  virtual const std::vector<std::string> & joint_names() const = 0;
  virtual size_t joint_count() const = 0;
  virtual bool update(HandStateData & state) = 0;
};

std::unique_ptr<HandStateProvider> create_hand_state_provider(
    rclcpp::Node & node,
    const HandProviderConfig & config);

std::string to_string(HandType hand_type);

}  // namespace teleop_server
