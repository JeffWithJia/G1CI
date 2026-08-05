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

#include "teleop_server.hpp"

#include "logging/logger.hpp"

#include <utility>

namespace teleop_server
{
namespace
{
const char * recording_result_tts(RecordingController::ToggleResult result)
{
  switch (result) {
    case RecordingController::ToggleResult::STARTED:
      return "开始录制";
    case RecordingController::ToggleResult::FINISHED:
      return "结束录制";
    case RecordingController::ToggleResult::SERVICE_UNAVAILABLE:
      return "服务不可用";
  }
  return "服务不可用";
}
}  // namespace

TeleopServer::TeleopServer(
    std::vector<CameraInfo> camera_infos,
    DEVICE_TYPE device_type,
    JointsPublisherConfig joints_config,
    PicoDataReceiver::Config pico_config,
    PicoTeleopSender::Config pico_teleop_config,
    HandProviderConfig hand_config)
  : Node("teleop_server")
{
  audio_client_ = std::make_unique<AudioClient>(*this);
  tts_thread_ = std::thread([this]() {
    tts_worker_loop();
  });
  camera_publisher_ = std::make_unique<CameraPublisher>(*this, std::move(camera_infos));
  joints_publisher_ =
      std::make_unique<JointsPublisher>(*this, device_type, std::move(joints_config));
  recording_controller_ = std::make_unique<RecordingController>(*this);
  pico_teleop_sender_ =
      std::make_unique<PicoTeleopSender>(
          *this,
          std::move(pico_teleop_config),
          std::move(hand_config),
          [this]() {
            if (recording_controller_) {
              recording_controller_->toggle_recording(
                  [this](RecordingController::ToggleResult result) {
                    enqueue_tts(recording_result_tts(result));
                  });
            } else {
              enqueue_tts("服务不可用");
            }
          },
          [this](const std::string & text) {
            enqueue_tts(text);
          });
  pico_data_receiver_ = std::make_unique<PicoDataReceiver>(
      *this,
      std::move(pico_config),
      [this](const PicoTeleopPacket & packet) {
        if (pico_teleop_sender_) {
          pico_teleop_sender_->process_packet(packet);
        }
      },
      [this](const std::string & pico_ip) {
        if (camera_publisher_) {
          camera_publisher_->set_pico_video_host(pico_ip);
        }
      });
}

TeleopServer::~TeleopServer()
{
  {
    std::lock_guard<std::mutex> lock(tts_mutex_);
    tts_stop_ = true;
  }
  tts_cv_.notify_one();
  if (tts_thread_.joinable()) {
    tts_thread_.join();
  }
}

void TeleopServer::enqueue_tts(std::string text)
{
  if (text.empty()) {
    return;
  }

  {
    std::lock_guard<std::mutex> lock(tts_mutex_);
    tts_queue_.push_back(std::move(text));
  }
  tts_cv_.notify_one();
}

void TeleopServer::tts_worker_loop()
{
  while (true) {
    std::string text;
    {
      std::unique_lock<std::mutex> lock(tts_mutex_);
      tts_cv_.wait(lock, [this]() {
        return tts_stop_ || !tts_queue_.empty();
      });
      if (tts_stop_ && tts_queue_.empty()) {
        return;
      }
      text = std::move(tts_queue_.front());
      tts_queue_.pop_front();
    }

    if (!audio_client_) {
      continue;
    }

    const int32_t ret = audio_client_->tts_maker(text, 0);
    if (ret != 0) {
      TELEOP_LOG_WARN("TTS failed. ret=%d text=%s", ret, text.c_str());
    }
  }
}
}  // namespace teleop_server
