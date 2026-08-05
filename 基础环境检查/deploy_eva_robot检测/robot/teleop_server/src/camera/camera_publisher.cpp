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

#include "camera/camera_publisher.hpp"

#include "logging/logger.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <fcntl.h>
#include <linux/videodev2.h>
#include <opencv2/core/mat.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <sensor_msgs/msg/compressed_image.hpp>
#include <std_msgs/msg/header.hpp>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#if defined(HAVE_REALSENSE2)
#include <librealsense2/rs.hpp>
#endif

namespace
{
constexpr unsigned int kV4L2BufferCount = 4;
constexpr int kV4L2PollTimeoutMs = 1000;
constexpr __u32 kV4L2MjpegPixelFormat = V4L2_PIX_FMT_MJPEG;

const char * camera_type_to_string(CameraType camera_type)
{
  switch (camera_type) {
    case CameraType::V4L2:
      return "V4L2";
    case CameraType::REALSENSE:
      return "REALSENSE";
  }
  return "UNKNOWN";
}

std::string fourcc_to_string(__u32 fourcc)
{
  std::array<char, 5> chars = {
      static_cast<char>(fourcc & 0xFF),
      static_cast<char>((fourcc >> 8) & 0xFF),
      static_cast<char>((fourcc >> 16) & 0xFF),
      static_cast<char>((fourcc >> 24) & 0xFF),
      '\0'};
  return std::string(chars.data());
}

int xioctl(int fd, unsigned long request, void * arg)
{
  int result = 0;
  do {
    result = ioctl(fd, request, arg);
  } while (result < 0 && errno == EINTR);
  return result;
}

#if defined(HAVE_REALSENSE2)
struct RealSenseDeviceInfo
{
  std::string serial_number;
  std::string name;
};

std::vector<RealSenseDeviceInfo> list_realsense_devices()
{
  std::vector<RealSenseDeviceInfo> devices;
  const rs2::context context;
  const rs2::device_list device_list = context.query_devices();
  for (const rs2::device & device : device_list) {
    if (!device.supports(RS2_CAMERA_INFO_SERIAL_NUMBER)) {
      continue;
    }

    RealSenseDeviceInfo info;
    info.serial_number = device.get_info(RS2_CAMERA_INFO_SERIAL_NUMBER);
    if (device.supports(RS2_CAMERA_INFO_NAME)) {
      info.name = device.get_info(RS2_CAMERA_INFO_NAME);
    }
    devices.push_back(std::move(info));
  }
  return devices;
}
#endif
}  // namespace

struct CameraPublisher::CameraWorker
{
  struct V4L2Buffer
  {
    void * start{nullptr};
    std::size_t length{0};
  };

  CameraInfo info;
  size_t index{0};
  std::string frame_id;
  std::string realsense_serial_number;
  bool use_software_flip{false};

  rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr compressed_image_publisher;
  std::mutex pico_video_mutex;
  std::shared_ptr<teleop_server::PicoVideoStreamer> pico_video_streamer;
  int v4l2_fd{-1};
  bool v4l2_streaming{false};
  std::vector<V4L2Buffer> v4l2_buffers;
  std::thread publish_thread;
  std::chrono::nanoseconds frame_period{0};
  std::chrono::steady_clock::time_point next_publish_time;

  bool camera_initialized{false};
  bool camera_disabled{false};

#if defined(HAVE_REALSENSE2)
  std::unique_ptr<rs2::pipeline> realsense_pipeline;
#endif
};

CameraPublisher::CameraPublisher(rclcpp::Node & node, std::vector<CameraInfo> camera_infos)
  : node_(node),
    camera_infos_(std::move(camera_infos))
{
  if (camera_infos_.empty()) {
    throw std::invalid_argument("CameraInfo vector is empty");
  }

  camera_workers_.reserve(camera_infos_.size());
  for (size_t index = 0; index < camera_infos_.size(); ++index) {
    const auto & camera_info = camera_infos_[index];
    auto worker = std::make_unique<CameraWorker>();
    worker->info = camera_info;
    worker->index = index;
    worker->frame_id = camera_info.topic_name + "_frame";

    const InitResult init_result = init_camera_worker(*worker);
    switch (init_result) {
      case InitResult::SUCCESS:
        worker->camera_initialized = true;
        break;
      case InitResult::RETRYABLE_FAILURE:
        cleanup_camera_worker(*worker);
        TELEOP_LOG_ERROR("Failed to initialize camera[%zu], will retry every 30s", index);
        break;
      case InitResult::FATAL_FAILURE:
        cleanup_camera_worker(*worker);
        worker->camera_disabled = true;
        TELEOP_LOG_ERROR("Failed to initialize camera[%zu], camera disabled with no retry", index);
        break;
    }

    camera_workers_.push_back(std::move(worker));
  }

  running_.store(true);
  for (auto & worker : camera_workers_) {
    worker->publish_thread = std::thread(&CameraPublisher::publish_loop, this, worker.get());
  }
}

CameraPublisher::~CameraPublisher()
{
  running_.store(false);
  for (auto & worker : camera_workers_) {
    if (worker->publish_thread.joinable()) {
      worker->publish_thread.join();
    }
  }
  cleanup_camera_workers();
}

void CameraPublisher::set_pico_video_host(const std::string & host)
{
  if (host.empty()) {
    return;
  }

  for (auto & worker : camera_workers_) {
    if (!worker || !worker->info.enable_pico_video) {
      continue;
    }
    if (worker->info.pico_video.host == host && worker->pico_video_streamer) {
      continue;
    }

    worker->info.pico_video.host = host;
    std::shared_ptr<teleop_server::PicoVideoStreamer> old_streamer;
    {
      std::lock_guard<std::mutex> lock(worker->pico_video_mutex);
      old_streamer = std::move(worker->pico_video_streamer);
      worker->pico_video_streamer.reset();
    }
    if (old_streamer) {
      old_streamer->stop();
    }
    if (worker->camera_disabled) {
      TELEOP_LOG_WARN("Camera[%zu] is disabled; skip Pico video streamer restart",
          worker->index);
      continue;
    }
    if (!worker->camera_initialized) {
      TELEOP_LOG_INFO("Camera[%zu] is not initialized; defer Pico video streamer restart",
          worker->index);
      continue;
    }
    init_pico_video_streamer(*worker);
  }
}

CameraPublisher::InitResult CameraPublisher::init_camera_worker(CameraWorker & worker)
{
  const auto & info = worker.info;
  if (info.camera_type == CameraType::V4L2 && info.device_name.empty()) {
    TELEOP_LOG_ERROR("Camera %zu has empty device name", worker.index);
    return InitResult::FATAL_FAILURE;
  }
  if (info.frame_rate <= 0) {
    TELEOP_LOG_ERROR("Camera %zu has invalid frame_rate: %d",
        worker.index,
        info.frame_rate);
    return InitResult::FATAL_FAILURE;
  }
  if (info.image_width <= 0 || info.image_height <= 0) {
    TELEOP_LOG_ERROR("Camera %zu has invalid image size: %dx%d",
        worker.index,
        info.image_width,
        info.image_height);
    return InitResult::FATAL_FAILURE;
  }

  worker.frame_period = std::chrono::nanoseconds(1000000000LL / info.frame_rate);
  TELEOP_LOG_INFO("Open camera[%zu]: type: %s, device: %s, width: %d, height: %d, frame rate: %d, flip: %s",
      worker.index,
      camera_type_to_string(info.camera_type),
      info.device_name.empty() ? "<auto>" : info.device_name.c_str(),
      info.image_width,
      info.image_height,
      info.frame_rate,
      info.flip ? "true" : "false");

  worker.use_software_flip =
      info.flip && (info.camera_type == CameraType::REALSENSE || !configure_hardware_flip(worker));
  if (worker.use_software_flip) {
    TELEOP_LOG_WARN("Camera[%zu] falls back to software flip because hardware flip is unavailable",
        worker.index);
  }

  if (info.camera_type == CameraType::REALSENSE) {
    const InitResult result = init_realsense_capture(worker);
    if (result == InitResult::SUCCESS) {
      init_pico_video_streamer(worker);
    }
    return result;
  }

  const InitResult result = init_v4l2_capture(worker);
  if (result == InitResult::SUCCESS) {
    init_pico_video_streamer(worker);
  }
  return result;
}

CameraPublisher::InitResult CameraPublisher::init_v4l2_capture(CameraWorker & worker)
{
  auto & info = worker.info;
  cleanup_v4l2(worker);

  worker.v4l2_fd = open(info.device_name.c_str(), O_RDWR | O_NONBLOCK);
  if (worker.v4l2_fd < 0) {
    TELEOP_LOG_ERROR("Could not open V4L2 camera[%zu]: %s: %s",
        worker.index,
        info.device_name.c_str(),
        std::strerror(errno));
    return InitResult::RETRYABLE_FAILURE;
  }

  v4l2_capability capability{};
  if (xioctl(worker.v4l2_fd, VIDIOC_QUERYCAP, &capability) < 0) {
    TELEOP_LOG_ERROR("Failed to query V4L2 capability for camera[%zu] %s: %s",
        worker.index,
        info.device_name.c_str(),
        std::strerror(errno));
    cleanup_v4l2(worker);
    return InitResult::RETRYABLE_FAILURE;
  }

  const __u32 device_capabilities =
      (capability.capabilities & V4L2_CAP_DEVICE_CAPS) != 0 ?
      capability.device_caps :
      capability.capabilities;
  if ((device_capabilities & V4L2_CAP_VIDEO_CAPTURE) == 0) {
    TELEOP_LOG_ERROR("Camera[%zu] %s does not support single-plane V4L2 video capture",
        worker.index,
        info.device_name.c_str());
    cleanup_v4l2(worker);
    return InitResult::FATAL_FAILURE;
  }
  if ((device_capabilities & V4L2_CAP_STREAMING) == 0) {
    TELEOP_LOG_ERROR("Camera[%zu] %s does not support V4L2 streaming I/O",
        worker.index,
        info.device_name.c_str());
    cleanup_v4l2(worker);
    return InitResult::FATAL_FAILURE;
  }

  bool supports_mjpg = false;
  v4l2_fmtdesc format_desc{};
  format_desc.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  for (format_desc.index = 0; xioctl(worker.v4l2_fd, VIDIOC_ENUM_FMT, &format_desc) == 0;
      ++format_desc.index) {
    if (format_desc.pixelformat == kV4L2MjpegPixelFormat) {
      supports_mjpg = true;
      break;
    }
  }
  if (!supports_mjpg) {
    TELEOP_LOG_ERROR("Camera[%zu] %s does not support required MJPG hardware output",
        worker.index,
        info.device_name.c_str());
    cleanup_v4l2(worker);
    return InitResult::FATAL_FAILURE;
  }

  v4l2_format format{};
  format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  format.fmt.pix.width = static_cast<__u32>(info.image_width);
  format.fmt.pix.height = static_cast<__u32>(info.image_height);
  format.fmt.pix.pixelformat = kV4L2MjpegPixelFormat;
  format.fmt.pix.field = V4L2_FIELD_ANY;
  if (xioctl(worker.v4l2_fd, VIDIOC_S_FMT, &format) < 0) {
    const bool unsupported_format = errno == EINVAL;
    TELEOP_LOG_ERROR("Failed to set camera[%zu] %s to MJPG %dx%d: %s",
        worker.index,
        info.device_name.c_str(),
        info.image_width,
        info.image_height,
        std::strerror(errno));
    cleanup_v4l2(worker);
    return unsupported_format ? InitResult::FATAL_FAILURE : InitResult::RETRYABLE_FAILURE;
  }

  if (format.fmt.pix.pixelformat != kV4L2MjpegPixelFormat) {
    TELEOP_LOG_ERROR("Camera[%zu] %s did not keep required MJPG format, actual format is %s",
        worker.index,
        info.device_name.c_str(),
        fourcc_to_string(format.fmt.pix.pixelformat).c_str());
    cleanup_v4l2(worker);
    return InitResult::FATAL_FAILURE;
  }

  if (static_cast<int>(format.fmt.pix.width) != info.image_width ||
      static_cast<int>(format.fmt.pix.height) != info.image_height) {
    TELEOP_LOG_WARN("Camera[%zu] adjusted image size from %dx%d to %ux%u",
        worker.index,
        info.image_width,
        info.image_height,
        format.fmt.pix.width,
        format.fmt.pix.height);
    info.image_width = static_cast<int>(format.fmt.pix.width);
    info.image_height = static_cast<int>(format.fmt.pix.height);
  }

  v4l2_streamparm stream_param{};
  stream_param.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  stream_param.parm.capture.timeperframe.numerator = 1;
  stream_param.parm.capture.timeperframe.denominator = static_cast<__u32>(info.frame_rate);
  if (xioctl(worker.v4l2_fd, VIDIOC_S_PARM, &stream_param) < 0) {
    TELEOP_LOG_WARN("Failed to set camera[%zu] frame rate to %d FPS: %s",
        worker.index,
        info.frame_rate,
        std::strerror(errno));
  }

  v4l2_requestbuffers request_buffers{};
  request_buffers.count = kV4L2BufferCount;
  request_buffers.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  request_buffers.memory = V4L2_MEMORY_MMAP;
  if (xioctl(worker.v4l2_fd, VIDIOC_REQBUFS, &request_buffers) < 0) {
    TELEOP_LOG_ERROR("Failed to request V4L2 mmap buffers for camera[%zu]: %s",
        worker.index,
        std::strerror(errno));
    cleanup_v4l2(worker);
    return InitResult::RETRYABLE_FAILURE;
  }
  if (request_buffers.count < 2) {
    TELEOP_LOG_ERROR("Camera[%zu] returned too few V4L2 buffers: %u",
        worker.index,
        request_buffers.count);
    cleanup_v4l2(worker);
    return InitResult::RETRYABLE_FAILURE;
  }

  worker.v4l2_buffers.resize(request_buffers.count);
  for (__u32 index = 0; index < request_buffers.count; ++index) {
    v4l2_buffer buffer{};
    buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buffer.memory = V4L2_MEMORY_MMAP;
    buffer.index = index;
    if (xioctl(worker.v4l2_fd, VIDIOC_QUERYBUF, &buffer) < 0) {
      TELEOP_LOG_ERROR("Failed to query V4L2 buffer %u for camera[%zu]: %s",
          index,
          worker.index,
          std::strerror(errno));
      cleanup_v4l2(worker);
      return InitResult::RETRYABLE_FAILURE;
    }

    void * start = mmap(
        nullptr,
        buffer.length,
        PROT_READ | PROT_WRITE,
        MAP_SHARED,
        worker.v4l2_fd,
        buffer.m.offset);
    if (start == MAP_FAILED) {
      TELEOP_LOG_ERROR("Failed to mmap V4L2 buffer %u for camera[%zu]: %s",
          index,
          worker.index,
          std::strerror(errno));
      cleanup_v4l2(worker);
      return InitResult::RETRYABLE_FAILURE;
    }
    worker.v4l2_buffers[index].start = start;
    worker.v4l2_buffers[index].length = buffer.length;
  }

  for (__u32 index = 0; index < request_buffers.count; ++index) {
    v4l2_buffer buffer{};
    buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buffer.memory = V4L2_MEMORY_MMAP;
    buffer.index = index;
    if (xioctl(worker.v4l2_fd, VIDIOC_QBUF, &buffer) < 0) {
      TELEOP_LOG_ERROR("Failed to queue V4L2 buffer %u for camera[%zu]: %s",
          index,
          worker.index,
          std::strerror(errno));
      cleanup_v4l2(worker);
      return InitResult::RETRYABLE_FAILURE;
    }
  }

  v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (xioctl(worker.v4l2_fd, VIDIOC_STREAMON, &type) < 0) {
    TELEOP_LOG_ERROR("Failed to start V4L2 streaming for camera[%zu]: %s",
        worker.index,
        std::strerror(errno));
    cleanup_v4l2(worker);
    return InitResult::RETRYABLE_FAILURE;
  }
  worker.v4l2_streaming = true;

  if (!worker.compressed_image_publisher) {
    const std::string topic_name = info.topic_name + "/compressed_image";
    worker.compressed_image_publisher =
        node_.create_publisher<sensor_msgs::msg::CompressedImage>(topic_name, 10);
    TELEOP_LOG_INFO("Camera[%zu] publishes ROS MJPG topic directly from hardware: %s",
        worker.index,
        topic_name.c_str());
  }

  return InitResult::SUCCESS;
}

CameraPublisher::InitResult CameraPublisher::init_realsense_capture(CameraWorker & worker)
{
#if defined(HAVE_REALSENSE2)
  const auto & info = worker.info;

  // Start the RealSense pipeline first; only create publishers when the hardware is confirmed
  // working, so failed retries do not churn ROS publishers and break subscribers.
  std::string serial_number;
  try {
    const std::vector<RealSenseDeviceInfo> devices = list_realsense_devices();
    if (devices.empty()) {
      TELEOP_LOG_ERROR("No RealSense camera was found for camera[%zu]",
          worker.index);
      return InitResult::RETRYABLE_FAILURE;
    }
    if (devices.size() > 1) {
      TELEOP_LOG_WARN("Found %zu RealSense devices for camera[%zu]; defaulting to the first device",
          devices.size(),
          worker.index);
    }

    const RealSenseDeviceInfo & selected_device = devices.front();
    serial_number = selected_device.serial_number;
    worker.realsense_serial_number = serial_number;

    if (!info.device_name.empty() && info.device_name != serial_number) {
      TELEOP_LOG_WARN("Camera[%zu] ignores configured RealSense device_name '%s' and uses auto-detected "
          "serial '%s'",
          worker.index,
          info.device_name.c_str(),
          serial_number.c_str());
    }
    TELEOP_LOG_INFO("Camera[%zu] selected RealSense device: serial: %s, name: %s",
        worker.index,
        serial_number.c_str(),
        selected_device.name.empty() ? "<unknown>" : selected_device.name.c_str());

    rs2::config config;
    config.enable_device(serial_number);
    config.enable_stream(
        RS2_STREAM_COLOR,
        info.image_width,
        info.image_height,
        RS2_FORMAT_BGR8,
        info.frame_rate);

    worker.realsense_pipeline = std::make_unique<rs2::pipeline>();
    worker.realsense_pipeline->start(config);
  } catch (const rs2::error & e) {
    TELEOP_LOG_ERROR("Failed to start RealSense camera[%zu] serial '%s': %s (%s)",
        worker.index,
        serial_number.empty() ? "<auto>" : serial_number.c_str(),
        e.what(),
        e.get_failed_function().c_str());
    cleanup_realsense(worker);
    return InitResult::RETRYABLE_FAILURE;
  } catch (const std::exception & e) {
    TELEOP_LOG_ERROR("Failed to start RealSense camera[%zu] serial '%s': %s",
        worker.index,
        serial_number.empty() ? "<auto>" : serial_number.c_str(),
        e.what());
    cleanup_realsense(worker);
    return InitResult::RETRYABLE_FAILURE;
  }

  if (!worker.compressed_image_publisher) {
    const std::string topic_name = info.topic_name + "/compressed_image";
    worker.compressed_image_publisher =
        node_.create_publisher<sensor_msgs::msg::CompressedImage>(topic_name, 10);
    TELEOP_LOG_INFO("Camera[%zu] publishes RealSense ROS JPEG topic: %s",
        worker.index,
        topic_name.c_str());
  }

  return InitResult::SUCCESS;
#else
  TELEOP_LOG_ERROR("Camera[%zu] is configured as REALSENSE, but teleop_server was built without librealsense2",
      worker.index);
  return InitResult::FATAL_FAILURE;
#endif
}

void CameraPublisher::cleanup_camera_workers()
{
  for (auto & worker : camera_workers_) {
    cleanup_camera_worker(*worker);
  }
}

void CameraPublisher::cleanup_camera_worker(CameraWorker & worker)
{
  if (worker.pico_video_streamer) {
    std::shared_ptr<teleop_server::PicoVideoStreamer> streamer;
    {
      std::lock_guard<std::mutex> lock(worker.pico_video_mutex);
      streamer = std::move(worker.pico_video_streamer);
      worker.pico_video_streamer.reset();
    }
    if (streamer) {
      streamer->stop();
    }
  }
  cleanup_v4l2(worker);
  cleanup_realsense(worker);
}

void CameraPublisher::cleanup_v4l2(CameraWorker & worker)
{
  if (worker.v4l2_fd >= 0 && worker.v4l2_streaming) {
    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(worker.v4l2_fd, VIDIOC_STREAMOFF, &type) < 0) {
      TELEOP_LOG_WARN("Failed to stop V4L2 streaming for camera[%zu]: %s",
          worker.index,
          std::strerror(errno));
    }
    worker.v4l2_streaming = false;
  }

  for (auto & buffer : worker.v4l2_buffers) {
    if (buffer.start != nullptr && buffer.length > 0) {
      if (munmap(buffer.start, buffer.length) < 0) {
        TELEOP_LOG_WARN("Failed to munmap V4L2 buffer for camera[%zu]: %s",
            worker.index,
            std::strerror(errno));
      }
    }
    buffer.start = nullptr;
    buffer.length = 0;
  }
  worker.v4l2_buffers.clear();

  if (worker.v4l2_fd >= 0) {
    close(worker.v4l2_fd);
    worker.v4l2_fd = -1;
  }
}

void CameraPublisher::cleanup_realsense(CameraWorker & worker)
{
#if defined(HAVE_REALSENSE2)
  if (worker.realsense_pipeline) {
    try {
      worker.realsense_pipeline->stop();
    } catch (const rs2::error & e) {
      TELEOP_LOG_WARN("Failed to stop RealSense camera[%zu]: %s",
          worker.index,
          e.what());
    } catch (const std::exception & e) {
      TELEOP_LOG_WARN("Failed to stop RealSense camera[%zu]: %s",
          worker.index,
          e.what());
    }
    worker.realsense_pipeline.reset();
  }
#else
  (void)worker;
#endif
}

void CameraPublisher::init_pico_video_streamer(CameraWorker & worker)
{
  if (!worker.info.enable_pico_video) {
    return;
  }

  teleop_server::PicoVideoStreamerConfig config = worker.info.pico_video;
  if (config.host.empty()) {
    TELEOP_LOG_INFO("Camera[%zu] waits for XRoboToolkit device IP before starting Pico video",
        worker.index);
    return;
  }
  config.enable = true;
  config.camera_width = worker.info.image_width;
  config.camera_height = worker.info.image_height;
  config.frame_rate = worker.info.frame_rate;
  auto streamer = std::make_shared<teleop_server::PicoVideoStreamer>(config);
  {
    std::lock_guard<std::mutex> lock(worker.pico_video_mutex);
    worker.pico_video_streamer = std::move(streamer);
  }
  TELEOP_LOG_INFO("Camera[%zu] streams Pico video to %s:%u, camera=%dx%d, stereo=%dx%d, fps=%d",
      worker.index,
      config.host.c_str(),
      static_cast<unsigned>(teleop_server::kPicoVideoPort),
      config.camera_width,
      config.camera_height,
      config.camera_width * 2,
      config.camera_height,
      config.frame_rate);
}

void CameraPublisher::publish_loop(CameraWorker * worker)
{
  if (worker == nullptr || worker->frame_period.count() <= 0) {
    return;
  }

  worker->next_publish_time = std::chrono::steady_clock::now();
  while (rclcpp::ok() && running_.load()) {
    if (worker->camera_disabled) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1000));
      continue;
    }

    if (!worker->camera_initialized) {
      TELEOP_LOG_INFO("Retrying initialization for camera[%zu]...", worker->index);
      cleanup_camera_worker(*worker);
      const InitResult init_result = init_camera_worker(*worker);
      if (init_result == InitResult::SUCCESS) {
        worker->camera_initialized = true;
        worker->next_publish_time = std::chrono::steady_clock::now();
        TELEOP_LOG_INFO("Camera[%zu] initialized successfully on retry",
            worker->index);
        continue;
      }

      cleanup_camera_worker(*worker);
      if (init_result == InitResult::FATAL_FAILURE) {
        worker->camera_disabled = true;
        TELEOP_LOG_ERROR("Camera[%zu] initialization failed fatally, no retry",
            worker->index);
        continue;
      }

      TELEOP_LOG_WARN("Camera[%zu] initialization failed, retrying in 30s",
          worker->index);
      // Sleep interruptibly so the thread can be stopped cleanly.
      const auto retry_until = std::chrono::steady_clock::now() + std::chrono::seconds(30);
      while (rclcpp::ok() && running_.load() && std::chrono::steady_clock::now() < retry_until) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
      continue;
    }

    const auto now = std::chrono::steady_clock::now();
    if (now < worker->next_publish_time) {
      std::this_thread::sleep_until(worker->next_publish_time);
    }

    publish_image(*worker);

    worker->next_publish_time += worker->frame_period;
    const auto after_publish = std::chrono::steady_clock::now();
    if (worker->next_publish_time <= after_publish) {
      worker->next_publish_time = after_publish + worker->frame_period;
    }
  }
}

void CameraPublisher::publish_image(CameraWorker & worker)
{
  if (worker.info.camera_type == CameraType::REALSENSE) {
    publish_realsense_image(worker);
    return;
  }

  publish_v4l2_image(worker);
}

void CameraPublisher::publish_realsense_image(CameraWorker & worker)
{
#if defined(HAVE_REALSENSE2)
  if (!worker.realsense_pipeline) {
    return;
  }

  rs2::frameset frames;
  try {
    const auto wait_timeout =
        std::chrono::duration_cast<std::chrono::milliseconds>(worker.frame_period);
    const unsigned int wait_timeout_ms =
        static_cast<unsigned int>(std::max<int64_t>(1, wait_timeout.count()));
    if (!worker.realsense_pipeline->try_wait_for_frames(&frames, wait_timeout_ms)) {
      TELEOP_LOG_WARN_THROTTLE(2000,
          "RealSense camera[%zu] frame wait timed out after %ums",
          worker.index,
          wait_timeout_ms);
      return;
    }
  } catch (const rs2::error & e) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Failed to wait for RealSense camera[%zu] frames: %s",
        worker.index,
        e.what());
    return;
  } catch (const std::exception & e) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Failed to wait for RealSense camera[%zu] frames: %s",
        worker.index,
        e.what());
    return;
  }

  const rs2::video_frame color_frame = frames.get_color_frame();
  if (!color_frame) {
    return;
  }

  cv::Mat frame_view(
      color_frame.get_height(),
      color_frame.get_width(),
      CV_8UC3,
      const_cast<void *>(color_frame.get_data()),
      color_frame.get_stride_in_bytes());
  if (frame_view.empty()) {
    return;
  }
  cv::Mat frame;
  if (worker.use_software_flip) {
    cv::flip(frame_view, frame, -1);
  } else {
    frame = frame_view;
  }

  publish_bgr_frame(worker, frame);
#else
  (void)worker;
#endif
}

void CameraPublisher::publish_v4l2_image(CameraWorker & worker)
{
  if (worker.v4l2_fd < 0) {
    return;
  }

  pollfd poll_fd{};
  poll_fd.fd = worker.v4l2_fd;
  poll_fd.events = POLLIN;
  const int poll_result = poll(&poll_fd, 1, kV4L2PollTimeoutMs);
  if (poll_result == 0) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "V4L2 camera[%zu] frame wait timed out after %dms",
        worker.index,
        kV4L2PollTimeoutMs);
    return;
  }
  if (poll_result < 0) {
    if (errno != EINTR) {
      TELEOP_LOG_WARN_THROTTLE(2000,
          "Failed to poll V4L2 camera[%zu]: %s",
          worker.index,
          std::strerror(errno));
    }
    return;
  }

  v4l2_buffer buffer{};
  buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  buffer.memory = V4L2_MEMORY_MMAP;
  if (xioctl(worker.v4l2_fd, VIDIOC_DQBUF, &buffer) < 0) {
    if (errno != EAGAIN) {
      TELEOP_LOG_WARN_THROTTLE(2000,
          "Failed to dequeue V4L2 buffer for camera[%zu]: %s",
          worker.index,
          std::strerror(errno));
    }
    return;
  }

  if (buffer.index >= worker.v4l2_buffers.size()) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Camera[%zu] returned invalid V4L2 buffer index %u",
        worker.index,
        buffer.index);
  } else {
    const auto & mapped_buffer = worker.v4l2_buffers[buffer.index];
    const auto * data = static_cast<const std::uint8_t *>(mapped_buffer.start);
    const std::size_t size = static_cast<std::size_t>(buffer.bytesused);
    if (data != nullptr && size > 0 && size <= mapped_buffer.length) {
      if (worker.use_software_flip) {
        const cv::Mat encoded_frame(
            1,
            static_cast<int>(size),
            CV_8UC1,
            const_cast<std::uint8_t *>(data));
        cv::Mat frame = cv::imdecode(encoded_frame, cv::IMREAD_COLOR);
        if (frame.empty()) {
          TELEOP_LOG_WARN_THROTTLE(2000,
              "Failed to decode MJPG frame for camera[%zu] software flip",
              worker.index);
        } else {
          cv::flip(frame, frame, -1);
          publish_bgr_frame(worker, frame);
        }
      } else {
        if (auto streamer = pico_video_streamer(worker)) {
          streamer->push_mjpeg_frame(data, size);
        }
        publish_mjpeg_frame(worker, data, size);
      }
    } else {
      TELEOP_LOG_WARN_THROTTLE(2000,
          "Camera[%zu] returned invalid V4L2 frame size: bytesused=%u buffer_length=%zu",
          worker.index,
          buffer.bytesused,
          mapped_buffer.length);
    }
  }

  if (xioctl(worker.v4l2_fd, VIDIOC_QBUF, &buffer) < 0) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Failed to requeue V4L2 buffer for camera[%zu]: %s",
        worker.index,
        std::strerror(errno));
  }
}

void CameraPublisher::publish_mjpeg_frame(
    CameraWorker & worker,
    const std::uint8_t * data,
    std::size_t size)
{
  if (data == nullptr || size == 0 || !worker.compressed_image_publisher) {
    return;
  }

  std_msgs::msg::Header header;
  header.stamp = node_.get_clock()->now();
  header.frame_id = worker.frame_id;

  auto compressed_msg = std::make_shared<sensor_msgs::msg::CompressedImage>();
  compressed_msg->header = header;
  compressed_msg->format = "jpeg";
  compressed_msg->data.assign(data, data + size);
  worker.compressed_image_publisher->publish(*compressed_msg);
}

void CameraPublisher::publish_bgr_frame(CameraWorker & worker, const cv::Mat & frame)
{
  if (frame.empty() || !worker.compressed_image_publisher) {
    return;
  }

  if (auto streamer = pico_video_streamer(worker)) {
    streamer->push_frame(frame);
  }

  std_msgs::msg::Header header;
  header.stamp = node_.get_clock()->now();
  header.frame_id = worker.frame_id;

  auto compressed_msg = std::make_shared<sensor_msgs::msg::CompressedImage>();
  compressed_msg->header = header;
  compressed_msg->format = "jpeg";
  const std::vector<int> encode_params = {cv::IMWRITE_JPEG_QUALITY, 80};
  if (!cv::imencode(".jpg", frame, compressed_msg->data, encode_params)) {
    TELEOP_LOG_WARN_THROTTLE(2000,
        "Failed to encode camera[%zu] frame to JPEG",
        worker.index);
    return;
  }
  worker.compressed_image_publisher->publish(*compressed_msg);
}

std::shared_ptr<teleop_server::PicoVideoStreamer> CameraPublisher::pico_video_streamer(
    CameraWorker & worker)
{
  std::lock_guard<std::mutex> lock(worker.pico_video_mutex);
  return worker.pico_video_streamer;
}

bool CameraPublisher::configure_hardware_flip(CameraWorker & worker)
{
  const auto & info = worker.info;
  if (!info.flip) {
    return true;
  }

  int fd = open(info.device_name.c_str(), O_RDWR | O_NONBLOCK);
  if (fd < 0) {
    TELEOP_LOG_WARN("Failed to open camera[%zu] for hardware flip configuration on %s: %s",
        worker.index,
        info.device_name.c_str(),
        std::strerror(errno));
    return false;
  }

  const int flip_value = info.flip ? 1 : 0;
  bool queried_any = false;
  bool configured_any = false;
  const std::array<std::pair<__u32, const char *>, 2> flip_controls = {
      {{V4L2_CID_HFLIP, "HFLIP"}, {V4L2_CID_VFLIP, "VFLIP"}}};

  for (const auto & [control_id, control_name] : flip_controls) {
    v4l2_queryctrl query{};
    query.id = control_id;
    if (ioctl(fd, VIDIOC_QUERYCTRL, &query) < 0) {
      if (errno != EINVAL) {
        TELEOP_LOG_WARN("Failed to query %s for camera[%zu]: %s",
            control_name,
            worker.index,
            std::strerror(errno));
      }
      continue;
    }

    queried_any = true;
    if (query.flags & V4L2_CTRL_FLAG_DISABLED) {
      continue;
    }

    v4l2_control control{};
    control.id = control_id;
    control.value = flip_value;
    if (ioctl(fd, VIDIOC_S_CTRL, &control) < 0) {
      TELEOP_LOG_WARN("Failed to set %s=%d for camera[%zu]: %s",
          control_name,
          flip_value,
          worker.index,
          std::strerror(errno));
      continue;
    }
    configured_any = true;
  }

  if (!queried_any) {
    TELEOP_LOG_WARN("Camera[%zu] does not expose V4L2 flip controls; output keeps device default orientation",
        worker.index);
  } else if (!configured_any) {
    TELEOP_LOG_WARN("Camera[%zu] flip controls found but could not be configured; output keeps device "
        "default orientation",
        worker.index);
  } else {
    TELEOP_LOG_INFO("Camera[%zu] hardware flip configured to %s",
        worker.index,
        info.flip ? "true" : "false");
  }

  close(fd);
  return configured_any;
}
