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
#include <mutex>
#include <string>
#include <vector>

#include <mujoco/mujoco.h>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>

struct GLFWwindow;

namespace teleop_server
{

class PicoMujocoViewer : public rclcpp::Node
{
public:
  PicoMujocoViewer();

  ~PicoMujocoViewer() override;

private:
  using JointPositions = std::array<std::array<double, 3>, 24>;

  static std::vector<double> parse_pose_82d_json(const std::string & json);

  static JointPositions joints_from_pose_82d(const std::vector<double> & pose_82d);

  void on_pose_message(const std_msgs::msg::String::SharedPtr msg);

  void run_viewer_loop();

  void append_skeleton_geoms(const JointPositions & joints);

  bool copy_latest_joints(JointPositions * joints, uint64_t * sequence);

  void load_model(const std::string & model_path);

  void init_window(int width, int height);

  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr pose_sub_;
  rclcpp::TimerBase::SharedPtr viewer_timer_;
  std::mutex joints_mutex_;
  JointPositions latest_joints_{};
  uint64_t latest_sequence_{0};
  uint64_t rendered_sequence_{0};
  bool has_latest_joints_{false};

  mjModel * model_{nullptr};
  mjData * data_{nullptr};
  mjvScene scene_{};
  mjvOption option_{};
  mjvCamera camera_{};
  mjrContext render_context_{};
  GLFWwindow * window_{nullptr};
  bool glfw_initialized_{false};
};

}  // namespace teleop_server
