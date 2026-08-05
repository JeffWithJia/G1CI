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
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

namespace teleop_server::logging
{

enum class LogLevel
{
  DEBUG = 1,
  INFO = 2,
  WARN = 4,
  ERROR = 8
};

class Logger
{
public:
  static Logger & instance();

  Logger();
  ~Logger();

  Logger(const Logger &) = delete;
  Logger & operator=(const Logger &) = delete;

  void log(LogLevel level, const std::string & message);
  void set_log_dir(const std::string & dir);
  void set_log_level(const std::string & level);
  void set_log_level(LogLevel level);

private:
  std::string log_dir_;
  std::mutex mutex_;
  std::ofstream current_file_;
  std::string current_date_;
  LogLevel current_level_{LogLevel::INFO};
  std::string current_level_string_{"info"};

  void check_and_rotate_log();
  void clean_old_logs() const;
  std::string get_log_file_name(const std::string & date) const;

  static std::string get_level_string(LogLevel level);
  static std::string get_current_time_str();
  static std::string get_current_date_str();
  static bool should_log(LogLevel msg_level, LogLevel filter_level);
  static std::string get_color_code(LogLevel level);
};

const char * get_filename(const char * path);
std::string format_string(const char * file, int line, const char * format);
bool should_log_throttle(std::atomic<int64_t> & last_log_ns, int64_t duration_ms);

template<typename ... Args>
inline std::string format_string(const char * file, int line, const char * format, Args... args)
{
  const int size = std::snprintf(nullptr, 0, format, args ...) + 1;
  if (size <= 0) {
    return "Format Error";
  }
  std::vector<char> buffer(static_cast<size_t>(size));
  std::snprintf(buffer.data(), static_cast<size_t>(size), format, args ...);

  std::ostringstream oss;
  oss << "[" << get_filename(file) << ":" << std::setfill('0') << std::setw(3) << line << "] "
      << std::string(buffer.data(), buffer.data() + size - 1);
  return oss.str();
}

}  // namespace teleop_server::logging

#define TELEOP_LOG_INFO(...) \
  ::teleop_server::logging::Logger::instance().log( \
      ::teleop_server::logging::LogLevel::INFO, \
      ::teleop_server::logging::format_string(__FILE__, __LINE__, __VA_ARGS__))

#define TELEOP_LOG_WARN(...) \
  ::teleop_server::logging::Logger::instance().log( \
      ::teleop_server::logging::LogLevel::WARN, \
      ::teleop_server::logging::format_string(__FILE__, __LINE__, __VA_ARGS__))

#define TELEOP_LOG_ERROR(...) \
  ::teleop_server::logging::Logger::instance().log( \
      ::teleop_server::logging::LogLevel::ERROR, \
      ::teleop_server::logging::format_string(__FILE__, __LINE__, __VA_ARGS__))

#define TELEOP_LOG_DEBUG(...) \
  ::teleop_server::logging::Logger::instance().log( \
      ::teleop_server::logging::LogLevel::DEBUG, \
      ::teleop_server::logging::format_string(__FILE__, __LINE__, __VA_ARGS__))

#define TELEOP_LOG_INFO_ONCE(...) \
  do { \
    static std::atomic_bool logged{false}; \
    bool expected = false; \
    if (logged.compare_exchange_strong(expected, true)) { \
      TELEOP_LOG_INFO(__VA_ARGS__); \
    } \
  } while (0)

#define TELEOP_LOG_WARN_THROTTLE(_duration_ms, ...) \
  do { \
    static std::atomic<int64_t> last_log_ns{0}; \
    if (::teleop_server::logging::should_log_throttle(last_log_ns, (_duration_ms))) { \
      TELEOP_LOG_WARN(__VA_ARGS__); \
    } \
  } while (0)

#define TELEOP_LOG_ERROR_THROTTLE(_duration_ms, ...) \
  do { \
    static std::atomic<int64_t> last_log_ns{0}; \
    if (::teleop_server::logging::should_log_throttle(last_log_ns, (_duration_ms))) { \
      TELEOP_LOG_ERROR(__VA_ARGS__); \
    } \
  } while (0)
