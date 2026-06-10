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

#include "hands/hand_state_provider.hpp"

#include "hands/modbus_tcp_reader.hpp"

#include <algorithm>
#include <array>
#include <mutex>
#include <utility>

#include "unitree_go/msg/motor_states.hpp"

namespace teleop_server
{
namespace
{

constexpr int kInspireAngleActMin = 0;
constexpr int kInspireAngleActMax = 1000;
constexpr std::array<double, 6> kInspireJointUpperLimits = {
    1.4381,  // little_1
    1.4381,  // ring_1
    1.4381,  // middle_1
    1.4381,  // index_1
    0.5864,  // thumb_2
    1.1641   // thumb_1
};

const std::vector<std::string> kInspireJointNames = {
    "right_little_1_joint",
    "right_ring_1_joint",
    "right_middle_1_joint",
    "right_index_1_joint",
    "right_thumb_2_joint",
    "right_thumb_1_joint",
    "left_little_1_joint",
    "left_ring_1_joint",
    "left_middle_1_joint",
    "left_index_1_joint",
    "left_thumb_2_joint",
    "left_thumb_1_joint"};

const std::vector<std::string> kDex1JointNames = {"left_gripper_joint", "right_gripper_joint"};

const std::vector<std::string> kBraincoJointNames = {
    "left_hand_thumb_joint",
    "left_hand_thumb_aux_joint",
    "left_hand_index_joint",
    "left_hand_middle_joint",
    "left_hand_ring_joint",
    "left_hand_pinky_joint",
    "right_hand_thumb_joint",
    "right_hand_thumb_aux_joint",
    "right_hand_index_joint",
    "right_hand_middle_joint",
    "right_hand_ring_joint",
    "right_hand_pinky_joint"};

class NullHandStateProvider final : public HandStateProvider
{
public:
  const std::vector<std::string> & joint_names() const override { return joint_names_; }

  size_t joint_count() const override { return 0; }

  bool update(HandStateData & state) override
  {
    state.position.clear();
    state.velocity.clear();
    state.effort.clear();
    return true;
  }

private:
  std::vector<std::string> joint_names_;
};

class InspireHandStateProvider final : public HandStateProvider
{
public:
  InspireHandStateProvider(rclcpp::Node & node, InspireHandConfig config)
    : node_(node),
      config_(std::move(config)),
      right_reader_(
          config_.right_ip,
          static_cast<uint16_t>(config_.port),
          static_cast<uint8_t>(config_.device_id),
          node_.get_logger()),
      left_reader_(
          config_.left_ip,
          static_cast<uint16_t>(config_.port),
          static_cast<uint8_t>(config_.device_id),
          node_.get_logger())
  {
    if (config_.angle_count != static_cast<int>(kInspireJointUpperLimits.size())) {
      RCLCPP_WARN(
          node_.get_logger(),
          "Inspire angle_count is %d, expected 6. Joint names still describe the standard "
          "6-DoF hand.",
          config_.angle_count);
    }
  }

  const std::vector<std::string> & joint_names() const override { return kInspireJointNames; }

  size_t joint_count() const override { return kInspireJointNames.size(); }

  bool update(HandStateData & state) override
  {
    if (config_.angle_count <= 0) {
      RCLCPP_ERROR_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "inspire angle_count must be > 0");
      return false;
    }

    std::vector<int16_t> right_angles;
    std::vector<int16_t> left_angles;
    if (!right_reader_.read_holding_registers(
            static_cast<uint16_t>(config_.angle_start_address),
            static_cast<uint16_t>(config_.angle_count),
            right_angles)) {
      return false;
    }

    if (!left_reader_.read_holding_registers(
            static_cast<uint16_t>(config_.angle_start_address),
            static_cast<uint16_t>(config_.angle_count),
            left_angles)) {
      return false;
    }

    if (right_angles.size() != kInspireJointUpperLimits.size() ||
        left_angles.size() != kInspireJointUpperLimits.size()) {
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Unexpected Inspire register count: left=%zu right=%zu expected=6",
          left_angles.size(),
          right_angles.size());
      return false;
    }

    state.position.assign(joint_count(), 0.0);
    state.velocity.assign(joint_count(), 0.0);
    state.effort.assign(joint_count(), 0.0);

    for (size_t i = 0; i < kInspireJointUpperLimits.size(); ++i) {
      const auto right_angle_act =
          std::clamp(static_cast<int>(right_angles[i]), kInspireAngleActMin, kInspireAngleActMax);
      const auto left_angle_act =
          std::clamp(static_cast<int>(left_angles[i]), kInspireAngleActMin, kInspireAngleActMax);
      const double ratio_right =
          static_cast<double>(right_angle_act) / static_cast<double>(kInspireAngleActMax);
      const double ratio_left =
          static_cast<double>(left_angle_act) / static_cast<double>(kInspireAngleActMax);
      const double joint_upper = kInspireJointUpperLimits[i];
      state.position[i] = joint_upper - ratio_right * joint_upper;
      state.position[kInspireJointUpperLimits.size() + i] = joint_upper - ratio_left * joint_upper;
    }
    return true;
  }

private:
  rclcpp::Node & node_;
  InspireHandConfig config_;
  ModbusTcpReader right_reader_;
  ModbusTcpReader left_reader_;
};

class MotorStatesHandStateProvider : public HandStateProvider
{
public:
  MotorStatesHandStateProvider(
      rclcpp::Node & node,
      TopicHandConfig config,
      std::vector<std::string> joint_names,
      size_t motors_per_hand,
      std::string log_name)
    : node_(node),
      joint_names_(std::move(joint_names)),
      motors_per_hand_(motors_per_hand),
      log_name_(std::move(log_name))
  {
    const auto qos = rclcpp::QoS(10).best_effort().durability_volatile();
    left_state_sub_ = node_.create_subscription<unitree_go::msg::MotorStates>(
        config.left_state_topic,
        qos,
        [this](const unitree_go::msg::MotorStates::SharedPtr msg) {
          std::lock_guard<std::mutex> lock(state_mutex_);
          left_state_ = msg;
        });
    right_state_sub_ = node_.create_subscription<unitree_go::msg::MotorStates>(
        config.right_state_topic,
        qos,
        [this](const unitree_go::msg::MotorStates::SharedPtr msg) {
          std::lock_guard<std::mutex> lock(state_mutex_);
          right_state_ = msg;
        });

    RCLCPP_INFO(
        node_.get_logger(),
        "%s hand state provider listening on %s and %s",
        log_name_.c_str(),
        config.left_state_topic.c_str(),
        config.right_state_topic.c_str());
  }

  const std::vector<std::string> & joint_names() const override { return joint_names_; }

  size_t joint_count() const override { return joint_names_.size(); }

  bool update(HandStateData & state) override
  {
    unitree_go::msg::MotorStates::SharedPtr left_state;
    unitree_go::msg::MotorStates::SharedPtr right_state;
    {
      std::lock_guard<std::mutex> lock(state_mutex_);
      left_state = left_state_;
      right_state = right_state_;
    }

    if (left_state == nullptr || right_state == nullptr) {
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Waiting for %s hand states",
          log_name_.c_str());
      return false;
    }

    if (left_state->states.size() < motors_per_hand_ ||
        right_state->states.size() < motors_per_hand_) {
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Unexpected %s hand state size: left=%zu right=%zu expected>=%zu",
          log_name_.c_str(),
          left_state->states.size(),
          right_state->states.size(),
          motors_per_hand_);
      return false;
    }

    state.position.assign(joint_count(), 0.0);
    state.velocity.assign(joint_count(), 0.0);
    state.effort.assign(joint_count(), 0.0);

    for (size_t i = 0; i < motors_per_hand_; ++i) {
      state.position[i] = left_state->states[i].q;
      state.velocity[i] = left_state->states[i].dq;
      state.effort[i] = left_state->states[i].tau_est;

      const size_t right_index = motors_per_hand_ + i;
      state.position[right_index] = right_state->states[i].q;
      state.velocity[right_index] = right_state->states[i].dq;
      state.effort[right_index] = right_state->states[i].tau_est;
    }
    return true;
  }

private:
  rclcpp::Node & node_;
  std::vector<std::string> joint_names_;
  size_t motors_per_hand_;
  std::string log_name_;
  std::mutex state_mutex_;
  unitree_go::msg::MotorStates::SharedPtr left_state_;
  unitree_go::msg::MotorStates::SharedPtr right_state_;
  rclcpp::Subscription<unitree_go::msg::MotorStates>::SharedPtr left_state_sub_;
  rclcpp::Subscription<unitree_go::msg::MotorStates>::SharedPtr right_state_sub_;
};

}  // namespace

std::unique_ptr<HandStateProvider> create_hand_state_provider(
    rclcpp::Node & node,
    const HandProviderConfig & config)
{
  switch (config.type) {
    case HandType::NONE:
      return std::make_unique<NullHandStateProvider>();
    case HandType::INSPIRE:
      return std::make_unique<InspireHandStateProvider>(node, config.inspire);
    case HandType::DEX1:
      return std::make_unique<MotorStatesHandStateProvider>(
          node,
          config.dex1,
          kDex1JointNames,
          1,
          "Dex1");
    case HandType::BRAINCO:
      return std::make_unique<MotorStatesHandStateProvider>(
          node,
          config.brainco,
          kBraincoJointNames,
          6,
          "Brainco");
  }
  return std::make_unique<NullHandStateProvider>();
}

std::string to_string(HandType hand_type)
{
  switch (hand_type) {
    case HandType::NONE:
      return "none";
    case HandType::INSPIRE:
      return "inspire";
    case HandType::DEX1:
      return "dex1";
    case HandType::BRAINCO:
      return "brainco";
  }
  return "unknown";
}

}  // namespace teleop_server
