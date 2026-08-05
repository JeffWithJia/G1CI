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

#include "pico/pico_82d_converter.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace teleop_server
{
namespace
{
constexpr int kPicoJointCount = 24;
constexpr int kSmplJointCount = 55;
constexpr double kEpsilon = 1.0e-9;

const std::array<int, kPicoJointCount> kPicoParentIndices = {
    -1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19, 20, 22};

const std::array<int, kSmplJointCount> kSmplParents = {
    -1, 0,  0,  0,  1,  2,  3,  4,  5,  6,  7,  8,  9,  9,  9,  12, 13, 14, 16,
    17, 18, 19, 15, 15, 15, 20, 25, 26, 20, 28, 29, 20, 31, 32, 20, 34, 35, 20,
    37, 38, 21, 40, 41, 21, 43, 44, 21, 46, 47, 21, 49, 50, 21, 52, 53};

const std::array<int, kPicoJointCount> kOutputJointIndex = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 39, 54};

const std::array<Eigen::Vector3d, kSmplJointCount> kSmplRestJoints = {{
    Eigen::Vector3d{0.003123260569, -0.3514074683, 0.01203655079},
    Eigen::Vector3d{0.06131265312, -0.4441709518, -0.01396463532},
    Eigen::Vector3d{-0.06014421582, -0.4553154707, -0.0092138201},
    Eigen::Vector3d{0.0003605620586, -0.2415168583, -0.01558108069},
    Eigen::Vector3d{0.1160081103, -0.8229243755, -0.02336069942},
    Eigen::Vector3d{-0.1043541729, -0.8176955581, -0.02603770234},
    Eigen::Vector3d{0.009808260947, -0.1096636057, -0.02152106538},
    Eigen::Vector3d{0.07255466282, -1.225983858, -0.05523664504},
    Eigen::Vector3d{-0.08893736452, -1.228423357, -0.04622997344},
    Eigen::Vector3d{-0.001522152917, -0.05742844939, 0.006925832015},
    Eigen::Vector3d{0.119811967, -1.283981204, 0.06297968328},
    Eigen::Vector3d{-0.1277497709, -1.286751747, 0.07281902432},
    Eigen::Vector3d{-0.01368661132, 0.1077386066, -0.02468951046},
    Eigen::Vector3d{0.04484200478, 0.02751527354, -0.0002946509048},
    Eigen::Vector3d{-0.04921707883, 0.02691022307, -0.006474069785},
    Eigen::Vector3d{0.01109687332, 0.2681904137, -0.003952245228},
    Eigen::Vector3d{0.164081037, 0.0852432996, -0.01575559005},
    Eigen::Vector3d{-0.151794821, 0.08043467253, -0.01914259791},
    Eigen::Vector3d{0.4182038903, 0.01309278142, -0.05821444467},
    Eigen::Vector3d{-0.4229443669, 0.04394219071, -0.0456096828},
    Eigen::Vector3d{0.6701906323, 0.03631401062, -0.06068652496},
    Eigen::Vector3d{-0.6722118258, 0.0394096449, -0.06093486771},
    Eigen::Vector3d{-0.004667762667, 0.267670691, -0.009591402486},
    Eigen::Vector3d{0.03159928322, 0.310832113, 0.0621951744},
    Eigen::Vector3d{-0.03159987554, 0.3108319342, 0.06219434366},
    Eigen::Vector3d{0.7720924616, 0.02762586996, -0.04133538902},
    Eigen::Vector3d{0.8040408492, 0.02984413132, -0.03832494467},
    Eigen::Vector3d{0.8265857697, 0.02749405056, -0.0382703729},
    Eigen::Vector3d{0.7795881629, 0.02998634242, -0.06466733664},
    Eigen::Vector3d{0.8101993799, 0.03079374321, -0.06868999451},
    Eigen::Vector3d{0.8337147832, 0.02878499404, -0.07280422002},
    Eigen::Vector3d{0.754237473, 0.02177468129, -0.1044361368},
    Eigen::Vector3d{0.7697082162, 0.02064310014, -0.1164355725},
    Eigen::Vector3d{0.7852417231, 0.0189785324, -0.1276525259},
    Eigen::Vector3d{0.7676349282, 0.02704637684, -0.08803117275},
    Eigen::Vector3d{0.795681715, 0.02853183635, -0.09329714626},
    Eigen::Vector3d{0.818503499, 0.02707355097, -0.1003914326},
    Eigen::Vector3d{0.7108263969, 0.01833728515, -0.03507564589},
    Eigen::Vector3d{0.7278420925, 0.01931309886, -0.01009750552},
    Eigen::Vector3d{0.7483652234, 0.01415354386, 0.005425570998},
    Eigen::Vector3d{-0.772092402, 0.02762678079, -0.04133493081},
    Eigen::Vector3d{-0.8040405512, 0.02984467335, -0.03832409531},
    Eigen::Vector3d{-0.8265854716, 0.02749528736, -0.03826868534},
    Eigen::Vector3d{-0.7795882225, 0.02998769842, -0.06466869265},
    Eigen::Vector3d{-0.8101993799, 0.03079517186, -0.06869153678},
    Eigen::Vector3d{-0.8337149024, 0.02878585272, -0.07280556113},
    Eigen::Vector3d{-0.7542385459, 0.02177520655, -0.1044378057},
    Eigen::Vector3d{-0.7697089911, 0.02064313367, -0.1164365485},
    Eigen::Vector3d{-0.7852423787, 0.01897839829, -0.1276528388},
    Eigen::Vector3d{-0.7676352859, 0.02704770304, -0.08803359419},
    Eigen::Vector3d{-0.795681715, 0.02853290737, -0.09329950064},
    Eigen::Vector3d{-0.8185037374, 0.02707411721, -0.1003922448},
    Eigen::Vector3d{-0.7108249664, 0.01833522134, -0.03507352248},
    Eigen::Vector3d{-0.7278403044, 0.01931131817, -0.01009594277},
    Eigen::Vector3d{-0.7483659387, 0.01415411662, 0.005425604992},
}};

Eigen::Quaterniond remove_smpl_base_rot(const Eigen::Quaterniond & quat)
{
  return Pico82dConverter::normalized(quat * Eigen::Quaterniond(0.5, -0.5, -0.5, -0.5));
}

Eigen::Quaterniond smpl_root_y_to_z_up(const Eigen::Quaterniond & quat)
{
  const Eigen::Quaterniond base(Eigen::AngleAxisd(M_PI / 2.0, Eigen::Vector3d::UnitX()));
  return Pico82dConverter::normalized(base * quat);
}

std::array<Eigen::Quaterniond, kSmplJointCount> compute_full_pose_rotations(
    const std::array<Eigen::Vector3d, 21> & body_pose,
    const Eigen::Vector3d & global_orient)
{
  std::array<Eigen::Quaterniond, kSmplJointCount> rotations;
  rotations[0] = Pico82dConverter::angle_axis_to_quaternion(global_orient);
  for (int i = 0; i < 21; ++i) {
    rotations[i + 1] = Pico82dConverter::angle_axis_to_quaternion(body_pose[i]);
  }
  for (int i = 22; i < kSmplJointCount; ++i) {
    rotations[i] = Eigen::Quaterniond::Identity();
  }
  return rotations;
}

std::array<Eigen::Vector3d, 24> compute_human_joints(
    const std::array<Eigen::Vector3d, 21> & body_pose,
    const Eigen::Vector3d & global_orient)
{
  const auto rotations = compute_full_pose_rotations(body_pose, global_orient);
  std::array<Eigen::Isometry3d, kSmplJointCount> transforms;

  for (int i = 0; i < kSmplJointCount; ++i) {
    Eigen::Vector3d rel_joint = kSmplRestJoints[i];
    if (kSmplParents[i] >= 0) {
      rel_joint -= kSmplRestJoints[kSmplParents[i]];
    }

    Eigen::Isometry3d local = Eigen::Isometry3d::Identity();
    local.linear() = rotations[i].toRotationMatrix();
    local.translation() = rel_joint;

    if (kSmplParents[i] < 0) {
      transforms[i] = local;
    } else {
      transforms[i] = transforms[kSmplParents[i]] * local;
    }
  }

  std::array<Eigen::Vector3d, 24> output;
  for (size_t i = 0; i < output.size(); ++i) {
    output[i] = transforms[kOutputJointIndex[i]].translation();
  }
  return output;
}

std::array<Eigen::Vector3d, 21> interpolate_pose_axis_angle(
    const std::array<Eigen::Vector3d, 21> & from,
    const std::array<Eigen::Vector3d, 21> & to,
    double alpha)
{
  std::array<Eigen::Vector3d, 21> out;
  for (size_t i = 0; i < out.size(); ++i) {
    const Eigen::Quaterniond q = Pico82dConverter::nlerp_near(
        Pico82dConverter::angle_axis_to_quaternion(from[i]),
        Pico82dConverter::angle_axis_to_quaternion(to[i]),
        alpha);
    out[i] = Pico82dConverter::quaternion_to_angle_axis(q);
  }
  return out;
}

Eigen::Quaterniond decompose_swing_axis_angle(
    const Eigen::Vector3d & axis_angle,
    const Eigen::Vector3d & axis)
{
  const double angle = axis_angle.norm();
  const Eigen::Quaterniond q =
      angle > kEpsilon ? Eigen::Quaterniond(Eigen::AngleAxisd(angle, axis_angle / angle))
                       : Eigen::Quaterniond::Identity();
  const Eigen::Vector3d q_vec(q.x(), q.y(), q.z());
  const Eigen::Vector3d twist_vec = q_vec.dot(axis) * axis;
  Eigen::Quaterniond q_twist(q.w(), twist_vec.x(), twist_vec.y(), twist_vec.z());
  q_twist = Pico82dConverter::normalized(q_twist);
  return Pico82dConverter::normalized(q_twist.conjugate() * q);
}

double clamp_deadzone(double value, double deadzone, double lo, double hi)
{
  const double filtered = std::abs(value) < deadzone ? 0.0 : value;
  return std::clamp(filtered, lo, hi);
}

Eigen::Vector3d euler_xyz_from_quaternion(const Eigen::Quaterniond & quat)
{
  const Eigen::Matrix3d rot = Pico82dConverter::normalized(quat).toRotationMatrix();
  const double sy = std::clamp(rot(0, 2), -1.0, 1.0);
  const double y = std::asin(sy);
  const double cy = std::cos(y);

  if (std::abs(cy) > 1.0e-8) {
    return Eigen::Vector3d(
        std::atan2(-rot(1, 2), rot(2, 2)),
        y,
        std::atan2(-rot(0, 1), rot(0, 0)));
  }

  return Eigen::Vector3d(std::atan2(rot(1, 0), rot(1, 1)), y, 0.0);
}
}  // namespace

Pico82dConverter::Pico82dConverter(double target_fps) : target_fps_(std::max(1.0, target_fps)) {}

void Pico82dConverter::reset()
{
  has_prev_ = false;
  prev_stamp_ns_ = 0;
  next_target_ns_ = 0;
  prev_ = SmplData{};
}

std::optional<std::vector<double>> Pico82dConverter::process(
    const std::vector<Eigen::Isometry3d> & body_poses,
    int64_t timestamp_ns)
{
  if (body_poses.size() < kPicoJointCount) {
    throw std::runtime_error("Pico82dConverter requires at least 24 body poses");
  }

  const SmplData current = compute_smpl_data(body_poses);
  const int64_t step_ns = static_cast<int64_t>(1.0e9 / std::max(1.0, target_fps_));

  if (!has_prev_) {
    prev_ = current;
    prev_stamp_ns_ = timestamp_ns;
    next_target_ns_ = timestamp_ns;
    has_prev_ = true;
    return std::nullopt;
  }

  if (timestamp_ns <= prev_stamp_ns_) {
    return std::nullopt;
  }

  if (next_target_ns_ < prev_stamp_ns_) {
    next_target_ns_ = prev_stamp_ns_;
  }
  if (next_target_ns_ > timestamp_ns) {
    return std::nullopt;
  }

  const double denom = static_cast<double>(timestamp_ns - prev_stamp_ns_);
  const double raw_alpha =
      denom > 0.0 ? static_cast<double>(next_target_ns_ - prev_stamp_ns_) / denom : 1.0;
  const double alpha = std::clamp(raw_alpha, 0.0, 1.0);

  std::array<Eigen::Vector3d, 24> joints_local;
  for (size_t i = 0; i < joints_local.size(); ++i) {
    joints_local[i] = (1.0 - alpha) * prev_.joints_local[i] + alpha * current.joints_local[i];
  }
  const auto pose_axis_angle =
      interpolate_pose_axis_angle(prev_.pose_axis_angle, current.pose_axis_angle, alpha);
  const Eigen::Quaterniond root_quat = nlerp_near(prev_.root_quat, current.root_quat, alpha);

  next_target_ns_ += step_ns;
  prev_ = current;
  prev_stamp_ns_ = timestamp_ns;
  return build_pose_82d(pose_axis_angle, joints_local, root_quat);
}

Pico82dConverter::SmplData Pico82dConverter::compute_smpl_data(
    const std::vector<Eigen::Isometry3d> & body_poses) const
{
  std::array<Eigen::Quaterniond, kPicoJointCount> global_rots;
  const Eigen::Quaterniond y_180(Eigen::AngleAxisd(M_PI, Eigen::Vector3d::UnitY()));
  for (int i = 0; i < kPicoJointCount; ++i) {
    global_rots[i] = normalized(Eigen::Quaterniond(body_poses[i].linear()) * y_180);
  }

  std::array<Eigen::Vector3d, kPicoJointCount> pose_axis_angle;
  for (int i = 0; i < kPicoJointCount; ++i) {
    Eigen::Quaterniond local = global_rots[i];
    const int parent = kPicoParentIndices[i];
    if (parent >= 0) {
      local = global_rots[parent].conjugate() * global_rots[i];
    }
    pose_axis_angle[i] = quaternion_to_angle_axis(normalized(local));
  }

  SmplData out;
  for (size_t i = 0; i < out.pose_axis_angle.size(); ++i) {
    out.pose_axis_angle[i] = pose_axis_angle[i + 1];
  }

  Eigen::Quaterniond global_orient_quat = angle_axis_to_quaternion(pose_axis_angle[0]);
  global_orient_quat = smpl_root_y_to_z_up(global_orient_quat);
  const Eigen::Vector3d global_orient_new = quaternion_to_angle_axis(global_orient_quat);

  const auto joints = compute_human_joints(out.pose_axis_angle, global_orient_new);
  out.root_quat = remove_smpl_base_rot(global_orient_quat);
  const Eigen::Quaterniond root_inv = out.root_quat.conjugate();
  for (size_t i = 0; i < out.joints_local.size(); ++i) {
    out.joints_local[i] = root_inv * joints[i];
  }
  return out;
}

std::vector<double> Pico82dConverter::build_pose_82d(
    const std::array<Eigen::Vector3d, 21> & pose_axis_angle,
    const std::array<Eigen::Vector3d, 24> & joints_local,
    const Eigen::Quaterniond & root_quat) const
{
  const Eigen::Vector3d l_elbow_aa = pose_axis_angle[17];
  const Eigen::Vector3d l_wrist_aa = pose_axis_angle[19];
  const Eigen::Vector3d r_elbow_aa = pose_axis_angle[18];
  const Eigen::Vector3d r_wrist_aa = pose_axis_angle[20];

  const Eigen::Vector3d y_axis = Eigen::Vector3d::UnitY();
  const Eigen::Vector3d l_elbow_euler =
      euler_xyz_from_quaternion(decompose_swing_axis_angle(l_elbow_aa, y_axis));
  const Eigen::Vector3d r_elbow_euler =
      euler_xyz_from_quaternion(decompose_swing_axis_angle(r_elbow_aa, y_axis));
  const Eigen::Vector3d l_wrist_euler =
      euler_xyz_from_quaternion(angle_axis_to_quaternion(l_wrist_aa));
  const Eigen::Vector3d r_wrist_euler =
      euler_xyz_from_quaternion(angle_axis_to_quaternion(r_wrist_aa));

  constexpr double kWristDeadzone = 0.03;
  std::array<double, 6> wrist_6d = {
      clamp_deadzone(l_elbow_euler.x() + l_wrist_euler.x(), kWristDeadzone, -0.9, 0.9),
      clamp_deadzone(l_wrist_euler.y(), kWristDeadzone, -0.8, 0.8),
      clamp_deadzone(l_elbow_euler.z() + l_wrist_euler.z(), kWristDeadzone, -0.8, 0.8),
      clamp_deadzone(-(r_elbow_euler.x() + r_wrist_euler.x()), kWristDeadzone, -0.9, 0.9),
      clamp_deadzone(-r_wrist_euler.y(), kWristDeadzone, -0.8, 0.8),
      clamp_deadzone(r_elbow_euler.z() + r_wrist_euler.z(), kWristDeadzone, -0.8, 0.8),
  };

  std::vector<double> pose_82d;
  pose_82d.reserve(82);
  for (const auto & joint : joints_local) {
    pose_82d.push_back(joint.x());
    pose_82d.push_back(joint.y());
    pose_82d.push_back(joint.z());
  }
  const Eigen::Quaterniond q = normalized(root_quat);
  pose_82d.push_back(q.w());
  pose_82d.push_back(q.x());
  pose_82d.push_back(q.y());
  pose_82d.push_back(q.z());
  pose_82d.insert(pose_82d.end(), wrist_6d.begin(), wrist_6d.end());
  return pose_82d;
}

Eigen::Quaterniond Pico82dConverter::normalized(Eigen::Quaterniond quat)
{
  if (!std::isfinite(quat.w()) || !std::isfinite(quat.x()) || !std::isfinite(quat.y()) ||
      !std::isfinite(quat.z()) || quat.norm() <= kEpsilon) {
    return Eigen::Quaterniond::Identity();
  }
  quat.normalize();
  return quat;
}

Eigen::Quaterniond Pico82dConverter::angle_axis_to_quaternion(const Eigen::Vector3d & axis_angle)
{
  const double theta = axis_angle.norm();
  if (theta <= kEpsilon) {
    return Eigen::Quaterniond::Identity();
  }
  return normalized(Eigen::Quaterniond(Eigen::AngleAxisd(theta, axis_angle / theta)));
}

Eigen::Vector3d Pico82dConverter::quaternion_to_angle_axis(Eigen::Quaterniond quat)
{
  quat = normalized(quat);
  if (quat.w() < 0.0) {
    quat.coeffs() *= -1.0;
  }
  const Eigen::Vector3d vec(quat.x(), quat.y(), quat.z());
  const double sin_theta = vec.norm();
  if (sin_theta <= kEpsilon) {
    return 2.0 * vec;
  }
  const double two_theta = 2.0 * std::atan2(sin_theta, quat.w());
  return vec * (two_theta / sin_theta);
}

Eigen::Quaterniond Pico82dConverter::slerp_near(
    const Eigen::Quaterniond & from,
    const Eigen::Quaterniond & to,
    double alpha)
{
  Eigen::Quaterniond q0 = normalized(from);
  Eigen::Quaterniond q1 = normalized(to);
  if (q0.dot(q1) < 0.0) {
    q1.coeffs() *= -1.0;
  }
  return normalized(q0.slerp(alpha, q1));
}

Eigen::Quaterniond Pico82dConverter::nlerp_near(
    const Eigen::Quaterniond & from,
    const Eigen::Quaterniond & to,
    double alpha)
{
  Eigen::Quaterniond q0 = normalized(from);
  Eigen::Quaterniond q1 = normalized(to);
  if (q0.dot(q1) < 0.0) {
    q1.coeffs() *= -1.0;
  }
  Eigen::Quaterniond out;
  out.coeffs() = (1.0 - alpha) * q0.coeffs() + alpha * q1.coeffs();
  return normalized(out);
}

}  // namespace teleop_server
