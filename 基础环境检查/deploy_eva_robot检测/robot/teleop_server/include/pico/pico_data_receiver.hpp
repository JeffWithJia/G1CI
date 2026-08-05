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
#include <memory>
#include <mutex>
#include <string>

#include <Eigen/Geometry>
#include "PXREARobotSDK.h"

#include <rclcpp/node.hpp>

#include "pico/pico_82d_converter.hpp"
#include "pico/pico_teleop_types.hpp"

namespace teleop_server {
class PicoDataReceiver {
public:
    using PacketCallback = std::function<void(const PicoTeleopPacket &)>;
    using DeviceIpCallback = std::function<void(const std::string &)>;

    struct Config {
        double pose_82d_hz{50.0};
    };

    PicoDataReceiver(
        rclcpp::Node &node,
        Config config,
        PacketCallback packet_callback = {},
        DeviceIpCallback device_ip_callback = {});
    ~PicoDataReceiver();

private:
    static void on_pxrea_callback(
        void *context,
        PXREAClientCallbackType type,
        int status,
        void *user_data);

    void handle_pxrea_callback(PXREAClientCallbackType type, int status, void *user_data);
    void handle_device_state_json(const char *state_json);
    void update_body_fps();

    rclcpp::Node &node_;
    Config config_;
    std::string robot_sn_;
    PacketCallback packet_callback_;
    DeviceIpCallback device_ip_callback_;
    std::mutex frame_mutex_;
    PicoControllerInput latest_input_;
    int64_t latest_input_timestamp_ns_{0};
    bool has_latest_controller_input_{false};
    uint64_t pose_82d_sequence_{0};
    uint64_t skipped_body_frame_count_{0};
    uint64_t body_fps_frame_count_{0};
    int64_t body_fps_window_start_ns_{0};

    std::unique_ptr<Pico82dConverter> pose_82d_converter_;
};

}  // namespace teleop_server
