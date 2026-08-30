from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    package_share = get_package_share_directory("teleop_server")
    default_config_file = f"{package_share}/config/teleop_server.yaml"

    config_file_arg = DeclareLaunchArgument(
        "config_file",
        default_value=default_config_file,
        description="Path to teleop server YAML config file",
    )
    urdf_file_arg = DeclareLaunchArgument(
        "urdf_file",
        description="Absolute path to robot URDF file",
    )

    teleop_server_node = Node(
        package="teleop_server",
        executable="teleop_server_node",
        output="screen",
        arguments=[LaunchConfiguration("config_file")],
        respawn=True,
        respawn_delay=2.0,
    )

    robot_state_publisher_node = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        output="screen",
        arguments=[LaunchConfiguration("urdf_file")],
        respawn=True,
        respawn_delay=2.0,
    )

    return LaunchDescription(
        [
            config_file_arg,
            urdf_file_arg,
            teleop_server_node,
            robot_state_publisher_node,
        ]
    )
