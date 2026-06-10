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

#include "pico/pico_data_receiver.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <vector>

#include <Eigen/Geometry>
#include <json/json.h>

namespace teleop_server
{
namespace
{
constexpr int kExpectedBodyJointCount = 24;

Eigen::Quaterniond normalized(Eigen::Quaterniond quat)
{
  if (quat.norm() <= 1e-9) {
    return Eigen::Quaterniond::Identity();
  }
  quat.normalize();
  return quat;
}

std::vector<double> parse_csv_doubles(const std::string & value)
{
  std::vector<double> result;
  std::stringstream stream(value);
  std::string token;
  while (std::getline(stream, token, ',')) {
    result.push_back(std::stod(token));
  }
  return result;
}

Json::Value parse_json_string(const char * raw_json)
{
  Json::Value root;
  Json::CharReaderBuilder builder;
  std::string errors;
  std::istringstream input(raw_json == nullptr ? "" : raw_json);
  if (!Json::parseFromStream(builder, input, &root, &errors)) {
    throw std::runtime_error("failed to parse XRoboToolkit JSON: " + errors);
  }
  return root;
}

std::string bounded_c_string(const char * value, size_t capacity)
{
  if (value == nullptr || capacity == 0) {
    return {};
  }
  return std::string(value, strnlen(value, capacity));
}

bool extract_tracking_value(const Json::Value & state_root, Json::Value * value)
{
  if (!state_root.isObject() || !state_root.isMember("value")) {
    return false;
  }

  Json::Value parsed = state_root["value"];
  if (parsed.isString()) {
    const std::string nested = parsed.asString();
    parsed = parse_json_string(nested.c_str());
  }
  if (!parsed.isObject()) {
    return false;
  }

  *value = std::move(parsed);
  return true;
}

bool parse_body_poses(const Json::Value & value, std::vector<Eigen::Isometry3d> * body_poses)
{
  if (!value.isObject() || !value.isMember("Body")) {
    return false;
  }

  const Json::Value & body = value["Body"];
  const Json::Value & joints = body["joints"];
  if (!joints.isArray() || static_cast<int>(joints.size()) < kExpectedBodyJointCount) {
    return false;
  }

  std::vector<Eigen::Isometry3d> parsed;
  parsed.reserve(kExpectedBodyJointCount);
  for (int i = 0; i < kExpectedBodyJointCount; ++i) {
    const Json::Value & joint = joints[i];
    if (!joint.isObject() || !joint.isMember("p")) {
      return false;
    }

    std::vector<double> values;
    if (joint["p"].isString()) {
      values = parse_csv_doubles(joint["p"].asString());
    } else if (joint["p"].isArray()) {
      for (const auto & entry : joint["p"]) {
        values.push_back(entry.asDouble());
      }
    } else {
      return false;
    }
    if (values.size() < 7) {
      return false;
    }

    Eigen::Isometry3d pose = Eigen::Isometry3d::Identity();
    pose.translation() = Eigen::Vector3d(values[0], values[1], values[2]);
    pose.linear() = normalized(Eigen::Quaterniond(values[6], values[3], values[4], values[5]))
                        .toRotationMatrix();
    parsed.push_back(pose);
  }

  *body_poses = std::move(parsed);
  return true;
}

bool parse_controller_input(
    const Json::Value & value,
    PicoControllerInput * input,
    int64_t * timestamp_ns)
{
  if (!value.isObject() || !value.isMember("Controller")) {
    return false;
  }

  const Json::Value & controller = value["Controller"];
  if (!controller.isObject()) {
    return false;
  }

  PicoControllerInput parsed;
  bool has_input = false;

  const Json::Value & left = controller["left"];
  if (left.isObject()) {
    parsed.trigger_l = left["trigger"].asDouble();
    parsed.grip_l = left["grip"].asDouble();
    parsed.axis_l[0] = left["axisX"].asDouble();
    parsed.axis_l[1] = left["axisY"].asDouble();
    parsed.X = left["primaryButton"].asBool();
    parsed.Y = left["secondaryButton"].asBool();
    has_input = true;
  }

  const Json::Value & right = controller["right"];
  if (right.isObject()) {
    parsed.trigger_r = right["trigger"].asDouble();
    parsed.grip_r = right["grip"].asDouble();
    parsed.axis_r[0] = right["axisX"].asDouble();
    parsed.axis_r[1] = right["axisY"].asDouble();
    parsed.A = right["primaryButton"].asBool();
    parsed.B = right["secondaryButton"].asBool();
    has_input = true;
  }

  if (!has_input) {
    return false;
  }

  *input = parsed;
  if (timestamp_ns != nullptr) {
    *timestamp_ns = value["timeStampNs"].asInt64();
  }
  return true;
}

bool read_int64(const Json::Value & value, int64_t * output)
{
  if (output == nullptr || value.isNull()) {
    return false;
  }

  if (value.isIntegral()) {
    *output = value.asInt64();
    return true;
  }
  if (value.isDouble()) {
    const double numeric = value.asDouble();
    if (!std::isfinite(numeric)) {
      return false;
    }
    *output = static_cast<int64_t>(numeric);
    return true;
  }
  if (value.isString()) {
    try {
      size_t consumed = 0;
      const int64_t parsed = std::stoll(value.asString(), &consumed);
      if (consumed == 0) {
        return false;
      }
      *output = parsed;
      return true;
    } catch (const std::exception &) {
      return false;
    }
  }
  return false;
}

int64_t extract_body_timestamp_ns(const Json::Value & value)
{
  int64_t timestamp_ns = 0;

  const Json::Value & body = value["Body"];
  if (body.isObject() && read_int64(body["timeStampNs"], &timestamp_ns) && timestamp_ns > 0) {
    return timestamp_ns;
  }

  if (read_int64(value["timeStampNs"], &timestamp_ns) && timestamp_ns > 0) {
    return timestamp_ns;
  }

  const Json::Value & joints = body["joints"];
  if (joints.isArray()) {
    for (const auto & joint : joints) {
      if (read_int64(joint["t"], &timestamp_ns) && timestamp_ns > 0) {
        return timestamp_ns;
      }
    }
  }

  return 0;
}

std::string tracking_payload_summary(const Json::Value & state_root)
{
  if (!state_root.isObject()) {
    return "root is not object";
  }

  std::ostringstream summary;
  if (state_root.isMember("functionName")) {
    summary << "functionName=" << state_root["functionName"].asString();
  } else {
    summary << "functionName=<missing>";
  }

  if (!state_root.isMember("value")) {
    summary << " value=<missing>";
    return summary.str();
  }

  Json::Value value = state_root["value"];
  if (value.isString()) {
    try {
      const std::string nested = value.asString();
      value = parse_json_string(nested.c_str());
    } catch (const std::exception &) {
      summary << " value=<invalid-json-string>";
      return summary.str();
    }
  }
  if (!value.isObject()) {
    summary << " value=<non-object>";
    return summary.str();
  }

  summary << " keys=[";
  bool first = true;
  for (const auto & name : value.getMemberNames()) {
    if (!first) {
      summary << ",";
    }
    summary << name;
    first = false;
  }
  summary << "]";

  if (value.isMember("Body")) {
    const Json::Value & body = value["Body"];
    const Json::Value & joints = body["joints"];
    summary << " body_joints=" << (joints.isArray() ? std::to_string(joints.size()) : "invalid");
  } else {
    summary << " body=<missing>";
  }

  if (value.isMember("Hand")) {
    const Json::Value & hand = value["Hand"];
    const int left_active = hand["leftHand"]["isActive"].asInt();
    const int right_active = hand["rightHand"]["isActive"].asInt();
    summary << " hand_active=" << left_active << "/" << right_active;
  }
  return summary.str();
}
}  // namespace

PicoDataReceiver::PicoDataReceiver(
    rclcpp::Node & node,
    Config config,
    PacketCallback packet_callback)
  : node_(node),
    config_(std::move(config)),
    packet_callback_(std::move(packet_callback))
{
  pose_82d_converter_ = std::make_unique<Pico82dConverter>(config_.pose_82d_hz);
  RCLCPP_INFO(node_.get_logger(), "Pico 82D converter enabled. hz=%.1f", config_.pose_82d_hz);

  const int init_result = PXREAInit(this, &PicoDataReceiver::on_pxrea_callback, PXREAFullMask);
  if (init_result != 0) {
    throw std::runtime_error("PXREAInit failed");
  }
  RCLCPP_INFO(node_.get_logger(), "PicoDataReceiver started.");
}

PicoDataReceiver::~PicoDataReceiver()
{
  PXREADeinit();
}

void PicoDataReceiver::on_pxrea_callback(
    void * context,
    PXREAClientCallbackType type,
    int status,
    void * user_data)
{
  auto * self = static_cast<PicoDataReceiver *>(context);
  if (self == nullptr) {
    return;
  }
  self->handle_pxrea_callback(type, status, user_data);
}

void PicoDataReceiver::handle_pxrea_callback(
    PXREAClientCallbackType type,
    int status,
    void * user_data)
{
  switch (type) {
    case PXREAServerConnect:
      RCLCPP_INFO(node_.get_logger(), "XRoboToolkit server connected");
      break;
    case PXREAServerDisconnect:
      RCLCPP_WARN(node_.get_logger(), "XRoboToolkit server disconnected");
      break;
    case PXREADeviceFind:
      RCLCPP_INFO(
          node_.get_logger(),
          "XRoboToolkit device found. status=%d user_data=%p",
          status,
          user_data);
      break;
    case PXREADeviceMissing:
      RCLCPP_WARN(
          node_.get_logger(),
          "XRoboToolkit device missing. status=%d user_data=%p",
          status,
          user_data);
      break;
    case PXREADeviceConnect:
      RCLCPP_INFO(
          node_.get_logger(),
          "XRoboToolkit device connected. status=%d user_data=%p",
          status,
          user_data);
      break;
    case PXREADeviceStateJson: {
      if (user_data == nullptr) {
        RCLCPP_WARN(node_.get_logger(), "XRoboToolkit device state JSON callback has null data");
        break;
      }
      const auto & state = *static_cast<PXREADevStateJson *>(user_data);
      const std::string state_json = bounded_c_string(state.stateJson, sizeof(state.stateJson));
      if (state_json.empty()) {
        RCLCPP_WARN(node_.get_logger(), "XRoboToolkit device state JSON is empty");
        break;
      }
      RCLCPP_INFO_ONCE(
          node_.get_logger(),
          "XRoboToolkit device state JSON stream started. devID=%s",
          state.devID);
      handle_device_state_json(state_json.c_str());
      break;
    }
    default:
      break;
  }
}

void PicoDataReceiver::handle_device_state_json(const char * state_json)
{
  try {
    const Json::Value root = parse_json_string(state_json);
    Json::Value tracking_value;
    if (!extract_tracking_value(root, &tracking_value)) {
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Pico tracking JSON has no usable value object: %s",
          tracking_payload_summary(root).c_str());
      return;
    }
    PicoControllerInput input;
    int64_t input_timestamp_ns = 0;
    const bool parsed_controller_input =
        parse_controller_input(tracking_value, &input, &input_timestamp_ns);
    bool has_controller_input = parsed_controller_input;
    if (!has_controller_input) {
      std::lock_guard<std::mutex> lock(frame_mutex_);
      if (has_latest_controller_input_) {
        input = latest_input_;
        input_timestamp_ns = latest_input_timestamp_ns_;
        has_controller_input = true;
      }
    }

    std::vector<Eigen::Isometry3d> body_poses;
    const bool has_body_poses = parse_body_poses(tracking_value, &body_poses);
    if (has_body_poses) {
      update_body_fps();
    }
    if (!has_body_poses) {
      if (has_controller_input) {
        std::lock_guard<std::mutex> lock(frame_mutex_);
        latest_input_ = input;
        latest_input_timestamp_ns_ = input_timestamp_ns;
        has_latest_controller_input_ = true;
      }
      if (packet_callback_ && has_controller_input) {
        PicoTeleopPacket packet;
        packet.input = input;
        packet.input_timestamp_ns = input_timestamp_ns;
        packet.has_controller_input = true;
        packet_callback_(packet);
      }
      ++skipped_body_frame_count_;
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Pico tracking JSON has no usable Body joints yet; skipped=%lu %s",
          skipped_body_frame_count_,
          tracking_payload_summary(root).c_str());
      return;
    }

    int64_t body_timestamp_ns = extract_body_timestamp_ns(tracking_value);
    if (body_timestamp_ns <= 0) {
      body_timestamp_ns = node_.get_clock()->now().nanoseconds();
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Pico Body timestamp is missing; using ROS clock timestamp");
    }
    if (parsed_controller_input) {
      std::lock_guard<std::mutex> lock(frame_mutex_);
      latest_input_ = input;
      latest_input_timestamp_ns_ = input_timestamp_ns;
      has_latest_controller_input_ = true;
    }

    std::optional<std::vector<double>> pose_82d;
    if (pose_82d_converter_) {
      pose_82d = pose_82d_converter_->process(body_poses, body_timestamp_ns);
    }

    if (pose_82d && packet_callback_) {
      PicoTeleopPacket packet;
      packet.pose_82d = std::move(*pose_82d);
      packet.has_pose_82d = packet.pose_82d.size() == 82;
      packet.input = input;
      packet.body_timestamp_ns = body_timestamp_ns;
      packet.input_timestamp_ns = input_timestamp_ns;
      packet.has_controller_input = has_controller_input;
      packet.pose_sequence = static_cast<int64_t>(++pose_82d_sequence_);
      packet_callback_(packet);
    }
    skipped_body_frame_count_ = 0;
  } catch (const std::exception & e) {
    RCLCPP_WARN_THROTTLE(
        node_.get_logger(),
        *node_.get_clock(),
        2000,
        "Failed to parse Pico body frame: %s",
        e.what());
  }
}

void PicoDataReceiver::update_body_fps()
{
  const int64_t now_ns = node_.get_clock()->now().nanoseconds();
  if (body_fps_window_start_ns_ <= 0) {
    body_fps_window_start_ns_ = now_ns;
  }

  ++body_fps_frame_count_;

  const int64_t elapsed_ns = now_ns - body_fps_window_start_ns_;
  if (elapsed_ns < 10000000000LL) {
    return;
  }

  const double elapsed_s = static_cast<double>(elapsed_ns) * 1.0e-9;
  const double body_fps = static_cast<double>(body_fps_frame_count_) / elapsed_s;
  RCLCPP_INFO(
      node_.get_logger(),
      "Pico Body receive fps: %.1f skipped_body=%lu",
      body_fps,
      skipped_body_frame_count_);

  body_fps_window_start_ns_ = now_ns;
  body_fps_frame_count_ = 0;
}
}  // namespace teleop_server
