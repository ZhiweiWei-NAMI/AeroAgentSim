#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/propagation-module.h"
#include "ns3/traffic-control-module.h"
#include "ns3/wifi-module.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

using namespace ns3;

namespace
{
constexpr std::uint16_t kFirstMessagePort = 40'000;
constexpr std::size_t kMaxLinks = 253;
constexpr std::size_t kMaxNodes = kMaxLinks + 1;
constexpr std::size_t kMaxRadioProfiles = kMaxNodes;
// Keep the real UDP backend bound identical to the provider protocol and the
// Python service validator.  The limit is below the IPv4 UDP payload maximum
// (65,507 bytes), so ns-3 can carry the complete datagram without truncation.
constexpr std::size_t kMaxPayloadBytes = 60 * 1024;
// TBF sees the complete IPv4/UDP packet, not only the application payload.
// Its burst must therefore admit the largest protocol datagram in one queue
// transaction while the configured rate still controls serialization.
constexpr std::size_t kQueueBurstBytes = kMaxPayloadBytes + 256;
constexpr double kLogDistanceExponent = 3.0;
constexpr double kRxNoiseFigureDb = 7.0;
constexpr double kThermalNoiseDensityDbmHz = -174.0;
constexpr char kNetworkModel[] = "wifi-adhoc-scene-mobility/v1";
constexpr char kProtocolVersion[] = "aero-bench.ns3-rpc/v5";
constexpr char kNs3Commit[] = "d2add90b452d600cfb4859baed8e9ea633519447";
constexpr char kNs3Version[] = "3.48";

std::vector<std::string>
Split(const std::string& value, char separator)
{
    std::vector<std::string> parts;
    std::size_t start = 0;
    while (true)
    {
        const std::size_t end = value.find(separator, start);
        parts.push_back(value.substr(start, end == std::string::npos ? end : end - start));
        if (end == std::string::npos)
        {
            return parts;
        }
        start = end + 1;
    }
}

std::uint64_t
ParseUnsigned(const std::string& value, const char* field)
{
    if (value.empty() ||
        (value.size() > 1 && value.front() == '0') ||
        std::any_of(value.begin(), value.end(), [](unsigned char character) {
            return character < '0' || character > '9';
        }))
    {
        throw std::runtime_error(std::string(field) + " must be a canonical unsigned integer");
    }
    std::size_t parsed = 0;
    const auto number = std::stoull(value, &parsed, 10);
    if (parsed != value.size())
    {
        throw std::runtime_error(std::string(field) + " must be a canonical unsigned integer");
    }
    return number;
}

double
ParseFinite(const std::string& value, const char* field, double minimum, double maximum)
{
    if (value.empty())
    {
        throw std::runtime_error(std::string(field) + " must be a finite number");
    }
    std::size_t parsed = 0;
    const double number = std::stod(value, &parsed);
    if (parsed != value.size() || !std::isfinite(number) || number < minimum ||
        number > maximum)
    {
        throw std::runtime_error(std::string(field) + " is outside its finite range");
    }
    return number;
}

void
RequireSha256(const std::string& value, const char* field)
{
    if (value.size() != 64 || value == std::string(64, '0') ||
        std::any_of(value.begin(), value.end(), [](unsigned char character) {
            return !((character >= '0' && character <= '9') ||
                     (character >= 'a' && character <= 'f'));
        }))
    {
        throw std::runtime_error(std::string(field) + " must be a non-placeholder SHA-256");
    }
}

std::vector<std::uint8_t>
DecodeBase64(const std::string& encoded)
{
    if (encoded.empty() || encoded.size() % 4 != 0)
    {
        throw std::runtime_error("base64 value is not canonical");
    }
    static constexpr char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::array<int, 256> values{};
    values.fill(-1);
    for (int index = 0; alphabet[index] != '\0'; ++index)
    {
        values[static_cast<unsigned char>(alphabet[index])] = index;
    }

    std::vector<std::uint8_t> output;
    output.reserve((encoded.size() / 4) * 3);
    for (std::size_t index = 0; index < encoded.size(); index += 4)
    {
        const char a = encoded[index];
        const char b = encoded[index + 1];
        const char c = encoded[index + 2];
        const char d = encoded[index + 3];
        if (a == '=' || b == '=' || values[static_cast<unsigned char>(a)] < 0 ||
            values[static_cast<unsigned char>(b)] < 0)
        {
            throw std::runtime_error("base64 value is not canonical");
        }
        const bool cPadding = c == '=';
        const bool dPadding = d == '=';
        if (cPadding && !dPadding)
        {
            throw std::runtime_error("base64 padding is not canonical");
        }
        if ((!cPadding && values[static_cast<unsigned char>(c)] < 0) ||
            (!dPadding && values[static_cast<unsigned char>(d)] < 0))
        {
            throw std::runtime_error("base64 value is not canonical");
        }
        if (index + 4 != encoded.size() && (cPadding || dPadding))
        {
            throw std::runtime_error("base64 padding must terminate the value");
        }
        const int first = values[static_cast<unsigned char>(a)];
        const int second = values[static_cast<unsigned char>(b)];
        const int third = cPadding ? 0 : values[static_cast<unsigned char>(c)];
        const int fourth = dPadding ? 0 : values[static_cast<unsigned char>(d)];
        if ((cPadding && (second & 0x0f) != 0) ||
            (dPadding && !cPadding && (third & 0x03) != 0))
        {
            throw std::runtime_error("base64 value has non-zero padding bits");
        }
        output.push_back(static_cast<std::uint8_t>((first << 2) | (second >> 4)));
        if (!cPadding)
        {
            output.push_back(static_cast<std::uint8_t>((second << 4) | (third >> 2)));
        }
        if (!dPadding)
        {
            output.push_back(static_cast<std::uint8_t>((third << 6) | fourth));
        }
    }
    return output;
}

std::string
DecodeText(const std::string& encoded, const char* field)
{
    const auto bytes = DecodeBase64(encoded);
    if (bytes.empty() ||
        std::any_of(bytes.begin(), bytes.end(), [](std::uint8_t byte) {
            return byte < 0x20 || byte > 0x7e;
        }))
    {
        throw std::runtime_error(std::string(field) + " must be non-empty ASCII text");
    }
    return std::string(bytes.begin(), bytes.end());
}

struct WifiModeContract
{
    std::string dataMode;
    std::string controlMode;
    std::uint64_t maxDataRateBps;
};

WifiModeContract
ModeContract(const std::string& standard, std::uint64_t widthMhz)
{
    if (standard == "802.11n")
    {
        if (widthMhz == 20)
        {
            return {"HtMcs7", "HtMcs0", 65'000'000};
        }
        if (widthMhz == 40)
        {
            return {"HtMcs7", "HtMcs0", 135'000'000};
        }
    }
    else if (standard == "802.11ac")
    {
        if (widthMhz == 20)
        {
            return {"VhtMcs8", "VhtMcs0", 78'000'000};
        }
        if (widthMhz == 40)
        {
            return {"VhtMcs9", "VhtMcs0", 200'000'000};
        }
        if (widthMhz == 80)
        {
            return {"VhtMcs9", "VhtMcs0", 433'000'000};
        }
        if (widthMhz == 160)
        {
            return {"VhtMcs9", "VhtMcs0", 866'000'000};
        }
    }
    else if (standard == "802.11ax")
    {
        if (widthMhz == 20)
        {
            return {"HeMcs11", "HeMcs0", 143'000'000};
        }
        if (widthMhz == 40)
        {
            return {"HeMcs11", "HeMcs0", 286'000'000};
        }
        if (widthMhz == 80)
        {
            return {"HeMcs11", "HeMcs0", 600'000'000};
        }
        if (widthMhz == 160)
        {
            return {"HeMcs11", "HeMcs0", 1'201'000'000};
        }
    }
    throw std::runtime_error("radio profile standard/channel width is unsupported");
}

WifiStandard
Ns3WifiStandard(const std::string& standard)
{
    if (standard == "802.11n")
    {
        return WIFI_STANDARD_80211n;
    }
    if (standard == "802.11ac")
    {
        return WIFI_STANDARD_80211ac;
    }
    if (standard == "802.11ax")
    {
        return WIFI_STANDARD_80211ax;
    }
    throw std::runtime_error("radio profile Wi-Fi standard is unsupported");
}

bool
IsOperatingChannel(const std::string& standard,
                   std::uint64_t frequencyMhz,
                   std::uint64_t channelNumber,
                   std::uint64_t widthMhz,
                   const std::string& band)
{
    if (band == "BAND_2_4GHZ")
    {
        const bool allowed =
            (widthMhz == 20 && channelNumber >= 1 && channelNumber <= 13) ||
            (widthMhz == 40 && channelNumber >= 3 && channelNumber <= 11);
        return standard != "802.11ac" && allowed &&
               frequencyMhz == 2'407 + 5 * channelNumber;
    }
    if (band == "BAND_5GHZ")
    {
        bool allowed = false;
        if (widthMhz == 20)
        {
            allowed =
                (channelNumber >= 36 && channelNumber <= 64 && channelNumber % 4 == 0) ||
                (channelNumber >= 100 && channelNumber <= 144 && channelNumber % 4 == 0) ||
                (channelNumber >= 149 && channelNumber <= 181 &&
                 (channelNumber - 149) % 4 == 0);
        }
        else if (widthMhz == 40)
        {
            allowed =
                (channelNumber >= 38 && channelNumber <= 62 &&
                 (channelNumber - 38) % 8 == 0) ||
                (channelNumber >= 102 && channelNumber <= 142 &&
                 (channelNumber - 102) % 8 == 0) ||
                (channelNumber >= 151 && channelNumber <= 175 &&
                 (channelNumber - 151) % 8 == 0);
        }
        else if (widthMhz == 80)
        {
            allowed =
                (channelNumber >= 42 && channelNumber <= 58 &&
                 (channelNumber - 42) % 16 == 0) ||
                (channelNumber >= 106 && channelNumber <= 138 &&
                 (channelNumber - 106) % 16 == 0) ||
                (channelNumber >= 155 && channelNumber <= 171 &&
                 (channelNumber - 155) % 16 == 0);
        }
        else if (widthMhz == 160)
        {
            allowed = channelNumber == 50 || channelNumber == 114 || channelNumber == 163;
        }
        return allowed && frequencyMhz == 5'000 + 5 * channelNumber;
    }
    if (band == "BAND_6GHZ")
    {
        bool allowed = false;
        if (widthMhz == 20)
        {
            allowed = channelNumber >= 1 && channelNumber <= 233 &&
                      (channelNumber - 1) % 4 == 0;
        }
        else if (widthMhz == 40)
        {
            allowed = channelNumber >= 3 && channelNumber <= 227 &&
                      (channelNumber - 3) % 8 == 0;
        }
        else if (widthMhz == 80)
        {
            allowed = channelNumber >= 7 && channelNumber <= 215 &&
                      (channelNumber - 7) % 16 == 0;
        }
        else if (widthMhz == 160)
        {
            allowed = channelNumber >= 15 && channelNumber <= 207 &&
                      (channelNumber - 15) % 32 == 0;
        }
        return standard == "802.11ax" && allowed &&
               frequencyMhz == 5'950 + 5 * channelNumber;
    }
    return false;
}

class DeclaredLinkDelayModel final : public PropagationDelayModel
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId typeId = TypeId("ns3::DeclaredLinkDelayModel")
                                   .SetParent<PropagationDelayModel>()
                                   .SetGroupName("Propagation")
                                   .AddConstructor<DeclaredLinkDelayModel>();
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

NS_OBJECT_ENSURE_REGISTERED(DeclaredLinkDelayModel);

class ScenePropagationLossModel : public PropagationLossModel
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::ScenePropagationLossModel")
                                .SetParent<PropagationLossModel>()
                                .SetGroupName("Propagation")
                                .AddConstructor<ScenePropagationLossModel>();
        return tid;
    }

    void Configure(double frequencyHz, double exponent)
    {
        if (!std::isfinite(frequencyHz) || frequencyHz <= 0.0 ||
            !std::isfinite(exponent) || exponent < 1.0 || exponent > 8.0)
        {
            throw std::runtime_error("scene propagation profile is invalid");
        }
        constexpr double speedOfLight = 299'792'458.0;
        const double pi = std::acos(-1.0);
        m_referenceLossDb =
            20.0 * std::log10(4.0 * pi * frequencyHz / speedOfLight);
        m_exponent = exponent;
    }

    void SetAdditionalLossDb(double value)
    {
        if (!std::isfinite(value) || value < 0.0 || value > 1'000.0)
        {
            throw std::runtime_error("scene propagation attenuation is invalid");
        }
        m_additionalLossDb = value;
    }

    double GetBasePathLossDb(Ptr<MobilityModel> source,
                             Ptr<MobilityModel> destination) const
    {
        const double distanceM = std::max(1.0, source->GetDistanceFrom(destination));
        return m_referenceLossDb + 10.0 * m_exponent * std::log10(distanceM);
    }

    double GetAdditionalLossDb() const
    {
        return m_additionalLossDb;
    }

    double GetRxPowerDbm(double txPowerDbm,
                         Ptr<MobilityModel> source,
                         Ptr<MobilityModel> destination) const
    {
        return txPowerDbm - GetBasePathLossDb(source, destination) - m_additionalLossDb;
    }

  protected:
    double DoCalcRxPower(double txPowerDbm,
                         Ptr<MobilityModel> source,
                         Ptr<MobilityModel> destination) const override
    {
        return GetRxPowerDbm(txPowerDbm, source, destination);
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

NS_OBJECT_ENSURE_REGISTERED(ScenePropagationLossModel);

struct RadioProfileSpec
{
    std::string profileToken;
    std::string providerToken;
    std::string standard;
    std::uint64_t frequencyMhz;
    std::uint64_t channelNumber;
    std::uint64_t channelWidthMhz;
    std::string band;
    double txPowerDbm;
    double rxSensitivityDbm;
    std::string dataMode;
    std::string controlMode;
    std::uint64_t maxDataRateBps;
};

struct NodeSpec
{
    std::string nodeToken;
    std::string entityToken;
    std::string endpointToken;
    std::string profileToken;
    Vector position;
};

struct LinkSpec
{
    std::string linkToken;
    std::string sourceToken;
    std::string destinationToken;
    std::uint64_t dataRateBps;
    std::uint64_t propagationDelayNs;
};

struct MessageState
{
    std::string messageId;
    std::string source;
    std::string destination;
    std::string payloadBase64;
    std::vector<std::uint8_t> payload;
    std::uint64_t sendTick;
    std::uint64_t sendTimeNs;
    std::uint16_t port;
    Ptr<Socket> sender;
    Ptr<Socket> receiver;
    bool sent = false;
    bool delivered = false;
    bool reported = false;
    std::uint64_t arrivalTimeNs = 0;
};

class ProviderBackend;

void SendPacket(ProviderBackend* backend, MessageState* message);
void ReceivePacket(ProviderBackend* backend, std::uint16_t port, Ptr<Socket> socket);

class ProviderBackend
{
  public:
    void Prepare(const std::vector<std::string>& fields)
    {
        if (fields.size() != 10)
        {
            throw std::runtime_error("PREPARE requires nine arguments");
        }
        DecodeText(fields[1], "provider_id");
        const auto protocol = DecodeText(fields[2], "protocol_version");
        DecodeText(fields[3], "runtime_image");
        const auto version = DecodeText(fields[4], "ns-3 version");
        const auto commit = DecodeText(fields[5], "ns-3 commit");
        const auto networkModel = DecodeText(fields[6], "network model");
        if (protocol != kProtocolVersion)
        {
            throw std::runtime_error("PREPARE protocol version is unsupported");
        }
        if (version != kNs3Version || commit != kNs3Commit)
        {
            throw std::runtime_error("ns-3 identity does not match 3.48/d2add90b");
        }
        if (networkModel != kNetworkModel)
        {
            throw std::runtime_error("PREPARE network model is unsupported");
        }

        m_profiles.clear();
        for (const auto& encodedProfile : Split(fields[7], ';'))
        {
            const auto profile = Split(encodedProfile, ',');
            if (profile.size() != 12)
            {
                throw std::runtime_error("PREPARE radio profile has invalid field count");
            }
            DecodeText(profile[0], "radio_profile_id");
            DecodeText(profile[1], "radio profile provider_id");
            if (profile[1] != fields[1])
            {
                throw std::runtime_error("PREPARE radio profile owner is invalid");
            }
            const auto standard = DecodeText(profile[2], "wifi_standard");
            const auto frequencyMhz = ParseUnsigned(profile[3], "frequency_mhz");
            const auto channelNumber = ParseUnsigned(profile[4], "channel_number");
            const auto widthMhz = ParseUnsigned(profile[5], "channel_width_mhz");
            const auto band = DecodeText(profile[6], "Wi-Fi band");
            const auto txPower = ParseFinite(profile[7], "tx_power_dbm", -100.0, 100.0);
            const auto sensitivity =
                ParseFinite(profile[8], "rx_sensitivity_dbm", -200.0, 0.0);
            const auto dataMode = DecodeText(profile[9], "Wi-Fi data mode");
            const auto controlMode = DecodeText(profile[10], "Wi-Fi control mode");
            const auto maxRate = ParseUnsigned(profile[11], "max_data_rate_bps");
            const auto expectedMode = ModeContract(standard, widthMhz);
            if (!IsOperatingChannel(
                    standard, frequencyMhz, channelNumber, widthMhz, band) ||
                dataMode != expectedMode.dataMode || controlMode != expectedMode.controlMode ||
                maxRate != expectedMode.maxDataRateBps || txPower <= sensitivity)
            {
                throw std::runtime_error("PREPARE radio profile contract is invalid");
            }
            if (!m_profiles
                     .emplace(profile[0],
                              RadioProfileSpec{profile[0],
                                               profile[1],
                                               standard,
                                               frequencyMhz,
                                               channelNumber,
                                               widthMhz,
                                               band,
                                               txPower,
                                               sensitivity,
                                               dataMode,
                                               controlMode,
                                               maxRate})
                     .second)
            {
                throw std::runtime_error("PREPARE radio profile ID repeats");
            }
        }
        if (m_profiles.empty() || m_profiles.size() > kMaxRadioProfiles)
        {
            throw std::runtime_error("PREPARE radio profile count is outside the model range");
        }

        m_nodeIds.clear();
        m_nodeIndex.clear();
        m_nodesById.clear();
        std::set<std::string> entityIds;
        std::set<std::string> endpointIds;
        std::set<std::string> usedProfiles;
        for (const auto& encodedNode : Split(fields[8], ';'))
        {
            const auto node = Split(encodedNode, ',');
            if (node.size() != 7)
            {
                throw std::runtime_error("PREPARE node binding has invalid field count");
            }
            DecodeText(node[0], "node_id");
            DecodeText(node[1], "entity_id");
            DecodeText(node[2], "endpoint_id");
            DecodeText(node[3], "radio_profile_id");
            if (m_profiles.count(node[3]) == 0 || !entityIds.insert(node[1]).second ||
                !endpointIds.insert(node[2]).second ||
                !m_nodeIndex.emplace(node[0], m_nodeIds.size()).second)
            {
                throw std::runtime_error("PREPARE node binding is not bijective");
            }
            const Vector position(
                ParseFinite(node[4], "node east_m", -1.0e9, 1.0e9),
                ParseFinite(node[5], "node north_m", -1.0e9, 1.0e9),
                ParseFinite(node[6], "node up_m", -1.0e9, 1.0e9));
            m_nodeIds.push_back(node[0]);
            m_nodesById.emplace(
                node[0], NodeSpec{node[0], node[1], node[2], node[3], position});
            usedProfiles.insert(node[3]);
        }
        if (m_nodeIds.size() < 2 || m_nodeIds.size() > kMaxNodes ||
            usedProfiles.size() != m_profiles.size())
        {
            throw std::runtime_error("PREPARE nodes do not close radio profiles");
        }

        m_links.clear();
        m_linkIds.clear();
        std::set<std::pair<std::string, std::string>> linkPairs;
        std::set<std::string> linkedNodes;
        for (const auto& encodedLink : Split(fields[9], ';'))
        {
            const auto link = Split(encodedLink, ',');
            if (link.size() != 5)
            {
                throw std::runtime_error("PREPARE link has invalid field count");
            }
            DecodeText(link[0], "link_id");
            DecodeText(link[1], "source_node_id");
            DecodeText(link[2], "destination_node_id");
            const auto source = m_nodesById.find(link[1]);
            const auto destination = m_nodesById.find(link[2]);
            if (source == m_nodesById.end() || destination == m_nodesById.end() ||
                source == destination)
            {
                throw std::runtime_error("PREPARE link endpoint is invalid");
            }
            auto pair = std::minmax(link[1], link[2]);
            if (!linkPairs.emplace(pair.first, pair.second).second)
            {
                throw std::runtime_error("PREPARE parallel Wi-Fi links are unsupported");
            }
            const auto& sourceProfile = m_profiles.at(source->second.profileToken);
            const auto& destinationProfile = m_profiles.at(destination->second.profileToken);
            if (sourceProfile.standard != destinationProfile.standard ||
                sourceProfile.frequencyMhz != destinationProfile.frequencyMhz ||
                sourceProfile.channelNumber != destinationProfile.channelNumber ||
                sourceProfile.channelWidthMhz != destinationProfile.channelWidthMhz ||
                sourceProfile.band != destinationProfile.band)
            {
                throw std::runtime_error("PREPARE link joins radio-incompatible nodes");
            }
            const auto rate = ParseUnsigned(link[3], "data_rate_bps");
            const auto delay = ParseUnsigned(link[4], "propagation_delay_ns");
            if (rate == 0 || rate > sourceProfile.maxDataRateBps ||
                rate > destinationProfile.maxDataRateBps ||
                delay > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
                !m_linkIds.insert(link[0]).second)
            {
                throw std::runtime_error("PREPARE link rate, delay, or identity is invalid");
            }
            linkedNodes.insert(link[1]);
            linkedNodes.insert(link[2]);
            m_links.push_back({link[0], link[1], link[2], rate, delay});
        }
        if (m_links.empty() || m_links.size() > kMaxLinks ||
            linkedNodes.size() != m_nodeIds.size() || !AllNodesConnected())
        {
            throw std::runtime_error("PREPARE links do not close a connected topology");
        }
        m_prepared = true;
        m_stopped = false;
        std::cout << "OK PREPARE " << fields[6] << ' ' << m_profiles.size() << ' '
                  << m_nodeIds.size() << ' ' << m_links.size() << "\n"
                  << std::flush;
    }

    void Reset(const std::vector<std::string>& fields)
    {
        RequirePrepared();
        if (fields.size() != 2)
        {
            throw std::runtime_error("RESET requires one seed argument");
        }
        const auto seed = ParseUnsigned(fields[1], "seed");
        if (seed == 0 || seed > std::numeric_limits<std::uint32_t>::max())
        {
            throw std::runtime_error("RESET seed must be in [1, 2^32-1]");
        }
        Simulator::Destroy();
        RngSeedManager::SetSeed(static_cast<std::uint32_t>(seed));
        RngSeedManager::SetRun(1);
        m_nodes = NodeContainer();
        m_primaryIp.clear();
        m_linkLossModels.clear();
        m_linkQueues.clear();
        m_messages.clear();
        m_messagesById.clear();
        m_currentTimeNs = 0;
        m_mobilityStaged = false;
        m_sceneStateDigest.clear();
        m_nextPort = kFirstMessagePort;
        m_fatalError.clear();
        BuildTopology();
        std::cout << "OK RESET 0 0 0 0\n" << std::flush;
    }

    void ApplyMobility(const std::vector<std::string>& fields)
    {
        RequireRunning();
        if (fields.size() != 4)
        {
            throw std::runtime_error("MOBILITY requires scene digest, nodes, and links");
        }
        RequireSha256(fields[1], "scene_state_digest");
        if (m_mobilityStaged)
        {
            throw std::runtime_error("MOBILITY was already staged for the next step");
        }

        const auto encodedNodes = Split(fields[2], ';');
        if (encodedNodes.size() != m_nodeIds.size())
        {
            throw std::runtime_error("MOBILITY node inventory differs from PREPARE");
        }
        std::vector<Vector> positions;
        positions.reserve(encodedNodes.size());
        for (std::size_t index = 0; index < encodedNodes.size(); ++index)
        {
            const auto node = Split(encodedNodes[index], ',');
            if (node.size() != 4 || node[0] != m_nodeIds[index])
            {
                throw std::runtime_error("MOBILITY node order or identity differs");
            }
            Ptr<ConstantPositionMobilityModel> mobility =
                m_nodes.Get(index)->GetObject<ConstantPositionMobilityModel>();
            if (mobility == nullptr)
            {
                throw std::runtime_error("MOBILITY node has no position model");
            }
            positions.emplace_back(
                ParseFinite(node[1], "node east_m", -1.0e9, 1.0e9),
                ParseFinite(node[2], "node north_m", -1.0e9, 1.0e9),
                ParseFinite(node[3], "node up_m", -1.0e9, 1.0e9));
        }

        const auto encodedLinks = Split(fields[3], ';');
        if (encodedLinks.size() != m_links.size())
        {
            throw std::runtime_error("MOBILITY link inventory differs from PREPARE");
        }
        std::vector<double> attenuationsDb;
        attenuationsDb.reserve(encodedLinks.size());
        for (std::size_t index = 0; index < encodedLinks.size(); ++index)
        {
            const auto link = Split(encodedLinks[index], ',');
            if (link.size() != 2 || link[0] != m_links[index].linkToken)
            {
                throw std::runtime_error("MOBILITY link order or identity differs");
            }
            const auto model = m_linkLossModels.find(link[0]);
            if (model == m_linkLossModels.end() || model->second == nullptr)
            {
                throw std::runtime_error("MOBILITY link has no propagation model");
            }
            attenuationsDb.push_back(
                ParseFinite(link[1], "link attenuation_db", 0.0, 1'000.0));
        }

        for (std::size_t index = 0; index < positions.size(); ++index)
        {
            Ptr<ConstantPositionMobilityModel> mobility =
                m_nodes.Get(index)->GetObject<ConstantPositionMobilityModel>();
            mobility->SetPosition(positions[index]);
            m_nodesById.at(m_nodeIds[index]).position = positions[index];
        }
        for (std::size_t index = 0; index < attenuationsDb.size(); ++index)
        {
            m_linkLossModels.at(m_links[index].linkToken)
                ->SetAdditionalLossDb(attenuationsDb[index]);
        }
        m_sceneStateDigest = fields[1];
        m_mobilityStaged = true;
        std::cout << "OK MOBILITY " << fields[1] << ' ' << encodedNodes.size() << ' '
                  << encodedLinks.size() << "\n"
                  << std::flush;
    }

    void SubmitMessage(const std::vector<std::string>& fields)
    {
        RequireRunning();
        if (fields.size() != 7)
        {
            throw std::runtime_error("MESSAGE requires six arguments");
        }
        DecodeText(fields[1], "message_id");
        const auto payload = DecodeBase64(fields[4]);
        if (payload.empty() || payload.size() > kMaxPayloadBytes)
        {
            throw std::runtime_error("MESSAGE payload size is outside the protocol range");
        }
        const auto sourceIt = m_nodeIndex.find(fields[2]);
        const auto destinationIt = m_nodeIndex.find(fields[3]);
        if (sourceIt == m_nodeIndex.end() || destinationIt == m_nodeIndex.end() ||
            !Reachable(sourceIt->second, destinationIt->second))
        {
            throw std::runtime_error("MESSAGE endpoints are not connected in ns-3 topology");
        }
        if (m_nextPort == std::numeric_limits<std::uint16_t>::max())
        {
            throw std::runtime_error("MESSAGE port range exhausted");
        }
        const auto sendTick = ParseUnsigned(fields[5], "send_tick");
        const auto sendTimeNs = ParseUnsigned(fields[6], "send_time_ns");
        if (sendTimeNs != m_currentTimeNs)
        {
            throw std::runtime_error("MESSAGE send time must equal simulator time");
        }
        if (m_messagesById.count(fields[1]) != 0)
        {
            throw std::runtime_error("MESSAGE message_id was already submitted");
        }

        auto message = std::make_unique<MessageState>();
        message->messageId = fields[1];
        message->source = fields[2];
        message->destination = fields[3];
        message->payloadBase64 = fields[4];
        message->payload = payload;
        message->sendTick = sendTick;
        message->sendTimeNs = sendTimeNs;
        message->port = m_nextPort++;
        MessageState* messagePointer = message.get();

        const auto destinationNode = m_nodes.Get(destinationIt->second);
        message->receiver = Socket::CreateSocket(destinationNode, UdpSocketFactory::GetTypeId());
        if (message->receiver->Bind(InetSocketAddress(Ipv4Address::GetAny(), message->port)) != 0)
        {
            throw std::runtime_error("MESSAGE receiver bind failed");
        }
        message->receiver->SetRecvCallback(
            MakeBoundCallback(&ReceivePacket, this, message->port));

        const auto sourceNode = m_nodes.Get(sourceIt->second);
        message->sender = Socket::CreateSocket(sourceNode, UdpSocketFactory::GetTypeId());
        if (message->sender->Connect(InetSocketAddress(m_primaryIp[destinationIt->second],
                                                       message->port)) != 0)
        {
            throw std::runtime_error("MESSAGE sender connect failed");
        }
        m_messagesById.emplace(message->messageId, messagePointer);
        m_messages.push_back(std::move(message));
        Simulator::ScheduleNow(&SendPacket, this, messagePointer);
        std::cout << "OK MESSAGE\n" << std::flush;
    }

    void Step(std::uint64_t targetTimeNs)
    {
        RequireRunning();
        if (!m_mobilityStaged || m_sceneStateDigest.empty())
        {
            throw std::runtime_error("STEP requires staged SceneState mobility");
        }
        if (targetTimeNs <= m_currentTimeNs ||
            targetTimeNs > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()))
        {
            throw std::runtime_error("STEP target must advance valid simulator time");
        }
        Simulator::Stop(NanoSeconds(targetTimeNs - m_currentTimeNs));
        Simulator::Run();
        if (!m_fatalError.empty())
        {
            throw std::runtime_error(m_fatalError);
        }
        m_currentTimeNs = Simulator::Now().GetNanoSeconds();
        if (m_currentTimeNs != targetTimeNs)
        {
            throw std::runtime_error("ns-3 simulator did not reach requested time");
        }
        m_mobilityStaged = false;

        std::size_t pending = 0;
        std::size_t delivered = 0;
        for (const auto& message : m_messages)
        {
            if (message->delivered)
            {
                ++delivered;
            }
            else
            {
                ++pending;
            }
        }
        std::cout << "OK STEP " << m_currentTimeNs << ' ' << m_messages.size() << ' '
                  << delivered << ' ' << pending << ' ' << m_sceneStateDigest << '\n';
        for (const auto& link : m_links)
        {
            const auto sourceIndex = m_nodeIndex.at(link.sourceToken);
            const auto destinationIndex = m_nodeIndex.at(link.destinationToken);
            Ptr<MobilityModel> sourceMobility =
                m_nodes.Get(sourceIndex)->GetObject<MobilityModel>();
            Ptr<MobilityModel> destinationMobility =
                m_nodes.Get(destinationIndex)->GetObject<MobilityModel>();
            const auto loss = m_linkLossModels.at(link.linkToken);
            const auto& queues = m_linkQueues.at(link.linkToken);
            if (sourceMobility == nullptr || destinationMobility == nullptr ||
                loss == nullptr || queues[0] == nullptr || queues[1] == nullptr)
            {
                throw std::runtime_error("STEP link runtime state is unavailable");
            }
            const auto& sourceProfile =
                m_profiles.at(m_nodesById.at(link.sourceToken).profileToken);
            const auto& destinationProfile =
                m_profiles.at(m_nodesById.at(link.destinationToken).profileToken);
            const double distanceM = sourceMobility->GetDistanceFrom(destinationMobility);
            const double basePathLossDb =
                loss->GetBasePathLossDb(sourceMobility, destinationMobility);
            const double additionalLossDb = loss->GetAdditionalLossDb();
            const double forwardRssiDbm = loss->GetRxPowerDbm(
                sourceProfile.txPowerDbm, sourceMobility, destinationMobility);
            const double reverseRssiDbm = loss->GetRxPowerDbm(
                destinationProfile.txPowerDbm, destinationMobility, sourceMobility);
            const double destinationNoiseFloorDbm =
                kThermalNoiseDensityDbmHz +
                10.0 * std::log10(destinationProfile.channelWidthMhz * 1'000'000.0) +
                kRxNoiseFigureDb;
            const double sourceNoiseFloorDbm =
                kThermalNoiseDensityDbmHz +
                10.0 * std::log10(sourceProfile.channelWidthMhz * 1'000'000.0) +
                kRxNoiseFigureDb;
            std::cout << std::setprecision(17) << "LINK " << link.linkToken << ' '
                      << link.sourceToken << ' ' << link.destinationToken << ' '
                      << distanceM << ' ' << basePathLossDb << ' ' << additionalLossDb
                      << ' ' << forwardRssiDbm << ' ' << reverseRssiDbm << ' '
                      << forwardRssiDbm - destinationNoiseFloorDbm << ' '
                      << reverseRssiDbm - sourceNoiseFloorDbm << ' '
                      << queues[0]->GetNPackets() << ' ' << queues[1]->GetNPackets()
                      << ' ' << queues[0]->GetNBytes() << ' ' << queues[1]->GetNBytes()
                      << '\n';
        }
        for (const auto& message : m_messages)
        {
            if (!message->delivered || message->reported)
            {
                continue;
            }
            std::cout << "DELIVERY " << message->messageId << ' ' << message->source << ' '
                      << message->destination << ' ' << message->sendTick << ' '
                      << message->sendTimeNs << ' ' << message->arrivalTimeNs << ' '
                      << message->payloadBase64 << '\n';
            message->reported = true;
        }
        std::cout << "END\n" << std::flush;
    }

    void Snapshot()
    {
        RequireRunning();
        std::size_t delivered = 0;
        for (const auto& message : m_messages)
        {
            if (message->delivered)
            {
                ++delivered;
            }
        }
        std::cout << "OK SNAPSHOT " << m_currentTimeNs << ' ' << m_messages.size() << ' '
                  << delivered << '\n'
                  << std::flush;
    }

    void Shutdown()
    {
        if (m_prepared)
        {
            Simulator::Destroy();
        }
        m_prepared = false;
        m_stopped = true;
        std::cout << "OK SHUTDOWN\n" << std::flush;
    }

    void Send(MessageState* message)
    {
        if (message->sender == nullptr)
        {
            m_fatalError = "ns-3 sender socket was destroyed before send";
            return;
        }
        const auto packet = Create<Packet>(message->payload.data(), message->payload.size());
        const auto sent = message->sender->Send(packet);
        if (sent != static_cast<int>(message->payload.size()))
        {
            m_fatalError = "ns-3 UDP socket did not accept the complete payload";
            return;
        }
        message->sent = true;
    }

    void Receive(std::uint16_t port, Ptr<Socket> socket)
    {
        MessageState* message = nullptr;
        for (const auto& candidate : m_messages)
        {
            if (candidate->port == port)
            {
                message = candidate.get();
                break;
            }
        }
        if (message == nullptr)
        {
            m_fatalError = "ns-3 received a packet for an unknown message";
            return;
        }
        while (socket->GetRxAvailable() > 0)
        {
            Address source;
            const auto packet = socket->RecvFrom(source);
            std::vector<std::uint8_t> bytes(packet->GetSize());
            packet->CopyData(bytes.data(), bytes.size());
            if (bytes != message->payload || message->delivered)
            {
                m_fatalError = "ns-3 delivered a duplicate or changed payload";
                return;
            }
            message->arrivalTimeNs = Simulator::Now().GetNanoSeconds();
            message->delivered = true;
        }
    }

  private:
    void RequirePrepared() const
    {
        if (!m_prepared)
        {
            throw std::runtime_error("ns-3 backend has not been prepared");
        }
    }

    void RequireRunning() const
    {
        RequirePrepared();
        if (m_stopped)
        {
            throw std::runtime_error("ns-3 backend has been stopped");
        }
        if (m_nodes.GetN() == 0)
        {
            throw std::runtime_error("ns-3 backend must be reset before this operation");
        }
    }

    void ConfigurePhy(YansWifiPhyHelper& phy,
                      Ptr<YansWifiChannel> channel,
                      const RadioProfileSpec& profile) const
    {
        phy.SetChannel(channel);
        const std::string channelSettings = "{" + std::to_string(profile.channelNumber) +
                                            ", " +
                                            std::to_string(profile.channelWidthMhz) + ", " +
                                            profile.band + ", 0}";
        phy.Set("ChannelSettings", StringValue(channelSettings));
        phy.Set("TxPowerStart", DoubleValue(profile.txPowerDbm));
        phy.Set("TxPowerEnd", DoubleValue(profile.txPowerDbm));
        phy.Set("TxPowerLevels", UintegerValue(1));
        phy.Set("RxSensitivity", DoubleValue(profile.rxSensitivityDbm));
        phy.Set("RxNoiseFigure", DoubleValue(kRxNoiseFigureDb));
    }

    Ptr<NetDevice> InstallWifiDevice(Ptr<Node> node,
                                     Ptr<YansWifiChannel> channel,
                                     const RadioProfileSpec& profile,
                                     const std::string& ssid) const
    {
        YansWifiPhyHelper phy;
        ConfigurePhy(phy, channel, profile);
        WifiHelper wifi;
        wifi.SetStandard(Ns3WifiStandard(profile.standard));
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue(profile.dataMode),
                                     "ControlMode",
                                     StringValue(profile.controlMode));
        WifiMacHelper mac;
        mac.SetType("ns3::AdhocWifiMac", "Ssid", SsidValue(Ssid(ssid)));
        const NetDeviceContainer installed = wifi.Install(phy, mac, node);
        if (installed.GetN() != 1)
        {
            throw std::runtime_error("Wi-Fi helper did not install exactly one device");
        }
        return installed.Get(0);
    }

    void BuildTopology()
    {
        m_nodes.Create(m_nodeIds.size());
        m_primaryIp.assign(m_nodeIds.size(), Ipv4Address("0.0.0.0"));

        Ptr<ListPositionAllocator> positions = CreateObject<ListPositionAllocator>();
        for (const auto& nodeId : m_nodeIds)
        {
            positions->Add(m_nodesById.at(nodeId).position);
        }
        MobilityHelper mobility;
        mobility.SetPositionAllocator(positions);
        mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        mobility.Install(m_nodes);

        InternetStackHelper internet;
        internet.Install(m_nodes);
        for (std::size_t index = 0; index < m_links.size(); ++index)
        {
            const auto& link = m_links[index];
            const auto source = m_nodeIndex.at(link.sourceToken);
            const auto destination = m_nodeIndex.at(link.destinationToken);
            const auto& sourceProfile =
                m_profiles.at(m_nodesById.at(link.sourceToken).profileToken);
            const auto& destinationProfile =
                m_profiles.at(m_nodesById.at(link.destinationToken).profileToken);

            Ptr<DeclaredLinkDelayModel> delay = CreateObject<DeclaredLinkDelayModel>();
            delay->SetDelay(NanoSeconds(link.propagationDelayNs));
            Ptr<ScenePropagationLossModel> loss =
                CreateObject<ScenePropagationLossModel>();
            loss->Configure(
                static_cast<double>(sourceProfile.frequencyMhz) * 1'000'000.0,
                kLogDistanceExponent);
            if (!m_linkLossModels.emplace(link.linkToken, loss).second)
            {
                throw std::runtime_error("Wi-Fi link propagation model repeats");
            }
            Ptr<YansWifiChannel> channel = CreateObject<YansWifiChannel>();
            channel->SetPropagationDelayModel(delay);
            channel->SetPropagationLossModel(loss);

            const std::string ssid = "aero-link-" + std::to_string(index + 1);
            NetDeviceContainer devices;
            devices.Add(InstallWifiDevice(
                m_nodes.Get(source), channel, sourceProfile, ssid));
            devices.Add(InstallWifiDevice(
                m_nodes.Get(destination), channel, destinationProfile, ssid));

            TrafficControlHelper trafficControl;
            trafficControl.SetRootQueueDisc(
                "ns3::TbfQueueDisc",
                "Burst",
                UintegerValue(kQueueBurstBytes),
                "Mtu",
                UintegerValue(0),
                "Rate",
                DataRateValue(DataRate(link.dataRateBps)),
                "PeakRate",
                DataRateValue(DataRate(0)));
            const QueueDiscContainer queueDiscs = trafficControl.Install(devices);
            if (queueDiscs.GetN() != 2)
            {
                throw std::runtime_error("Wi-Fi link shaper installation failed");
            }
            if (!m_linkQueues
                     .emplace(link.linkToken,
                              std::array<Ptr<QueueDisc>, 2>{queueDiscs.Get(0),
                                                           queueDiscs.Get(1)})
                     .second)
            {
                throw std::runtime_error("Wi-Fi link queue state repeats");
            }

            Ipv4AddressHelper addresses;
            addresses.SetBase(("10.73." + std::to_string(index + 1) + ".0").c_str(),
                              "255.255.255.0");
            const Ipv4InterfaceContainer interfaces = addresses.Assign(devices);
            if (m_primaryIp[source] == Ipv4Address("0.0.0.0"))
            {
                m_primaryIp[source] = interfaces.GetAddress(0);
            }
            if (m_primaryIp[destination] == Ipv4Address("0.0.0.0"))
            {
                m_primaryIp[destination] = interfaces.GetAddress(1);
            }
        }
        for (const auto& address : m_primaryIp)
        {
            if (address == Ipv4Address("0.0.0.0"))
            {
                throw std::runtime_error("ns-3 Wi-Fi topology contains an unconnected node");
            }
        }
        Ipv4GlobalRoutingHelper::PopulateRoutingTables();
    }

    bool AllNodesConnected() const
    {
        if (m_nodeIds.empty())
        {
            return false;
        }
        std::set<std::string> visited{m_nodeIds.front()};
        std::vector<std::string> pending{m_nodeIds.front()};
        while (!pending.empty())
        {
            const auto current = pending.back();
            pending.pop_back();
            for (const auto& link : m_links)
            {
                std::string neighbour;
                if (link.sourceToken == current)
                {
                    neighbour = link.destinationToken;
                }
                else if (link.destinationToken == current)
                {
                    neighbour = link.sourceToken;
                }
                if (!neighbour.empty() && visited.insert(neighbour).second)
                {
                    pending.push_back(neighbour);
                }
            }
        }
        return visited.size() == m_nodeIds.size();
    }

    bool Reachable(std::size_t source, std::size_t destination) const
    {
        if (source == destination)
        {
            return true;
        }
        std::vector<bool> visited(m_nodeIds.size(), false);
        std::vector<std::size_t> pending{source};
        visited[source] = true;
        while (!pending.empty())
        {
            const auto current = pending.back();
            pending.pop_back();
            for (const auto& link : m_links)
            {
                const auto linkSource = m_nodeIndex.at(link.sourceToken);
                const auto linkDestination = m_nodeIndex.at(link.destinationToken);
                std::size_t neighbour = m_nodeIds.size();
                if (linkSource == current)
                {
                    neighbour = linkDestination;
                }
                else if (linkDestination == current)
                {
                    neighbour = linkSource;
                }
                if (neighbour < m_nodeIds.size() && !visited[neighbour])
                {
                    if (neighbour == destination)
                    {
                        return true;
                    }
                    visited[neighbour] = true;
                    pending.push_back(neighbour);
                }
            }
        }
        return false;
    }

    bool m_prepared = false;
    bool m_stopped = true;
    std::map<std::string, RadioProfileSpec> m_profiles;
    std::vector<std::string> m_nodeIds;
    std::map<std::string, std::size_t> m_nodeIndex;
    std::map<std::string, NodeSpec> m_nodesById;
    std::set<std::string> m_linkIds;
    std::vector<LinkSpec> m_links;
    std::map<std::string, Ptr<ScenePropagationLossModel>> m_linkLossModels;
    std::map<std::string, std::array<Ptr<QueueDisc>, 2>> m_linkQueues;
    NodeContainer m_nodes;
    std::vector<Ipv4Address> m_primaryIp;
    std::vector<std::unique_ptr<MessageState>> m_messages;
    std::map<std::string, MessageState*> m_messagesById;
    std::uint16_t m_nextPort = kFirstMessagePort;
    std::uint64_t m_currentTimeNs = 0;
    bool m_mobilityStaged = false;
    std::string m_sceneStateDigest;
    std::string m_fatalError;
};

void
SendPacket(ProviderBackend* backend, MessageState* message)
{
    backend->Send(message);
}

void
ReceivePacket(ProviderBackend* backend, std::uint16_t port, Ptr<Socket> socket)
{
    backend->Receive(port, socket);
}

std::vector<std::string>
CommandFields(const std::string& line)
{
    std::istringstream input(line);
    std::vector<std::string> fields;
    std::string field;
    while (input >> field)
    {
        fields.push_back(std::move(field));
    }
    return fields;
}
} // namespace

int
main()
{
    ProviderBackend backend;
    std::string line;
    while (std::getline(std::cin, line))
    {
        if (line.empty())
        {
            std::cout << "ERR empty command\n" << std::flush;
            continue;
        }
        try
        {
            const auto fields = CommandFields(line);
            if (fields.empty())
            {
                throw std::runtime_error("empty command");
            }
            if (fields[0] == "PREPARE")
            {
                backend.Prepare(fields);
            }
            else if (fields[0] == "RESET")
            {
                backend.Reset(fields);
            }
            else if (fields[0] == "MOBILITY")
            {
                backend.ApplyMobility(fields);
            }
            else if (fields[0] == "MESSAGE")
            {
                backend.SubmitMessage(fields);
            }
            else if (fields[0] == "STEP")
            {
                if (fields.size() != 2)
                {
                    throw std::runtime_error("STEP requires target time");
                }
                backend.Step(ParseUnsigned(fields[1], "target_time_ns"));
            }
            else if (fields[0] == "SNAPSHOT")
            {
                if (fields.size() != 1)
                {
                    throw std::runtime_error("SNAPSHOT takes no arguments");
                }
                backend.Snapshot();
            }
            else if (fields[0] == "SHUTDOWN")
            {
                if (fields.size() != 1)
                {
                    throw std::runtime_error("SHUTDOWN takes no arguments");
                }
                backend.Shutdown();
                return 0;
            }
            else
            {
                throw std::runtime_error("unknown ns-3 backend command");
            }
        }
        catch (const std::exception& error)
        {
            std::cout << "ERR " << error.what() << '\n' << std::flush;
        }
    }
    return 0;
}
