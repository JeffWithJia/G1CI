from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    package_share = get_package_share_directory("teleop_server")
    default_model_file = f"{package_share}/config/pico_82d_viewer.xml"

    pose_topic_arg = DeclareLaunchArgument(
        "pose_topic",
        default_value="/pico/teleop_packet",
        description="std_msgs/String JSON topic containing pose_82d or frame[82]",
    )
    model_file_arg = DeclareLaunchArgument(
        "model_file",
        default_value=default_model_file,
        description="MuJoCo XML scene used by the Pico 82D viewer",
    )

    viewer_node = Node(
        package="teleop_server",
        executable="pico_mujoco_viewer_node",
        output="screen",
        parameters=[
            {
                "pose_topic": LaunchConfiguration("pose_topic"),
                "model_path": LaunchConfiguration("model_file"),
            }
        ],
    )

    return LaunchDescription(
        [
            pose_topic_arg,
            model_file_arg,
            viewer_node,
        ]
    )
