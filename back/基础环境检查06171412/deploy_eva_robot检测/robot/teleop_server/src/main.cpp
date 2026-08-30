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
#include "version.hpp"

#include "logging/logger.hpp"

#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include <yaml-cpp/yaml.h>

namespace
{
CameraType parse_camera_type(const YAML::Node & node)
{
  if (!node) {
    return CameraType::V4L2;
  }
  if (node.IsScalar()) {
    const std::string value = node.as<std::string>();
    if (value == "V4L2") {
      return CameraType::V4L2;
    }
    if (value == "REALSENSE") {
      return CameraType::REALSENSE;
    }
  }
  throw std::invalid_argument("camera camera_type must be V4L2 or REALSENSE");
}

CompressType parse_compress_type(const YAML::Node & node)
{
  if (!node) {
    throw std::invalid_argument("camera compress_type is required");
  }
  if (node.IsScalar()) {
    const std::string value = node.as<std::string>();
    if (value == "NONE_COMPRESS") {
      return CompressType::NONE_COMPRESS;
    }
    if (value == "MJPEG_COMPRESS") {
      return CompressType::MJPEG_COMPRESS;
    }
  }
  throw std::invalid_argument("camera compress_type must be NONE_COMPRESS or MJPEG_COMPRESS");
}

DEVICE_TYPE parse_device_type(const YAML::Node & node)
{
  if (!node || !node.IsScalar()) {
    throw std::invalid_argument("device_type is required");
  }
  const std::string value = node.as<std::string>();
  if (value == "G1_29_DOF") {
    return DEVICE_TYPE::G1_29_DOF;
  }
  if (value == "G1_23_DOF") {
    return DEVICE_TYPE::G1_23_DOF;
  }
  if (value == "G1_DUAL_ARM") {
    return DEVICE_TYPE::G1_DUAL_ARM;
  }
  throw std::invalid_argument("device_type must be one of G1_29_DOF, G1_23_DOF, G1_DUAL_ARM");
}

teleop_server::HandType parse_hand_type(const YAML::Node & node)
{
  if (!node || !node.IsScalar()) {
    return teleop_server::HandType::NONE;
  }

  const std::string value = node.as<std::string>();
  if (value == "none" || value == "NONE") {
    return teleop_server::HandType::NONE;
  }
  if (value == "inspire" || value == "INSPIRE") {
    return teleop_server::HandType::INSPIRE;
  }
  if (value == "dex1" || value == "DEX1") {
    return teleop_server::HandType::DEX1;
  }
  if (value == "brainco" || value == "BRAINCO") {
    return teleop_server::HandType::BRAINCO;
  }
  throw std::invalid_argument("joints hand_type must be one of none, inspire, dex1, brainco");
}

teleop_server::PicoVideoStreamerConfig load_pico_video_config(const YAML::Node & root)
{
  teleop_server::PicoVideoStreamerConfig config;
  const auto pico_node = root["pico"];
  if (!pico_node || !pico_node.IsMap()) {
    return config;
  }

  const auto video_node = pico_node["video"];
  if (!video_node || !video_node.IsMap()) {
    return config;
  }

  config.drop_old = video_node["drop_old"].as<bool>(config.drop_old);
  return config;
}

std::vector<CameraInfo> load_camera_infos(const YAML::Node & root)
{
  const auto cameras_node = root["cameras"];
  if (!cameras_node || !cameras_node.IsSequence() || cameras_node.size() == 0) {
    throw std::invalid_argument("cameras must be a non-empty sequence");
  }

  const teleop_server::PicoVideoStreamerConfig pico_video_config = load_pico_video_config(root);

  std::vector<CameraInfo> camera_infos;
  camera_infos.reserve(cameras_node.size());
  for (const auto & camera_node : cameras_node) {
    CameraInfo camera_info;
    camera_info.camera_type = parse_camera_type(camera_node["camera_type"]);
    camera_info.device_name = camera_node["device_name"].as<std::string>("");
    camera_info.topic_name = camera_node["topic_name"].as<std::string>();
    camera_info.image_width = camera_node["image_width"].as<int>();
    camera_info.image_height = camera_node["image_height"].as<int>();
    camera_info.frame_rate = camera_node["frame_rate"].as<int>();
    camera_info.compress_type = parse_compress_type(camera_node["compress_type"]);
    camera_info.flip = camera_node["flip"] ? camera_node["flip"].as<bool>() : false;
    camera_info.enable_pico_video = camera_node["enable_pico_video"].as<bool>(false);
    camera_info.pico_video = pico_video_config;
    camera_infos.push_back(std::move(camera_info));
  }
  return camera_infos;
}

JointsPublisherConfig load_joints_config(const YAML::Node & joints_node)
{
  if (!joints_node || !joints_node.IsMap()) {
    throw std::invalid_argument("joints config is required");
  }

  JointsPublisherConfig config;
  config.hand_provider.type = parse_hand_type(joints_node["hand_type"]);

  const auto inspire_node = joints_node["inspire"];
  if (inspire_node && inspire_node.IsMap()) {
    config.hand_provider.inspire.right_ip = inspire_node["right_ip"].as<std::string>("");
    config.hand_provider.inspire.left_ip = inspire_node["left_ip"].as<std::string>("");
    config.hand_provider.inspire.left_cmd_topic =
        inspire_node["left_cmd_topic"].as<std::string>("/inspire_hand/ctrl/l");
    config.hand_provider.inspire.right_cmd_topic =
        inspire_node["right_cmd_topic"].as<std::string>("/inspire_hand/ctrl/r");
    config.hand_provider.inspire.port = inspire_node["port"].as<int>(6000);
    config.hand_provider.inspire.device_id = inspire_node["device_id"].as<int>(1);
    config.hand_provider.inspire.angle_start_address =
        inspire_node["angle_start_address"].as<int>(1546);
    config.hand_provider.inspire.angle_count = inspire_node["angle_count"].as<int>(6);
  } else {
    config.hand_provider.inspire.right_ip = joints_node["inspire_right_ip"].as<std::string>("");
    config.hand_provider.inspire.left_ip = joints_node["inspire_left_ip"].as<std::string>("");
    config.hand_provider.inspire.left_cmd_topic =
        joints_node["inspire_left_cmd_topic"].as<std::string>("/inspire_hand/ctrl/l");
    config.hand_provider.inspire.right_cmd_topic =
        joints_node["inspire_right_cmd_topic"].as<std::string>("/inspire_hand/ctrl/r");
    config.hand_provider.inspire.port = joints_node["inspire_port"].as<int>(6000);
    config.hand_provider.inspire.device_id = joints_node["inspire_device_id"].as<int>(1);
    config.hand_provider.inspire.angle_start_address =
        joints_node["inspire_angle_start_address"].as<int>(1546);
    config.hand_provider.inspire.angle_count = joints_node["inspire_angle_count"].as<int>(6);
  }

  const auto dex1_node = joints_node["dex1"];
  config.hand_provider.dex1.left_cmd_topic = "/dex1/left/cmd";
  config.hand_provider.dex1.right_cmd_topic = "/dex1/right/cmd";
  config.hand_provider.dex1.left_state_topic = "/dex1/left/state";
  config.hand_provider.dex1.right_state_topic = "/dex1/right/state";
  if (dex1_node && dex1_node.IsMap()) {
    if (dex1_node["left_cmd_topic"]) {
      config.hand_provider.dex1.left_cmd_topic = dex1_node["left_cmd_topic"].as<std::string>();
    }
    if (dex1_node["right_cmd_topic"]) {
      config.hand_provider.dex1.right_cmd_topic = dex1_node["right_cmd_topic"].as<std::string>();
    }
    if (dex1_node["left_state_topic"]) {
      config.hand_provider.dex1.left_state_topic = dex1_node["left_state_topic"].as<std::string>();
    }
    if (dex1_node["right_state_topic"]) {
      config.hand_provider.dex1.right_state_topic =
          dex1_node["right_state_topic"].as<std::string>();
    }
  }

  const auto brainco_node = joints_node["brainco"];
  config.hand_provider.brainco.left_cmd_topic = "/brainco/left/cmd";
  config.hand_provider.brainco.right_cmd_topic = "/brainco/right/cmd";
  config.hand_provider.brainco.left_state_topic = "/brainco/left/state";
  config.hand_provider.brainco.right_state_topic = "/brainco/right/state";
  if (brainco_node && brainco_node.IsMap()) {
    if (brainco_node["left_cmd_topic"]) {
      config.hand_provider.brainco.left_cmd_topic =
          brainco_node["left_cmd_topic"].as<std::string>();
    }
    if (brainco_node["right_cmd_topic"]) {
      config.hand_provider.brainco.right_cmd_topic =
          brainco_node["right_cmd_topic"].as<std::string>();
    }
    if (brainco_node["left_state_topic"]) {
      config.hand_provider.brainco.left_state_topic =
          brainco_node["left_state_topic"].as<std::string>();
    }
    if (brainco_node["right_state_topic"]) {
      config.hand_provider.brainco.right_state_topic =
          brainco_node["right_state_topic"].as<std::string>();
    }
  }
  return config;
}

teleop_server::PicoDataReceiver::Config load_pico_config(const YAML::Node & root)
{
  teleop_server::PicoDataReceiver::Config config;
  const auto pico_node = root["pico"];
  if (!pico_node || !pico_node.IsMap()) {
    return config;
  }

  const auto receiver_node = pico_node["receiver"];
  if (receiver_node && receiver_node.IsMap()) {
    config.pose_82d_hz = receiver_node["pose_82d_hz"].as<double>(50.0);
    return config;
  }

  config.pose_82d_hz = pico_node["pose_82d_hz"].as<double>(50.0);
  return config;
}

teleop_server::PicoTeleopSender::Config load_pico_teleop_config(const YAML::Node & root)
{
  teleop_server::PicoTeleopSender::Config config;
  const auto pico_node = root["pico"];
  if (!pico_node || !pico_node.IsMap()) {
    return config;
  }

  const auto teleop_node = pico_node["teleop"];
  if (!teleop_node || !teleop_node.IsMap()) {
    return config;
  }

  config.enable = teleop_node["enable"].as<bool>(false);
  config.publish_packet_json = teleop_node["publish_packet_json"].as<bool>(true);
  config.packet_topic = teleop_node["packet_topic"].as<std::string>("/pico/teleop_packet");
  config.teleop_cmd_topic = teleop_node["teleop_cmd_topic"].as<std::string>("/fsm/teleop/cmd");
  config.sport_request_topic =
      teleop_node["sport_request_topic"].as<std::string>("/api/sport/request");
  config.teleop_fsm_id = teleop_node["teleop_fsm_id"].as<int>(505);
  config.locomotion_fsm_id = teleop_node["locomotion_fsm_id"].as<int>(801);
  return config;
}
}  // namespace

int main(int argc, char * argv[])
{
  const std::string start_info = "GIT HASH: %s\033[94m"
    "\n╔═════════════════════════════════════════════════════════════════════════╗"
    "\n║                  Teleop Server Started - version: %s                 ║"
    "\n╚═════════════════════════════════════════════════════════════════════════╝\033[0m";
  TELEOP_LOG_INFO(start_info.c_str(), teleop_server::TELEOP_GIT_HASH, teleop_server::TELEOP_VERSION);

  std::vector<CameraInfo> camera_infos;
  DEVICE_TYPE device_type;
  JointsPublisherConfig joints_config;
  teleop_server::PicoDataReceiver::Config pico_config;
  teleop_server::PicoTeleopSender::Config pico_teleop_config;

  try {
    const auto non_ros_args = rclcpp::remove_ros_arguments(argc, argv);
    if (non_ros_args.size() < 2) {
      throw std::invalid_argument("Usage: teleop_server_node <config_yaml_path>");
    }

    const std::string config_path = non_ros_args[1];
    const YAML::Node config = YAML::LoadFile(config_path);
    camera_infos = load_camera_infos(config);
    device_type = parse_device_type(config["device_type"]);
    joints_config = load_joints_config(config["joints"]);
    pico_config = load_pico_config(config);
    pico_teleop_config = load_pico_teleop_config(config);
  } catch (const std::exception & e) {
    TELEOP_LOG_ERROR("Failed to load teleop_server config: %s", e.what());
    return 1;
  }

  rclcpp::init(argc, argv);
  try {
    auto node = std::make_shared<teleop_server::TeleopServer>(
        camera_infos,
        device_type,
        joints_config,
        pico_config,
        pico_teleop_config,
        joints_config.hand_provider);
    rclcpp::spin(std::move(node));
  } catch (const std::exception & e) {
    TELEOP_LOG_ERROR("Failed to start teleop_server: %s", e.what());
    rclcpp::shutdown();
    return 1;
  }
  rclcpp::shutdown();
  return 0;
}
