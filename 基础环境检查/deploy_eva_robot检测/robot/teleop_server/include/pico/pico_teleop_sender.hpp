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

#include <cstdint>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <utility>

#include <rclcpp/node.hpp>
#include <rclcpp/publisher.hpp>
#include <rclcpp/subscription.hpp>
#include <std_msgs/msg/int16_multi_array.hpp>
#include <std_msgs/msg/string.hpp>
#include <unitree_api/msg/request.hpp>
#include <unitree_go/msg/motor_cmds.hpp>
#include <unitree_go/msg/motor_states.hpp>

#include "hands/hand_state_provider.hpp"
#include "pico/pico_teleop_types.hpp"

namespace teleop_server
{

class PicoTeleopSender
{
public:
  struct Config
  {
    bool enable{false};
    bool publish_packet_json{true};
    std::string packet_topic{"/pico/teleop_packet"};
    std::string teleop_cmd_topic{"/fsm/teleop/cmd"};
    std::string sport_request_topic{"/api/sport/request"};
    int teleop_fsm_id{505};
    int locomotion_fsm_id{801};
  };

  using RecordingToggleCallback = std::function<void()>;

  PicoTeleopSender(
      rclcpp::Node & node,
      Config config,
      HandProviderConfig hand_config,
      RecordingToggleCallback recording_toggle_callback = {});

  void process_packet(const PicoTeleopPacket & packet);

private:
  void handle_vr_input(const PicoTeleopPacket & packet);
  void publish_observation_packet(const PicoTeleopPacket & packet, double dt_s);
  void publish_pico_smpl_command(const PicoTeleopPacket & packet);
  void publish_hand_command(const PicoTeleopPacket & packet);
  void publish_hand_mode_default_command();
  void publish_inspire_hand_command(double left_ratio, double right_ratio);
  void publish_dex1_hand_command(double left_position, double right_position);
  void publish_brainco_hand_command(double left_ratio, double right_ratio);
  void initialize_dex1_force_feedback();
  void update_dex1_left_state(const unitree_go::msg::MotorStates::SharedPtr & msg);
  void update_dex1_right_state(const unitree_go::msg::MotorStates::SharedPtr & msg);
  void update_dex1_tau_calibration_locked();
  std::pair<double, double> apply_dex1_grip_force_limit(
      double left_desired,
      double right_desired);
  void request_recording_toggle();
  void publish_fsm_request(int fsm_id);
  void publish_velocity_request(double vx, double vy, double vyaw);
  unitree_api::msg::Request make_sport_request(int64_t api_id, const std::string & parameter);
  int64_t next_request_id();
  double compute_dt_s(const PicoTeleopPacket & packet);
  static double apply_deadzone(double value, double deadzone);

  rclcpp::Node & node_;
  Config config_;
  HandProviderConfig hand_config_;
  RecordingToggleCallback recording_toggle_callback_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr packet_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr teleop_cmd_pub_;
  rclcpp::Publisher<unitree_api::msg::Request>::SharedPtr sport_request_pub_;
  rclcpp::Publisher<std_msgs::msg::Int16MultiArray>::SharedPtr inspire_left_cmd_pub_;
  rclcpp::Publisher<std_msgs::msg::Int16MultiArray>::SharedPtr inspire_right_cmd_pub_;
  rclcpp::Publisher<unitree_go::msg::MotorCmds>::SharedPtr left_motor_cmd_pub_;
  rclcpp::Publisher<unitree_go::msg::MotorCmds>::SharedPtr right_motor_cmd_pub_;
  rclcpp::Subscription<unitree_go::msg::MotorStates>::SharedPtr dex1_left_state_sub_;
  rclcpp::Subscription<unitree_go::msg::MotorStates>::SharedPtr dex1_right_state_sub_;

  bool is_running_{true};
  bool y_button_pressed_last_frame_{false};
  bool x_button_pressed_last_frame_{false};
  bool a_button_pressed_last_frame_{false};
  bool have_last_packet_timestamp_{false};
  bool teleop_inference_warning_printed_{false};
  int current_fsm_mode_{801};
  double yaw_anchor_{0.0};
  double yaw_rel_unwrapped_{0.0};
  double yaw_output_offset_{0.0};
  double last_output_yaw_{0.0};
  int64_t last_consumed_pose_sequence_{-1};
  bool has_yaw_anchor_{false};
  std::vector<double> last_publish_pose_;
  int64_t last_body_timestamp_ns_{0};
  int64_t request_sequence_{0};
  int64_t recording_toggle_requests_{0};

  std::mutex dex1_state_mutex_;
  bool dex1_has_left_state_{false};
  bool dex1_has_right_state_{false};
  double dex1_left_q_{0.0};
  double dex1_right_q_{0.0};
  double dex1_left_tau_{0.0};
  double dex1_right_tau_{0.0};
  bool dex1_tau_calibration_started_{false};
  bool dex1_tau_calibrated_{false};
  int64_t dex1_tau_calibration_start_ns_{0};
  int64_t dex1_tau_calibration_samples_{0};
  double dex1_tau0_left_sum_{0.0};
  double dex1_tau0_right_sum_{0.0};
  double dex1_tau0_left_{0.0};
  double dex1_tau0_right_{0.0};
  double dex1_left_tau_filtered_{0.0};
  double dex1_right_tau_filtered_{0.0};
  std::optional<double> dex1_left_latch_pos_;
  std::optional<double> dex1_right_latch_pos_;
};

}  // namespace teleop_server
