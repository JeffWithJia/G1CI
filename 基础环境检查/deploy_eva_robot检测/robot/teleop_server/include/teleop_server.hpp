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
#include <memory>
#include <vector>

#include "camera/camera_publisher.hpp"
#include "joints/joints_publisher.h"
#include "pico/pico_data_receiver.hpp"
#include "pico/pico_teleop_sender.hpp"
#include "recording/recording_controller.hpp"

namespace teleop_server
{
class TeleopServer : public rclcpp::Node
{
public:
  explicit TeleopServer(
      std::vector<CameraInfo> camera_infos,
      DEVICE_TYPE device_type,
      JointsPublisherConfig joints_config,
      PicoDataReceiver::Config pico_config,
      PicoTeleopSender::Config pico_teleop_config,
      teleop_server::HandProviderConfig hand_config);

private:
  std::unique_ptr<CameraPublisher> camera_publisher_;
  std::unique_ptr<JointsPublisher> joints_publisher_;
  std::unique_ptr<RecordingController> recording_controller_;
  std::unique_ptr<PicoTeleopSender> pico_teleop_sender_;
  std::unique_ptr<PicoDataReceiver> pico_data_receiver_;
};
}  // namespace teleop_server
