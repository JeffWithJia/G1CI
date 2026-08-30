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
#include <unistd.h>

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

struct CameraPublisher::CameraWorker
{
  CameraInfo info;
  size_t index{0};
  std::string frame_id;
  std::string realsense_serial_number;
  bool use_software_flip{false};

  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr image_publisher;
  rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr compressed_image_publisher;
  std::mutex pico_video_mutex;
  std::shared_ptr<teleop_server::PicoVideoStreamer> pico_video_streamer;
  cv::VideoCapture video_cap;
  std::thread publish_thread;
  std::chrono::nanoseconds frame_period{0};
  std::chrono::steady_clock::time_point next_publish_time;

  bool camera_initialized{false};

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

    if (!init_camera_worker(*worker)) {
      cleanup_camera_worker(*worker);
      TELEOP_LOG_ERROR("Failed to initialize camera[%zu], will retry every 30s",
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
    if (!init_pico_video_streamer(*worker)) {
      TELEOP_LOG_WARN("Failed to restart Pico video streamer for camera[%zu] with host %s",
          worker->index,
          host.c_str());
    }
  }
}

bool CameraPublisher::init_camera_worker(CameraWorker & worker)
{
  const auto & info = worker.info;
  if (info.camera_type == CameraType::V4L2 && info.device_name.empty()) {
    TELEOP_LOG_ERROR("Camera %zu has empty device name", worker.index);
    return false;
  }
  if (info.frame_rate <= 0) {
    TELEOP_LOG_ERROR("Camera %zu has invalid frame_rate: %d",
        worker.index,
        info.frame_rate);
    return false;
  }
  if (info.image_width <= 0 || info.image_height <= 0) {
    TELEOP_LOG_ERROR("Camera %zu has invalid image size: %dx%d",
        worker.index,
        info.image_width,
        info.image_height);
    return false;
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
    return init_realsense_capture(worker);
  }

  return init_opencv_capture(worker);
}

bool CameraPublisher::open_opencv_capture(CameraWorker & worker)
{
  const auto & info = worker.info;
  const bool opened_with_v4l2 = worker.video_cap.open(info.device_name, cv::CAP_V4L2);
  if (!opened_with_v4l2) {
    TELEOP_LOG_WARN("Failed to open camera[%zu] with V4L2 backend: %s, fallback to default backend",
        worker.index,
        info.device_name.c_str());
    worker.video_cap.open(info.device_name, cv::CAP_ANY);
  }
  if (!worker.video_cap.isOpened()) {
    TELEOP_LOG_ERROR("Could not open camera[%zu]: %s",
        worker.index,
        info.device_name.c_str());
    return false;
  }

  worker.video_cap.set(cv::CAP_PROP_FRAME_WIDTH, static_cast<double>(info.image_width));
  worker.video_cap.set(cv::CAP_PROP_FRAME_HEIGHT, static_cast<double>(info.image_height));
  worker.video_cap.set(cv::CAP_PROP_FPS, static_cast<double>(info.frame_rate));
  worker.video_cap.set(cv::CAP_PROP_FOURCC, cv::VideoWriter::fourcc('Y', 'U', 'Y', 'V'));
  return true;
}

bool CameraPublisher::init_opencv_capture(CameraWorker & worker)
{
  const auto & info = worker.info;
  if (!open_opencv_capture(worker)) {
    return false;
  }

  if (info.compress_type == CompressType::NONE_COMPRESS) {
    if (!worker.image_publisher) {
      const std::string topic_name = info.topic_name + "/raw_image";
      worker.image_publisher = node_.create_publisher<sensor_msgs::msg::Image>(topic_name, 10);
      TELEOP_LOG_INFO("Camera[%zu] publishes ROS raw topic: %s",
          worker.index,
          topic_name.c_str());
    }
  } else if (info.compress_type == CompressType::MJPEG_COMPRESS) {
    if (!worker.compressed_image_publisher) {
      const std::string topic_name = info.topic_name + "/compressed_image";
      worker.compressed_image_publisher =
          node_.create_publisher<sensor_msgs::msg::CompressedImage>(topic_name, 10);
      TELEOP_LOG_INFO("Camera[%zu] publishes ROS JPEG topic from raw frames: %s",
          worker.index,
          topic_name.c_str());
    }
  } else {
    TELEOP_LOG_ERROR("Camera %zu has unsupported compress type", worker.index);
    return false;
  }
  return true;
}

bool CameraPublisher::init_realsense_capture(CameraWorker & worker)
{
#if defined(HAVE_REALSENSE2)
  const auto & info = worker.info;
  if (info.compress_type != CompressType::NONE_COMPRESS &&
      info.compress_type != CompressType::MJPEG_COMPRESS) {
    TELEOP_LOG_ERROR("Camera %zu has unsupported compress type", worker.index);
    return false;
  }

  // Start the RealSense pipeline first; only create publishers when the hardware is confirmed
  // working, so failed retries do not churn ROS publishers and break subscribers.
  std::string serial_number;
  try {
    const std::vector<RealSenseDeviceInfo> devices = list_realsense_devices();
    if (devices.empty()) {
      TELEOP_LOG_ERROR("No RealSense camera was found for camera[%zu]",
          worker.index);
      return false;
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
    return false;
  } catch (const std::exception & e) {
    TELEOP_LOG_ERROR("Failed to start RealSense camera[%zu] serial '%s': %s",
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
      TELEOP_LOG_INFO("Camera[%zu] publishes RealSense ROS topic: %s",
          worker.index,
          topic_name.c_str());
    }
  } else {
    if (!worker.compressed_image_publisher) {
      const std::string topic_name = info.topic_name + "/compressed_image";
      worker.compressed_image_publisher =
          node_.create_publisher<sensor_msgs::msg::CompressedImage>(topic_name, 10);
      TELEOP_LOG_INFO("Camera[%zu] publishes RealSense ROS topic: %s",
          worker.index,
          topic_name.c_str());
    }
  }

  return true;
#else
  TELEOP_LOG_ERROR("Camera[%zu] is configured as REALSENSE, but teleop_server was built without librealsense2",
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
  if (worker.video_cap.isOpened()) {
    worker.video_cap.release();
  }
  cleanup_realsense(worker);
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

bool CameraPublisher::init_pico_video_streamer(CameraWorker & worker)
{
  if (!worker.info.enable_pico_video) {
    return true;
  }

  teleop_server::PicoVideoStreamerConfig config = worker.info.pico_video;
  if (config.host.empty()) {
    TELEOP_LOG_INFO("Camera[%zu] waits for XRoboToolkit device IP before starting Pico video",
        worker.index);
    return true;
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
  return true;
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
      TELEOP_LOG_INFO("Retrying initialization for camera[%zu]...", worker->index);
      cleanup_camera_worker(*worker);
      if (init_camera_worker(*worker)) {
        worker->camera_initialized = true;
        worker->next_publish_time = std::chrono::steady_clock::now();
        TELEOP_LOG_INFO("Camera[%zu] initialized successfully on retry",
            worker->index);
      } else {
        cleanup_camera_worker(*worker);
        TELEOP_LOG_WARN("Camera[%zu] initialization failed, retrying in 30s",
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
    // Block until the camera produces a new frame so the camera drives the publish rate.
    constexpr unsigned int wait_timeout_ms = 1000;
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

  publish_frame(worker, frame);
#else
  (void)worker;
#endif
}

void CameraPublisher::publish_v4l2_image(CameraWorker & worker)
{
  cv::Mat frame;
  worker.video_cap >> frame;
  if (frame.empty()) {
    return;
  }
  if (worker.use_software_flip) {
    cv::flip(frame, frame, -1);
  }

  publish_frame(worker, frame);
}

void CameraPublisher::publish_frame(CameraWorker & worker, const cv::Mat & frame)
{
  if (frame.empty()) {
    return;
  }

  std::shared_ptr<teleop_server::PicoVideoStreamer> pico_video_streamer;
  {
    std::lock_guard<std::mutex> lock(worker.pico_video_mutex);
    pico_video_streamer = worker.pico_video_streamer;
  }
  if (pico_video_streamer) {
    pico_video_streamer->push_frame(frame);
  }

  std_msgs::msg::Header header;
  header.stamp = node_.get_clock()->now();
  header.frame_id = worker.frame_id;

  switch (worker.info.compress_type) {
    case CompressType::NONE_COMPRESS: {
      if (!worker.image_publisher) {
        return;
      }
      auto image_msg =
          cv_bridge::CvImage(header, sensor_msgs::image_encodings::BGR8, frame).toImageMsg();
      worker.image_publisher->publish(*image_msg);
      return;
    }
    case CompressType::MJPEG_COMPRESS: {
      if (!worker.compressed_image_publisher) {
        return;
      }
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
      return;
    }
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
