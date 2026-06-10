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

#include "sim/pico_mujoco_viewer.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <utility>

#include <GLFW/glfw3.h>
#include <json/json.h>

namespace teleop_server
{
namespace
{
constexpr size_t kPose82dSize = 82;
constexpr size_t kJointCount = 24;

const std::array<std::pair<int, int>, 23> kSkeletonEdges = {
    std::pair{0, 1},   std::pair{0, 2},   std::pair{0, 3},   std::pair{1, 4},
    std::pair{2, 5},   std::pair{3, 6},   std::pair{4, 7},   std::pair{5, 8},
    std::pair{6, 9},   std::pair{7, 10},  std::pair{8, 11},  std::pair{9, 12},
    std::pair{9, 13},  std::pair{9, 14},  std::pair{12, 15}, std::pair{13, 16},
    std::pair{14, 17}, std::pair{16, 18}, std::pair{17, 19}, std::pair{18, 20},
    std::pair{19, 21}, std::pair{20, 22}, std::pair{21, 23}};

Json::Value parse_json(const std::string & text)
{
  Json::Value root;
  Json::CharReaderBuilder builder;
  std::string errors;
  std::istringstream input(text);
  if (!Json::parseFromStream(builder, input, &root, &errors)) {
    throw std::runtime_error("failed to parse 82D JSON: " + errors);
  }
  return root;
}

bool append_double_array(const Json::Value & value, std::vector<double> * out)
{
  if (!value.isArray()) {
    return false;
  }
  out->clear();
  out->reserve(value.size());
  for (const auto & entry : value) {
    if (!entry.isNumeric()) {
      return false;
    }
    out->push_back(entry.asDouble());
  }
  return true;
}

void add_sphere(mjvScene * scene, const std::array<double, 3> & point, const float rgba[4])
{
  if (scene == nullptr || scene->ngeom >= scene->maxgeom) {
    return;
  }

  mjtNum size[3] = {0.025, 0.025, 0.025};
  mjtNum pos[3] = {point[0], point[1], point[2]};
  mjtNum mat[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
  mjvGeom * geom = &scene->geoms[scene->ngeom++];
  mjv_initGeom(geom, mjGEOM_SPHERE, size, pos, mat, rgba);
}

void add_capsule(
    mjvScene * scene,
    const std::array<double, 3> & from,
    const std::array<double, 3> & to,
    const float rgba[4])
{
  if (scene == nullptr || scene->ngeom >= scene->maxgeom) {
    return;
  }

  mjtNum size[3] = {0.012, 0.0, 0.0};
  mjtNum pos[3] = {0, 0, 0};
  mjtNum mat[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
  mjvGeom * geom = &scene->geoms[scene->ngeom++];
  mjv_initGeom(geom, mjGEOM_CAPSULE, size, pos, mat, rgba);

  const mjtNum start[3] = {from[0], from[1], from[2]};
  const mjtNum end[3] = {to[0], to[1], to[2]};
  mjv_connector(geom, mjGEOM_CAPSULE, 0.012, start, end);
}
}  // namespace




PicoMujocoViewer::PicoMujocoViewer() : Node("pico_mujoco_viewer")
{
  const std::string pose_topic = declare_parameter<std::string>("pose_topic", "/pico/teleop_packet");
  const std::string model_path = declare_parameter<std::string>(
      "model_path",
      "/home/fei/coscene/eva-00-mass-produced/robot/teleop_server/config/pico_82d_viewer.xml");
  const int window_width = declare_parameter<int>("window_width", 1280);
  const int window_height = declare_parameter<int>("window_height", 720);

  load_model(model_path);
  init_window(window_width, window_height);

  pose_sub_ = create_subscription<std_msgs::msg::String>(
      pose_topic,
      rclcpp::QoS(10).best_effort(),
      [this](const std_msgs::msg::String::SharedPtr msg) { on_pose_message(msg); });

  viewer_timer_ =
      create_wall_timer(std::chrono::milliseconds(16), [this]() { run_viewer_loop(); });

  RCLCPP_INFO(get_logger(), "Pico MuJoCo viewer subscribes to %s", pose_topic.c_str());
}

PicoMujocoViewer::~PicoMujocoViewer()
{
  mjr_freeContext(&render_context_);
  if (window_ != nullptr) {
    glfwDestroyWindow(window_);
    window_ = nullptr;
  }
  if (glfw_initialized_) {
    glfwTerminate();
    glfw_initialized_ = false;
  }
  mjv_freeScene(&scene_);
  if (data_ != nullptr) {
    mj_deleteData(data_);
    data_ = nullptr;
  }
  if (model_ != nullptr) {
    mj_deleteModel(model_);
    model_ = nullptr;
  }
}

std::vector<double> PicoMujocoViewer::parse_pose_82d_json(const std::string & json)
{
  const Json::Value root = parse_json(json);
  std::vector<double> pose;
  if (append_double_array(root["pose_82d"], &pose)) {
    return pose;
  }
  if (append_double_array(root["frame"], &pose)) {
    return pose;
  }
  throw std::runtime_error("82D JSON contains neither pose_82d nor frame array");
}

PicoMujocoViewer::JointPositions PicoMujocoViewer::joints_from_pose_82d(
    const std::vector<double> & pose_82d)
{
  if (pose_82d.size() != kPose82dSize) {
    throw std::runtime_error("82D pose has invalid size: " + std::to_string(pose_82d.size()));
  }

  JointPositions joints{};
  const std::array<double, 3> pelvis = {
      pose_82d[0],
      pose_82d[1],
      pose_82d[2],
  };
  for (size_t i = 0; i < kJointCount; ++i) {
    joints[i][0] = pose_82d[i * 3 + 0] - pelvis[0];
    joints[i][1] = pose_82d[i * 3 + 1] - pelvis[1];
    joints[i][2] = pose_82d[i * 3 + 2] - pelvis[2];
  }

  double min_z = joints[0][2];
  for (const auto & joint : joints) {
    min_z = std::min(min_z, joint[2]);
  }
  for (auto & joint : joints) {
    joint[2] += -min_z + 0.03;
  }
  return joints;
}

void PicoMujocoViewer::on_pose_message(const std_msgs::msg::String::SharedPtr msg)
{
  try {
    const JointPositions joints = joints_from_pose_82d(parse_pose_82d_json(msg->data));
    std::lock_guard<std::mutex> lock(joints_mutex_);
    latest_joints_ = joints;
    ++latest_sequence_;
    has_latest_joints_ = true;
  } catch (const std::exception & e) {
    RCLCPP_WARN_THROTTLE(
        get_logger(),
        *get_clock(),
        2000,
        "Failed to parse Pico 82D message for MuJoCo viewer: %s",
        e.what());
  }
}

void PicoMujocoViewer::run_viewer_loop()
{
  if (window_ == nullptr) {
    return;
  }
  if (glfwWindowShouldClose(window_)) {
    rclcpp::shutdown();
    return;
  }
  glfwPollEvents();

  JointPositions joints{};
  uint64_t sequence = 0;
  const bool has_joints = copy_latest_joints(&joints, &sequence);

  mj_forward(model_, data_);
  mjv_updateScene(model_, data_, &option_, nullptr, &camera_, mjCAT_ALL, &scene_);
  if (has_joints) {
    append_skeleton_geoms(joints);
    rendered_sequence_ = sequence;
  }

  int width = 0;
  int height = 0;
  glfwGetFramebufferSize(window_, &width, &height);
  const mjrRect viewport{0, 0, width, height};
  mjr_render(viewport, &scene_, &render_context_);
  glfwSwapBuffers(window_);
}

void PicoMujocoViewer::append_skeleton_geoms(const JointPositions & joints)
{
  const float joint_rgba[4] = {0.15F, 0.55F, 1.0F, 1.0F};
  const float bone_rgba[4] = {0.9F, 0.9F, 0.9F, 1.0F};

  for (const auto & edge : kSkeletonEdges) {
    add_capsule(&scene_, joints[edge.first], joints[edge.second], bone_rgba);
  }
  for (const auto & joint : joints) {
    add_sphere(&scene_, joint, joint_rgba);
  }
}

bool PicoMujocoViewer::copy_latest_joints(JointPositions * joints, uint64_t * sequence)
{
  std::lock_guard<std::mutex> lock(joints_mutex_);
  if (!has_latest_joints_) {
    return false;
  }
  *joints = latest_joints_;
  *sequence = latest_sequence_;
  return true;
}

void PicoMujocoViewer::load_model(const std::string & model_path)
{
  char error[1024] = "";
  model_ = mj_loadXML(model_path.c_str(), nullptr, error, sizeof(error));
  if (model_ == nullptr) {
    throw std::runtime_error("failed to load MuJoCo model " + model_path + ": " + error);
  }
  data_ = mj_makeData(model_);
  if (data_ == nullptr) {
    throw std::runtime_error("failed to allocate MuJoCo data");
  }

  mjv_defaultScene(&scene_);
  mjv_defaultOption(&option_);
  mjv_defaultCamera(&camera_);
  mjv_makeScene(model_, &scene_, 256);
  camera_.azimuth = 135.0;
  camera_.elevation = -20.0;
  camera_.distance = 3.0;
  camera_.lookat[2] = 0.8;
}

void PicoMujocoViewer::init_window(int width, int height)
{
  if (!glfwInit()) {
    throw std::runtime_error("failed to initialize GLFW");
  }
  glfw_initialized_ = true;
  glfwWindowHint(GLFW_VISIBLE, GLFW_TRUE);
  window_ = glfwCreateWindow(width, height, "Pico 82D MuJoCo Viewer", nullptr, nullptr);
  if (window_ == nullptr) {
    throw std::runtime_error("failed to create GLFW window for MuJoCo viewer");
  }
  glfwMakeContextCurrent(window_);
  glfwSwapInterval(1);
  mjr_defaultContext(&render_context_);
  mjr_makeContext(model_, &render_context_, mjFONTSCALE_150);
}

}  // namespace teleop_server

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<teleop_server::PicoMujocoViewer>());
  } catch (const std::exception & e) {
    RCLCPP_ERROR(
        rclcpp::get_logger("pico_mujoco_viewer_main"),
        "Failed to start Pico MuJoCo viewer: %s",
        e.what());
    rclcpp::shutdown();
    return 1;
  }
  rclcpp::shutdown();
  return 0;
}
