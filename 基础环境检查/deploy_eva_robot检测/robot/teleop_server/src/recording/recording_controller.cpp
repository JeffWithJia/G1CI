#include "recording/recording_controller.hpp"

#include <chrono>
#include <exception>
#include <functional>

namespace teleop_server
{
RecordingController::RecordingController(rclcpp::Node & node) : node_(node)
{
  recording_control_client_ =
      node_.create_client<agent::srv::RecordingControl>("/recording_control");
  recording_status_client_ = node_.create_client<agent::srv::RecordingStatus>("/recording_status");
  task_status_client_ = node_.create_client<agent::srv::TaskStatus>("/task_status");

  RCLCPP_INFO(node_.get_logger(), "RecordingController started.");
}

void RecordingController::toggle_recording()
{
  if (!acquire_command_slot()) {
    RCLCPP_WARN_THROTTLE(
        node_.get_logger(),
        *node_.get_clock(),
        2000,
        "Ignore recording toggle because previous command is still in progress");
    return;
  }

  handle_toggle_recording_command();
}

void RecordingController::cancel_recording()
{
  if (!acquire_command_slot()) {
    RCLCPP_WARN_THROTTLE(
        node_.get_logger(),
        *node_.get_clock(),
        2000,
        "Ignore recording cancel because previous command is still in progress");
    return;
  }

  handle_cancel_recording_command();
}

bool RecordingController::acquire_command_slot()
{
  bool expected = false;
  return command_in_progress_.compare_exchange_strong(expected, true);
}

void RecordingController::release_command_slot()
{
  command_in_progress_.store(false);
}

void RecordingController::handle_toggle_recording_command()
{
  if (!recording_status_client_->wait_for_service(std::chrono::seconds(0))) {
    RCLCPP_WARN(node_.get_logger(), "Service /recording_status not available");
    release_command_slot();
    return;
  }

  auto request = std::make_shared<agent::srv::RecordingStatus::Request>();
  request->recording_id = "";
  recording_status_client_->async_send_request(request, [this](RecordingStatusFuture future) {
    try {
      const auto response = future.get();
      if (response == nullptr) {
        RCLCPP_WARN(node_.get_logger(), "Empty response from /recording_status");
        release_command_slot();
        return;
      }

      if (response->status == "recording") {
        const std::string current_recording_id = get_recording_id();
        send_recording_control_request(
            "finish",
            current_recording_id,
            get_task_id(),
            [this, current_recording_id](const RecordingControlResponse & control_response) {
              if (control_response->success) {
                RCLCPP_INFO(
                    node_.get_logger(),
                    "Recording finished successfully, recording_id=%s",
                    current_recording_id.c_str());
              } else {
                RCLCPP_WARN(
                    node_.get_logger(),
                    "Failed to finish recording: %s",
                    control_response->message.c_str());
              }
              release_command_slot();
            });
        return;
      }

      request_task_id_and_start_recording();
    } catch (const std::exception & e) {
      RCLCPP_ERROR(node_.get_logger(), "Failed to query /recording_status: %s", e.what());
      release_command_slot();
    }
  });
}

void RecordingController::handle_cancel_recording_command()
{
  if (!recording_status_client_->wait_for_service(std::chrono::seconds(0))) {
    RCLCPP_WARN(node_.get_logger(), "Service /recording_status not available");
    release_command_slot();
    return;
  }

  auto status_request = std::make_shared<agent::srv::RecordingStatus::Request>();
  status_request->recording_id = "";
  recording_status_client_->async_send_request(
      status_request,
      [this](RecordingStatusFuture future) {
        try {
          const auto status_response = future.get();
          if (status_response == nullptr) {
            RCLCPP_WARN(node_.get_logger(), "Empty response from /recording_status");
            release_command_slot();
            return;
          }
          if (status_response->status != "recording") {
            RCLCPP_WARN(
                node_.get_logger(),
                "Not currently recording, will still send cancel command");
          }

          const std::string current_recording_id = get_recording_id();
          send_recording_control_request(
              "cancel",
              current_recording_id,
              get_task_id(),
              [this, current_recording_id](const RecordingControlResponse & response) {
                if (!response->success) {
                  RCLCPP_WARN(
                      node_.get_logger(),
                      "Failed to cancel recording: %s",
                      response->message.c_str());
                } else {
                  RCLCPP_INFO(
                      node_.get_logger(),
                      "Recording canceled successfully, recording_id=%s",
                      current_recording_id.c_str());
                }
                std::lock_guard<std::mutex> lock(state_mutex_);
                recording_id_.clear();
                release_command_slot();
              });
        } catch (const std::exception & e) {
          RCLCPP_ERROR(
              node_.get_logger(),
              "Failed to query /recording_status for cancel: %s",
              e.what());
          release_command_slot();
        }
      });
}

void RecordingController::request_task_id_and_start_recording()
{
  if (!task_status_client_->wait_for_service(std::chrono::seconds(0))) {
    RCLCPP_WARN(node_.get_logger(), "Service /task_status not available");
    release_command_slot();
    return;
  }

  auto request = std::make_shared<agent::srv::TaskStatus::Request>();
  task_status_client_->async_send_request(request, [this](TaskStatusFuture future) {
    try {
      const auto task_response = future.get();
      if (task_response == nullptr) {
        RCLCPP_WARN(node_.get_logger(), "Empty response from /task_status");
        release_command_slot();
        return;
      }

      {
        std::lock_guard<std::mutex> lock(state_mutex_);
        if (!task_response->task_id.empty()) {
          task_id_ = task_response->task_id;
        }
      }

      send_recording_control_request(
          "start",
          "",
          get_task_id(),
          [this](const RecordingControlResponse & control_response) {
            if (!control_response->success) {
              RCLCPP_WARN(
                  node_.get_logger(),
                  "Failed to start recording: %s",
                  control_response->message.c_str());
              release_command_slot();
              return;
            }
            if (!control_response->recording_id.empty()) {
              std::lock_guard<std::mutex> lock(state_mutex_);
              recording_id_ = control_response->recording_id;
            }
            RCLCPP_INFO(
                node_.get_logger(),
                "Recording started successfully, recording_id=%s",
                control_response->recording_id.c_str());
            release_command_slot();
          });
    } catch (const std::exception & e) {
      RCLCPP_ERROR(node_.get_logger(), "Failed to query /task_status: %s", e.what());
      release_command_slot();
    }
  });
}

void RecordingController::send_recording_control_request(
    const std::string & command,
    const std::string & recording_id,
    const std::string & task_id,
    const std::function<void(const RecordingControlResponse &)> & on_response)
{
  if (!recording_control_client_->wait_for_service(std::chrono::seconds(0))) {
    RCLCPP_WARN(node_.get_logger(), "Service /recording_control not available");
    release_command_slot();
    return;
  }

  auto request = std::make_shared<agent::srv::RecordingControl::Request>();
  request->command = command;
  request->recording_id = recording_id;
  request->task_id = task_id;
  recording_control_client_->async_send_request(
      request,
      [this, command, on_response](RecordingControlFuture future) {
        try {
          const auto response = future.get();
          if (response == nullptr) {
            RCLCPP_WARN(
                node_.get_logger(),
                "Empty response from /recording_control for command=%s",
                command.c_str());
            release_command_slot();
            return;
          }
          on_response(response);
        } catch (const std::exception & e) {
          RCLCPP_ERROR(
              node_.get_logger(),
              "Failed to call /recording_control for command=%s: %s",
              command.c_str(),
              e.what());
          release_command_slot();
        }
      });
}

std::string RecordingController::get_recording_id()
{
  std::lock_guard<std::mutex> lock(state_mutex_);
  return recording_id_;
}

std::string RecordingController::get_task_id()
{
  std::lock_guard<std::mutex> lock(state_mutex_);
  return task_id_;
}
}  // namespace teleop_server
