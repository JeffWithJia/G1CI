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
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <opencv2/core/mat.hpp>
#include <x264.h>

namespace teleop_server
{

inline constexpr uint16_t kPicoVideoPort = 12345;

struct PicoVideoStreamerConfig
{
  bool enable{false};
  std::string host;
  int camera_width{0};
  int camera_height{0};
  int frame_rate{30};
  bool drop_old{true};
};

class PicoVideoStreamer
{
public:
  explicit PicoVideoStreamer(PicoVideoStreamerConfig config);
  ~PicoVideoStreamer();

  PicoVideoStreamer(const PicoVideoStreamer &) = delete;
  PicoVideoStreamer & operator=(const PicoVideoStreamer &) = delete;

  void push_frame(const cv::Mat & bgr_frame);
  void stop();

private:
  void worker_loop();
  bool connect_to_pico();
  bool open_encoder();
  bool encode_frame(const cv::Mat & bgr_frame, std::vector<uint8_t> & payload);
  bool send_h264_payload(const std::vector<uint8_t> & payload);
  bool send_all(const uint8_t * data, size_t size);
  void mark_disconnected();
  void close_socket();
  void close_encoder();
  void record_sent_stats(size_t payload_size, double send_ms);
  void record_empty_h264();

  PicoVideoStreamerConfig config_;
  std::atomic<bool> running_{false};
  std::thread worker_thread_;

  std::mutex frame_mutex_;
  std::condition_variable frame_cv_;
  cv::Mat latest_frame_;
  bool has_latest_frame_{false};

  std::mutex stats_mutex_;
  std::chrono::steady_clock::time_point stats_window_start_;
  size_t stats_pushed_{0};
  size_t stats_sent_{0};
  size_t stats_empty_h264_{0};
  size_t stats_payload_bytes_{0};
  size_t stats_max_payload_bytes_{0};
  double stats_send_ms_{0.0};
  double stats_max_send_ms_{0.0};

  int socket_fd_{-1};
  x264_t * encoder_{nullptr};
  x264_picture_t picture_in_{};
  bool picture_allocated_{false};
  int encoder_width_{0};
  int encoder_height_{0};
  int64_t pts_{0};
};

}  // namespace teleop_server
