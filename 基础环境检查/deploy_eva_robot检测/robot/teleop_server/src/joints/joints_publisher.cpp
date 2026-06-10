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

#include "joints/joints_publisher.h"

#include <algorithm>
#include <utility>
#include <vector>

JointsPublisher::JointsPublisher(
    rclcpp::Node & node,
    DEVICE_TYPE device_type,
    JointsPublisherConfig config)
  : node_(node),
    device_type_(device_type)
{
  switch (device_type) {
    case DEVICE_TYPE::G1_29_DOF:
      body_joint_count_ = G1_29_DOF_JOINTS.size();
      joint_names_ = G1_29_DOF_JOINTS;
      break;
    case DEVICE_TYPE::G1_23_DOF:
      body_joint_count_ = G1_23_DOF_JOINTS.size();
      joint_names_ = G1_23_DOF_JOINTS;
      break;
    case DEVICE_TYPE::G1_DUAL_ARM:
      RCLCPP_WARN(
          node_.get_logger(),
          "G1_DUAL_ARM joint publishing is not implemented; only configured hand joints "
          "will be published.");
      body_joint_count_ = 0;
      break;
  }

  hand_state_provider_ = teleop_server::create_hand_state_provider(node_, config.hand_provider);
  if (hand_state_provider_ != nullptr) {
    const auto & hand_joint_names = hand_state_provider_->joint_names();
    joint_names_.insert(joint_names_.end(), hand_joint_names.begin(), hand_joint_names.end());
    hand_state_.position.resize(hand_state_provider_->joint_count(), 0.0);
    hand_state_.velocity.resize(hand_state_provider_->joint_count(), 0.0);
    hand_state_.effort.resize(hand_state_provider_->joint_count(), 0.0);
  }

  joints_count_ = joint_names_.size();
  joints_position_.resize(joints_count_, 0.0);
  joints_velocity_.resize(joints_count_, 0.0);
  joints_effort_.resize(joints_count_, 0.0);

  RCLCPP_INFO(
      node_.get_logger(),
      "JointsPublisher started, body joints: %zu, hand type: %s, total joints: %zu",
      body_joint_count_,
      teleop_server::to_string(config.hand_provider.type).c_str(),
      joints_count_);
  const auto pub_qos = rclcpp::QoS(10).reliable();
  joint_state_pub_ = node_.create_publisher<sensor_msgs::msg::JointState>("/joint_states", pub_qos);
  publish_flag_ = 0;

  initialize_lowstate_pipeline();
}

JointsPublisher::~JointsPublisher() = default;

void JointsPublisher::initialize_lowstate_pipeline()
{
  const std::string topic = "/lowstate";
  const auto sub_qos = get_qos_from_topic(topic);
  low_state_sub_ = node_.create_subscription<unitree_hg::msg::LowState>(
      topic,
      sub_qos,
      [this](unitree_hg::msg::LowState::SharedPtr msg) {
        const auto flag = publish_flag_ % 10;
        if (flag == 0) {
          publish_flag_ = 0;
          for (size_t i = 0; i < body_joint_count_; ++i) {
            joints_position_[i] = msg->motor_state[i].q;
            joints_velocity_[i] = msg->motor_state[i].dq;
            joints_effort_[i] = msg->motor_state[i].tau_est;
          }
          update_from_hand_provider();
          publish_joint_state();
        }
        publish_flag_++;
      });
}

void JointsPublisher::update_from_hand_provider()
{
  if (hand_state_provider_ == nullptr) {
    return;
  }

  if (!hand_state_provider_->update(hand_state_)) {
    return;
  }

  if (hand_state_.position.size() != hand_state_provider_->joint_count()) {
    RCLCPP_WARN_THROTTLE(
        node_.get_logger(),
        *node_.get_clock(),
        2000,
        "Hand provider returned %zu positions, expected %zu",
        hand_state_.position.size(),
        hand_state_provider_->joint_count());
    return;
  }

  const size_t hand_joint_offset = body_joint_count_;
  for (size_t i = 0; i < hand_state_.position.size(); ++i) {
    const size_t joint_index = hand_joint_offset + i;
    joints_position_[joint_index] = hand_state_.position[i];
    joints_velocity_[joint_index] = i < hand_state_.velocity.size() ? hand_state_.velocity[i] : 0.0;
    joints_effort_[joint_index] = i < hand_state_.effort.size() ? hand_state_.effort[i] : 0.0;
  }
}

void JointsPublisher::publish_joint_state()
{
  auto joint_states = sensor_msgs::msg::JointState();
  joint_states.header.stamp = node_.now();
  joint_states.name = joint_names_;
  joint_states.position = joints_position_;
  joint_states.velocity = joints_velocity_;
  joint_states.effort = joints_effort_;
  joint_state_pub_->publish(joint_states);
}

rclcpp::QoS JointsPublisher::get_qos_from_topic(const std::string & topic) const
{
  size_t depth = 0;
  size_t reliability_reliable_endpoints_count = 0;
  size_t durability_transient_local_endpoints_count = 0;

  const auto publisher_info = node_.get_publishers_info_by_topic(topic);
  if (publisher_info.empty()) {
    // Use the most compatible profile while waiting for publishers to appear.
    return rclcpp::QoS(rclcpp::KeepLast(DEFAULT_MIN_QOS_DEPTH)).best_effort().durability_volatile();
  }

  for (const auto & publisher : publisher_info) {
    const auto & qos = publisher.qos_profile();
    if (qos.get_rmw_qos_profile().reliability == RMW_QOS_POLICY_RELIABILITY_RELIABLE) {
      ++reliability_reliable_endpoints_count;
    }
    if (qos.get_rmw_qos_profile().durability == RMW_QOS_POLICY_DURABILITY_TRANSIENT_LOCAL) {
      ++durability_transient_local_endpoints_count;
    }
    const size_t publisher_history_depth = std::max(1ul, qos.get_rmw_qos_profile().depth);
    depth = depth + publisher_history_depth;
  }

  depth = std::max(depth, static_cast<size_t>(DEFAULT_MIN_QOS_DEPTH));
  if (depth > DEFAULT_MAX_QOS_DEPTH) {
    RCLCPP_WARN(
        node_.get_logger(),
        "Limiting history depth for topic '%s' to %zu (was %zu). You may want to increase "
        "the max_qos_depth parameter value.",
        topic.c_str(),
        DEFAULT_MAX_QOS_DEPTH,
        depth);
    depth = DEFAULT_MAX_QOS_DEPTH;
  }

  rclcpp::QoS qos{rclcpp::KeepLast(depth)};

  if (reliability_reliable_endpoints_count > 0 &&
      reliability_reliable_endpoints_count == publisher_info.size()) {
    qos.reliable();
  } else {
    if (reliability_reliable_endpoints_count > 0) {
      RCLCPP_INFO(
          node_.get_logger(),
          "Some, but not all, publishers on topic '%s' are offering QoSReliabilityPolicy.RELIABLE."
          "Falling back to QoSReliabilityPolicy.BEST_EFFORT as it will connect to all publishers",
          topic.c_str());
    }
    qos.best_effort();
  }

  // If all endpoints are transient_local, ask for transient_local
  if (durability_transient_local_endpoints_count > 0 &&
      durability_transient_local_endpoints_count == publisher_info.size()) {
    qos.transient_local();
  } else {
    if (durability_transient_local_endpoints_count > 0) {
      RCLCPP_INFO(
          node_.get_logger(),
          "Some, but not all, publishers on topic '%s' are offering "
          "QoSDurabilityPolicy.TRANSIENT_LOCAL. Falling back to "
          "QoSDurabilityPolicy.VOLATILE as it will connect to all publishers",
          topic.c_str());
    }
    qos.durability_volatile();
  }
  return qos;
}
