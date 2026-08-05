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

#include "pico/pico_teleop_sender.hpp"

#include "logging/logger.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <sstream>
#include <utility>

#include <geometry_msgs/msg/transform_stamped.hpp>
#include <json/json.h>

namespace teleop_server
{
namespace
{
constexpr int64_t kRobotApiIdLocoSetFsmId = 7101;
constexpr int64_t kRobotApiIdLocoSetVelocity = 7105;
constexpr double kDefaultDtS = 0.01;
constexpr double kAxisDeadzone = 0.05;
constexpr double kLocomotionVxScale = 0.8;
constexpr double kLocomotionVyScale = 0.7;
constexpr double kLocomotionVyawScale = 1.0;
constexpr double kMoveDurationS = 1.0;
constexpr double kGripperDeadzone = 0.05;
constexpr double kDex1OpenPosition = 5.5;
constexpr double kDex1GripTorqueLimit = -0.9;
constexpr double kDex1TorqueFilterAlpha = 0.85;
constexpr double kDex1LatchMaxPosition = 5.0;
constexpr int64_t kDex1TauCalibrationDurationNs = 1500000000LL;
constexpr size_t kPicoSmplFrameSize = 82;
constexpr size_t kInspireMotorCount = 6;
constexpr size_t kBraincoMotorCount = 6;

const std::array<int16_t, kInspireMotorCount> kInspireOpenPose801 = {900, 900, 900, 900, 800, 1000};
const std::array<int16_t, kInspireMotorCount> kInspireOpenPose505 = {900, 900, 900, 900, 800, 0};
const std::array<int16_t, kInspireMotorCount> kInspireClosePose = {300, 300, 300, 300, 400, 0};
const std::array<double, kBraincoMotorCount> kBraincoOpenPose801 = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
const std::array<double, kBraincoMotorCount> kBraincoOpenPose505 = {0.0, 0.8, 0.0, 0.0, 0.0, 0.0};
const std::array<double, kBraincoMotorCount> kBraincoClosePose = {0.8, 0.8, 0.8, 0.8, 0.8, 0.8};

Json::Value double_array(const std::vector<double> & values)
{
  Json::Value array(Json::arrayValue);
  for (const double value : values) {
    array.append(value);
  }
  return array;
}

Json::Value axis_array(const std::array<double, 2> & axis)
{
  Json::Value array(Json::arrayValue);
  array.append(axis[0]);
  array.append(axis[1]);
  return array;
}

Json::Value vector3_array(const std::array<double, 3> & value)
{
  Json::Value array(Json::arrayValue);
  array.append(value[0]);
  array.append(value[1]);
  array.append(value[2]);
  return array;
}

std::string compact_json(const Json::Value & root)
{
  Json::StreamWriterBuilder builder;
  builder["indentation"] = "";
  return Json::writeString(builder, root);
}

std::string fsm_parameter(int fsm_id)
{
  Json::Value root(Json::objectValue);
  root["data"] = fsm_id;
  return compact_json(root);
}

std::string velocity_parameter(double vx, double vy, double vyaw, double duration_s)
{
  Json::Value root(Json::objectValue);
  Json::Value velocity(Json::arrayValue);
  velocity.append(vx);
  velocity.append(vy);
  velocity.append(vyaw);
  root["velocity"] = std::move(velocity);
  root["duration"] = duration_s;
  return compact_json(root);
}

struct RootQuat
{
  double w{1.0};
  double x{0.0};
  double y{0.0};
  double z{0.0};
};

RootQuat sanitize_quat(const RootQuat & quat)
{
  const double norm = std::sqrt(
      quat.w * quat.w + quat.x * quat.x + quat.y * quat.y + quat.z * quat.z);
  if (!std::isfinite(norm) || norm < 1.0e-8) {
    return {};
  }
  return {quat.w / norm, quat.x / norm, quat.y / norm, quat.z / norm};
}

RootQuat conjugate(const RootQuat & quat)
{
  return {quat.w, -quat.x, -quat.y, -quat.z};
}

RootQuat multiply(const RootQuat & a, const RootQuat & b)
{
  return {
      a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
      a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
      a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
      a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}

RootQuat root_quat_from_pose(const std::vector<double> & pose_82d)
{
  if (pose_82d.size() < 76) {
    return {};
  }
  return sanitize_quat({pose_82d[72], pose_82d[73], pose_82d[74], pose_82d[75]});
}

double quat_wxyz_to_yaw(const std::vector<double> & pose_82d)
{
  const RootQuat q = root_quat_from_pose(pose_82d);
  return std::atan2(
      2.0 * (q.w * q.z + q.x * q.y),
      1.0 - 2.0 * (q.y * q.y + q.z * q.z));
}

double wrap_pi(double angle)
{
  double wrapped = std::fmod(angle + M_PI, 2.0 * M_PI);
  if (wrapped < 0.0) {
    wrapped += 2.0 * M_PI;
  }
  return wrapped - M_PI;
}

double clipped_ratio(double value)
{
  return std::clamp(value, 0.0, 1.0);
}

double with_deadzone(double value, double deadzone)
{
  return std::abs(value) < deadzone ? 0.0 : value;
}

// VR joystick axes are nominally normalized to [-1, 1]. Reject non-finite values
// (NaN/Inf would propagate to a robot velocity command) and clamp out-of-range input
// so a malformed packet cannot command an unbounded velocity on a real robot.
double sanitize_unit_axis(double value)
{
  return std::isfinite(value) ? std::clamp(value, -1.0, 1.0) : 0.0;
}

std::vector<int16_t> interpolate_inspire_pose(
    const std::array<int16_t, kInspireMotorCount> & open_pose,
    double ratio)
{
  const double clamped = clipped_ratio(ratio);
  std::vector<int16_t> command;
  command.reserve(kInspireMotorCount);
  for (size_t i = 0; i < kInspireMotorCount; ++i) {
    const double value =
        static_cast<double>(open_pose[i]) +
        static_cast<double>(kInspireClosePose[i] - open_pose[i]) * clamped;
    command.push_back(static_cast<int16_t>(std::lround(value)));
  }
  return command;
}

std::vector<double> interpolate_brainco_pose(
    const std::array<double, kBraincoMotorCount> & open_pose,
    double ratio)
{
  const double clamped = clipped_ratio(ratio);
  std::vector<double> command;
  command.reserve(kBraincoMotorCount);
  for (size_t i = 0; i < kBraincoMotorCount; ++i) {
    command.push_back(open_pose[i] + (kBraincoClosePose[i] - open_pose[i]) * clamped);
  }
  return command;
}

unitree_go::msg::MotorCmd make_dex1_motor_cmd(double position)
{
  unitree_go::msg::MotorCmd cmd;
  cmd.q = static_cast<float>(position);
  cmd.kp = 5.0F;
  cmd.kd = 0.05F;
  return cmd;
}

unitree_go::msg::MotorCmd make_brainco_motor_cmd(double position)
{
  unitree_go::msg::MotorCmd cmd;
  cmd.q = static_cast<float>(position);
  cmd.dq = 1.0F;
  return cmd;
}

unitree_go::msg::MotorCmds make_dex1_motor_cmds(double position)
{
  unitree_go::msg::MotorCmds msg;
  msg.cmds.push_back(make_dex1_motor_cmd(position));
  return msg;
}

unitree_go::msg::MotorCmds make_brainco_motor_cmds(const std::vector<double> & positions)
{
  unitree_go::msg::MotorCmds msg;
  msg.cmds.reserve(positions.size());
  for (const double position : positions) {
    msg.cmds.push_back(make_brainco_motor_cmd(position));
  }
  return msg;
}

double unwrap_near(double angle_wrapped, double reference_unwrapped)
{
  return reference_unwrapped + wrap_pi(angle_wrapped - reference_unwrapped);
}

void set_output_yaw(std::vector<double> * pose_82d, double yaw)
{
  if (pose_82d == nullptr || pose_82d->size() < 76) {
    return;
  }
  const double half = 0.5 * yaw;
  const RootQuat q = root_quat_from_pose(*pose_82d);
  const RootQuat q_twist = sanitize_quat({q.w, 0.0, 0.0, q.z});
  const RootQuat q_swing = multiply(conjugate(q_twist), q);
  const RootQuat q_output_yaw{std::cos(half), 0.0, 0.0, std::sin(half)};
  const RootQuat q_result = sanitize_quat(multiply(q_output_yaw, q_swing));
  (*pose_82d)[72] = q_result.w;
  (*pose_82d)[73] = q_result.x;
  (*pose_82d)[74] = q_result.y;
  (*pose_82d)[75] = q_result.z;
}

void set_root_orientation(
    const PicoTeleopPacket & packet,
    geometry_msgs::msg::TransformStamped * transform)
{
  if (transform == nullptr || !packet.has_pose_82d || packet.pose_82d.size() < 76) {
    return;
  }

  double w = packet.pose_82d[72];
  double x = packet.pose_82d[73];
  double y = packet.pose_82d[74];
  double z = packet.pose_82d[75];
  const double norm = std::sqrt(w * w + x * x + y * y + z * z);
  if (!std::isfinite(norm) || norm < 1.0e-8) {
    return;
  }

  w /= norm;
  x /= norm;
  y /= norm;
  z /= norm;
  transform->transform.rotation.x = x;
  transform->transform.rotation.y = y;
  transform->transform.rotation.z = z;
  transform->transform.rotation.w = w;
}
}  // namespace

PicoTeleopSender::PicoTeleopSender(
    rclcpp::Node & node,
    Config config,
    HandProviderConfig hand_config,
    RecordingToggleCallback recording_toggle_callback,
    VoicePromptCallback voice_prompt_callback)
  : node_(node),
    config_(std::move(config)),
    hand_config_(std::move(hand_config)),
    recording_toggle_callback_(std::move(recording_toggle_callback)),
    voice_prompt_callback_(std::move(voice_prompt_callback)),
    current_fsm_mode_(config_.locomotion_fsm_id)
{
  if (!config_.enable) {
    TELEOP_LOG_INFO("PicoTeleopSender disabled.");
    return;
  }

  if (config_.publish_packet_json) {
    packet_pub_ = node_.create_publisher<std_msgs::msg::String>(config_.packet_topic, 10);
  }
  teleop_cmd_pub_ = node_.create_publisher<std_msgs::msg::String>(config_.teleop_cmd_topic, 10);
  sport_request_pub_ =
      node_.create_publisher<unitree_api::msg::Request>(config_.sport_request_topic, 10);
  tf_broadcaster_ = std::make_unique<tf2_ros::TransformBroadcaster>(node_);
  const auto hand_qos = rclcpp::QoS(10).best_effort().durability_volatile();
  switch (hand_config_.type) {
    case HandType::INSPIRE:
      if (!hand_config_.inspire.left_cmd_topic.empty()) {
        inspire_left_cmd_pub_ = node_.create_publisher<std_msgs::msg::Int16MultiArray>(
            hand_config_.inspire.left_cmd_topic,
            hand_qos);
      }
      if (!hand_config_.inspire.right_cmd_topic.empty()) {
        inspire_right_cmd_pub_ = node_.create_publisher<std_msgs::msg::Int16MultiArray>(
            hand_config_.inspire.right_cmd_topic,
            hand_qos);
      }
      break;
    case HandType::DEX1:
      if (!hand_config_.dex1.left_cmd_topic.empty()) {
        left_motor_cmd_pub_ = node_.create_publisher<unitree_go::msg::MotorCmds>(
            hand_config_.dex1.left_cmd_topic,
            hand_qos);
      }
      if (!hand_config_.dex1.right_cmd_topic.empty()) {
        right_motor_cmd_pub_ = node_.create_publisher<unitree_go::msg::MotorCmds>(
            hand_config_.dex1.right_cmd_topic,
            hand_qos);
      }
      initialize_dex1_force_feedback();
      break;
    case HandType::BRAINCO:
      if (!hand_config_.brainco.left_cmd_topic.empty()) {
        left_motor_cmd_pub_ = node_.create_publisher<unitree_go::msg::MotorCmds>(
            hand_config_.brainco.left_cmd_topic,
            hand_qos);
      }
      if (!hand_config_.brainco.right_cmd_topic.empty()) {
        right_motor_cmd_pub_ = node_.create_publisher<unitree_go::msg::MotorCmds>(
            hand_config_.brainco.right_cmd_topic,
            hand_qos);
      }
      break;
    case HandType::NONE:
      break;
  }

  TELEOP_LOG_INFO("PicoTeleopSender enabled. packet_topic=%s teleop_cmd_topic=%s "
      "sport_request_topic=%s teleop=%d locomotion=%d hand_type=%s",
      config_.packet_topic.c_str(),
      config_.teleop_cmd_topic.c_str(),
      config_.sport_request_topic.c_str(),
      config_.teleop_fsm_id,
      config_.locomotion_fsm_id,
      to_string(hand_config_.type).c_str());
}

void PicoTeleopSender::process_packet(const PicoTeleopPacket & packet)
{
  if (!config_.enable) {
    return;
  }

  const double dt_s = compute_dt_s(packet);
  handle_vr_input(packet);
  publish_pelvis_tf(packet);

  publish_hand_command(packet);

  if (current_fsm_mode_ == config_.locomotion_fsm_id) {
    const double vx = apply_deadzone(
        sanitize_unit_axis(packet.input.axis_l[1]) * kLocomotionVxScale,
        kAxisDeadzone);
    const double vy = apply_deadzone(
        -sanitize_unit_axis(packet.input.axis_l[0]) * kLocomotionVyScale,
        kAxisDeadzone);
    const double vyaw = apply_deadzone(
        -sanitize_unit_axis(packet.input.axis_r[0]) * kLocomotionVyawScale,
        kAxisDeadzone);
    publish_velocity_request(vx, vy, vyaw);
  } else if (current_fsm_mode_ == config_.teleop_fsm_id) {
    publish_pico_smpl_command(packet);
    if (!packet.has_pose_82d && !teleop_inference_warning_printed_) {
      teleop_inference_warning_printed_ = true;
      TELEOP_LOG_WARN("Pico teleop mode is active, but no 82D pose is available yet.");
    }
  }

  publish_observation_packet(packet, dt_s);
}

void PicoTeleopSender::handle_vr_input(const PicoTeleopPacket & packet)
{
  const bool y_button_current_state = current_fsm_mode_ == config_.teleop_fsm_id && packet.input.Y;
  if (y_button_current_state && !y_button_pressed_last_frame_) {
    is_running_ = !is_running_;
    if (is_running_) {
      has_yaw_anchor_ = false;
      yaw_rel_unwrapped_ = 0.0;
      yaw_output_offset_ = last_output_yaw_;
      last_consumed_pose_sequence_ = packet.pose_sequence;
    } else {
      yaw_output_offset_ = last_output_yaw_;
    }
    TELEOP_LOG_INFO("Pico Y: %s motion tracking", is_running_ ? "resume" : "pause");
    play_voice_prompt(is_running_ ? "恢复运动追踪" : "暂停运动跟踪");
  }
  y_button_pressed_last_frame_ = y_button_current_state;

  const bool x_button_current_state = packet.input.X;
  if (x_button_current_state && !x_button_pressed_last_frame_) {
    if (packet.has_pelvis_position) {
      odom_ground_anchor_ = {
          packet.pelvis_position[0],
          packet.pelvis_position[1],
          0.0,
      };
      has_odom_ground_anchor_ = true;
      TELEOP_LOG_INFO("Pico X: reset odom anchor at pelvis ground projection x=%.3f y=%.3f",
          odom_ground_anchor_[0],
          odom_ground_anchor_[1]);
    } else {
      TELEOP_LOG_WARN("Pico X: cannot reset odom anchor because no pelvis position is available yet.");
    }

    if (current_fsm_mode_ == config_.teleop_fsm_id) {
      current_fsm_mode_ = config_.locomotion_fsm_id;
      has_yaw_anchor_ = false;
      yaw_rel_unwrapped_ = 0.0;
      yaw_output_offset_ = 0.0;
      last_publish_pose_.clear();
      publish_hand_mode_default_command();
    } else {
      current_fsm_mode_ = config_.teleop_fsm_id;
      has_yaw_anchor_ = false;
      yaw_rel_unwrapped_ = 0.0;
      yaw_output_offset_ = last_output_yaw_;
      publish_hand_mode_default_command();
    }
    last_consumed_pose_sequence_ = packet.pose_sequence;
    TELEOP_LOG_INFO("Pico X: switch FSM to %d", current_fsm_mode_);
    publish_fsm_request(current_fsm_mode_);
  }
  x_button_pressed_last_frame_ = x_button_current_state;

  bool record_button_current_state = packet.input.A;
  if (current_fsm_mode_ == config_.locomotion_fsm_id) {
    record_button_current_state = false;
  }
  if (current_fsm_mode_ == config_.teleop_fsm_id && record_button_current_state &&
      !a_button_pressed_last_frame_) {
    request_recording_toggle();
  }
  a_button_pressed_last_frame_ = record_button_current_state;
}

void PicoTeleopSender::publish_pelvis_tf(const PicoTeleopPacket & packet)
{
  if (!tf_broadcaster_ || !has_odom_ground_anchor_ || !packet.has_pelvis_position) {
    return;
  }

  geometry_msgs::msg::TransformStamped transform;
  transform.header.stamp = node_.get_clock()->now();
  transform.header.frame_id = "odom";
  transform.child_frame_id = "pelvis";
  transform.transform.translation.x = packet.pelvis_position[0] - odom_ground_anchor_[0];
  transform.transform.translation.y = packet.pelvis_position[1] - odom_ground_anchor_[1];
  transform.transform.translation.z = packet.pelvis_position[2];
  transform.transform.rotation.w = 1.0;
  set_root_orientation(packet, &transform);

  tf_broadcaster_->sendTransform(transform);
}

void PicoTeleopSender::publish_observation_packet(const PicoTeleopPacket & packet, double dt_s)
{
  if (!packet_pub_) {
    return;
  }

  Json::Value root(Json::objectValue);
  root["body_timestamp_ns"] = static_cast<Json::Int64>(packet.body_timestamp_ns);
  root["input_timestamp_ns"] = static_cast<Json::Int64>(packet.input_timestamp_ns);
  root["has_controller_input"] = packet.has_controller_input;
  root["dt_s"] = dt_s;
  root["pose_82d"] = double_array(packet.pose_82d);
  root["has_pose_82d"] = packet.has_pose_82d;
  root["pelvis_position"] = vector3_array(packet.pelvis_position);
  root["has_pelvis_position"] = packet.has_pelvis_position;
  root["pose_sequence"] = static_cast<Json::Int64>(packet.pose_sequence);

  Json::Value input(Json::objectValue);
  input["trigger_l"] = packet.input.trigger_l;
  input["trigger_r"] = packet.input.trigger_r;
  input["grip_l"] = packet.input.grip_l;
  input["grip_r"] = packet.input.grip_r;
  input["axis_l"] = axis_array(packet.input.axis_l);
  input["axis_r"] = axis_array(packet.input.axis_r);
  input["A"] = packet.input.A;
  input["B"] = packet.input.B;
  input["X"] = packet.input.X;
  input["Y"] = packet.input.Y;
  root["vr_input"] = std::move(input);

  Json::Value state(Json::objectValue);
  state["fsm_mode"] = current_fsm_mode_;
  state["is_running"] = is_running_;
  state["recording_toggle_requests"] = static_cast<Json::Int64>(recording_toggle_requests_);
  root["sender_state"] = std::move(state);

  std_msgs::msg::String msg;
  msg.data = compact_json(root);
  packet_pub_->publish(msg);
}

void PicoTeleopSender::publish_pico_smpl_command(const PicoTeleopPacket & packet)
{
  if (!teleop_cmd_pub_) {
    return;
  }

  if (is_running_ && packet.has_pose_82d && packet.pose_82d.size() == kPicoSmplFrameSize &&
      packet.pose_sequence != last_consumed_pose_sequence_) {
    last_consumed_pose_sequence_ = packet.pose_sequence;
    const double raw_yaw = quat_wxyz_to_yaw(packet.pose_82d);
    if (!has_yaw_anchor_) {
      has_yaw_anchor_ = true;
      yaw_anchor_ = raw_yaw;
      yaw_rel_unwrapped_ = 0.0;
      TELEOP_LOG_INFO("Pico yaw anchor: raw=%.2f deg offset=%.2f deg",
          raw_yaw * 180.0 / M_PI,
          yaw_output_offset_ * 180.0 / M_PI);
    } else {
      const double rel_wrapped = wrap_pi(raw_yaw - yaw_anchor_);
      yaw_rel_unwrapped_ = unwrap_near(rel_wrapped, yaw_rel_unwrapped_);
    }

    last_output_yaw_ = yaw_output_offset_ + yaw_rel_unwrapped_;
    last_publish_pose_ = packet.pose_82d;
    set_output_yaw(&last_publish_pose_, last_output_yaw_);
  }

  if (last_publish_pose_.size() != kPicoSmplFrameSize) {
    return;
  }

  Json::Value root(Json::objectValue);
  root["name"] = "pico_smpl";
  root["frame"] = double_array(last_publish_pose_);

  std_msgs::msg::String msg;
  msg.data = compact_json(root);
  teleop_cmd_pub_->publish(msg);
}

void PicoTeleopSender::publish_hand_command(const PicoTeleopPacket & packet)
{
  if (!is_running_ || current_fsm_mode_ != config_.teleop_fsm_id) {
    return;
  }

  double left_trigger = with_deadzone(packet.input.trigger_l, kGripperDeadzone);
  double right_trigger = with_deadzone(packet.input.trigger_r, kGripperDeadzone);
  left_trigger = clipped_ratio(left_trigger);
  right_trigger = clipped_ratio(right_trigger);

  switch (hand_config_.type) {
    case HandType::INSPIRE:
      publish_inspire_hand_command(left_trigger, right_trigger);
      break;
    case HandType::DEX1:
      publish_dex1_hand_command(
          (1.0 - left_trigger) * kDex1OpenPosition,
          (1.0 - right_trigger) * kDex1OpenPosition);
      break;
    case HandType::BRAINCO:
      publish_brainco_hand_command(left_trigger, right_trigger);
      break;
    case HandType::NONE:
      break;
  }
}

void PicoTeleopSender::publish_hand_mode_default_command()
{
  switch (hand_config_.type) {
    case HandType::INSPIRE:
      publish_inspire_hand_command(0.0, 0.0);
      break;
    case HandType::DEX1:
      publish_dex1_hand_command(
          current_fsm_mode_ == config_.teleop_fsm_id ? kDex1OpenPosition : 0.0,
          current_fsm_mode_ == config_.teleop_fsm_id ? kDex1OpenPosition : 0.0);
      break;
    case HandType::BRAINCO:
      publish_brainco_hand_command(0.0, 0.0);
      break;
    case HandType::NONE:
      break;
  }
}

void PicoTeleopSender::publish_inspire_hand_command(double left_ratio, double right_ratio)
{
  const auto & open_pose =
      current_fsm_mode_ == config_.locomotion_fsm_id ? kInspireOpenPose801 : kInspireOpenPose505;

  if (inspire_left_cmd_pub_) {
    std_msgs::msg::Int16MultiArray msg;
    msg.data = interpolate_inspire_pose(open_pose, left_ratio);
    inspire_left_cmd_pub_->publish(msg);
  }
  if (inspire_right_cmd_pub_) {
    std_msgs::msg::Int16MultiArray msg;
    msg.data = interpolate_inspire_pose(open_pose, right_ratio);
    inspire_right_cmd_pub_->publish(msg);
  }
}

void PicoTeleopSender::publish_dex1_hand_command(double left_position, double right_position)
{
  left_position = std::clamp(left_position, 0.0, kDex1OpenPosition);
  right_position = std::clamp(right_position, 0.0, kDex1OpenPosition);
  std::tie(left_position, right_position) =
      apply_dex1_grip_force_limit(left_position, right_position);

  if (left_motor_cmd_pub_) {
    left_motor_cmd_pub_->publish(make_dex1_motor_cmds(left_position));
  }
  if (right_motor_cmd_pub_) {
    right_motor_cmd_pub_->publish(make_dex1_motor_cmds(right_position));
  }
}

void PicoTeleopSender::initialize_dex1_force_feedback()
{
  const auto qos = rclcpp::QoS(10).best_effort().durability_volatile();
  if (!hand_config_.dex1.left_state_topic.empty()) {
    dex1_left_state_sub_ = node_.create_subscription<unitree_go::msg::MotorStates>(
        hand_config_.dex1.left_state_topic,
        qos,
        [this](const unitree_go::msg::MotorStates::SharedPtr msg) {
          update_dex1_left_state(msg);
        });
  }
  if (!hand_config_.dex1.right_state_topic.empty()) {
    dex1_right_state_sub_ = node_.create_subscription<unitree_go::msg::MotorStates>(
        hand_config_.dex1.right_state_topic,
        qos,
        [this](const unitree_go::msg::MotorStates::SharedPtr msg) {
          update_dex1_right_state(msg);
        });
  }
}

void PicoTeleopSender::update_dex1_left_state(const unitree_go::msg::MotorStates::SharedPtr & msg)
{
  if (msg == nullptr || msg->states.empty()) {
    return;
  }
  std::lock_guard<std::mutex> lock(dex1_state_mutex_);
  dex1_left_q_ = msg->states.front().q;
  dex1_left_tau_ = msg->states.front().tau_est;
  dex1_has_left_state_ = true;
}

void PicoTeleopSender::update_dex1_right_state(const unitree_go::msg::MotorStates::SharedPtr & msg)
{
  if (msg == nullptr || msg->states.empty()) {
    return;
  }
  std::lock_guard<std::mutex> lock(dex1_state_mutex_);
  dex1_right_q_ = msg->states.front().q;
  dex1_right_tau_ = msg->states.front().tau_est;
  dex1_has_right_state_ = true;
}

void PicoTeleopSender::update_dex1_tau_calibration_locked()
{
  if (!dex1_has_left_state_ || !dex1_has_right_state_ || dex1_tau_calibrated_) {
    return;
  }

  const int64_t now_ns = node_.get_clock()->now().nanoseconds();
  if (!dex1_tau_calibration_started_) {
    dex1_tau_calibration_started_ = true;
    dex1_tau_calibration_start_ns_ = now_ns;
    dex1_tau_calibration_samples_ = 0;
    dex1_tau0_left_sum_ = 0.0;
    dex1_tau0_right_sum_ = 0.0;
  }

  dex1_tau0_left_sum_ += dex1_left_tau_;
  dex1_tau0_right_sum_ += dex1_right_tau_;
  ++dex1_tau_calibration_samples_;

  if (now_ns - dex1_tau_calibration_start_ns_ >= kDex1TauCalibrationDurationNs &&
      dex1_tau_calibration_samples_ > 0) {
    dex1_tau0_left_ = dex1_tau0_left_sum_ / static_cast<double>(dex1_tau_calibration_samples_);
    dex1_tau0_right_ = dex1_tau0_right_sum_ / static_cast<double>(dex1_tau_calibration_samples_);
    dex1_left_tau_filtered_ = 0.0;
    dex1_right_tau_filtered_ = 0.0;
    dex1_tau_calibrated_ = true;
    TELEOP_LOG_INFO("Dex1 torque zero calibrated: left=%.3f right=%.3f samples=%ld",
        dex1_tau0_left_,
        dex1_tau0_right_,
        static_cast<long>(dex1_tau_calibration_samples_));
  }
}

std::pair<double, double> PicoTeleopSender::apply_dex1_grip_force_limit(
    double left_desired,
    double right_desired)
{
  std::lock_guard<std::mutex> lock(dex1_state_mutex_);
  if (!dex1_has_left_state_ || !dex1_has_right_state_) {
    return {left_desired, right_desired};
  }

  update_dex1_tau_calibration_locked();
  if (!dex1_tau_calibrated_) {
    return {left_desired, right_desired};
  }

  const double left_load = dex1_left_tau_ - dex1_tau0_left_;
  const double right_load = dex1_right_tau_ - dex1_tau0_right_;
  dex1_left_tau_filtered_ =
      kDex1TorqueFilterAlpha * dex1_left_tau_filtered_ +
      (1.0 - kDex1TorqueFilterAlpha) * left_load;
  dex1_right_tau_filtered_ =
      kDex1TorqueFilterAlpha * dex1_right_tau_filtered_ +
      (1.0 - kDex1TorqueFilterAlpha) * right_load;

  double left_position = left_desired;
  if (dex1_left_latch_pos_) {
    if (left_desired < *dex1_left_latch_pos_) {
      left_position = *dex1_left_latch_pos_;
    } else {
      dex1_left_latch_pos_.reset();
    }
  } else if (
      dex1_left_q_ < kDex1LatchMaxPosition && left_desired < dex1_left_q_ &&
      dex1_left_tau_filtered_ < kDex1GripTorqueLimit) {
    dex1_left_latch_pos_ = dex1_left_q_;
    left_position = dex1_left_q_;
  }

  double right_position = right_desired;
  if (dex1_right_latch_pos_) {
    if (right_desired < *dex1_right_latch_pos_) {
      right_position = *dex1_right_latch_pos_;
    } else {
      dex1_right_latch_pos_.reset();
    }
  } else if (
      dex1_right_q_ < kDex1LatchMaxPosition && right_desired < dex1_right_q_ &&
      dex1_right_tau_filtered_ < kDex1GripTorqueLimit) {
    dex1_right_latch_pos_ = dex1_right_q_;
    right_position = dex1_right_q_;
  }

  return {
      std::clamp(left_position, 0.0, kDex1OpenPosition),
      std::clamp(right_position, 0.0, kDex1OpenPosition)};
}

void PicoTeleopSender::publish_brainco_hand_command(double left_ratio, double right_ratio)
{
  const auto & open_pose =
      current_fsm_mode_ == config_.locomotion_fsm_id ? kBraincoOpenPose801 : kBraincoOpenPose505;

  if (left_motor_cmd_pub_) {
    left_motor_cmd_pub_->publish(
        make_brainco_motor_cmds(interpolate_brainco_pose(open_pose, left_ratio)));
  }
  if (right_motor_cmd_pub_) {
    right_motor_cmd_pub_->publish(
        make_brainco_motor_cmds(interpolate_brainco_pose(open_pose, right_ratio)));
  }
}

void PicoTeleopSender::request_recording_toggle()
{
  ++recording_toggle_requests_;
  if (!recording_toggle_callback_) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Pico A recording toggle ignored: no recording callback configured");
    play_voice_prompt("服务不可用");
    return;
  }

  TELEOP_LOG_INFO("Pico A: toggle recording via RecordingController");
  recording_toggle_callback_();
}

void PicoTeleopSender::play_voice_prompt(const std::string & text)
{
  if (voice_prompt_callback_) {
    voice_prompt_callback_(text);
  }
}

void PicoTeleopSender::publish_fsm_request(int fsm_id)
{
  if (!sport_request_pub_) {
    return;
  }

  sport_request_pub_->publish(make_sport_request(kRobotApiIdLocoSetFsmId, fsm_parameter(fsm_id)));
}

void PicoTeleopSender::publish_velocity_request(double vx, double vy, double vyaw)
{
  if (!sport_request_pub_) {
    return;
  }

  sport_request_pub_->publish(make_sport_request(
      kRobotApiIdLocoSetVelocity,
      velocity_parameter(vx, vy, vyaw, kMoveDurationS)));
}

unitree_api::msg::Request PicoTeleopSender::make_sport_request(
    int64_t api_id,
    const std::string & parameter)
{
  unitree_api::msg::Request request;
  request.header.identity.id = next_request_id();
  request.header.identity.api_id = api_id;
  request.header.policy.noreply = true;
  request.parameter = parameter;
  return request;
}

int64_t PicoTeleopSender::next_request_id()
{
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  const auto now_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
  return static_cast<int64_t>(now_ns + request_sequence_++);
}

double PicoTeleopSender::compute_dt_s(const PicoTeleopPacket & packet)
{
  if (!have_last_packet_timestamp_ || packet.body_timestamp_ns <= last_body_timestamp_ns_) {
    have_last_packet_timestamp_ = true;
    last_body_timestamp_ns_ = packet.body_timestamp_ns;
    return kDefaultDtS;
  }

  const double dt_s =
      static_cast<double>(packet.body_timestamp_ns - last_body_timestamp_ns_) * 1e-9;
  last_body_timestamp_ns_ = packet.body_timestamp_ns;
  return std::clamp(dt_s, 0.001, 0.1);
}

double PicoTeleopSender::apply_deadzone(double value, double deadzone)
{
  return std::abs(value) < deadzone ? 0.0 : value;
}

}  // namespace teleop_server
