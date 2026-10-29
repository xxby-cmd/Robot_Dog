#pragma once

#include <cmath>
#include <initializer_list>
#include <limits>
#include <champ/quadruped_base/quadruped_components.h>

namespace champ_base {

inline champ::Velocities velocity_command(double forward, double lateral, double turn)
{
  champ::Velocities result;
  // Reject invalid / unrepresentable numbers, without clipping finite values
  // to an application-specific speed range. A bad vector becomes a stop.
  constexpr double representable = std::numeric_limits<float>::max();
  for (const double value : {forward, lateral, turn})
    if (!std::isfinite(value) || std::abs(value) > representable) return result;
  result.linear.x = static_cast<float>(forward);
  result.linear.y = static_cast<float>(lateral);
  result.angular.z = static_cast<float>(turn);
  return result;
}

}  // namespace champ_base
