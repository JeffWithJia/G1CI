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

#include "pico/pico_video_streamer.hpp"

#include "logging/logger.hpp"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <thread>
#include <utility>

#include <opencv2/imgproc.hpp>

namespace teleop_server
{
namespace
{
constexpr int kConnectTimeoutMs = 3000;
constexpr int kSocketTimeoutMs = 1000;

timeval make_timeval(int timeout_ms)
{
  timeval timeout{};
  timeout.tv_sec = timeout_ms / 1000;
  timeout.tv_usec = (timeout_ms % 1000) * 1000;
  return timeout;
}

bool set_nonblocking(int fd, bool nonblocking)
{
  const int flags = fcntl(fd, F_GETFL, 0);
  if (flags < 0) {
    return false;
  }
  const int new_flags = nonblocking ? (flags | O_NONBLOCK) : (flags & ~O_NONBLOCK);
  return fcntl(fd, F_SETFL, new_flags) == 0;
}
}  // namespace

PicoVideoStreamer::PicoVideoStreamer(PicoVideoStreamerConfig config)
  : config_(std::move(config)),
    stats_window_start_(std::chrono::steady_clock::now())
{
  if (config_.frame_rate <= 0) {
    throw std::invalid_argument("Pico video frame_rate must be positive");
  }
  if (config_.host.empty()) {
    throw std::invalid_argument("Pico video host must not be empty");
  }
  if (config_.camera_width <= 0 || config_.camera_height <= 0) {
    throw std::invalid_argument("Pico video camera size must be positive");
  }
  if (config_.camera_height % 2 != 0) {
    throw std::invalid_argument("Pico video camera height must be even for I420/x264");
  }

  running_.store(true);
  worker_thread_ = std::thread(&PicoVideoStreamer::worker_loop, this);
}

PicoVideoStreamer::~PicoVideoStreamer()
{
  stop();
}

void PicoVideoStreamer::push_frame(const cv::Mat & bgr_frame)
{
  if (!running_.load() || bgr_frame.empty()) {
    return;
  }

  {
    std::lock_guard<std::mutex> lock(frame_mutex_);
    latest_frame_ = bgr_frame.clone();
    has_latest_frame_ = true;
  }
  {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    ++stats_pushed_;
  }
  frame_cv_.notify_one();
}

void PicoVideoStreamer::stop()
{
  const bool was_running = running_.exchange(false);
  if (!was_running) {
    return;
  }
  frame_cv_.notify_one();
  if (worker_thread_.joinable()) {
    worker_thread_.join();
  }
  close_socket();
  close_encoder();
}

void PicoVideoStreamer::worker_loop()
{
  const auto frame_period =
      std::chrono::nanoseconds(1000000000LL / std::max(1, config_.frame_rate));
  auto next_frame_time = std::chrono::steady_clock::now();

  while (running_.load()) {
    if (socket_fd_ < 0) {
      close_encoder();
      if (!connect_to_pico()) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        continue;
      }
      if (!open_encoder()) {
        mark_disconnected();
        std::this_thread::sleep_for(std::chrono::seconds(1));
        continue;
      }
      next_frame_time = std::chrono::steady_clock::now();
    }

    cv::Mat frame;
    {
      std::unique_lock<std::mutex> lock(frame_mutex_);
      frame_cv_.wait_for(lock, std::chrono::milliseconds(100), [this]() {
        return !running_.load() || has_latest_frame_;
      });
      if (!running_.load()) {
        break;
      }
      if (!has_latest_frame_) {
        continue;
      }
      frame = latest_frame_;
      if (config_.drop_old) {
        latest_frame_.release();
        has_latest_frame_ = false;
      }
    }

    const auto now = std::chrono::steady_clock::now();
    if (now < next_frame_time) {
      std::this_thread::sleep_until(next_frame_time);
    }
    next_frame_time = std::max(next_frame_time + frame_period, std::chrono::steady_clock::now());

    std::vector<uint8_t> payload;
    if (!encode_frame(frame, payload)) {
      mark_disconnected();
      std::this_thread::sleep_for(std::chrono::milliseconds(500));
      continue;
    }
    if (payload.empty()) {
      record_empty_h264();
      continue;
    }

    const auto send_start = std::chrono::steady_clock::now();
    if (!send_h264_payload(payload)) {
      TELEOP_LOG_WARN_THROTTLE(2000,
          "Pico video send failed, reconnecting to %s:%u",
          config_.host.c_str(),
          static_cast<unsigned>(kPicoVideoPort));
      mark_disconnected();
      std::this_thread::sleep_for(std::chrono::milliseconds(500));
      continue;
    }
    const auto send_ms =
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - send_start)
            .count();
    record_sent_stats(payload.size(), send_ms);
  }
}

bool PicoVideoStreamer::connect_to_pico()
{
  close_socket();

  socket_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
  if (socket_fd_ < 0) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Failed to create Pico video socket: %s",
        std::strerror(errno));
    return false;
  }

  const timeval socket_timeout = make_timeval(kSocketTimeoutMs);
  setsockopt(socket_fd_, SOL_SOCKET, SO_SNDTIMEO, &socket_timeout, sizeof(socket_timeout));
  setsockopt(socket_fd_, SOL_SOCKET, SO_RCVTIMEO, &socket_timeout, sizeof(socket_timeout));

  sockaddr_in server_addr{};
  server_addr.sin_family = AF_INET;
  server_addr.sin_port = htons(kPicoVideoPort);
  if (::inet_pton(AF_INET, config_.host.c_str(), &server_addr.sin_addr) != 1) {
    TELEOP_LOG_ERROR("Invalid Pico video host: %s", config_.host.c_str());
    close_socket();
    return false;
  }

  if (!set_nonblocking(socket_fd_, true)) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Failed to set Pico video socket nonblocking: %s",
        std::strerror(errno));
    close_socket();
    return false;
  }

  const int connect_result =
      ::connect(socket_fd_, reinterpret_cast<sockaddr *>(&server_addr), sizeof(server_addr));
  if (connect_result != 0 && errno != EINPROGRESS) {
    close_socket();
    return false;
  }
  if (connect_result != 0) {
    pollfd poll_fd{};
    poll_fd.fd = socket_fd_;
    poll_fd.events = POLLOUT;
    const int poll_result = ::poll(&poll_fd, 1, kConnectTimeoutMs);
    if (poll_result <= 0) {
      close_socket();
      return false;
    }

    int socket_error = 0;
    socklen_t socket_error_len = sizeof(socket_error);
    if (getsockopt(socket_fd_, SOL_SOCKET, SO_ERROR, &socket_error, &socket_error_len) != 0 ||
        socket_error != 0) {
      close_socket();
      return false;
    }
  }
  if (!set_nonblocking(socket_fd_, false)) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Failed to restore Pico video socket blocking mode: %s",
        std::strerror(errno));
    close_socket();
    return false;
  }

  TELEOP_LOG_INFO("Pico video connected to %s:%u",
      config_.host.c_str(),
      static_cast<unsigned>(kPicoVideoPort));
  return true;
}

bool PicoVideoStreamer::open_encoder()
{
  close_encoder();

  const int encoded_width = config_.camera_width * 2;
  const int encoded_height = config_.camera_height;

  x264_param_t param{};
  if (x264_param_default_preset(&param, "ultrafast", "zerolatency") < 0) {
    TELEOP_LOG_ERROR("Failed to initialize x264 params");
    return false;
  }

  param.i_width = encoded_width;
  param.i_height = encoded_height;
  param.i_fps_num = config_.frame_rate;
  param.i_fps_den = 1;
  param.i_keyint_max = std::max(1, config_.frame_rate / 2);
  param.i_bframe = 0;
  param.b_repeat_headers = 1;
  param.b_annexb = 1;
  param.rc.i_rc_method = X264_RC_CRF;
  param.rc.f_rf_constant = 23.0F;
  param.i_threads = 1;
  param.i_csp = X264_CSP_I420;

  if (x264_param_apply_profile(&param, "baseline") < 0) {
    TELEOP_LOG_ERROR("Failed to apply x264 baseline profile");
    return false;
  }

  encoder_ = x264_encoder_open(&param);
  if (encoder_ == nullptr) {
    TELEOP_LOG_ERROR("Failed to open x264 encoder");
    return false;
  }

  if (x264_picture_alloc(
          &picture_in_,
          X264_CSP_I420,
          encoded_width,
          encoded_height) < 0) {
    TELEOP_LOG_ERROR("Failed to allocate x264 input picture");
    close_encoder();
    return false;
  }
  picture_allocated_ = true;
  encoder_width_ = encoded_width;
  encoder_height_ = encoded_height;
  pts_ = 0;
  TELEOP_LOG_INFO("Pico video encoder opened, stereo frame size=%dx%d",
      encoder_width_,
      encoder_height_);
  return true;
}

bool PicoVideoStreamer::encode_frame(const cv::Mat & bgr_frame, std::vector<uint8_t> & payload)
{
  if (encoder_ == nullptr && !open_encoder()) {
    return false;
  }
  if (bgr_frame.empty()) {
    return true;
  }

  if (bgr_frame.cols != config_.camera_width || bgr_frame.rows != config_.camera_height) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Pico video frame size mismatch, expected camera=%dx%d, got=%dx%d",
        config_.camera_width,
        config_.camera_height,
        bgr_frame.cols,
        bgr_frame.rows);
    return true;
  }

  cv::Mat stereo_frame(encoder_height_, encoder_width_, bgr_frame.type());
  bgr_frame.copyTo(stereo_frame(cv::Rect(0, 0, config_.camera_width, config_.camera_height)));
  bgr_frame.copyTo(
      stereo_frame(cv::Rect(config_.camera_width, 0, config_.camera_width, config_.camera_height)));

  cv::Mat bgr_stereo_frame;
  if (stereo_frame.channels() == 3) {
    bgr_stereo_frame = stereo_frame;
  } else {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Pico video expects BGR frames with 3 channels, got %d channels",
        stereo_frame.channels());
    return true;
  }

  cv::Mat yuv_i420;
  try {
    cv::cvtColor(bgr_stereo_frame, yuv_i420, cv::COLOR_BGR2YUV_I420);
  } catch (const cv::Exception & e) {
    TELEOP_LOG_WARN_THROTTLE(2000, "Failed to convert Pico video frame to I420: %s", e.what());
    return true;
  }

  const size_t y_size = static_cast<size_t>(encoder_width_) * encoder_height_;
  const size_t uv_size = y_size / 4;
  if (!yuv_i420.isContinuous() || yuv_i420.total() * yuv_i420.elemSize() < y_size + 2 * uv_size) {
    TELEOP_LOG_WARN_THROTTLE(2000, "Unexpected I420 frame layout for Pico video");
    return true;
  }

  const uint8_t * yuv_data = yuv_i420.ptr<uint8_t>(0);
  for (int row = 0; row < encoder_height_; ++row) {
    std::memcpy(
        picture_in_.img.plane[0] + row * picture_in_.img.i_stride[0],
        yuv_data + static_cast<size_t>(row) * encoder_width_,
        static_cast<size_t>(encoder_width_));
  }
  const uint8_t * u_data = yuv_data + y_size;
  const uint8_t * v_data = u_data + uv_size;
  const int chroma_width = encoder_width_ / 2;
  const int chroma_height = encoder_height_ / 2;
  for (int row = 0; row < chroma_height; ++row) {
    std::memcpy(
        picture_in_.img.plane[1] + row * picture_in_.img.i_stride[1],
        u_data + static_cast<size_t>(row) * chroma_width,
        static_cast<size_t>(chroma_width));
    std::memcpy(
        picture_in_.img.plane[2] + row * picture_in_.img.i_stride[2],
        v_data + static_cast<size_t>(row) * chroma_width,
        static_cast<size_t>(chroma_width));
  }
  picture_in_.i_pts = pts_++;

  x264_nal_t * nals = nullptr;
  int nal_count = 0;
  x264_picture_t picture_out{};
  const int frame_size = x264_encoder_encode(
      encoder_,
      &nals,
      &nal_count,
      &picture_in_,
      &picture_out);
  if (frame_size < 0) {
    TELEOP_LOG_WARN_THROTTLE(2000, "x264 failed to encode Pico video frame");
    return false;
  }

  payload.clear();
  for (int i = 0; i < nal_count; ++i) {
    if (nals[i].i_payload <= 0 || nals[i].p_payload == nullptr) {
      continue;
    }
    payload.insert(
        payload.end(),
        nals[i].p_payload,
        nals[i].p_payload + nals[i].i_payload);
  }
  return true;
}

bool PicoVideoStreamer::send_h264_payload(const std::vector<uint8_t> & payload)
{
  if (socket_fd_ < 0 || payload.empty() || payload.size() > UINT32_MAX) {
    return false;
  }

  const uint32_t payload_size = htonl(static_cast<uint32_t>(payload.size()));
  const auto * size_bytes = reinterpret_cast<const uint8_t *>(&payload_size);
  return send_all(size_bytes, sizeof(payload_size)) && send_all(payload.data(), payload.size());
}

bool PicoVideoStreamer::send_all(const uint8_t * data, size_t size)
{
  size_t sent = 0;
  while (sent < size && running_.load()) {
    const ssize_t n = ::send(socket_fd_, data + sent, size - sent, MSG_NOSIGNAL);
    if (n <= 0) {
      return false;
    }
    sent += static_cast<size_t>(n);
  }
  return sent == size;
}

void PicoVideoStreamer::mark_disconnected()
{
  close_socket();
  close_encoder();
}

void PicoVideoStreamer::close_socket()
{
  if (socket_fd_ >= 0) {
    ::close(socket_fd_);
    socket_fd_ = -1;
  }
}

void PicoVideoStreamer::close_encoder()
{
  if (picture_allocated_) {
    x264_picture_clean(&picture_in_);
    picture_allocated_ = false;
  }
  if (encoder_ != nullptr) {
    x264_encoder_close(encoder_);
    encoder_ = nullptr;
  }
  encoder_width_ = 0;
  encoder_height_ = 0;
  pts_ = 0;
}

void PicoVideoStreamer::record_sent_stats(size_t payload_size, double send_ms)
{
  const auto now = std::chrono::steady_clock::now();
  std::lock_guard<std::mutex> lock(stats_mutex_);
  ++stats_sent_;
  stats_payload_bytes_ += payload_size;
  stats_max_payload_bytes_ = std::max(stats_max_payload_bytes_, payload_size);
  stats_send_ms_ += send_ms;
  stats_max_send_ms_ = std::max(stats_max_send_ms_, send_ms);

  const double elapsed = std::chrono::duration<double>(now - stats_window_start_).count();
  if (elapsed < 1.0) {
    return;
  }

  const double pushed_fps = stats_pushed_ / elapsed;
  const double sent_fps = stats_sent_ / elapsed;
  const double avg_payload_kb =
      stats_sent_ == 0 ? 0.0 : stats_payload_bytes_ / static_cast<double>(stats_sent_) / 1024.0;
  const double avg_send_ms = stats_sent_ == 0 ? 0.0 : stats_send_ms_ / stats_sent_;
  TELEOP_LOG_INFO(
      "Pico video stream pushed/sent: %.1f/%.1f fps, payload avg/max: %.1f/%.1f KB, "
      "send avg/max: %.1f/%.1f ms, empty_h264=%zu",
      pushed_fps,
      sent_fps,
      avg_payload_kb,
      stats_max_payload_bytes_ / 1024.0,
      avg_send_ms,
      stats_max_send_ms_,
      stats_empty_h264_);

  stats_window_start_ = now;
  stats_pushed_ = 0;
  stats_sent_ = 0;
  stats_empty_h264_ = 0;
  stats_payload_bytes_ = 0;
  stats_max_payload_bytes_ = 0;
  stats_send_ms_ = 0.0;
  stats_max_send_ms_ = 0.0;
}

void PicoVideoStreamer::record_empty_h264()
{
  const auto now = std::chrono::steady_clock::now();
  std::lock_guard<std::mutex> lock(stats_mutex_);
  ++stats_empty_h264_;
  const double elapsed = std::chrono::duration<double>(now - stats_window_start_).count();
  if (elapsed >= 1.0 && stats_sent_ == 0) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Pico video encoder produced no H.264 payloads, empty_h264=%zu",
        stats_empty_h264_);
  }
}

}  // namespace teleop_server
