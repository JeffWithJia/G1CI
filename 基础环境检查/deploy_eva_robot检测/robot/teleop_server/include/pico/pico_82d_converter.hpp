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
#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <utility>
#include <vector>

#include <Eigen/Core>
#include <Eigen/Geometry>

namespace teleop_server
{
class Pico82dConverter
{
public:
  struct Diagnostics
  {
    uint64_t ignored_raw_samples{0};
    uint64_t skipped_target_frames{0};
    uint64_t target_resyncs{0};
  };

  explicit Pico82dConverter(double target_fps = 50.0);

  void ingest(
      const std::vector<Eigen::Isometry3d> & body_poses,
      int64_t timestamp_ns);

  std::optional<std::vector<double>> produce();

  std::optional<std::vector<double>> process(
      const std::vector<Eigen::Isometry3d> & body_poses,
      int64_t timestamp_ns);

  void reset();

  const Diagnostics & diagnostics() const;

  int64_t last_output_timestamp_ns() const;

  static Eigen::Quaterniond normalized(Eigen::Quaterniond quat);

  static Eigen::Quaterniond angle_axis_to_quaternion(const Eigen::Vector3d & axis_angle);

  static Eigen::Vector3d quaternion_to_angle_axis(Eigen::Quaterniond quat);

  static Eigen::Quaterniond
  slerp_near(const Eigen::Quaterniond & from, const Eigen::Quaterniond & to, double alpha);

  static Eigen::Quaterniond
  nlerp_near(const Eigen::Quaterniond & from, const Eigen::Quaterniond & to, double alpha);

private:
  struct SmplData
  {
    std::array<Eigen::Vector3d, 21> pose_axis_angle{};
    std::array<Eigen::Vector3d, 24> joints_local{};
    Eigen::Quaterniond root_quat{Eigen::Quaterniond::Identity()};
  };

  struct ProcessedSample
  {
    int64_t stamp_ns{0};
    SmplData data;
  };

  using ProcessedSamplePair = std::pair<const ProcessedSample *, const ProcessedSample *>;

  SmplData compute_smpl_data(const std::vector<Eigen::Isometry3d> & body_poses) const;

  std::optional<ProcessedSamplePair> find_bracketing_samples(int64_t target_ns) const;

  std::vector<double> interpolate_pose_82d(
      const ProcessedSample & previous,
      const ProcessedSample & current,
      double alpha) const;

  std::vector<double> build_pose_82d(
      const std::array<Eigen::Vector3d, 21> & pose_axis_angle,
      const std::array<Eigen::Vector3d, 24> & joints_local,
      const Eigen::Quaterniond & root_quat) const;

  static constexpr size_t kProcessedSampleCacheSize = 8;
  static constexpr double kTargetFps = 50.0;

  int64_t step_ns_{20000000};
  int64_t next_target_ns_{0};
  int64_t last_output_timestamp_ns_{0};
  int64_t last_processed_raw_stamp_ns_{0};
  bool has_next_target_{false};
  std::deque<ProcessedSample> processed_samples_;
  Diagnostics diagnostics_;
};
}  // namespace teleop_server
