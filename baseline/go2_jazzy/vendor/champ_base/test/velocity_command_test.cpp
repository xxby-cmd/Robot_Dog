#include <champ_base/velocity_command.hpp>
#include <champ/leg_controller/leg_controller.h>
#include <champ/kinematics/kinematics.h>
#include <cmath>
#include <limits>
#include <stdexcept>

void require(bool condition)
{
  if (!condition) throw std::runtime_error("Velocity / kinematics regression failed");
}

int main()
{
  auto request = champ_base::velocity_command(.75, -.5, 1.2);
  require(std::abs(request.linear.x-.75) < 1e-6);
  require(std::abs(request.linear.y+.5) < 1e-6);
  require(std::abs(request.angular.z-1.2) < 1e-6);
  for (double invalid : {std::numeric_limits<double>::quiet_NaN(),
                         std::numeric_limits<double>::infinity(),
                         std::numeric_limits<double>::max()}) {
    const auto stopped = champ_base::velocity_command(.2, invalid, .4);
    require(stopped.linear.x == 0 && stopped.linear.y == 0 && stopped.angular.z == 0);
  }

  champ::QuadrupedBase base;
  base.gait_config.stance_duration = .25;
  base.gait_config.swing_height = .025;
  base.gait_config.stance_depth = .001;
  base.gait_config.nominal_height = .24;
  base.gait_config.max_linear_velocity_x = .3;
  base.gait_config.max_linear_velocity_y = .25;
  base.gait_config.max_angular_velocity_z = .5;
  for (int i = 0; i < 4; ++i) {
    const float side = (i % 2 == 0) ? 1.f : -1.f;
    base.legs[i]->hip.setTranslation(i < 2 ? .1934f : -.1934f, side*.0465f, 0.f);
    base.legs[i]->upper_leg.setTranslation(0.f, side*.0955f, 0.f);
    base.legs[i]->lower_leg.setTranslation(0.f, 0.f, -.213f);
    base.legs[i]->foot.setTranslation(0.f, 0.f, -.213f);
  }
  champ::LegController controller(base, 1000000);
  geometry::Transformation feet[4];
  controller.velocityCommand(feet, request, 1010000);
  // The planner previously modified this vector to the nominal configuration maxima.
  require(std::abs(request.linear.x-.75) < 1e-6);
  require(std::abs(request.linear.y+.5) < 1e-6);
  require(std::abs(request.angular.z-1.2) < 1e-6);

  champ::Kinematics kinematics(base);
  for (int i = 0; i < 4; ++i) {
    feet[i].X() = 0.f;
    feet[i].Y() = (i % 2 == 0) ? .0955f : -.0955f;
    feet[i].Z() = -.24f;
  }
  float joints[12]{};
  require(kinematics.inverse(joints, feet));
  float previous[12];
  for (int i = 0; i < 12; ++i) previous[i] = joints[i];
  feet[2].X() = 10.f;
  require(!kinematics.inverse(joints, feet));
  for (int i = 0; i < 12; ++i) require(joints[i] == previous[i]);
}
