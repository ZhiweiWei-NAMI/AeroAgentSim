// Strictly internal numeric control pipe. The public JSON protocol is Python.
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/wifi-module.h"
#include "scene.h"
#include <array>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <vector>

using namespace ns3;
NS_OBJECT_ENSURE_REGISTERED(SceneLoss);
NS_OBJECT_ENSURE_REGISTERED(PacketIdentity);

struct Message {
    uint64_t sequence, sent, expires;
    uint32_t source, destination, size;
    bool terminal = false, hasSignal = false;
    double rssi = 0, snr = 0;
    EventId expiry;
};

class Backend {
  public:
    void Reset(std::istream& in) {
        uint32_t seed, count, channelNumber, width;
        double sensitivity, exponent, reference, noise;
        in >> seed >> count >> m_tx >> sensitivity >> exponent >> reference
           >> noise >> channelNumber >> width;
        Require(in && !m_reset && count >= 2 && count <= 254, "invalid RESET");
        RngSeedManager::SetSeed(seed);
        RngSeedManager::SetRun(1);
        m_reset = true;
        m_nodes.Create(count);
        MobilityHelper mobility;
        mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        mobility.Install(m_nodes);
        InternetStackHelper internet;
        internet.Install(m_nodes);
        m_loss = CreateObject<SceneLoss>();
        m_loss->Configure((5000.0 + 5.0 * channelNumber) * 1e6, exponent, reference);
        auto channel = CreateObject<YansWifiChannel>();
        channel->SetPropagationLossModel(m_loss);
        channel->SetPropagationDelayModel(CreateObject<ConstantSpeedPropagationDelayModel>());
        YansWifiPhyHelper phy;
        phy.SetChannel(channel);
        phy.Set("ChannelSettings", StringValue("{" + std::to_string(channelNumber) +
            ", " + std::to_string(width) + ", BAND_5GHZ, 0}"));
        phy.Set("TxPowerStart", DoubleValue(m_tx));
        phy.Set("TxPowerEnd", DoubleValue(m_tx));
        phy.Set("TxPowerLevels", UintegerValue(1));
        phy.Set("RxSensitivity", DoubleValue(sensitivity));
        phy.Set("RxNoiseFigure", DoubleValue(noise));
        WifiHelper wifi;
        wifi.SetStandard(WIFI_STANDARD_80211n);
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
            "DataMode", StringValue("HtMcs7"), "ControlMode", StringValue("HtMcs0"));
        WifiMacHelper mac;
        // Disable aggregation: monitor traces then carry one packet identity.
        mac.SetType("ns3::AdhocWifiMac", "Ssid", SsidValue(Ssid("aeroagentsim")),
            "BE_MaxAmpduSize", UintegerValue(0), "BE_MaxAmsduSize", UintegerValue(0));
        m_devices = wifi.Install(phy, mac, m_nodes);
        wifi.AssignStreams(m_devices, 0);
        Ipv4AddressHelper addresses;
        addresses.SetBase("10.73.0.0", "255.255.255.0");
        m_addresses = addresses.Assign(m_devices);
        for (uint32_t n = 0; n < count; ++n) {
            auto socket = Socket::CreateSocket(m_nodes.Get(n), UdpSocketFactory::GetTypeId());
            Require(socket->Bind(InetSocketAddress(Ipv4Address::GetAny(), 40000)) == 0,
                    "UDP bind failed");
            socket->SetRecvCallback(MakeCallback(&Backend::Receive, this));
            m_socketNodes[socket] = n;
            m_sockets.push_back(socket);
            auto device = DynamicCast<WifiNetDevice>(m_devices.Get(n));
            Require(device->GetPhy()->TraceConnectWithoutContext("MonitorSnifferRx",
                MakeBoundCallback(&Backend::Monitor, this, n)), "missing PHY trace");
        }
        std::cout << "OK RESET\n" << std::flush;
    }

    void Position(std::istream& in) {
        uint32_t node;
        uint64_t at;
        Vector position;
        in >> node >> at >> position.x >> position.y >> position.z;
        Require(in && node < m_nodes.GetN() && at >= Now(), "invalid MOVE");
        auto mobility = m_nodes.Get(node)->GetObject<MobilityModel>();
        if (at == Now()) mobility->SetPosition(position);
        else Simulator::Schedule(NanoSeconds(at - Now()),
            [mobility, position] { mobility->SetPosition(position); });
        std::cout << "OK MOVE\n" << std::flush;
    }

    void Volume(std::istream& in) {
        SceneVolume box;
        in >> box.minimum.x >> box.minimum.y >> box.minimum.z
           >> box.maximum.x >> box.maximum.y >> box.maximum.z >> box.lossDb;
        Require(bool(in) && Now() == 0, "invalid VOLUME");
        m_loss->AddVolume(box);
        std::cout << "OK VOLUME\n" << std::flush;
    }

    void Submit(std::istream& in) {
        Message message{};
        uint64_t lifetime;
        in >> message.sequence >> message.source >> message.destination
           >> message.size >> lifetime;
        Require(in && message.source < m_nodes.GetN() &&
            message.destination < m_nodes.GetN() && message.source != message.destination &&
            message.size > 0 && message.size <= 61440 && !m_messages.count(message.sequence),
            "invalid SEND");
        message.sent = Now();
        message.expires = message.sent + lifetime;
        auto [it, inserted] = m_messages.emplace(message.sequence, message);
        Require(inserted, "duplicate packet sequence");
        it->second.expiry = Simulator::Schedule(NanoSeconds(lifetime),
            &Backend::Expire, this, message.sequence);
        Simulator::ScheduleNow(&Backend::Send, this, message.sequence);
        std::cout << "OK SEND " << message.sent << '\n' << std::flush;
    }

    void Step(std::istream& in) {
        uint64_t target;
        in >> target;
        Require(in && target > Now() && target <= INT64_MAX, "invalid STEP");
        Simulator::Stop(NanoSeconds(target - Now()));
        Simulator::Run();
        Require(Now() == target, "native frontier mismatch");
        std::cout << "OK STEP " << Now() << '\n';
        for (const auto& event : m_events) std::cout << event << '\n';
        m_events.clear();
        for (uint32_t a = 0; a < m_nodes.GetN(); ++a) {
            for (uint32_t b = a + 1; b < m_nodes.GetN(); ++b) {
                auto source = m_nodes.Get(a)->GetObject<MobilityModel>();
                auto dest = m_nodes.Get(b)->GetObject<MobilityModel>();
                auto& counters = m_stats[{a, b}];
                std::cout << std::setprecision(17) << "LINK " << a << ' ' << b << ' '
                    << source->GetDistanceFrom(dest) << ' ' << m_loss->PathLoss(source, dest)
                    << ' ' << m_tx - m_loss->PathLoss(source, dest)
                    << ' ' << counters[0] << ' ' << counters[1] << ' ' << counters[2] << '\n';
            }
        }
        std::cout << "END\n" << std::flush;
        // Expired/delivered objects are no longer retained. IDs remain unique in
        // Python; canceled expiry callbacks only retain their numeric sequence.
        for (auto it = m_messages.begin(); it != m_messages.end();) {
            if (it->second.terminal) it = m_messages.erase(it);
            else ++it;
        }
    }

    ~Backend() { Simulator::Destroy(); }

  private:
    static uint64_t Now() { return Simulator::Now().GetNanoSeconds(); }
    static void Require(bool condition, const char* message) {
        if (!condition) throw std::runtime_error(message);
    }
    std::array<uint64_t, 3>& Stats(const Message& m) {
        return m_stats[{std::min(m.source, m.destination),
                        std::max(m.source, m.destination)}];
    }
    void Drop(Message& m, const char* reason) {
        if (m.terminal) return;
        m.terminal = true;
        Simulator::Cancel(m.expiry);
        ++Stats(m)[2];
        std::ostringstream out;
        out << "DROP " << m.sequence << ' ' << Now() << ' ' << reason;
        m_events.push_back(out.str());
    }
    void Send(uint64_t sequence) {
        auto& m = m_messages.at(sequence);
        ++Stats(m)[0];
        auto packet = Create<Packet>(m.size);
        PacketIdentity tag;
        tag.sequence = sequence;
        packet->AddPacketTag(tag);
        int sent = m_sockets.at(m.source)->SendTo(packet, 0,
            InetSocketAddress(m_addresses.GetAddress(m.destination), 40000));
        if (sent != static_cast<int>(m.size)) Drop(m, "udp_send_failed");
    }
    void Expire(uint64_t sequence) {
        auto it = m_messages.find(sequence);
        if (it != m_messages.end()) Drop(it->second, "delivery_timeout");
    }
    void Receive(Ptr<Socket> socket) {
        while (auto packet = socket->Recv()) {
            PacketIdentity tag;
            Require(packet->PeekPacketTag(tag), "received packet has no identity");
            auto it = m_messages.find(tag.sequence);
            // The explicit application lifetime can expire before late UDP
            // reception. This is a terminal application drop, never delivery.
            if (it == m_messages.end() || it->second.terminal) continue;
            auto& m = it->second;
            Require(m.destination == m_socketNodes.at(socket) && packet->GetSize() == m.size,
                "UDP destination or size mismatch");
            m.terminal = true;
            Simulator::Cancel(m.expiry);
            ++Stats(m)[1];
            std::ostringstream out;
            out << std::setprecision(17) << "DELIVERY " << m.sequence << ' ' << m.sent << ' '
                << Now() << ' ' << m.size;
            if (m.hasSignal) out << ' ' << m.rssi << ' ' << m.snr;
            m_events.push_back(out.str());
        }
    }
    static void Monitor(Backend* self, uint32_t node, Ptr<const Packet> packet,
                        uint16_t, WifiTxVector, MpduInfo, SignalNoiseDbm signal, uint16_t) {
        PacketIdentity tag;
        if (!packet->PeekPacketTag(tag)) return; // ACK/ARP frames have no app tag.
        auto it = self->m_messages.find(tag.sequence);
        if (it != self->m_messages.end() && it->second.destination == node) {
            it->second.hasSignal = true;
            it->second.rssi = signal.signal;
            it->second.snr = signal.signal - signal.noise;
        }
    }
    bool m_reset = false;
    double m_tx = 20;
    NodeContainer m_nodes;
    NetDeviceContainer m_devices;
    Ipv4InterfaceContainer m_addresses;
    Ptr<SceneLoss> m_loss;
    std::vector<Ptr<Socket>> m_sockets;
    std::map<Ptr<Socket>, uint32_t> m_socketNodes;
    std::map<uint64_t, Message> m_messages;
    std::map<std::pair<uint32_t, uint32_t>, std::array<uint64_t, 3>> m_stats;
    std::vector<std::string> m_events;
};

int main() {
    Backend backend;
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            std::istringstream in(line);
            std::string op;
            in >> op;
            if (op == "RESET") backend.Reset(in);
            else if (op == "MOVE") backend.Position(in);
            else if (op == "VOLUME") backend.Volume(in);
            else if (op == "SEND") backend.Submit(in);
            else if (op == "STEP") backend.Step(in);
            else if (op == "CLOSE") { std::cout << "OK CLOSE\n" << std::flush; return 0; }
            else throw std::runtime_error("unknown native command");
        } catch (const std::exception& error) {
            std::cerr << error.what() << '\n';
            std::cout << "ERR native_failure\n" << std::flush;
            return 1; // A partial mutation cannot be retried.
        }
    }
}
