// Ray-based obstacle attenuation loss model (see provenance).
// Volumes now intersect the actual ENU ray inside ns-3 instead of requiring a
// benchmark-derived link attenuation input. Wi-Fi peers share one medium.
#pragma once
#include "ns3/propagation-module.h"
#include <algorithm>
#include <cmath>
#include <vector>

namespace ns3 {
struct SceneVolume {
    Vector minimum, maximum;
    double lossDb;
};

class SceneLoss final : public PropagationLossModel {
  public:
    static TypeId GetTypeId() {
        static TypeId id = TypeId("ns3::AasSceneLoss")
            .SetParent<PropagationLossModel>().AddConstructor<SceneLoss>();
        return id;
    }
    void Configure(double frequencyHz, double exponent, double referenceLossDb) {
        m_exponent = exponent;
        m_reference = referenceLossDb;
        m_frequency = frequencyHz;
    }
    void AddVolume(SceneVolume volume) { m_volumes.push_back(volume); }
    double AdditionalLoss(Vector source, Vector dest) const {
        double sum = 0;
        for (const auto& box : m_volumes) {
            // Slab intersection. Merely grazing a face has no interior path.
            double enter = 0, leave = 1;
            const double a[] = {source.x, source.y, source.z};
            const double b[] = {dest.x, dest.y, dest.z};
            const double lo[] = {box.minimum.x, box.minimum.y, box.minimum.z};
            const double hi[] = {box.maximum.x, box.maximum.y, box.maximum.z};
            bool hit = true;
            for (int axis = 0; axis < 3; ++axis) {
                double delta = b[axis] - a[axis];
                if (delta == 0) {
                    if (a[axis] <= lo[axis] || a[axis] >= hi[axis]) hit = false;
                } else {
                    double left = (lo[axis] - a[axis]) / delta;
                    double right = (hi[axis] - a[axis]) / delta;
                    enter = std::max(enter, std::min(left, right));
                    leave = std::min(leave, std::max(left, right));
                }
            }
            if (hit && enter < leave) sum += box.lossDb;
        }
        return sum;
    }
    double PathLoss(Ptr<MobilityModel> source, Ptr<MobilityModel> dest) const {
        return m_reference + 10 * m_exponent *
            std::log10(std::max(1.0, source->GetDistanceFrom(dest))) +
            AdditionalLoss(source->GetPosition(), dest->GetPosition());
    }
  private:
    double DoCalcRxPower(double tx, Ptr<MobilityModel> a,
                         Ptr<MobilityModel> b) const override {
        return tx - PathLoss(a, b);
    }
    int64_t DoAssignStreams(int64_t) override { return 0; }
    double m_exponent = 3, m_reference = 46.6777, m_frequency = 5180000000.0;
    std::vector<SceneVolume> m_volumes;
};

// PacketTag survives ns-3 packet copies and IP reassembly without changing
// modeled application size. It is metadata, not a transmitted payload header.
class PacketIdentity final : public Tag {
  public:
    uint64_t sequence = 0;
    static TypeId GetTypeId() {
        static TypeId id = TypeId("ns3::AasPacketIdentity")
            .SetParent<Tag>().AddConstructor<PacketIdentity>();
        return id;
    }
    TypeId GetInstanceTypeId() const override { return GetTypeId(); }
    uint32_t GetSerializedSize() const override { return 8; }
    void Serialize(TagBuffer b) const override { b.WriteU64(sequence); }
    void Deserialize(TagBuffer b) override { sequence = b.ReadU64(); }
    void Print(std::ostream& os) const override { os << sequence; }
};
} // namespace ns3
