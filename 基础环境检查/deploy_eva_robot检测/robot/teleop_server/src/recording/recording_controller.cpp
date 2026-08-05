#include "recording/recording_controller.hpp"

#include "logging/logger.hpp"

#include <chrono>
#include <exception>
#include <functional>
#include <utility>

namespace teleop_server
{
namespace
{
void notify_toggle_result(
    const RecordingController::ToggleResultCallback & callback,
    RecordingController::ToggleResult result)
{
  if (callback) {
    callback(result);
  }
}
}  // namespace

RecordingController::RecordingController(rclcpp::Node & node) : node_(node)
{
  recording_control_client_ =
      node_.create_client<agent::srv::RecordingControl>("/recording_control");
  recording_status_client_ = node_.create_client<agent::srv::RecordingStatus>("/recording_status");
  task_status_client_ = node_.create_client<agent::srv::TaskStatus>("/task_status");

  TELEOP_LOG_INFO("RecordingController started.");
}

void RecordingController::toggle_recording(ToggleResultCallback result_callback)
{
  if (!acquire_command_slot()) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Ignore recording toggle because previous command is still in progress");
    notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
    return;
  }

  handle_toggle_recording_command(std::move(result_callback));
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

void RecordingController::handle_toggle_recording_command(ToggleResultCallback result_callback)
{
  if (!recording_status_client_->wait_for_service(std::chrono::seconds(0))) {
    TELEOP_LOG_WARN("Service /recording_status not available");
    notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
    release_command_slot();
    return;
  }

  auto request = std::make_shared<agent::srv::RecordingStatus::Request>();
  request->recording_id = "";
  recording_status_client_->async_send_request(
      request,
      [this, result_callback](RecordingStatusFuture future) {
    try {
      const auto response = future.get();
      if (response == nullptr) {
        TELEOP_LOG_WARN("Empty response from /recording_status");
        notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
        release_command_slot();
        return;
      }

      if (response->status == "recording") {
        const std::string current_recording_id = get_recording_id();
        send_recording_control_request(
            "finish",
            current_recording_id,
            get_task_id(),
            [this, current_recording_id, result_callback](
                const RecordingControlResponse & control_response) {
              if (control_response->success) {
                TELEOP_LOG_INFO("Recording finished successfully, recording_id=%s",
                    current_recording_id.c_str());
                notify_toggle_result(result_callback, ToggleResult::FINISHED);
              } else {
                TELEOP_LOG_WARN("Failed to finish recording: %s",
                    control_response->message.c_str());
                notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
              }
              release_command_slot();
            },
            [result_callback]() {
              notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
            });
        return;
      }

      request_task_id_and_start_recording(result_callback);
    } catch (const std::exception & e) {
      TELEOP_LOG_ERROR("Failed to query /recording_status: %s", e.what());
      notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
      release_command_slot();
    }
  });
}

void RecordingController::request_task_id_and_start_recording(ToggleResultCallback result_callback)
{
  if (!task_status_client_->wait_for_service(std::chrono::seconds(0))) {
    TELEOP_LOG_WARN("Service /task_status not available");
    notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
    release_command_slot();
    return;
  }

  auto request = std::make_shared<agent::srv::TaskStatus::Request>();
  task_status_client_->async_send_request(request, [this, result_callback](TaskStatusFuture future) {
    try {
      const auto task_response = future.get();
      if (task_response == nullptr) {
        TELEOP_LOG_WARN("Empty response from /task_status");
        notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
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
          [this, result_callback](const RecordingControlResponse & control_response) {
            if (!control_response->success) {
              TELEOP_LOG_WARN("Failed to start recording: %s",
                  control_response->message.c_str());
              notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
              release_command_slot();
              return;
            }
            if (!control_response->recording_id.empty()) {
              std::lock_guard<std::mutex> lock(state_mutex_);
              recording_id_ = control_response->recording_id;
            }
            TELEOP_LOG_INFO("Recording started successfully, recording_id=%s",
                control_response->recording_id.c_str());
            notify_toggle_result(result_callback, ToggleResult::STARTED);
            release_command_slot();
          },
          [result_callback]() {
            notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
          });
    } catch (const std::exception & e) {
      TELEOP_LOG_ERROR("Failed to query /task_status: %s", e.what());
      notify_toggle_result(result_callback, ToggleResult::SERVICE_UNAVAILABLE);
      release_command_slot();
    }
  });
}

void RecordingController::send_recording_control_request(
    const std::string & command,
    const std::string & recording_id,
    const std::string & task_id,
    const std::function<void(const RecordingControlResponse &)> & on_response,
    const std::function<void()> & on_failure)
{
  if (!recording_control_client_->wait_for_service(std::chrono::seconds(0))) {
    TELEOP_LOG_WARN("Service /recording_control not available");
    if (on_failure) {
      on_failure();
    }
    release_command_slot();
    return;
  }

  auto request = std::make_shared<agent::srv::RecordingControl::Request>();
  request->command = command;
  request->recording_id = recording_id;
  request->task_id = task_id;
  recording_control_client_->async_send_request(
      request,
      [this, command, on_response, on_failure](RecordingControlFuture future) {
        try {
          const auto response = future.get();
          if (response == nullptr) {
            TELEOP_LOG_WARN("Empty response from /recording_control for command=%s",
                command.c_str());
            if (on_failure) {
              on_failure();
            }
            release_command_slot();
            return;
          }
          on_response(response);
        } catch (const std::exception & e) {
          TELEOP_LOG_ERROR("Failed to call /recording_control for command=%s: %s",
              command.c_str(),
              e.what());
          if (on_failure) {
            on_failure();
          }
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
