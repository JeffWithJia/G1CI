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

namespace teleop_server
{
TeleopServer::TeleopServer(
    std::vector<CameraInfo> camera_infos,
    DEVICE_TYPE device_type,
    JointsPublisherConfig joints_config,
    PicoDataReceiver::Config pico_config,
    PicoTeleopSender::Config pico_teleop_config,
    HandProviderConfig hand_config)
  : Node("teleop_server")
{
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
              recording_controller_->toggle_recording();
            }
          });
  pico_data_receiver_ = std::make_unique<PicoDataReceiver>(
      *this,
      std::move(pico_config),
      [this](const PicoTeleopPacket & packet) {
        if (pico_teleop_sender_) {
          pico_teleop_sender_->process_packet(packet);
        }
      });
}
}  // namespace teleop_server
