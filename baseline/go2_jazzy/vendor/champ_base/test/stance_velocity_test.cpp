#include <champ_base/stance_velocity.hpp>
#include <cmath>
#include <stdexcept>

void require(bool condition)
{
  if (!condition) throw std::runtime_error("Stance velocity regression failed");
}

int main()
{
  using Samples = std::array<std::array<double, 3>, 4>;
  const Samples measured{{{0.15, 0.0, 0.0}, {0.15, 0.0, 0.0},
                          {1.20, -0.8, -0.5}, {0.15, 0.0, 0.0}}};
  const auto robust = champ_base::stance_velocity(measured, 3);
  require(std::abs(robust.velocity[0] - 0.15) < 1e-12);
  require(robust.velocity[1] == 0.0 && robust.velocity[2] == 0.0);
  require(robust.variance[0] > 0.1);
  const auto four = champ_base::stance_velocity(measured, 4);
  require(std::abs(four.velocity[0] - 0.15) < 1e-12);
  const Samples pair{{{0.1, 0.0, 0.0}, {0.3, 0.0, 0.0}, {}, {}}};
  const auto two = champ_base::stance_velocity(pair, 2);
  require(std::abs(two.velocity[0] - 0.2) < 1e-12);
  require(two.variance[0] > champ_base::stance_velocity(measured, 2).variance[0]);
  const auto missing = champ_base::stance_velocity(measured, 0);
  require(missing.velocity[0] == 0.0 && missing.variance[0] == 1.0);
  const auto single = champ_base::stance_velocity(measured, 1);
  require(single.variance[0] == 1.0);
}
