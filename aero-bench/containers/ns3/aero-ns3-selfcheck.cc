#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/propagation-module.h"
#include "ns3/traffic-control-module.h"
#include "ns3/wifi-module.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace ns3;

namespace
{
constexpr std::uint16_t kPort = 45001;
constexpr std::size_t kPacketBytes = 1'200;
constexpr std::uint32_t kPacketCount = 3;
constexpr std::uint8_t kPayloadByte = 0xa5;
std::uint32_t gDeliveredPackets = 0;
bool gPayloadsValid = true;
std::uint64_t gFirstArrivalTimeNs = 0;
std::uint64_t gLastArrivalTimeNs = 0;

class SelfcheckDelayModel final : public PropagationDelayModel
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId typeId = TypeId("ns3::SelfcheckDelayModel")
                                   .SetParent<PropagationDelayModel>()
                                   .SetGroupName("Propagation")
                                   .AddConstructor<SelfcheckDelayModel>();
        return typeId;
    }

    void SetDelay(Time delay)
    {
        m_delay = delay;
    }

    Time GetDelay(Ptr<MobilityModel>, Ptr<MobilityModel>) const override
    {
        return m_delay;
    }

  protected:
    std::int64_t DoAssignStreams(std::int64_t) override
    {
        return 0;
    }

  private:
    Time m_delay{NanoSeconds(0)};
};

NS_OBJECT_ENSURE_REGISTERED(SelfcheckDelayModel);

class SelfcheckSceneLossModel final : public PropagationLossModel
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId typeId = TypeId("ns3::SelfcheckSceneLossModel")
                                   .SetParent<PropagationLossModel>()
                                   .SetGroupName("Propagation")
                                   .AddConstructor<SelfcheckSceneLossModel>();
        return typeId;
    }

    void Configure(double frequencyHz, double exponent, double additionalLossDb)
    {
        constexpr double speedOfLight = 299'792'458.0;
        const double pi = std::acos(-1.0);
        m_referenceLossDb =
            20.0 * std::log10(4.0 * pi * frequencyHz / speedOfLight);
        m_exponent = exponent;
        m_additionalLossDb = additionalLossDb;
    }

    double RxPowerDbm(double txPowerDbm,
                      Ptr<MobilityModel> source,
                      Ptr<MobilityModel> destination) const
    {
        const double distanceM = std::max(1.0, source->GetDistanceFrom(destination));
        return txPowerDbm - m_referenceLossDb -
               10.0 * m_exponent * std::log10(distanceM) - m_additionalLossDb;
    }

  protected:
    double DoCalcRxPower(double txPowerDbm,
                         Ptr<MobilityModel> source,
                         Ptr<MobilityModel> destination) const override
    {
        return RxPowerDbm(txPowerDbm, source, destination);
    }

    std::int64_t DoAssignStreams(std::int64_t) override
    {
        return 0;
    }

  private:
    double m_referenceLossDb = 0.0;
    double m_exponent = 3.0;
    double m_additionalLossDb = 0.0;
};

NS_OBJECT_ENSURE_REGISTERED(SelfcheckSceneLossModel);

void
Receive(Ptr<Socket> socket)
{
    while (socket->GetRxAvailable() > 0)
    {
        Address source;
        Ptr<Packet> packet = socket->RecvFrom(source);
        std::vector<std::uint8_t> bytes(packet->GetSize());
        packet->CopyData(bytes.data(), bytes.size());
        if (bytes.size() != kPacketBytes ||
            !std::all_of(bytes.begin(), bytes.end(), [](std::uint8_t byte) {
                return byte == kPayloadByte;
            }))
        {
            gPayloadsValid = false;
        }
        const auto arrivalTimeNs =
            static_cast<std::uint64_t>(Simulator::Now().GetNanoSeconds());
        if (gDeliveredPackets == 0)
        {
            gFirstArrivalTimeNs = arrivalTimeNs;
        }
        gLastArrivalTimeNs = arrivalTimeNs;
        ++gDeliveredPackets;
    }
}

void
Send(Ptr<Socket> socket)
{
    const std::vector<std::uint8_t> bytes(kPacketBytes, kPayloadByte);
    for (std::uint32_t index = 0; index < kPacketCount; ++index)
    {
        Ptr<Packet> packet = Create<Packet>(bytes.data(), bytes.size());
        if (socket->Send(packet) != static_cast<int>(bytes.size()))
        {
            throw std::runtime_error("ns-3 UDP socket did not accept a complete payload");
        }
    }
}
} // namespace

int
main(int argc, char** argv)
{
    constexpr std::uint64_t sendTimeNs = 1'000'000;
    // The largest selfcheck separation is 4 m: ceil(4 / c * 1e9) = 14 ns.
    std::uint64_t propagationDelayNs = 14;
    constexpr std::uint64_t dataRateBps = 1'000'000;
    constexpr std::uint64_t frequencyMhz = 5'805;
    constexpr std::uint64_t channelNumber = 161;
    constexpr std::uint64_t channelWidthMhz = 20;

    CommandLine arguments(__FILE__);
    arguments.AddValue("propagationDelayNs", "Declared one-way channel delay in ns", propagationDelayNs);
    arguments.Parse(argc, argv);

    RngSeedManager::SetSeed(7);
    RngSeedManager::SetRun(1);

    NodeContainer nodes;
    nodes.Create(2);
    Ptr<ListPositionAllocator> positions = CreateObject<ListPositionAllocator>();
    positions->Add(Vector(0.0, 0.0, 0.0));
    positions->Add(Vector(2.0, 0.0, 0.0));
    MobilityHelper mobility;
    mobility.SetPositionAllocator(positions);
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(nodes);

    Ptr<SelfcheckDelayModel> delay = CreateObject<SelfcheckDelayModel>();
    delay->SetDelay(NanoSeconds(propagationDelayNs));
    Ptr<SelfcheckSceneLossModel> loss = CreateObject<SelfcheckSceneLossModel>();
    loss->Configure(frequencyMhz * 1'000'000.0, 3.0, 6.0);
    Ptr<MobilityModel> sourceMobility = nodes.Get(0)->GetObject<MobilityModel>();
    Ptr<ConstantPositionMobilityModel> destinationMobility =
        nodes.Get(1)->GetObject<ConstantPositionMobilityModel>();
    if (sourceMobility == nullptr || destinationMobility == nullptr)
    {
        throw std::runtime_error("selfcheck mobility model is unavailable");
    }
    const double initialRssiDbm =
        loss->RxPowerDbm(20.0, sourceMobility, destinationMobility);
    destinationMobility->SetPosition(Vector(4.0, 0.0, 0.0));
    const double movedRssiDbm =
        loss->RxPowerDbm(20.0, sourceMobility, destinationMobility);
    const double expectedRssiDeltaDb = 30.0 * std::log10(2.0);
    if (std::abs((initialRssiDbm - movedRssiDbm) - expectedRssiDeltaDb) > 1.0e-9)
    {
        throw std::runtime_error(
            "scene propagation did not consume updated ConstantPosition mobility");
    }
    Ptr<YansWifiChannel> channel = CreateObject<YansWifiChannel>();
    channel->SetPropagationDelayModel(delay);
    channel->SetPropagationLossModel(loss);

    YansWifiPhyHelper phy;
    phy.SetChannel(channel);
    phy.Set("ChannelSettings", StringValue("{161, 20, BAND_5GHZ, 0}"));
    phy.Set("TxPowerStart", DoubleValue(20.0));
    phy.Set("TxPowerEnd", DoubleValue(20.0));
    phy.Set("TxPowerLevels", UintegerValue(1));
    phy.Set("RxSensitivity", DoubleValue(-92.0));
    phy.Set("RxNoiseFigure", DoubleValue(7.0));
    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211ax);
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                 "DataMode",
                                 StringValue("HeMcs11"),
                                 "ControlMode",
                                 StringValue("HeMcs0"));
    WifiMacHelper mac;
    mac.SetType("ns3::AdhocWifiMac", "Ssid", SsidValue(Ssid("aero-selfcheck")));
    NetDeviceContainer devices = wifi.Install(phy, mac, nodes);

    InternetStackHelper internet;
    internet.Install(nodes);
    TrafficControlHelper trafficControl;
    trafficControl.SetRootQueueDisc("ns3::TbfQueueDisc",
                                    "Burst",
                                    UintegerValue(1'500),
                                    "Mtu",
                                    UintegerValue(0),
                                    "Rate",
                                    DataRateValue(DataRate(dataRateBps)),
                                    "PeakRate",
                                    DataRateValue(DataRate(0)));
    QueueDiscContainer queueDiscs = trafficControl.Install(devices);
    if (queueDiscs.GetN() != 2)
    {
        throw std::runtime_error("ns-3 Wi-Fi shaper installation failed");
    }

    Ipv4AddressHelper addresses;
    addresses.SetBase("10.73.1.0", "255.255.255.0");
    Ipv4InterfaceContainer interfaces = addresses.Assign(devices);

    Ptr<Socket> receiver = Socket::CreateSocket(nodes.Get(1), UdpSocketFactory::GetTypeId());
    if (receiver->Bind(InetSocketAddress(Ipv4Address::GetAny(), kPort)) != 0)
    {
        throw std::runtime_error("ns-3 receiver bind failed");
    }
    receiver->SetRecvCallback(MakeCallback(&Receive));

    Ptr<Socket> sender = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
    if (sender->Connect(InetSocketAddress(interfaces.GetAddress(1), kPort)) != 0)
    {
        throw std::runtime_error("ns-3 sender connect failed");
    }

    Simulator::Schedule(NanoSeconds(sendTimeNs), &Send, sender);
    Simulator::Stop(MilliSeconds(50));
    Simulator::Run();
    Simulator::Destroy();

    constexpr std::uint64_t minimumShapingSpanNs =
        (kPacketBytes * 8ULL * 1'000'000'000ULL) / dataRateBps;
    if (gDeliveredPackets != kPacketCount || !gPayloadsValid ||
        gFirstArrivalTimeNs <= sendTimeNs + propagationDelayNs ||
        gLastArrivalTimeNs - gFirstArrivalTimeNs < minimumShapingSpanNs)
    {
        std::ostringstream failure;
        failure << "real ns-3 Wi-Fi simulation did not enforce delivery and rate shaping"
                << "; delivered_packets=" << gDeliveredPackets
                << "; expected_packets=" << kPacketCount
                << "; payloads_valid=" << gPayloadsValid
                << "; first_arrival_time_ns=" << gFirstArrivalTimeNs
                << "; last_arrival_time_ns=" << gLastArrivalTimeNs
                << "; minimum_shaping_span_ns=" << minimumShapingSpanNs
                << "; propagation_delay_ns=" << propagationDelayNs
                << "; initial_rssi_dbm=" << initialRssiDbm
                << "; moved_rssi_dbm=" << movedRssiDbm;
        throw std::runtime_error(failure.str());
    }

    std::cout << std::setprecision(17)
              << "{\"channel_number\":" << channelNumber
              << ",\"channel_width_mhz\":" << channelWidthMhz
              << ",\"commit\":\"d2add90b452d600cfb4859baed8e9ea633519447\""
              << ",\"data_rate_bps\":" << dataRateBps
              << ",\"delivered_packets\":" << gDeliveredPackets
              << ",\"first_arrival_time_ns\":" << gFirstArrivalTimeNs
              << ",\"frequency_mhz\":" << frequencyMhz
              << ",\"initial_rssi_dbm\":" << initialRssiDbm
              << ",\"last_arrival_time_ns\":" << gLastArrivalTimeNs
              << ",\"log_distance_exponent\":3.0"
              << ",\"minimum_shaping_span_ns\":" << minimumShapingSpanNs
              << ",\"moved_rssi_dbm\":" << movedRssiDbm
              << ",\"network_model\":\"wifi-adhoc-scene-mobility/v1\""
              << ",\"obstruction_loss_db\":6.0"
              << ",\"packet_bytes\":" << kPacketBytes
              << ",\"propagation_delay_ns\":" << propagationDelayNs
              << ",\"send_time_ns\":" << sendTimeNs
              << ",\"status\":\"real-wifi-scene-mobility-shaped-message-in-loop-ok\""
              << ",\"version\":\"3.48\""
              << ",\"wifi_standard\":\"802.11ax\"}\n";
    return 0;
}
