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

#include "audio/audio_client.hpp"

#include <algorithm>
#include <chrono>
#include <sstream>
#include <utility>

#include <json/json.h>
#include <rclcpp/subscription_factory.hpp>

namespace teleop_server
{
namespace
{
constexpr int64_t kAudioApiTts = 1001;
constexpr int64_t kAudioApiStartPlay = 1003;
constexpr int64_t kAudioApiStopPlay = 1004;
constexpr int64_t kAudioApiGetVolume = 1005;
constexpr int64_t kAudioApiSetVolume = 1006;
constexpr int64_t kAudioApiSetRgbLed = 1010;

constexpr int32_t kSuccess = 0;
constexpr int32_t kTimeout = -1101;
constexpr int32_t kParseError = -1102;

std::string compact_json(const Json::Value & root)
{
  Json::StreamWriterBuilder builder;
  builder["indentation"] = "";
  return Json::writeString(builder, root);
}

bool parse_json(const std::string & text, Json::Value * output)
{
  if (output == nullptr) {
    return false;
  }
  Json::CharReaderBuilder builder;
  std::string errors;
  std::istringstream input(text);
  return Json::parseFromStream(builder, input, output, &errors);
}
}  // namespace

AudioClient::AudioClient(rclcpp::Node & node, std::chrono::milliseconds timeout)
: node_(node),
  timeout_(timeout)
{
  request_pub_ =
      node_.create_publisher<unitree_api::msg::Request>("/api/voice/request", rclcpp::QoS(10));

  auto options = rclcpp::SubscriptionOptions();
  std::shared_ptr<
      rclcpp::topic_statistics::SubscriptionTopicStatistics<unitree_api::msg::Response>>
      topic_statistics;
  auto factory = rclcpp::create_subscription_factory<unitree_api::msg::Response>(
      [this](const unitree_api::msg::Response::SharedPtr msg) {
        on_response(msg);
      },
      options,
      rclcpp::message_memory_strategy::MessageMemoryStrategy<
          unitree_api::msg::Response,
          std::allocator<void>>::create_default(),
      topic_statistics);
  auto node_topics_interface = node_.get_node_topics_interface();
  auto response_sub_base =
      node_topics_interface->create_subscription("/api/voice/response", factory, rclcpp::QoS(10));
  node_topics_interface->add_subscription(response_sub_base, options.callback_group);
  response_sub_ =
      std::dynamic_pointer_cast<rclcpp::Subscription<unitree_api::msg::Response>>(
          response_sub_base);
}

int32_t AudioClient::tts_maker(const std::string & text, int32_t speaker_id)
{
  Json::Value root(Json::objectValue);
  root["index"] = static_cast<Json::UInt>(tts_index_++);
  root["text"] = text;
  root["speaker_id"] = speaker_id;
  return call(kAudioApiTts, compact_json(root), {});
}

int32_t AudioClient::set_volume(uint8_t volume)
{
  Json::Value root(Json::objectValue);
  root["volume"] = static_cast<int>(volume);
  return call(kAudioApiSetVolume, compact_json(root), {});
}

int32_t AudioClient::get_volume(uint8_t * volume)
{
  std::string response_data;
  const int32_t ret = call(kAudioApiGetVolume, "{}", {}, &response_data);
  if (ret != kSuccess) {
    return ret;
  }

  Json::Value root;
  if (!parse_json(response_data, &root) || !root.isMember("volume")) {
    return kParseError;
  }
  if (volume != nullptr) {
    const int parsed = root["volume"].asInt();
    *volume = static_cast<uint8_t>(std::clamp(parsed, 0, 255));
  }
  return kSuccess;
}

int32_t AudioClient::led_control(uint8_t r, uint8_t g, uint8_t b)
{
  Json::Value root(Json::objectValue);
  root["R"] = static_cast<int>(r);
  root["G"] = static_cast<int>(g);
  root["B"] = static_cast<int>(b);
  return call(kAudioApiSetRgbLed, compact_json(root), {});
}

int32_t AudioClient::play_stream(
    const std::string & app_name,
    const std::string & stream_id,
    const std::vector<uint8_t> & pcm_data)
{
  Json::Value root(Json::objectValue);
  root["app_name"] = app_name;
  root["stream_id"] = stream_id;
  return call(kAudioApiStartPlay, compact_json(root), pcm_data);
}

int32_t AudioClient::play_stop(const std::string & app_name)
{
  Json::Value root(Json::objectValue);
  root["app_name"] = app_name;
  return call(kAudioApiStopPlay, compact_json(root), {});
}

int32_t AudioClient::call(
    int64_t api_id,
    const std::string & parameter,
    const std::vector<uint8_t> & binary,
    std::string * response_data)
{
  unitree_api::msg::Request request;
  request.header.identity.id = next_request_id();
  request.header.identity.api_id = api_id;
  request.parameter = parameter;
  request.binary = binary;

  {
    std::lock_guard<std::mutex> lock(response_mutex_);
    pending_request_id_ = request.header.identity.id;
    matched_response_.reset();
    has_pending_request_ = true;
  }

  request_pub_->publish(request);

  std::unique_lock<std::mutex> lock(response_mutex_);
  const bool ready = response_cv_.wait_for(lock, timeout_, [&]() {
    return matched_response_.has_value();
  });
  if (!ready) {
    has_pending_request_ = false;
    return kTimeout;
  }

  const auto response = *matched_response_;
  has_pending_request_ = false;
  matched_response_.reset();
  if (response.header.status.code != kSuccess) {
    return response.header.status.code;
  }
  if (response_data != nullptr) {
    *response_data = response.data;
  }
  return kSuccess;
}

void AudioClient::on_response(const unitree_api::msg::Response::SharedPtr msg)
{
  if (msg == nullptr) {
    return;
  }

  {
    std::lock_guard<std::mutex> lock(response_mutex_);
    if (!has_pending_request_ || msg->header.identity.id != pending_request_id_) {
      return;
    }
    matched_response_ = *msg;
  }
  response_cv_.notify_one();
}

int64_t AudioClient::next_request_id()
{
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  const auto now_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
  return static_cast<int64_t>(now_ns + request_sequence_++);
}

}  // namespace teleop_server
