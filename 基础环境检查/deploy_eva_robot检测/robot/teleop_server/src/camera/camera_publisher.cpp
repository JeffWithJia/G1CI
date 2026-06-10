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

#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <cv_bridge/cv_bridge.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <opencv2/core/mat.hpp>
#include <opencv2/opencv.hpp>
#include <opencv2/videoio.hpp>
#include <sensor_msgs/image_encodings.hpp>
#include <sensor_msgs/msg/compressed_image.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <std_msgs/msg/header.hpp>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <zmq.h>

#if defined(HAVE_REALSENSE2)
#include <librealsense2/rs.hpp>
#endif

namespace
{
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

struct CameraPublisher::MmapBuffer
{
  void * start{nullptr};
  size_t length{0};
};

struct CameraPublisher::CameraWorker
{
  CameraInfo info;
  size_t index{0};
  std::string frame_id;
  std::string realsense_serial_number;
  bool use_software_flip{false};
  bool use_direct_mjpeg_capture{false};

  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr image_publisher;
  rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr compressed_image_publisher;
  cv::VideoCapture video_cap;
  std::thread publish_thread;
  std::chrono::nanoseconds frame_period{0};
  std::chrono::steady_clock::time_point next_publish_time;

  bool camera_initialized{false};

  int v4l2_fd{-1};
  bool v4l2_streaming{false};
  std::vector<MmapBuffer> v4l2_mmap_buffers;

#if defined(HAVE_REALSENSE2)
  std::unique_ptr<rs2::pipeline> realsense_pipeline;
#endif

  void * socket_context{nullptr};
  void * socket_publisher{nullptr};
  size_t socket_send_count{0};
  size_t socket_drop_count{0};
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

    if (!init_camera_worker(*worker)) {
      cleanup_camera_worker(*worker);
      RCLCPP_ERROR(
          node_.get_logger(),
          "Failed to initialize camera[%zu], will retry every 30s",
          index);
    } else {
      worker->camera_initialized = true;
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

bool CameraPublisher::init_camera_worker(CameraWorker & worker)
{
  const auto & info = worker.info;
  if (info.camera_type == CameraType::V4L2 && info.device_name.empty()) {
    RCLCPP_ERROR(node_.get_logger(), "Camera %zu has empty device name", worker.index);
    return false;
  }
  if (info.frame_rate <= 0) {
    RCLCPP_ERROR(
        node_.get_logger(),
        "Camera %zu has invalid frame_rate: %d",
        worker.index,
        info.frame_rate);
    return false;
  }
  if (info.image_width <= 0 || info.image_height <= 0) {
    RCLCPP_ERROR(
        node_.get_logger(),
        "Camera %zu has invalid image size: %dx%d",
        worker.index,
        info.image_width,
        info.image_height);
    return false;
  }

  worker.frame_period = std::chrono::nanoseconds(1000000000LL / info.frame_rate);
  RCLCPP_INFO(
      node_.get_logger(),
      "Open camera[%zu]: type: %s, device: %s, width: %d, height: %d, frame rate: %d, flip: %s",
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
    RCLCPP_WARN(
        node_.get_logger(),
        "Camera[%zu] falls back to software flip because hardware flip is unavailable",
        worker.index);
  }

  if (info.enable_socket_publish && !init_socket_publisher(worker)) {
    return false;
  }

  if (info.camera_type == CameraType::REALSENSE) {
    return init_realsense_capture(worker);
  }

  switch (info.compress_type) {
    case CompressType::NONE_COMPRESS:
      return init_opencv_capture(worker);
    case CompressType::MJPEG_COMPRESS:
      if (worker.use_software_flip) {
        RCLCPP_WARN(
            node_.get_logger(),
            "Camera[%zu] MJPEG capture falls back to OpenCV path for software flip and JPEG encode",
            worker.index);
        return init_mjpeg_opencv_fallback_capture(worker);
      }
      return init_mjpeg_capture(worker);
  }

  RCLCPP_ERROR(node_.get_logger(), "Camera %zu has unsupported compress type", worker.index);
  return false;
}

bool CameraPublisher::open_opencv_capture(CameraWorker & worker)
{
  const auto & info = worker.info;
  const bool opened_with_v4l2 = worker.video_cap.open(info.device_name, cv::CAP_V4L2);
  if (!opened_with_v4l2) {
    RCLCPP_WARN(
        node_.get_logger(),
        "Failed to open camera[%zu] with V4L2 backend: %s, fallback to default backend",
        worker.index,
        info.device_name.c_str());
    worker.video_cap.open(info.device_name, cv::CAP_ANY);
  }
  if (!worker.video_cap.isOpened()) {
    RCLCPP_ERROR(
        node_.get_logger(),
        "Could not open camera[%zu]: %s",
        worker.index,
        info.device_name.c_str());
    return false;
  }

  worker.video_cap.set(cv::CAP_PROP_FRAME_WIDTH, static_cast<double>(info.image_width));
  worker.video_cap.set(cv::CAP_PROP_FRAME_HEIGHT, static_cast<double>(info.image_height));
  worker.video_cap.set(cv::CAP_PROP_FPS, static_cast<double>(info.frame_rate));
  worker.video_cap.set(cv::CAP_PROP_FOURCC, cv::VideoWriter::fourcc('M', 'J', 'P', 'G'));
  return true;
}

bool CameraPublisher::init_opencv_capture(CameraWorker & worker)
{
  const auto & info = worker.info;
  if (!open_opencv_capture(worker)) {
    return false;
  }
  if (!worker.image_publisher) {
    const std::string topic_name = info.topic_name + "/raw_image";
    worker.image_publisher = node_.create_publisher<sensor_msgs::msg::Image>(topic_name, 10);
    RCLCPP_INFO(
        node_.get_logger(),
        "Camera[%zu] publishes ROS topic: %s",
        worker.index,
        topic_name.c_str());
  }
  return true;
}

bool CameraPublisher::init_mjpeg_opencv_fallback_capture(CameraWorker & worker)
{
  worker.use_direct_mjpeg_capture = false;
  if (!open_opencv_capture(worker)) {
    return false;
  }
  if (!worker.compressed_image_publisher) {
    const std::string topic_name = worker.info.topic_name + "/compressed_image";
    worker.compressed_image_publisher =
        node_.create_publisher<sensor_msgs::msg::CompressedImage>(topic_name, 10);
    RCLCPP_INFO(
        node_.get_logger(),
        "Camera[%zu] publishes ROS topic through OpenCV MJPEG fallback: %s",
        worker.index,
        topic_name.c_str());
  }
  return true;
}

bool CameraPublisher::init_mjpeg_capture(CameraWorker & worker)
{
  const auto & info = worker.info;
  if (!init_v4l2_mjpeg_capture(worker)) {
    RCLCPP_ERROR(
        node_.get_logger(),
        "Failed to initialize V4L2 direct MJPEG capture for camera[%zu]: %s",
        worker.index,
        info.device_name.c_str());
    return false;
  }
  worker.use_direct_mjpeg_capture = true;

  if (!worker.compressed_image_publisher) {
    const std::string topic_name = info.topic_name + "/compressed_image";
    worker.compressed_image_publisher =
        node_.create_publisher<sensor_msgs::msg::CompressedImage>(topic_name, 10);
    RCLCPP_INFO(
        node_.get_logger(),
        "Camera[%zu] uses V4L2 direct MJPEG path and publishes ROS topic: %s",
        worker.index,
        topic_name.c_str());
  }
  return true;
}

bool CameraPublisher::init_realsense_capture(CameraWorker & worker)
{
#if defined(HAVE_REALSENSE2)
  const auto & info = worker.info;
  if (info.compress_type != CompressType::NONE_COMPRESS &&
      info.compress_type != CompressType::MJPEG_COMPRESS) {
    RCLCPP_ERROR(node_.get_logger(), "Camera %zu has unsupported compress type", worker.index);
    return false;
  }

  // Start the RealSense pipeline first; only create publishers when the hardware is confirmed
  // working, so failed retries do not churn ROS publishers and break subscribers.
  std::string serial_number;
  try {
    const std::vector<RealSenseDeviceInfo> devices = list_realsense_devices();
    if (devices.empty()) {
      RCLCPP_ERROR(
          node_.get_logger(),
          "No RealSense camera was found for camera[%zu]",
          worker.index);
      return false;
    }
    if (devices.size() > 1) {
      RCLCPP_WARN(
          node_.get_logger(),
          "Found %zu RealSense devices for camera[%zu]; defaulting to the first device",
          devices.size(),
          worker.index);
    }

    const RealSenseDeviceInfo & selected_device = devices.front();
    serial_number = selected_device.serial_number;
    worker.realsense_serial_number = serial_number;

    if (!info.device_name.empty() && info.device_name != serial_number) {
      RCLCPP_WARN(
          node_.get_logger(),
          "Camera[%zu] ignores configured RealSense device_name '%s' and uses auto-detected "
          "serial '%s'",
          worker.index,
          info.device_name.c_str(),
          serial_number.c_str());
    }
    RCLCPP_INFO(
        node_.get_logger(),
        "Camera[%zu] selected RealSense device: serial: %s, name: %s",
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
    RCLCPP_ERROR(
        node_.get_logger(),
        "Failed to start RealSense camera[%zu] serial '%s': %s (%s)",
        worker.index,
        serial_number.empty() ? "<auto>" : serial_number.c_str(),
        e.what(),
        e.get_failed_function().c_str());
    cleanup_realsense(worker);
    return false;
  } catch (const std::exception & e) {
    RCLCPP_ERROR(
        node_.get_logger(),
        "Failed to start RealSense camera[%zu] serial '%s': %s",
        worker.index,
        serial_number.empty() ? "<auto>" : serial_number.c_str(),
        e.what());
    cleanup_realsense(worker);
    return false;
  }

  if (info.compress_type == CompressType::NONE_COMPRESS) {
    if (!worker.image_publisher) {
      const std::string topic_name = info.topic_name + "/raw_image";
      worker.image_publisher = node_.create_publisher<sensor_msgs::msg::Image>(topic_name, 10);
      RCLCPP_INFO(
          node_.get_logger(),
          "Camera[%zu] publishes RealSense ROS topic: %s",
          worker.index,
          topic_name.c_str());
    }
  } else {
    if (!worker.compressed_image_publisher) {
      const std::string topic_name = info.topic_name + "/compressed_image";
      worker.compressed_image_publisher =
          node_.create_publisher<sensor_msgs::msg::CompressedImage>(topic_name, 10);
      RCLCPP_INFO(
          node_.get_logger(),
          "Camera[%zu] publishes RealSense ROS topic: %s",
          worker.index,
          topic_name.c_str());
    }
  }

  return true;
#else
  RCLCPP_ERROR(
      node_.get_logger(),
      "Camera[%zu] is configured as REALSENSE, but teleop_server was built without librealsense2",
      worker.index);
  return false;
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
  if (worker.v4l2_streaming && worker.v4l2_fd >= 0) {
    auto type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(worker.v4l2_fd, VIDIOC_STREAMOFF, &type);
    worker.v4l2_streaming = false;
  }
  cleanup_socket_publisher(worker);
  cleanup_realsense(worker);
  cleanup_v4l2(worker);
}

void CameraPublisher::cleanup_v4l2(CameraWorker & worker)
{
  for (auto & buffer : worker.v4l2_mmap_buffers) {
    if (buffer.start != nullptr && buffer.length > 0) {
      munmap(buffer.start, buffer.length);
      buffer.start = nullptr;
      buffer.length = 0;
    }
  }
  worker.v4l2_mmap_buffers.clear();
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
      RCLCPP_WARN(
          node_.get_logger(),
          "Failed to stop RealSense camera[%zu]: %s",
          worker.index,
          e.what());
    } catch (const std::exception & e) {
      RCLCPP_WARN(
          node_.get_logger(),
          "Failed to stop RealSense camera[%zu]: %s",
          worker.index,
          e.what());
    }
    worker.realsense_pipeline.reset();
  }
#else
  (void)worker;
#endif
}

bool CameraPublisher::init_socket_publisher(CameraWorker & worker)
{
  worker.socket_context = zmq_ctx_new();
  if (worker.socket_context == nullptr) {
    RCLCPP_ERROR(
        node_.get_logger(),
        "Failed to create ZeroMQ context for camera[%zu]: %s",
        worker.index,
        zmq_strerror(errno));
    return false;
  }

  worker.socket_publisher = zmq_socket(worker.socket_context, ZMQ_PUB);
  if (worker.socket_publisher == nullptr) {
    RCLCPP_ERROR(
        node_.get_logger(),
        "Failed to create ZeroMQ PUB socket for camera[%zu]: %s",
        worker.index,
        zmq_strerror(errno));
    cleanup_socket_publisher(worker);
    return false;
  }

  const int send_high_water_mark = 2;
  zmq_setsockopt(
      worker.socket_publisher,
      ZMQ_SNDHWM,
      &send_high_water_mark,
      sizeof(send_high_water_mark));
  // Keep only the latest frame on PUB queue to avoid latency growth.
  const int conflate = 1;
  zmq_setsockopt(worker.socket_publisher, ZMQ_CONFLATE, &conflate, sizeof(conflate));
  // Do not queue to not-yet-connected peers.
  const int immediate = 1;
  zmq_setsockopt(worker.socket_publisher, ZMQ_IMMEDIATE, &immediate, sizeof(immediate));
  // Ensure send path is non-blocking even under network backpressure.
  const int send_timeout_ms = 0;
  zmq_setsockopt(worker.socket_publisher, ZMQ_SNDTIMEO, &send_timeout_ms, sizeof(send_timeout_ms));
  const int linger_ms = 0;
  zmq_setsockopt(worker.socket_publisher, ZMQ_LINGER, &linger_ms, sizeof(linger_ms));

  const std::string endpoint = "tcp://*:" + std::to_string(worker.info.socket_publish_port);
  if (zmq_bind(worker.socket_publisher, endpoint.c_str()) != 0) {
    RCLCPP_ERROR(
        node_.get_logger(),
        "Failed to bind ZeroMQ PUB socket for camera[%zu] to %s: %s",
        worker.index,
        endpoint.c_str(),
        zmq_strerror(errno));
    cleanup_socket_publisher(worker);
    return false;
  }

  RCLCPP_INFO(
      node_.get_logger(),
      "Camera[%zu] publishes JPEG images through ZeroMQ PUB socket: %s",
      worker.index,
      endpoint.c_str());
  return true;
}

void CameraPublisher::cleanup_socket_publisher(CameraWorker & worker)
{
  if (worker.socket_publisher != nullptr) {
    zmq_close(worker.socket_publisher);
    worker.socket_publisher = nullptr;
  }
  if (worker.socket_context != nullptr) {
    zmq_ctx_term(worker.socket_context);
    worker.socket_context = nullptr;
  }
}

void CameraPublisher::publish_socket_jpeg(
    CameraWorker & worker,
    const std::vector<uint8_t> & jpeg_data)
{
  if (!worker.info.enable_socket_publish || worker.socket_publisher == nullptr ||
      jpeg_data.empty()) {
    return;
  }

  const int rc =
      zmq_send(worker.socket_publisher, jpeg_data.data(), jpeg_data.size(), ZMQ_DONTWAIT);
  if (rc >= 0) {
    ++worker.socket_send_count;
    return;
  }

  if (errno == EAGAIN) {
    ++worker.socket_drop_count;
    if (worker.socket_drop_count % 120 == 0) {
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Drop JPEG for camera[%zu] due to slow socket consumer (sent=%zu, dropped=%zu)",
          worker.index,
          worker.socket_send_count,
          worker.socket_drop_count);
    }
    return;
  }

  {
    RCLCPP_WARN_THROTTLE(
        node_.get_logger(),
        *node_.get_clock(),
        2000,
        "Failed to publish JPEG for camera[%zu] through ZeroMQ socket: %s",
        worker.index,
        zmq_strerror(errno));
  }
}

bool CameraPublisher::init_v4l2_mjpeg_capture(CameraWorker & worker)
{
  const auto & info = worker.info;
  worker.v4l2_fd = open(info.device_name.c_str(), O_RDWR | O_NONBLOCK);
  if (worker.v4l2_fd < 0) {
    RCLCPP_WARN(
        node_.get_logger(),
        "Failed to open V4L2 device for camera[%zu] %s: %s",
        worker.index,
        info.device_name.c_str(),
        std::strerror(errno));
    return false;
  }

  v4l2_capability capability{};
  if (ioctl(worker.v4l2_fd, VIDIOC_QUERYCAP, &capability) < 0) {
    RCLCPP_WARN(
        node_.get_logger(),
        "VIDIOC_QUERYCAP failed for camera[%zu]: %s",
        worker.index,
        std::strerror(errno));
    cleanup_v4l2(worker);
    return false;
  }
  if (!(capability.capabilities & V4L2_CAP_VIDEO_CAPTURE) ||
      !(capability.capabilities & V4L2_CAP_STREAMING)) {
    RCLCPP_WARN(
        node_.get_logger(),
        "Device %s for camera[%zu] does not support V4L2 streaming capture",
        info.device_name.c_str(),
        worker.index);
    cleanup_v4l2(worker);
    return false;
  }

  v4l2_format format{};
  format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  format.fmt.pix.width = static_cast<uint32_t>(info.image_width);
  format.fmt.pix.height = static_cast<uint32_t>(info.image_height);
  format.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
  format.fmt.pix.field = V4L2_FIELD_ANY;
  if (ioctl(worker.v4l2_fd, VIDIOC_S_FMT, &format) < 0) {
    RCLCPP_WARN(
        node_.get_logger(),
        "VIDIOC_S_FMT failed for camera[%zu]: %s",
        worker.index,
        std::strerror(errno));
    cleanup_v4l2(worker);
    return false;
  }

  v4l2_streamparm stream_param{};
  stream_param.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  stream_param.parm.capture.timeperframe.numerator = 1;
  stream_param.parm.capture.timeperframe.denominator = info.frame_rate;
  if (ioctl(worker.v4l2_fd, VIDIOC_S_PARM, &stream_param) < 0) {
    RCLCPP_WARN(
        node_.get_logger(),
        "VIDIOC_S_PARM failed for camera[%zu]: %s",
        worker.index,
        std::strerror(errno));
  }

  v4l2_requestbuffers request_buffers{};
  request_buffers.count = 4;
  request_buffers.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  request_buffers.memory = V4L2_MEMORY_MMAP;
  if (ioctl(worker.v4l2_fd, VIDIOC_REQBUFS, &request_buffers) < 0) {
    RCLCPP_WARN(
        node_.get_logger(),
        "VIDIOC_REQBUFS failed for camera[%zu]: %s",
        worker.index,
        std::strerror(errno));
    cleanup_v4l2(worker);
    return false;
  }
  if (request_buffers.count < 2) {
    RCLCPP_WARN(
        node_.get_logger(),
        "Insufficient V4L2 buffers for camera[%zu]: %u",
        worker.index,
        request_buffers.count);
    cleanup_v4l2(worker);
    return false;
  }

  worker.v4l2_mmap_buffers.resize(request_buffers.count);
  for (uint32_t i = 0; i < request_buffers.count; ++i) {
    v4l2_buffer buffer{};
    buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buffer.memory = V4L2_MEMORY_MMAP;
    buffer.index = i;
    if (ioctl(worker.v4l2_fd, VIDIOC_QUERYBUF, &buffer) < 0) {
      RCLCPP_WARN(
          node_.get_logger(),
          "VIDIOC_QUERYBUF failed for camera[%zu]: %s",
          worker.index,
          std::strerror(errno));
      cleanup_v4l2(worker);
      return false;
    }

    void * start = mmap(
        nullptr,
        buffer.length,
        PROT_READ | PROT_WRITE,
        MAP_SHARED,
        worker.v4l2_fd,
        buffer.m.offset);
    if (start == MAP_FAILED) {
      RCLCPP_WARN(
          node_.get_logger(),
          "mmap failed for camera[%zu]: %s",
          worker.index,
          std::strerror(errno));
      cleanup_v4l2(worker);
      return false;
    }
    worker.v4l2_mmap_buffers[i].start = start;
    worker.v4l2_mmap_buffers[i].length = buffer.length;

    if (ioctl(worker.v4l2_fd, VIDIOC_QBUF, &buffer) < 0) {
      RCLCPP_WARN(
          node_.get_logger(),
          "VIDIOC_QBUF failed for camera[%zu]: %s",
          worker.index,
          std::strerror(errno));
      cleanup_v4l2(worker);
      return false;
    }
  }

  auto type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(worker.v4l2_fd, VIDIOC_STREAMON, &type) < 0) {
    RCLCPP_WARN(
        node_.get_logger(),
        "VIDIOC_STREAMON failed for camera[%zu]: %s",
        worker.index,
        std::strerror(errno));
    cleanup_v4l2(worker);
    return false;
  }
  worker.v4l2_streaming = true;
  return true;
}

bool CameraPublisher::read_direct_mjpeg_frame(
    CameraWorker & worker,
    std::vector<uint8_t> & jpeg_data)
{
  if (worker.v4l2_fd < 0) {
    errno = EINVAL;
    return false;
  }

  v4l2_buffer buffer{};
  buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  buffer.memory = V4L2_MEMORY_MMAP;
  if (ioctl(worker.v4l2_fd, VIDIOC_DQBUF, &buffer) < 0) {
    return false;
  }
  if (buffer.index >= worker.v4l2_mmap_buffers.size()) {
    errno = EINVAL;
    return false;
  }

  const auto & mmap_buffer = worker.v4l2_mmap_buffers[buffer.index];
  if (buffer.bytesused < 4 || mmap_buffer.start == nullptr) {
    ioctl(worker.v4l2_fd, VIDIOC_QBUF, &buffer);
    errno = EINVAL;
    return false;
  }

  const auto * data_ptr = static_cast<const uint8_t *>(mmap_buffer.start);
  if (data_ptr[0] != 0xFF || data_ptr[1] != 0xD8) {
    ioctl(worker.v4l2_fd, VIDIOC_QBUF, &buffer);
    errno = EINVAL;
    return false;
  }

  jpeg_data.assign(data_ptr, data_ptr + buffer.bytesused);
  return ioctl(worker.v4l2_fd, VIDIOC_QBUF, &buffer) >= 0;
}

void CameraPublisher::publish_loop(CameraWorker * worker)
{
  if (worker == nullptr || worker->frame_period.count() <= 0) {
    return;
  }

  // For RealSense the camera itself paces frame delivery via try_wait_for_frames(), so the
  // outer loop must not impose an additional frame_period sleep that would halve the rate.
  const bool externally_paced = worker->info.camera_type == CameraType::REALSENSE;
  worker->next_publish_time = std::chrono::steady_clock::now();
  while (rclcpp::ok() && running_.load()) {
    if (!worker->camera_initialized) {
      RCLCPP_INFO(node_.get_logger(), "Retrying initialization for camera[%zu]...", worker->index);
      cleanup_camera_worker(*worker);
      if (init_camera_worker(*worker)) {
        worker->camera_initialized = true;
        worker->next_publish_time = std::chrono::steady_clock::now();
        RCLCPP_INFO(
            node_.get_logger(),
            "Camera[%zu] initialized successfully on retry",
            worker->index);
      } else {
        cleanup_camera_worker(*worker);
        RCLCPP_WARN(
            node_.get_logger(),
            "Camera[%zu] initialization failed, retrying in 30s",
            worker->index);
        // Sleep interruptibly so the thread can be stopped cleanly.
        const auto retry_until = std::chrono::steady_clock::now() + std::chrono::seconds(30);
        while (rclcpp::ok() && running_.load() && std::chrono::steady_clock::now() < retry_until) {
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
      }
      continue;
    }

    if (!externally_paced) {
      const auto now = std::chrono::steady_clock::now();
      if (now < worker->next_publish_time) {
        std::this_thread::sleep_until(worker->next_publish_time);
      }
    }

    publish_image(*worker);

    if (!externally_paced) {
      worker->next_publish_time += worker->frame_period;
      const auto after_publish = std::chrono::steady_clock::now();
      if (worker->next_publish_time <= after_publish) {
        worker->next_publish_time = after_publish + worker->frame_period;
      }
    }
  }
}

void CameraPublisher::publish_image(CameraWorker & worker)
{
  if (worker.info.camera_type == CameraType::REALSENSE) {
    publish_realsense_image(worker);
    return;
  }

  switch (worker.info.compress_type) {
    case CompressType::NONE_COMPRESS:
      publish_opencv_image(worker);
      return;
    case CompressType::MJPEG_COMPRESS:
      publish_mjpeg_image(worker);
      return;
  }
}

void CameraPublisher::publish_realsense_image(CameraWorker & worker)
{
#if defined(HAVE_REALSENSE2)
  if (!worker.realsense_pipeline) {
    return;
  }

  rs2::frameset frames;
  try {
    // Block until the camera produces a new frame so the camera drives the publish rate.
    constexpr unsigned int wait_timeout_ms = 1000;
    if (!worker.realsense_pipeline->try_wait_for_frames(&frames, wait_timeout_ms)) {
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "RealSense camera[%zu] frame wait timed out after %ums",
          worker.index,
          wait_timeout_ms);
      return;
    }
  } catch (const rs2::error & e) {
    RCLCPP_WARN_THROTTLE(
        node_.get_logger(),
        *node_.get_clock(),
        2000,
        "Failed to wait for RealSense camera[%zu] frames: %s",
        worker.index,
        e.what());
    return;
  } catch (const std::exception & e) {
    RCLCPP_WARN_THROTTLE(
        node_.get_logger(),
        *node_.get_clock(),
        2000,
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

  std_msgs::msg::Header header;
  header.stamp = node_.get_clock()->now();
  header.frame_id = worker.frame_id;

  switch (worker.info.compress_type) {
    case CompressType::NONE_COMPRESS: {
      auto image_msg =
          cv_bridge::CvImage(header, sensor_msgs::image_encodings::BGR8, frame).toImageMsg();
      worker.image_publisher->publish(*image_msg);

      if (worker.info.enable_socket_publish) {
        std::vector<uint8_t> jpeg_data;
        const std::vector<int> encode_params = {cv::IMWRITE_JPEG_QUALITY, 80};
        if (cv::imencode(".jpg", frame, jpeg_data, encode_params)) {
          publish_socket_jpeg(worker, jpeg_data);
        } else {
          RCLCPP_WARN_THROTTLE(
              node_.get_logger(),
              *node_.get_clock(),
              2000,
              "Failed to encode RealSense camera[%zu] frame as JPEG for ZeroMQ socket publishing",
              worker.index);
        }
      }
      return;
    }
    case CompressType::MJPEG_COMPRESS: {
      auto compressed_msg = std::make_shared<sensor_msgs::msg::CompressedImage>();
      compressed_msg->header = header;
      compressed_msg->format = "jpeg";
      const std::vector<int> encode_params = {cv::IMWRITE_JPEG_QUALITY, 80};
      if (!cv::imencode(".jpg", frame, compressed_msg->data, encode_params)) {
        RCLCPP_WARN_THROTTLE(
            node_.get_logger(),
            *node_.get_clock(),
            2000,
            "Failed to encode RealSense camera[%zu] frame to JPEG",
            worker.index);
        return;
      }

      worker.compressed_image_publisher->publish(*compressed_msg);
      publish_socket_jpeg(worker, compressed_msg->data);
      return;
    }
  }
#else
  (void)worker;
#endif
}

void CameraPublisher::publish_opencv_image(CameraWorker & worker)
{
  cv::Mat frame;
  worker.video_cap >> frame;
  if (frame.empty()) {
    return;
  }
  if (worker.use_software_flip) {
    cv::flip(frame, frame, -1);
  }

  std_msgs::msg::Header header;
  header.stamp = node_.get_clock()->now();
  header.frame_id = worker.frame_id;
  auto image_msg =
      cv_bridge::CvImage(header, sensor_msgs::image_encodings::BGR8, frame).toImageMsg();
  worker.image_publisher->publish(*image_msg);

  if (worker.info.enable_socket_publish) {
    std::vector<uint8_t> jpeg_data;
    const std::vector<int> encode_params = {cv::IMWRITE_JPEG_QUALITY, 80};
    if (cv::imencode(".jpg", frame, jpeg_data, encode_params)) {
      publish_socket_jpeg(worker, jpeg_data);
    } else {
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Failed to encode camera[%zu] frame as JPEG for ZeroMQ socket publishing",
          worker.index);
    }
  }
}

void CameraPublisher::publish_mjpeg_image(CameraWorker & worker)
{
  if (!worker.use_direct_mjpeg_capture) {
    cv::Mat frame;
    worker.video_cap >> frame;
    if (frame.empty()) {
      return;
    }
    if (worker.use_software_flip) {
      cv::flip(frame, frame, -1);
    }

    auto compressed_msg = std::make_shared<sensor_msgs::msg::CompressedImage>();
    compressed_msg->header.stamp = node_.get_clock()->now();
    compressed_msg->header.frame_id = worker.frame_id;
    compressed_msg->format = "jpeg";
    const std::vector<int> encode_params = {cv::IMWRITE_JPEG_QUALITY, 80};
    if (!cv::imencode(".jpg", frame, compressed_msg->data, encode_params)) {
      RCLCPP_WARN_THROTTLE(
          node_.get_logger(),
          *node_.get_clock(),
          2000,
          "Failed to encode camera[%zu] frame to JPEG in MJPEG fallback path",
          worker.index);
      return;
    }

    worker.compressed_image_publisher->publish(*compressed_msg);
    publish_socket_jpeg(worker, compressed_msg->data);
    return;
  }

  auto compressed_msg = std::make_shared<sensor_msgs::msg::CompressedImage>();
  compressed_msg->header.stamp = node_.get_clock()->now();
  compressed_msg->header.frame_id = worker.frame_id;
  compressed_msg->format = "jpeg";

  std::vector<uint8_t> direct_jpeg;
  if (read_direct_mjpeg_frame(worker, direct_jpeg)) {
    compressed_msg->data = std::move(direct_jpeg);
    worker.compressed_image_publisher->publish(*compressed_msg);
    publish_socket_jpeg(worker, compressed_msg->data);
  } else if (errno != EAGAIN) {
    RCLCPP_WARN_THROTTLE(
        node_.get_logger(),
        *node_.get_clock(),
        2000,
        "Failed to read direct MJPEG frame for camera[%zu]",
        worker.index);
  }
}

bool CameraPublisher::configure_hardware_flip(CameraWorker & worker)
{
  const auto & info = worker.info;
  if (!info.flip) {
    return true;
  }

  int fd = open(info.device_name.c_str(), O_RDWR | O_NONBLOCK);
  if (fd < 0) {
    RCLCPP_WARN(
        node_.get_logger(),
        "Failed to open camera[%zu] for hardware flip configuration on %s: %s",
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
        RCLCPP_WARN(
            node_.get_logger(),
            "Failed to query %s for camera[%zu]: %s",
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
      RCLCPP_WARN(
          node_.get_logger(),
          "Failed to set %s=%d for camera[%zu]: %s",
          control_name,
          flip_value,
          worker.index,
          std::strerror(errno));
      continue;
    }
    configured_any = true;
  }

  if (!queried_any) {
    RCLCPP_WARN(
        node_.get_logger(),
        "Camera[%zu] does not expose V4L2 flip controls; output keeps device default orientation",
        worker.index);
  } else if (!configured_any) {
    RCLCPP_WARN(
        node_.get_logger(),
        "Camera[%zu] flip controls found but could not be configured; output keeps device "
        "default orientation",
        worker.index);
  } else {
    RCLCPP_INFO(
        node_.get_logger(),
        "Camera[%zu] hardware flip configured to %s",
        worker.index,
        info.flip ? "true" : "false");
  }

  close(fd);
  return configured_any;
}
