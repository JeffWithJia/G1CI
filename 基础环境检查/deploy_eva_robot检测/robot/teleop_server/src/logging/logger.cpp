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

#include "logging/logger.hpp"

#include <dirent.h>
#include <sys/stat.h>
#include <sys/time.h>

#include <cerrno>
#include <chrono>
#include <cstring>
#include <ctime>
#include <iostream>
#include <stdexcept>

namespace teleop_server::logging
{
namespace
{
std::string runtime_directory()
{
  const char * home = std::getenv("HOME");
  return home == nullptr ? "/tmp/teleop_server/" : std::string(home) + "/.local/state/teleop_server/";
}

void create_directory(const std::string & path)
{
  if (path.empty()) {
    return;
  }

  std::string dir_path = path;
  const size_t last_slash = path.find_last_of('/');
  if (last_slash != std::string::npos) {
    dir_path = path.substr(0, last_slash);
  }

  struct stat st {};
  if (stat(dir_path.c_str(), &st) == 0 && S_ISDIR(st.st_mode)) {
    return;
  }

  size_t pos = 0;
  while ((pos = path.find('/', pos + 1)) != std::string::npos) {
    const std::string sub_dir = path.substr(0, pos);
    if (sub_dir.empty()) {
      continue;
    }

    const int ret = mkdir(sub_dir.c_str(), 0755);
    if (ret == -1 && errno != EEXIST) {
      throw std::runtime_error(
          "Failed to create log directory: " + path + " (errno: " + std::to_string(errno) +
          ", " + std::strerror(errno) + ")");
    }
  }
}

int64_t steady_now_ns()
{
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}
}  // namespace

Logger & Logger::instance()
{
  static Logger instance;
  return instance;
}

Logger::Logger()
{
  log_dir_ = runtime_directory() + "log/";
  create_directory(log_dir_);
}

Logger::~Logger()
{
  if (current_file_.is_open()) {
    current_file_.close();
  }
}

void Logger::log(LogLevel level, const std::string & message)
{
  std::lock_guard<std::mutex> lock(mutex_);

  if (!should_log(level, current_level_)) {
    return;
  }

  check_and_rotate_log();

  const std::string line =
      get_current_time_str() + " [" + get_level_string(level) + "] " + message;
  std::cout << get_color_code(level) << line << "\033[0m" << std::endl;

  if (current_file_.is_open()) {
    current_file_ << line << std::endl;
    current_file_.flush();
  }
}

void Logger::set_log_dir(const std::string & dir)
{
  std::lock_guard<std::mutex> lock(mutex_);
  if (log_dir_ == dir) {
    return;
  }

  if (current_file_.is_open()) {
    current_file_.close();
  }

  log_dir_ = dir;
  if (!log_dir_.empty() && log_dir_.back() != '/') {
    log_dir_ += '/';
  }
  create_directory(log_dir_);
  current_date_.clear();
}

void Logger::set_log_level(const std::string & level)
{
  std::lock_guard<std::mutex> lock(mutex_);
  if (current_level_string_ == level) {
    return;
  }

  if (level == "Debug" || level == "DEBUG" || level == "debug") {
    current_level_ = LogLevel::DEBUG;
  } else if (level == "Info" || level == "INFO" || level == "info") {
    current_level_ = LogLevel::INFO;
  } else if (level == "Warn" || level == "WARN" || level == "warn") {
    current_level_ = LogLevel::WARN;
  } else if (level == "Error" || level == "ERROR" || level == "error") {
    current_level_ = LogLevel::ERROR;
  }

  current_level_string_ = level;
}

void Logger::set_log_level(LogLevel level)
{
  std::lock_guard<std::mutex> lock(mutex_);
  current_level_ = level;
}

void Logger::check_and_rotate_log()
{
  const std::string date = get_current_date_str();
  if (date == current_date_ && current_file_.is_open()) {
    return;
  }

  if (current_file_.is_open()) {
    current_file_.close();
  }

  current_date_ = date;
  current_file_.open(get_log_file_name(date), std::ios::app);
  clean_old_logs();
}

void Logger::clean_old_logs() const
{
  constexpr int kMaxDays = 7;
  DIR * dir = opendir(log_dir_.c_str());
  if (dir == nullptr) {
    return;
  }

  struct dirent * entry = nullptr;
  while ((entry = readdir(dir)) != nullptr) {
    if (entry->d_type != DT_REG) {
      continue;
    }

    const std::string filepath = log_dir_ + entry->d_name;
    struct stat file_stat {};
    if (stat(filepath.c_str(), &file_stat) != 0) {
      continue;
    }

    const auto now = std::time(nullptr);
    const double days = difftime(now, file_stat.st_mtime) / (60 * 60 * 24);
    if (days >= kMaxDays - 1) {
      remove(filepath.c_str());
    }
  }
  closedir(dir);
}

std::string Logger::get_log_file_name(const std::string & date) const
{
  return log_dir_ + "teleop_server_" + date + ".log";
}

std::string Logger::get_level_string(LogLevel level)
{
  switch (level) {
    case LogLevel::DEBUG:
      return "DEBUG";
    case LogLevel::INFO:
      return " INFO";
    case LogLevel::WARN:
      return " WARN";
    case LogLevel::ERROR:
      return "ERROR";
  }
  return "UNKNOWN";
}

std::string Logger::get_current_time_str()
{
  timeval tv {};
  gettimeofday(&tv, nullptr);

  const time_t raw_time = tv.tv_sec;
  const tm * time_info = std::localtime(&raw_time);

  char buffer[100];
  std::strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", time_info);

  const int milliseconds = tv.tv_usec / 1000;
  std::ostringstream oss;
  oss << buffer << "." << std::setfill('0') << std::setw(3) << milliseconds;
  return oss.str();
}

std::string Logger::get_current_date_str()
{
  const std::time_t raw_time = std::time(nullptr);
  const tm * time_info = std::localtime(&raw_time);
  char buffer[80];
  std::strftime(buffer, sizeof(buffer), "%Y-%m-%d", time_info);
  return std::string(buffer);
}

bool Logger::should_log(LogLevel msg_level, LogLevel filter_level)
{
  return static_cast<int>(msg_level) >= static_cast<int>(filter_level);
}

std::string Logger::get_color_code(LogLevel level)
{
  switch (level) {
    case LogLevel::DEBUG:
      return "\033[90m";
    case LogLevel::INFO:
      return "\033[37m";
    case LogLevel::WARN:
      return "\033[33m";
    case LogLevel::ERROR:
      return "\033[31m";
  }
  return "";
}

const char * get_filename(const char * path)
{
  const char * filename = strrchr(path, '/');
  return filename == nullptr ? path : filename + 1;
}

std::string format_string(const char * file, int line, const char * format)
{
  std::ostringstream oss;
  oss << "[" << get_filename(file) << ":" << std::setfill('0') << std::setw(3) << line << "] "
      << format;
  return oss.str();
}

bool should_log_throttle(std::atomic<int64_t> & last_log_ns, int64_t duration_ms)
{
  const int64_t now_ns = steady_now_ns();
  const int64_t duration_ns = duration_ms * 1000000;
  int64_t last_ns = last_log_ns.load(std::memory_order_relaxed);

  while (last_ns == 0 || now_ns - last_ns >= duration_ns) {
    if (last_log_ns.compare_exchange_weak(
            last_ns,
            now_ns,
            std::memory_order_relaxed,
            std::memory_order_relaxed)) {
      return true;
    }
  }
  return false;
}

}  // namespace teleop_server::logging
