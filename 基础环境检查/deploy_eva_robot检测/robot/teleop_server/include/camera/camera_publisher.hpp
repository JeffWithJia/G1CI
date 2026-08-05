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
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include <rclcpp/rclcpp.hpp>

#include "pico/pico_video_streamer.hpp"

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
  bool flip;
  bool enable_pico_video;
  teleop_server::PicoVideoStreamerConfig pico_video;
};

class CameraPublisher
{
public:
  CameraPublisher(rclcpp::Node & node, std::vector<CameraInfo> camera_infos);

  ~CameraPublisher();

  CameraPublisher(const CameraPublisher &) = delete;

  CameraPublisher & operator=(const CameraPublisher &) = delete;

  void set_pico_video_host(const std::string & host);

private:
  struct CameraWorker;

  enum class InitResult
  {
    SUCCESS,
    RETRYABLE_FAILURE,
    FATAL_FAILURE,
  };

  InitResult init_camera_worker(CameraWorker & worker);

  InitResult init_v4l2_capture(CameraWorker & worker);

  InitResult init_realsense_capture(CameraWorker & worker);

  void init_pico_video_streamer(CameraWorker & worker);

  void cleanup_camera_workers();

  void cleanup_camera_worker(CameraWorker & worker);

  void cleanup_v4l2(CameraWorker & worker);

  void cleanup_realsense(CameraWorker & worker);

  bool configure_hardware_flip(CameraWorker & worker);

  void publish_loop(CameraWorker * worker);

  void publish_image(CameraWorker & worker);

  void publish_v4l2_image(CameraWorker & worker);

  void publish_realsense_image(CameraWorker & worker);

  void publish_mjpeg_frame(CameraWorker & worker, const std::uint8_t * data, std::size_t size);

  void publish_bgr_frame(CameraWorker & worker, const cv::Mat & frame);

  std::shared_ptr<teleop_server::PicoVideoStreamer> pico_video_streamer(CameraWorker & worker);

  rclcpp::Node & node_;
  std::vector<CameraInfo> camera_infos_;
  std::vector<std::unique_ptr<CameraWorker>> camera_workers_;
  std::atomic<bool> running_{false};
};
