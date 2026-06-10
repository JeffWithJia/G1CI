// Copyright 2026 coScene
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <Eigen/Geometry>
#include <json/json.h>

#include "pico/pico_82d_converter.hpp"

namespace
{
constexpr int kExpectedBodyJointCount = 24;

struct Args
{
  std::string input;
  std::string output;
};

void print_usage(const char * argv0)
{
  std::cerr
      << "Usage: " << argv0 << " --input RAW.jsonl --output CPP.jsonl\n"
      << "\n"
      << "Converts Pico raw body-pose JSONL into teleop_server C++ pose_82d JSONL.\n";
}

Args parse_args(int argc, char ** argv)
{
  Args args;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    auto require_value = [&](const std::string & name) -> std::string {
      if (i + 1 >= argc) {
        throw std::runtime_error(name + " requires a value");
      }
      return argv[++i];
    };

    if (arg == "--input") {
      args.input = require_value(arg);
    } else if (arg == "--output") {
      args.output = require_value(arg);
    } else if (arg == "--help" || arg == "-h") {
      print_usage(argv[0]);
      std::exit(0);
    } else {
      throw std::runtime_error("unknown argument: " + arg);
    }
  }

  if (args.input.empty() || args.output.empty()) {
    print_usage(argv[0]);
    throw std::runtime_error("--input and --output are required");
  }
  return args;
}

Json::Value parse_json(const std::string & text)
{
  Json::Value root;
  Json::CharReaderBuilder builder;
  std::string errors;
  std::istringstream input(text);
  if (!Json::parseFromStream(builder, input, &root, &errors)) {
    throw std::runtime_error("failed to parse JSON: " + errors);
  }
  return root;
}

std::string compact_json(const Json::Value & root)
{
  Json::StreamWriterBuilder builder;
  builder["indentation"] = "";
  return Json::writeString(builder, root);
}

std::optional<int64_t> read_int64(const Json::Value & value)
{
  if (value.isIntegral()) {
    return value.asInt64();
  }
  if (value.isDouble()) {
    return static_cast<int64_t>(value.asDouble());
  }
  if (value.isString()) {
    try {
      return std::stoll(value.asString());
    } catch (const std::exception &) {
      return std::nullopt;
    }
  }
  return std::nullopt;
}

std::vector<std::vector<double>> body_pose_array_from_record(const Json::Value & record)
{
  if (!record.isMember("raw_body_joints_pose") || !record["raw_body_joints_pose"].isArray()) {
    throw std::runtime_error("record has no raw_body_joints_pose array");
  }

  std::vector<std::vector<double>> out;
  out.reserve(kExpectedBodyJointCount);
  const Json::Value & body = record["raw_body_joints_pose"];
  for (int i = 0; i < kExpectedBodyJointCount && i < static_cast<int>(body.size()); ++i) {
    if (!body[i].isArray() || body[i].size() < 7) {
      throw std::runtime_error("raw_body_joints_pose[" + std::to_string(i) + "] is invalid");
    }
    std::vector<double> values;
    values.reserve(7);
    for (int j = 0; j < 7; ++j) {
      values.push_back(body[i][j].asDouble());
    }
    out.push_back(std::move(values));
  }
  return out;
}

std::vector<Eigen::Isometry3d> body_poses_from_record(const Json::Value & record)
{
  const auto body = body_pose_array_from_record(record);
  if (body.size() < kExpectedBodyJointCount) {
    throw std::runtime_error("record has fewer than 24 Pico body joints");
  }

  std::vector<Eigen::Isometry3d> poses;
  poses.reserve(kExpectedBodyJointCount);
  for (int i = 0; i < kExpectedBodyJointCount; ++i) {
    const auto & values = body[i];
    Eigen::Quaterniond quat(values[6], values[3], values[4], values[5]);
    if (quat.norm() <= 1.0e-9) {
      quat = Eigen::Quaterniond::Identity();
    } else {
      quat.normalize();
    }
    Eigen::Isometry3d pose = Eigen::Isometry3d::Identity();
    pose.translation() = Eigen::Vector3d(values[0], values[1], values[2]);
    pose.linear() = quat.toRotationMatrix();
    poses.push_back(pose);
  }
  return poses;
}

int64_t timestamp_from_record(const Json::Value & record, int64_t fallback)
{
  for (const std::string key : {"body_timestamp_ns", "xrt_timestamp_ns", "timestamp_ns"}) {
    const auto timestamp = read_int64(record[key]);
    if (timestamp && *timestamp > 0) {
      return *timestamp;
    }
  }
  return fallback;
}

Json::Value double_array(const std::vector<double> & values)
{
  Json::Value array(Json::arrayValue);
  for (double value : values) {
    array.append(value);
  }
  return array;
}
}  // namespace

int main(int argc, char ** argv)
{
  try {
    const Args args = parse_args(argc, argv);
    std::ifstream input(args.input);
    if (!input) {
      throw std::runtime_error("failed to open input: " + args.input);
    }
    std::ofstream output(args.output);
    if (!output) {
      throw std::runtime_error("failed to open output: " + args.output);
    }

    Json::Value metadata(Json::objectValue);
    metadata["type"] = "metadata";
    metadata["schema"] = "teleop_server_cpp_pose_82d_v1";
    metadata["input"] = args.input;
    metadata["mode"] = "Pico82dConverter::process";
    metadata["hz"] = 50.0;
    output << compact_json(metadata) << '\n';

    teleop_server::Pico82dConverter converter(50.0);
    std::string line;
    int64_t seq = 0;
    int64_t out_seq = 0;
    int64_t fallback_timestamp = 0;
    const int64_t fallback_step_ns = static_cast<int64_t>(1.0e9 / 50.0);

    while (std::getline(input, line)) {
      if (line.empty()) {
        continue;
      }
      Json::Value record = parse_json(line);
      if (record["type"].asString() != "frame") {
        continue;
      }

      fallback_timestamp += fallback_step_ns;
      const int64_t timestamp_ns = timestamp_from_record(record, fallback_timestamp);
      const auto body_poses = body_poses_from_record(record);
      const std::optional<std::vector<double>> pose_82d = converter.process(body_poses, timestamp_ns);
      if (!pose_82d) {
        ++seq;
        continue;
      }

      Json::Value out(Json::objectValue);
      out["type"] = "frame";
      out["seq"] = static_cast<Json::Int64>(out_seq++);
      out["input_seq"] = record.isMember("seq") ? record["seq"] : static_cast<Json::Int64>(seq);
      out["body_timestamp_ns"] = static_cast<Json::Int64>(timestamp_ns);
      out["cpp_pose_82d"] = double_array(*pose_82d);
      out["frame"] = out["cpp_pose_82d"];
      out["name"] = "pico_smpl";
      output << compact_json(out) << '\n';
      ++seq;
    }

    std::cerr << "Wrote " << out_seq << " C++ pose_82d frames to " << args.output << "\n";
    return 0;
  } catch (const std::exception & exc) {
    std::cerr << "pico_82d_convert_dataset: " << exc.what() << "\n";
    return 1;
  }
}
