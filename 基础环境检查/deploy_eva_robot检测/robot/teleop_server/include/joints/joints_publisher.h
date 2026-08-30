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

#include <rclcpp/rclcpp.hpp>
#include <string>
#include <memory>
#include <vector>
#include <utility>
#include "unitree_hg/msg/low_state.hpp"
#include "sensor_msgs/msg/joint_state.hpp"
#include "hands/hand_state_provider.hpp"

enum struct DEVICE_TYPE
{
  G1_29_DOF,
  G1_23_DOF,
  G1_DUAL_ARM,
};

const std::vector<std::string> G1_29_DOF_JOINTS = {
    "left_hip_pitch_joint",      "left_hip_roll_joint",        "left_hip_yaw_joint",
    "left_knee_joint",           "left_ankle_pitch_joint",     "left_ankle_roll_joint",
    "right_hip_pitch_joint",     "right_hip_roll_joint",       "right_hip_yaw_joint",
    "right_knee_joint",          "right_ankle_pitch_joint",    "right_ankle_roll_joint",
    "waist_yaw_joint",           "waist_roll_joint",           "waist_pitch_joint",
    "left_shoulder_pitch_joint", "left_shoulder_roll_joint",   "left_shoulder_yaw_joint",
    "left_elbow_joint",          "left_wrist_roll_joint",      "left_wrist_pitch_joint",
    "left_wrist_yaw_joint",      "right_shoulder_pitch_joint", "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",  "right_elbow_joint",          "right_wrist_roll_joint",
    "right_wrist_pitch_joint",   "right_wrist_yaw_joint"};

const std::vector<std::string> G1_23_DOF_JOINTS = {
    "left_hip_pitch_joint",
    "left_hip_roll_joint",
    "left_hip_yaw_joint",
    "left_knee_joint",
    "left_ankle_pitch_joint",
    "left_ankle_roll_joint",
    "right_hip_pitch_joint",
    "right_hip_roll_joint",
    "right_hip_yaw_joint",
    "right_knee_joint",
    "right_ankle_pitch_joint",
    "right_ankle_roll_joint",
    "waist_yaw_joint",
    "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint",
    "left_shoulder_yaw_joint",
    "left_elbow_joint",
    "left_wrist_roll_joint",
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint"};

constexpr int64_t DEFAULT_MIN_QOS_DEPTH = 100;
constexpr int64_t DEFAULT_MAX_QOS_DEPTH = 3000;

struct JointsPublisherConfig
{
  teleop_server::HandProviderConfig hand_provider;
};

class JointsPublisher
{
public:
  explicit JointsPublisher(
      rclcpp::Node & node,
      DEVICE_TYPE device_type,
      JointsPublisherConfig config);
  ~JointsPublisher();

private:
  rclcpp::QoS get_qos_from_topic(const std::string & topic) const;
  void publish_joint_state();
  void initialize_lowstate_pipeline();
  void update_from_hand_provider();

  std::shared_ptr<rclcpp::Subscription<unitree_hg::msg::LowState>> low_state_sub_;
  std::shared_ptr<rclcpp::Publisher<sensor_msgs::msg::JointState>> joint_state_pub_;

  std::vector<std::string> joint_names_;
  std::vector<double> joints_position_;
  std::vector<double> joints_velocity_;
  std::vector<double> joints_effort_;
  size_t body_joint_count_{0};
  size_t joints_count_{0};
  uint16_t publish_flag_;
  teleop_server::HandStateData hand_state_;
  std::unique_ptr<teleop_server::HandStateProvider> hand_state_provider_;

  rclcpp::Node & node_;
  DEVICE_TYPE device_type_;
};
