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

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include <rclcpp/node.hpp>
#include <rclcpp/publisher.hpp>
#include <rclcpp/subscription.hpp>
#include <unitree_api/msg/request.hpp>
#include <unitree_api/msg/response.hpp>

namespace teleop_server
{

class AudioClient
{
public:
  explicit AudioClient(
      rclcpp::Node & node,
      std::chrono::milliseconds timeout = std::chrono::seconds(5));

  int32_t tts_maker(const std::string & text, int32_t speaker_id);
  int32_t set_volume(uint8_t volume);
  int32_t get_volume(uint8_t * volume);
  int32_t led_control(uint8_t r, uint8_t g, uint8_t b);
  int32_t play_stream(
      const std::string & app_name,
      const std::string & stream_id,
      const std::vector<uint8_t> & pcm_data);
  int32_t play_stop(const std::string & app_name);

private:
  int32_t call(
      int64_t api_id,
      const std::string & parameter,
      const std::vector<uint8_t> & binary,
      std::string * response_data = nullptr);
  void on_response(const unitree_api::msg::Response::SharedPtr msg);
  int64_t next_request_id();

  rclcpp::Node & node_;
  std::chrono::milliseconds timeout_;
  rclcpp::Publisher<unitree_api::msg::Request>::SharedPtr request_pub_;
  rclcpp::Subscription<unitree_api::msg::Response>::SharedPtr response_sub_;
  uint32_t tts_index_{0};
  int64_t request_sequence_{0};
  std::mutex response_mutex_;
  std::condition_variable response_cv_;
  std::optional<unitree_api::msg::Response> matched_response_;
  int64_t pending_request_id_{0};
  bool has_pending_request_{false};
};

}  // namespace teleop_server
