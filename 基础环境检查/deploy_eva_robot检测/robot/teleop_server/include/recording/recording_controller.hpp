#pragma once

#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <string>

#include <agent/srv/recording_control.hpp>
#include <agent/srv/recording_status.hpp>
#include <agent/srv/task_status.hpp>
#include <rclcpp/rclcpp.hpp>

namespace teleop_server
{
class RecordingController
{
public:
  enum class ToggleResult
  {
    SERVICE_UNAVAILABLE,
    STARTED,
    FINISHED,
  };

  using ToggleResultCallback = std::function<void(ToggleResult)>;

  explicit RecordingController(rclcpp::Node & node);
  void toggle_recording(ToggleResultCallback result_callback = {});

private:
  using RecordingControlResponse = agent::srv::RecordingControl::Response::SharedPtr;
  using RecordingStatusFuture = rclcpp::Client<agent::srv::RecordingStatus>::SharedFuture;
  using RecordingControlFuture = rclcpp::Client<agent::srv::RecordingControl>::SharedFuture;
  using TaskStatusFuture = rclcpp::Client<agent::srv::TaskStatus>::SharedFuture;

  bool acquire_command_slot();
  void release_command_slot();
  void handle_toggle_recording_command(ToggleResultCallback result_callback);
  void request_task_id_and_start_recording(ToggleResultCallback result_callback);
  void send_recording_control_request(
      const std::string & command,
      const std::string & recording_id,
      const std::string & task_id,
      const std::function<void(const RecordingControlResponse &)> & on_response,
      const std::function<void()> & on_failure);
  std::string get_recording_id();
  std::string get_task_id();

  rclcpp::Node & node_;
  std::mutex state_mutex_;
  std::atomic<bool> command_in_progress_{false};
  std::string recording_id_;
  std::string task_id_{"0000"};

  rclcpp::Client<agent::srv::RecordingControl>::SharedPtr recording_control_client_;
  rclcpp::Client<agent::srv::RecordingStatus>::SharedPtr recording_status_client_;
  rclcpp::Client<agent::srv::TaskStatus>::SharedPtr task_status_client_;
};
}  // namespace teleop_server
