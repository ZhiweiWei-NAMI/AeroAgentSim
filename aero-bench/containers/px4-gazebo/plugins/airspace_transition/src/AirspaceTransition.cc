#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdint>
#include <exception>
#include <iomanip>
#include <limits>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <utility>

#include <gz/plugin/Register.hh>
#include <gz/msgs/stringmsg.pb.h>
#include <gz/math/Vector3.hh>
#include <gz/sim/System.hh>
#include <gz/sim/components/Model.hh>
#include <gz/sim/components/Name.hh>
#include <gz/sim/components/Pose.hh>
#include <gz/transport/Node.hh>
#include <sdf/Element.hh>

namespace aero_bench::gazebo
{
namespace
{
constexpr char kSchema[] = "aero-bench.gazebo-airspace-transition/v1";
constexpr char kSource[] = "gazebo.system";

struct Bounds
{
  double minEast{};
  double maxEast{};
  double minNorth{};
  double maxNorth{};
  double minUp{};
  double maxUp{};
};

bool IsIdentifier(const std::string &value)
{
  if (value.empty() || value.front() < 'a' || value.front() > 'z')
    return false;
  return std::all_of(value.begin() + 1, value.end(), [](const char character)
  {
    return (character >= 'a' && character <= 'z') ||
           (character >= '0' && character <= '9') || character == '_' ||
           character == '.' || character == '-';
  });
}

bool IsDigest(const std::string &value)
{
  if (value.size() != 64 || value == std::string(64, '0'))
    return false;
  return std::all_of(value.begin(), value.end(), [](const char character)
  {
    return (character >= '0' && character <= '9') ||
           (character >= 'a' && character <= 'f');
  });
}

bool IsFinite(const Bounds &bounds)
{
  return std::isfinite(bounds.minEast) && std::isfinite(bounds.maxEast) &&
         std::isfinite(bounds.minNorth) && std::isfinite(bounds.maxNorth) &&
         std::isfinite(bounds.minUp) && std::isfinite(bounds.maxUp);
}

bool IsInside(const Bounds &bounds, const gz::math::Vector3d &position)
{
  return position.X() >= bounds.minEast && position.X() <= bounds.maxEast &&
         position.Y() >= bounds.minNorth && position.Y() <= bounds.maxNorth &&
         position.Z() >= bounds.minUp && position.Z() <= bounds.maxUp;
}

std::string JsonString(const std::string &value)
{
  std::ostringstream output;
  output << '"';
  for (const char character : value)
  {
    switch (character)
    {
      case '"': output << "\\\""; break;
      case '\\': output << "\\\\"; break;
      case '\b': output << "\\b"; break;
      case '\f': output << "\\f"; break;
      case '\n': output << "\\n"; break;
      case '\r': output << "\\r"; break;
      case '\t': output << "\\t"; break;
      default:
        if (static_cast<unsigned char>(character) < 0x20U)
        {
          output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                 << static_cast<unsigned int>(static_cast<unsigned char>(character))
                 << std::dec << std::setfill(' ');
        }
        else
        {
          output << character;
        }
        break;
    }
  }
  output << '"';
  return output.str();
}

std::string Number(double value)
{
  std::ostringstream output;
  output << std::setprecision(17) << value;
  return output.str();
}

std::string TransitionJson(
    const std::string &transition,
    const std::string &airspaceState,
    std::uint64_t sequence,
    std::int64_t simTimeNs,
    const std::string &worldId,
    const std::string &worldDigest,
    const std::string &regionId,
    const std::string &regionDigest,
    const std::string &vehicle,
    const gz::math::Vector3d &position)
{
  std::ostringstream output;
  output << "{\"schema_version\":" << JsonString(kSchema)
         << ",\"source\":" << JsonString(kSource)
         << ",\"sequence\":" << sequence
         << ",\"transition\":" << JsonString(transition)
         << ",\"airspace_state\":" << JsonString(airspaceState)
         << ",\"sim_time_ns\":" << simTimeNs
         << ",\"world_id\":" << JsonString(worldId)
         << ",\"world_digest\":" << JsonString(worldDigest)
         << ",\"region_id\":" << JsonString(regionId)
         << ",\"region_digest\":" << JsonString(regionDigest)
         << ",\"incident_vehicle\":" << JsonString(vehicle)
         << ",\"position_enu_m\":{\"east\":" << Number(position.X())
         << ",\"north\":" << Number(position.Y())
         << ",\"up\":" << Number(position.Z()) << "}}";
  return output.str();
}

bool ReadString(
    const std::shared_ptr<const sdf::Element> &sdf,
    const char *name,
    std::string &value,
    std::string &error)
{
  if (!sdf->HasElement(name))
  {
    error = std::string("missing required SDF element <") + name + ">";
    return false;
  }
  try
  {
    value = sdf->Get<std::string>(name);
  }
  catch (const std::exception &exception)
  {
    error = std::string("invalid SDF element <") + name + ">: " + exception.what();
    return false;
  }
  if (value.empty())
  {
    error = std::string("SDF element <") + name + "> must not be empty";
    return false;
  }
  return true;
}

bool ReadDouble(
    const std::shared_ptr<const sdf::Element> &sdf,
    const char *name,
    double &value,
    std::string &error)
{
  if (!sdf->HasElement(name))
  {
    error = std::string("missing required SDF element <") + name + ">";
    return false;
  }
  try
  {
    value = sdf->Get<double>(name);
  }
  catch (const std::exception &exception)
  {
    error = std::string("invalid SDF element <") + name + ">: " + exception.what();
    return false;
  }
  if (!std::isfinite(value))
  {
    error = std::string("SDF element <") + name + "> must be finite";
    return false;
  }
  return true;
}
}  // namespace

class AirspaceTransition final : public gz::sim::System,
                                 public gz::sim::ISystemConfigure,
                                 public gz::sim::ISystemPostUpdate
{
public:
  void Configure(
      const gz::sim::Entity &,
      const std::shared_ptr<const sdf::Element> &sdf,
      gz::sim::EntityComponentManager &,
      gz::sim::EventManager &) override
  {
    this->configured_ = false;
    this->wasInside_.reset();
    this->nextSequence_ = 1;
    this->sequenceExhausted_ = false;
    std::string error;
    if (!sdf)
    {
      gzerr << "AirspaceTransition requires an SDF configuration\n";
      return;
    }

    if (!ReadString(sdf, "world_id", this->worldId_, error) ||
        !ReadString(sdf, "world_digest", this->worldDigest_, error) ||
        !ReadString(sdf, "region_id", this->regionId_, error) ||
        !ReadString(sdf, "region_digest", this->regionDigest_, error) ||
        !ReadString(sdf, "incident_vehicle", this->incidentVehicle_, error) ||
        !ReadString(sdf, "transition_topic", this->topic_, error) ||
        !ReadDouble(sdf, "min_east_m", this->bounds_.minEast, error) ||
        !ReadDouble(sdf, "max_east_m", this->bounds_.maxEast, error) ||
        !ReadDouble(sdf, "min_north_m", this->bounds_.minNorth, error) ||
        !ReadDouble(sdf, "max_north_m", this->bounds_.maxNorth, error) ||
        !ReadDouble(sdf, "min_up_m", this->bounds_.minUp, error) ||
        !ReadDouble(sdf, "max_up_m", this->bounds_.maxUp, error))
    {
      gzerr << "AirspaceTransition configuration error: " << error << '\n';
      return;
    }

    if (!IsIdentifier(this->worldId_) || !IsIdentifier(this->regionId_) ||
        !IsIdentifier(this->incidentVehicle_))
    {
      gzerr << "AirspaceTransition identities must match [a-z][a-z0-9_.-]*\n";
      return;
    }
    if (!IsDigest(this->worldDigest_) || !IsDigest(this->regionDigest_))
    {
      gzerr << "AirspaceTransition identities require non-placeholder lowercase SHA-256 digests\n";
      return;
    }
    if (this->topic_.front() != '/' ||
        std::any_of(this->topic_.begin(), this->topic_.end(), [](const char character)
        {
          return std::isspace(static_cast<unsigned char>(character)) != 0;
        }))
    {
      gzerr << "AirspaceTransition transition_topic must be an absolute Gazebo topic without whitespace\n";
      return;
    }
    if (!IsFinite(this->bounds_) || this->bounds_.minEast > this->bounds_.maxEast ||
        this->bounds_.minNorth > this->bounds_.maxNorth ||
        this->bounds_.minUp > this->bounds_.maxUp)
    {
      gzerr << "AirspaceTransition bounds must be finite and ordered\n";
      return;
    }

    this->publisher_ = this->node_.Advertise<gz::msgs::StringMsg>(this->topic_);
    if (!this->publisher_)
    {
      gzerr << "AirspaceTransition could not advertise " << this->topic_ << '\n';
      return;
    }
    this->configured_ = true;
  }

  void PostUpdate(
      const gz::sim::UpdateInfo &info,
      const gz::sim::EntityComponentManager &ecm) override
  {
    if (!this->configured_)
      return;

    const auto simTimeNs = std::chrono::duration_cast<std::chrono::nanoseconds>(
                               info.simTime)
                               .count();
    if (simTimeNs < 0)
      return;

    ecm.Each<gz::sim::components::Model,
             gz::sim::components::Name,
             gz::sim::components::Pose>(
        [&](const gz::sim::Entity &,
            const gz::sim::components::Model *,
            const gz::sim::components::Name *name,
            const gz::sim::components::Pose *pose)
        {
          if (name == nullptr || pose == nullptr ||
              name->Data() != this->incidentVehicle_)
            return true;

          const auto &position = pose->Data().Pos();
          if (!std::isfinite(position.X()) || !std::isfinite(position.Y()) ||
              !std::isfinite(position.Z()))
            return true;

          const bool inside = IsInside(this->bounds_, position);
          if (!this->wasInside_.has_value())
          {
            this->wasInside_ = inside;
            if (!inside)
            {
              this->Publish("none", "outside", simTimeNs, position, false);
              return true;
            }
            this->Publish("entered", "inside", simTimeNs, position, true);
            return true;
          }
          if (*this->wasInside_ == inside)
          {
            this->Publish(
                "none", inside ? "inside" : "outside", simTimeNs, position, false);
            return true;
          }

          this->wasInside_ = inside;
          this->Publish(
              inside ? "entered" : "exited",
              inside ? "inside" : "outside",
              simTimeNs,
              position,
              true);
          return true;
        });

  }

private:
  void Publish(
      const std::string &transition,
      const std::string &airspaceState,
      std::int64_t simTimeNs,
      const gz::math::Vector3d &position,
      bool isEvent)
  {
    if (isEvent && this->sequenceExhausted_)
      return;
    const auto sequence = isEvent ? this->nextSequence_ : 0U;
    gz::msgs::StringMsg message;
    message.set_data(TransitionJson(
        transition,
        airspaceState,
        sequence,
        simTimeNs,
        this->worldId_,
        this->worldDigest_,
        this->regionId_,
        this->regionDigest_,
        this->incidentVehicle_,
        position));
    if (!this->publisher_.Publish(message))
    {
      gzerr << "AirspaceTransition failed to publish " << transition << " transition\n";
      return;
    }
    if (!isEvent)
      return;
    if (this->nextSequence_ == std::numeric_limits<std::uint64_t>::max())
      this->sequenceExhausted_ = true;
    else
      ++this->nextSequence_;
  }

  bool configured_{false};
  std::optional<bool> wasInside_;
  std::uint64_t nextSequence_{1};
  bool sequenceExhausted_{false};
  Bounds bounds_;
  std::string worldId_;
  std::string worldDigest_;
  std::string regionId_;
  std::string regionDigest_;
  std::string incidentVehicle_;
  std::string topic_;
  gz::transport::Node node_;
  gz::transport::Node::Publisher publisher_;
};
}  // namespace aero_bench::gazebo

GZ_ADD_PLUGIN(
    aero_bench::gazebo::AirspaceTransition,
    gz::sim::System,
    gz::sim::ISystemConfigure,
    gz::sim::ISystemPostUpdate)
GZ_ADD_PLUGIN_ALIAS(
    aero_bench::gazebo::AirspaceTransition,
    "aero_bench::gazebo::AirspaceTransition")
