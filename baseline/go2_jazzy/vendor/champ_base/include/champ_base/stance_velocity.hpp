#pragma once

#include <algorithm>
#include <array>
#include <cstddef>

namespace champ_base {

struct StanceVelocity {
  std::array<double, 3> velocity{};
  std::array<double, 3> variance{1.0, 1.0, 1.0};
};

// Each input is a finite velocity inferred from a measured supporting foot.
// The component median tolerates one discordant foot when >=3 are available.
// Two feet still give their mean; their slip cannot be disambiguated here.
inline StanceVelocity stance_velocity(
    const std::array<std::array<double, 3>, 4>& samples, std::size_t count)
{
  StanceVelocity result;
  if (count == 0 || count > samples.size()) return result;
  for (std::size_t axis = 0; axis < 3; ++axis) {
    std::array<double, 4> values{};
    for (std::size_t i = 0; i < count; ++i) values[i] = samples[i][axis];
    std::sort(values.begin(), values.begin() + count);
    result.velocity[axis] = (values[(count - 1) / 2] + values[count / 2]) * 0.5;
    double residual = 0.0;
    for (std::size_t i = 0; i < count; ++i) {
      const double error = samples[i][axis] - result.velocity[axis];
      residual += error * error;
    }
    // Preserve the former noise floor, but no longer claim equal certainty
    // when the purported supporting feet disagree.
    result.variance[axis] = count < 2 ? 1.0 : 0.01 + residual / count;
  }
  return result;
}

}  // namespace champ_base
