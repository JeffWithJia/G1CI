cmake_minimum_required(VERSION 3.8)
project(teleop_server)

if(POLICY CMP0072)
  cmake_policy(SET CMP0072 NEW)
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

if("$ENV{ROS_DISTRO}" STREQUAL "foxy")
  add_definitions(-DROS_FOXY)
endif()

file(READ ${CMAKE_CURRENT_SOURCE_DIR}/package.xml _package_xml)
string(
        REGEX MATCH "<version>([^<]+)</version>"
        _version_match
        "${_package_xml}"
)
set(TELEOP_VERSION "${CMAKE_MATCH_1}")

set(TELEOP_GIT_HASH "" CACHE STRING "Teleop server git hash")
if(NOT TELEOP_GIT_HASH AND DEFINED ENV{TELEOP_GIT_HASH} AND NOT "$ENV{TELEOP_GIT_HASH}" STREQUAL "")
  set(TELEOP_GIT_HASH "$ENV{TELEOP_GIT_HASH}")
endif()
message(STATUS "agent git hash: ${TELEOP_GIT_HASH} ")

configure_file(
        ${CMAKE_CURRENT_SOURCE_DIR}/src/version.cpp.in
        ${CMAKE_CURRENT_SOURCE_DIR}/src/version.cpp
        @ONLY
)

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(PkgConfig REQUIRED)
find_package(Eigen3 REQUIRED)
find_package(OpenCV REQUIRED)
find_package(yaml-cpp REQUIRED)
find_package(cv_bridge REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(unitree_api REQUIRED)
find_package(unitree_go REQUIRED)
find_package(unitree_hg REQUIRED)
find_package(agent REQUIRED)
find_package(realsense2 QUIET)
find_package(OpenGL QUIET)

pkg_check_modules(X264 REQUIRED x264)
pkg_check_modules(JSONCPP REQUIRED jsoncpp)
if(NOT realsense2_FOUND)
  pkg_check_modules(REALSENSE2 QUIET IMPORTED_TARGET realsense2)
endif()

find_path(MUJOCO_INCLUDE_DIR
  NAMES mujoco/mujoco.h
  PATHS /opt/mujoco/include
)
find_library(MUJOCO_LIBRARY
  NAMES mujoco
  PATHS /opt/mujoco/lib
)
find_path(GLFW_INCLUDE_DIR
  NAMES GLFW/glfw3.h
)
find_library(GLFW_LIBRARY
  NAMES glfw glfw3
)

set(XROBOTOOLKIT_ROOT /opt/apps/xrobotoolkit-pc-service CACHE PATH "XRoboToolkit PC service install root")
set(XROBOTOOLKIT_INCLUDE_DIR ${XROBOTOOLKIT_ROOT}/sdk/include)
set(XROBOTOOLKIT_LIB_DIR ${XROBOTOOLKIT_ROOT}/sdk/lib)
# set(XROBOTOOLKIT_ROOT /opt/apps/roboticsservice CACHE PATH "XRoboToolkit PC service install root")
# set(XROBOTOOLKIT_INCLUDE_DIR ${XROBOTOOLKIT_ROOT}/SDK/include)
# set(XROBOTOOLKIT_LIB_DIR ${XROBOTOOLKIT_ROOT}/SDK/x64)

find_library(PXREARobotSDK_LIB
  NAMES PXREARobotSDK
  PATHS ${XROBOTOOLKIT_LIB_DIR}
  NO_DEFAULT_PATH
  REQUIRED
)

set(TELEOP_NODE_RUNTIME_LIBRARY_DIRS
  ${XROBOTOOLKIT_LIB_DIR}
)
list(REMOVE_ITEM TELEOP_NODE_RUNTIME_LIBRARY_DIRS "")
list(REMOVE_DUPLICATES TELEOP_NODE_RUNTIME_LIBRARY_DIRS)

add_executable(teleop_server_node
  src/main.cpp
  src/teleop_server.cpp
  src/version.cpp
  src/audio/audio_client.cpp
  src/camera/camera_publisher.cpp
  src/logging/logger.cpp
  src/recording/recording_controller.cpp
  src/joints/joints_publisher.cpp
  src/hands/hand_state_provider.cpp
  src/hands/modbus_tcp_reader.cpp
  src/pico/pico_82d_converter.cpp
  src/pico/pico_data_receiver.cpp
  src/pico/pico_teleop_sender.cpp
  src/pico/pico_video_streamer.cpp
)

add_executable(pico_82d_convert_dataset
  src/tools/pico_82d_convert_dataset.cpp
  src/pico/pico_82d_converter.cpp
)

if(MUJOCO_INCLUDE_DIR AND MUJOCO_LIBRARY AND GLFW_INCLUDE_DIR AND GLFW_LIBRARY AND OpenGL_FOUND)
  add_executable(pico_mujoco_viewer_node
    src/logging/logger.cpp
    src/sim/pico_mujoco_viewer.cpp
  )
  target_include_directories(pico_mujoco_viewer_node PRIVATE
    include
    ${MUJOCO_INCLUDE_DIR}
    ${GLFW_INCLUDE_DIR}
    ${JSONCPP_INCLUDE_DIRS}
  )
  target_link_libraries(pico_mujoco_viewer_node
    Eigen3::Eigen
    ${MUJOCO_LIBRARY}
    ${GLFW_LIBRARY}
    OpenGL::GL
    ${JSONCPP_LIBRARIES}
  )
  ament_target_dependencies(pico_mujoco_viewer_node
    rclcpp
    std_msgs
  )
  set_target_properties(pico_mujoco_viewer_node PROPERTIES
    BUILD_RPATH "/opt/mujoco/lib"
    INSTALL_RPATH "/opt/mujoco/lib"
  )
else()
  message(WARNING
    "pico_mujoco_viewer_node will not be built. "
    "Need MuJoCo under /opt/mujoco, GLFW, and OpenGL. "
    "Install GLFW, for example: sudo apt install libglfw3-dev")
endif()

set(TELEOP_COMMON_INCLUDE_DIRS
    include
    ${XROBOTOOLKIT_INCLUDE_DIR}
    ${JSONCPP_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
    ${X264_INCLUDE_DIRS}
)

target_include_directories(teleop_server_node PRIVATE ${TELEOP_COMMON_INCLUDE_DIRS})
target_include_directories(pico_82d_convert_dataset PRIVATE
  include
  ${JSONCPP_INCLUDE_DIRS}
)

target_link_libraries(teleop_server_node
  Eigen3::Eigen
  ${OpenCV_LIBRARIES}
  ${PXREARobotSDK_LIB}
  ${JSONCPP_LIBRARIES}
  ${X264_LIBRARIES}
  yaml-cpp
)

target_link_libraries(pico_82d_convert_dataset
  Eigen3::Eigen
  ${JSONCPP_LIBRARIES}
)

set_target_properties(teleop_server_node PROPERTIES
  BUILD_RPATH "${TELEOP_NODE_RUNTIME_LIBRARY_DIRS}"
  INSTALL_RPATH "${TELEOP_NODE_RUNTIME_LIBRARY_DIRS}"
)

if(realsense2_FOUND)
  target_compile_definitions(teleop_server_node PRIVATE HAVE_REALSENSE2)
  if(TARGET realsense2::realsense2)
    target_link_libraries(teleop_server_node realsense2::realsense2)
  else()
    target_link_libraries(teleop_server_node ${realsense2_LIBRARY})
  endif()
elseif(REALSENSE2_FOUND)
  target_compile_definitions(teleop_server_node PRIVATE HAVE_REALSENSE2)
  target_link_libraries(teleop_server_node PkgConfig::REALSENSE2)
endif()

ament_target_dependencies(teleop_server_node
  cv_bridge
  geometry_msgs
  rclcpp
  sensor_msgs
  std_msgs
  tf2_ros
  unitree_api
  unitree_go
  unitree_hg
  agent
)

install(TARGETS teleop_server_node pico_82d_convert_dataset
  DESTINATION lib/${PROJECT_NAME}
)

if(TARGET pico_mujoco_viewer_node)
  install(TARGETS pico_mujoco_viewer_node
    DESTINATION lib/${PROJECT_NAME}
  )
endif()

install(DIRECTORY
  config
  launch
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
