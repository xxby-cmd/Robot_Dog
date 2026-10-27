// Feedback-only leg odometry. CHAMP supplies forward kinematics, not commands.
// Stance foot velocity: v_base = -dr_foot/dt - omega_base x r_foot.
#include <algorithm>
#include <array>
#include <cmath>
#include <memory>
#include <rclcpp/rclcpp.hpp>
#include <champ/utils/urdf_loader.h>
#include <champ_msgs/msg/contacts_stamped.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Matrix3x3.h>
#include <tf2/LinearMath/Vector3.h>

class LegOdometry : public rclcpp::Node
{
  champ::QuadrupedBase base_;
  std::vector<std::string> names_;
  std::array<tf2::Vector3, 4> previous_{};
  std::array<bool, 4> previous_contact_{};
  champ_msgs::msg::ContactsStamped::SharedPtr contacts_;
  sensor_msgs::msg::Imu::SharedPtr imu_;
  rclcpp::Time previous_time_{0, 0, RCL_ROS_TIME};
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr publisher_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joint_sub_;
  rclcpp::Subscription<champ_msgs::msg::ContactsStamped>::SharedPtr contact_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_sub_;

  void update(const sensor_msgs::msg::JointState::SharedPtr msg)
  {
    const rclcpp::Time stamp(msg->header.stamp, RCL_ROS_TIME);
    if (!contacts_ || !imu_ || stamp.nanoseconds() <= 0) return;
    if (std::abs((stamp - rclcpp::Time(contacts_->header.stamp, RCL_ROS_TIME)).seconds()) > 0.10 ||
        std::abs((stamp - rclcpp::Time(imu_->header.stamp, RCL_ROS_TIME)).seconds()) > 0.10 ||
        contacts_->contacts.size() != 4) return;
    float angles[12];
    for (size_t i = 0; i < 12; ++i) {
      const auto it = std::find(msg->name.begin(), msg->name.end(), names_[i]);
      if (it == msg->name.end()) return;
      const auto index = std::distance(msg->name.begin(), it);
      if (static_cast<size_t>(index) >= msg->position.size() || !std::isfinite(msg->position[index])) return;
      angles[i] = msg->position[index];
    }
    base_.updateJointPositions(angles);
    const auto &q = imu_->orientation;
    tf2::Quaternion attitude(q.x, q.y, q.z, q.w);
    if (!std::isfinite(attitude.length2()) || attitude.length2() < 0.9) return;
    attitude.normalize();
    double roll, pitch, yaw;
    tf2::Matrix3x3(attitude).getRPY(roll, pitch, yaw);
    tf2::Quaternion tilt;
    tilt.setRPY(roll, pitch, 0.0);
    const tf2::Matrix3x3 rotation(tilt);
    const auto &w = imu_->angular_velocity;
    const tf2::Vector3 omega(w.x, w.y, w.z);
    const double dt = (stamp - previous_time_).seconds();
    tf2::Vector3 velocity(0, 0, 0);
    double height = 0.0;
    int used = 0, grounded = 0;
    for (size_t i = 0; i < 4; ++i) {
      auto foot = base_.legs[i]->foot_from_base();
      const tf2::Vector3 position(foot.X(), foot.Y(), foot.Z());
      const bool contact = contacts_->contacts[i];
      if (contact) {
        // URDF foot sphere: FK locates its centre, not the ground surface.
        height += -(rotation * position).z() + 0.02;
        ++grounded;
      }
      if (contact && previous_contact_[i] && dt > 0.0001 && dt < 0.10) {
        const tf2::Vector3 estimate = -(position - previous_[i]) / dt - omega.cross(position);
        if (std::isfinite(estimate.length2()) && estimate.length() < 2.0) {
          velocity += estimate;
          ++used;
        }
      }
      // Refresh history even in all-feet stance and airborne samples.
      previous_[i] = position;
      previous_contact_[i] = contact;
    }
    previous_time_ = stamp;
    if (!grounded) return;
    if (used) velocity /= used;
    nav_msgs::msg::Odometry out;
    out.header = msg->header;
    out.header.frame_id = "odom";
    out.child_frame_id = "base_link";
    // x/y/yaw are NOT estimates and are excluded from EKF pose fusion.
    out.pose.pose.position.z = height / grounded;
    out.pose.pose.orientation.w = 1.0;
    out.twist.twist.linear.x = velocity.x();
    out.twist.twist.linear.y = velocity.y();
    out.twist.twist.linear.z = velocity.z();
    for (int i : {0, 7, 35, 21, 28}) out.pose.covariance[i] = 1e6;
    out.pose.covariance[14] = 0.0025;
    for (int i : {0, 7, 14}) out.twist.covariance[i] = used >= 2 ? 0.01 : 1.0;
    for (int i : {21, 28, 35}) out.twist.covariance[i] = 1e6;
    publisher_->publish(out);
  }

public:
  LegOdometry() : Node("leg_odometry", rclcpp::NodeOptions()
      .allow_undeclared_parameters(true).automatically_declare_parameters_from_overrides(true))
  {
    std::string urdf;
    if (!get_parameter("urdf", urdf) || urdf.empty()) throw std::runtime_error("Missing URDF");
    champ::URDF::loadFromString(base_, get_node_parameters_interface(), urdf);
    names_ = champ::URDF::getJointNames(get_node_parameters_interface());
    if (names_.size() != 12) throw std::runtime_error("Expected 12 joint names");
    publisher_ = create_publisher<nav_msgs::msg::Odometry>("odom/leg", 10);
    contact_sub_ = create_subscription<champ_msgs::msg::ContactsStamped>("foot_contacts", 10,
      [this](champ_msgs::msg::ContactsStamped::SharedPtr msg) { contacts_ = msg; });
    imu_sub_ = create_subscription<sensor_msgs::msg::Imu>("imu/data", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::Imu::SharedPtr msg) { imu_ = msg; });
    joint_sub_ = create_subscription<sensor_msgs::msg::JointState>("joint_states", rclcpp::SensorDataQoS(),
      std::bind(&LegOdometry::update, this, std::placeholders::_1));
    RCLCPP_INFO(get_logger(), "Inputs: measured joints, contacts, IMU; no cmd_vel or ground truth");
  }
};

int main(int argc, char **argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<LegOdometry>());
  rclcpp::shutdown();
}
