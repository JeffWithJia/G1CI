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
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>

#include <Eigen/Geometry>
#include "PXREARobotSDK.h"

#include <rclcpp/node.hpp>
#include <rclcpp/timer.hpp>

#include "pico/pico_82d_converter.hpp"
#include "pico/pico_teleop_types.hpp"

namespace teleop_server {
class PicoDataReceiver {
public:
    using PacketCallback = std::function<void(const PicoTeleopPacket &)>;
    using DeviceIpCallback = std::function<void(const std::string &)>;
    using DeviceMissingCallback = std::function<void()>;

    struct Config {
        double pose_82d_hz{50.0};
    };

    PicoDataReceiver(
        rclcpp::Node &node,
        Config config,
        PacketCallback packet_callback = {},
        DeviceIpCallback device_ip_callback = {},
        DeviceMissingCallback device_missing_callback = {});
    ~PicoDataReceiver();

private:
    static void on_pxrea_callback(
        void *context,
        PXREAClientCallbackType type,
        int status,
        void *user_data);

    void handle_pxrea_callback(PXREAClientCallbackType type, int status, void *user_data);
    void handle_device_state_json(const char *state_json);
    void publish_pose_82d();
    void update_body_fps();
    void update_pose_output_fps();

    rclcpp::Node &node_;
    Config config_;
    std::string robot_sn_;
    PacketCallback packet_callback_;
    DeviceIpCallback device_ip_callback_;
    DeviceMissingCallback device_missing_callback_;
    std::mutex frame_mutex_;
    PicoControllerInput latest_input_;
    int64_t latest_input_timestamp_ns_{0};
    bool has_latest_controller_input_{false};
    std::array<double, 3> latest_pelvis_position_{0.0, 0.0, 0.0};
    bool has_latest_pelvis_position_{false};
    uint64_t pose_82d_sequence_{0};
    uint64_t skipped_body_frame_count_{0};
    uint64_t body_fps_frame_count_{0};
    int64_t body_fps_window_start_ns_{0};
    uint64_t pose_output_fps_frame_count_{0};
    int64_t pose_output_fps_window_start_ns_{0};
    Pico82dConverter::Diagnostics last_converter_diagnostics_;

    std::unique_ptr<Pico82dConverter> pose_82d_converter_;
    rclcpp::TimerBase::SharedPtr pose_82d_timer_;
};

}  // namespace teleop_server
