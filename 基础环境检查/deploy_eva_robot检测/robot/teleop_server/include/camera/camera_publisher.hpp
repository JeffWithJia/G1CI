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

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include <rclcpp/rclcpp.hpp>

enum class CompressType
{
  NONE_COMPRESS = 0,
  MJPEG_COMPRESS = 1,
};

enum class CameraType
{
  V4L2 = 0,
  REALSENSE = 1,
};

struct CameraInfo
{
  CameraType camera_type;
  std::string device_name;
  std::string topic_name;
  int image_width;
  int image_height;
  int frame_rate;
  CompressType compress_type;
  bool flip;
  bool enable_socket_publish;
  int socket_publish_port;
};

class CameraPublisher
{
public:
  CameraPublisher(rclcpp::Node & node, std::vector<CameraInfo> camera_infos);

  ~CameraPublisher();

  CameraPublisher(const CameraPublisher &) = delete;

  CameraPublisher & operator=(const CameraPublisher &) = delete;

private:
  struct MmapBuffer;
  struct CameraWorker;

  bool init_camera_worker(CameraWorker & worker);

  bool open_opencv_capture(CameraWorker & worker);

  bool init_opencv_capture(CameraWorker & worker);

  bool init_mjpeg_opencv_fallback_capture(CameraWorker & worker);

  bool init_mjpeg_capture(CameraWorker & worker);

  bool init_realsense_capture(CameraWorker & worker);

  void cleanup_camera_workers();

  void cleanup_camera_worker(CameraWorker & worker);

  void cleanup_v4l2(CameraWorker & worker);

  void cleanup_realsense(CameraWorker & worker);

  bool init_socket_publisher(CameraWorker & worker);

  void cleanup_socket_publisher(CameraWorker & worker);

  void publish_socket_jpeg(CameraWorker & worker, const std::vector<uint8_t> & jpeg_data);

  bool configure_hardware_flip(CameraWorker & worker);

  bool init_v4l2_mjpeg_capture(CameraWorker & worker);

  bool read_direct_mjpeg_frame(CameraWorker & worker, std::vector<uint8_t> & jpeg_data);

  void publish_loop(CameraWorker * worker);

  void publish_image(CameraWorker & worker);

  void publish_opencv_image(CameraWorker & worker);

  void publish_mjpeg_image(CameraWorker & worker);

  void publish_realsense_image(CameraWorker & worker);

  rclcpp::Node & node_;
  std::vector<CameraInfo> camera_infos_;
  std::vector<std::unique_ptr<CameraWorker>> camera_workers_;
  std::atomic<bool> running_{false};
};
