#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/wifi-module.h"
#include "ns3/propagation-module.h"
#include <sys/resource.h>
#include <iostream>
#include <iomanip>
#include <vector>
#include <map>
#include <string>
#include <cstdint>
#include <chrono>
#include <tuple>
#include <sstream>
#include <algorithm>
#include <cmath>
#include <memory>
#include <set>
#include <limits>
#include <numeric>
using namespace ns3;

struct Life { std::string id; int64_t birth,death; Ptr<WaypointMobilityModel> mobility; Ptr<WifiPhy> phy; std::vector<Ptr<WifiMacQueue>> queues; Ptr<WifiMac> mac; };
struct Flow { std::string id,link; uint32_t src,dst,bytes; int64_t period; std::vector<std::pair<int64_t,int64_t>> windows; Ptr<Socket> tx,rx; uint64_t sequence=0; };
struct Stamp { uint32_t flow; int64_t tx; bool accepted; bool delivered=false; };
std::vector<Life> lives;
std::vector<Flow> flows;
std::map<uint64_t,Stamp> packets;
int64_t horizon;
uint64_t packetSequence=0,eventSequence=0;
struct DiagnosticCount { uint64_t count=0; int64_t first=0,last=0; };
using DiagnosticKey=std::tuple<uint32_t,int64_t,std::string,std::string,std::string>;
std::map<DiagnosticKey,DiagnosticCount> diagnostics;
struct RadioConfig {
 uint32_t channel,width,maxPackets;
 std::string standard,dataMode,controlMode;
 double txPower,txGain,rxGain,sensitivity,noise,exponent,referenceDistance,referenceLoss;
 int64_t maxDelayNs;
};
struct GeoBox {double minX,minY,maxX,maxY;};
struct XY { long double x,y; };
struct Solid { uint32_t feature;double base,roof;std::vector<std::vector<XY>> rings; };
std::vector<Solid> solids;
class GeometryIndex {
 struct Node {GeoBox box;uint32_t left,right,solid;};
 static constexpr uint32_t absent=std::numeric_limits<uint32_t>::max();
 std::vector<Node> nodes;std::vector<GeoBox> boxes;
 uint32_t Build(std::vector<uint32_t>& order,size_t begin,size_t end){
  GeoBox bounds=boxes[order[begin]];
  for(size_t i=begin+1;i<end;++i){const auto& box=boxes[order[i]];bounds.minX=std::min(bounds.minX,box.minX);bounds.minY=std::min(bounds.minY,box.minY);bounds.maxX=std::max(bounds.maxX,box.maxX);bounds.maxY=std::max(bounds.maxY,box.maxY);}
  const uint32_t node=nodes.size();nodes.push_back({bounds,absent,absent,absent});
  if(end-begin==1){nodes[node].solid=order[begin];return node;}
  const size_t middle=begin+(end-begin)/2;const bool xAxis=bounds.maxX-bounds.minX>=bounds.maxY-bounds.minY;
  std::nth_element(order.begin()+begin,order.begin()+middle,order.begin()+end,[&](uint32_t a,uint32_t b){const auto& x=boxes[a];const auto& y=boxes[b];const double ca=xAxis?x.minX+x.maxX:x.minY+x.maxY,cb=xAxis?y.minX+y.maxX:y.minY+y.maxY;return ca==cb?a<b:ca<cb;});
  const uint32_t left=Build(order,begin,middle),right=Build(order,middle,end);nodes[node].left=left;nodes[node].right=right;return node;
 }
 void QueryNode(uint32_t index,const GeoBox& query,std::vector<uint32_t>& results) const {
  const auto& node=nodes[index];const auto& box=node.box;
  if(box.maxX<query.minX||box.minX>query.maxX||box.maxY<query.minY||box.minY>query.maxY)return;
  if(node.solid!=absent)results.push_back(node.solid);else{QueryNode(node.left,query,results);QueryNode(node.right,query,results);}
 }
public:
 explicit GeometryIndex(std::vector<GeoBox> input):boxes(std::move(input)){
  if(boxes.empty())throw std::runtime_error("active geometry requires authored polygon components");
  nodes.reserve(2*boxes.size()-1);std::vector<uint32_t> order(boxes.size());std::iota(order.begin(),order.end(),0);Build(order,0,order.size());
 }
 std::vector<uint32_t> Query(const GeoBox& box)const{std::vector<uint32_t> results;QueryNode(0,box,results);return results;}
};
std::unique_ptr<GeometryIndex> geometryIndex;
bool geometryEnabled=false;
double losExponent=2.2,nlosExponent=3,geometryReferenceDistance=1,geometryReferenceLoss=0;
uint64_t losEvaluations=0,nlosEvaluations=0;
long double Cross(XY a,XY b){return a.x*b.y-a.y*b.x;}
XY Subtract(XY a,XY b){return {a.x-b.x,a.y-b.y};}
int RingLocation(XY p,const std::vector<XY>& ring){
 bool inside=false;
 for(size_t i=1;i<ring.size();++i){const auto a=ring[i-1],b=ring[i];
  if(Cross(Subtract(p,a),Subtract(b,a))==0&&p.x>=std::min(a.x,b.x)&&p.x<=std::max(a.x,b.x)&&p.y>=std::min(a.y,b.y)&&p.y<=std::max(a.y,b.y))return 2;
  if((a.y>p.y)!=(b.y>p.y)&&p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x)inside=!inside;
 }
 return inside?1:0;
}
bool ClosedFootprint(XY p,const Solid& solid){
 const int outer=RingLocation(p,solid.rings[0]);if(outer==0)return false;if(outer==2)return true;
 for(size_t r=1;r<solid.rings.size();++r){const int hole=RingLocation(p,solid.rings[r]);if(hole==2)return true;if(hole==1)return false;}
 return true;
}
bool SegmentSolid(const Vector& a,const Vector& b,const Solid& solid){
 long double low=0,high=1;const long double dz=static_cast<long double>(b.z)-a.z;
 if(dz==0){if(a.z<solid.base||a.z>solid.roof)return false;}
 else{const auto u=(solid.base-a.z)/dz,v=(solid.roof-a.z)/dz;low=std::max(low,std::min(u,v));high=std::min(high,std::max(u,v));if(low>high)return false;}
 const XY origin{a.x,a.y},direction{static_cast<long double>(b.x)-a.x,static_cast<long double>(b.y)-a.y};
 auto at=[&](long double t){return XY{origin.x+t*direction.x,origin.y+t*direction.y};};
 if(ClosedFootprint(at(low),solid)||ClosedFootprint(at(high),solid))return true;
 const auto norm=direction.x*direction.x+direction.y*direction.y;if(norm==0)return false;
 // Clip against Z first. Any ring-edge contact within that exact parameter
 // interval touches the closed solid, including hole boundaries and tangency.
 for(const auto& ring:solid.rings)for(size_t i=1;i<ring.size();++i){
  const XY edge=Subtract(ring[i],ring[i-1]),offset=Subtract(ring[i-1],origin);
  const auto denominator=Cross(direction,edge);
  if(denominator!=0){const auto t=Cross(offset,edge)/denominator,u=Cross(offset,direction)/denominator;
   if(t>=low&&t<=high&&u>=0&&u<=1)return true;
  }else if(Cross(offset,direction)==0){
   const auto t0=(offset.x*direction.x+offset.y*direction.y)/norm;
   const auto last=Subtract(ring[i],origin);const auto t1=(last.x*direction.x+last.y*direction.y)/norm;
   if(std::max(low,std::min(t0,t1))<=std::min(high,std::max(t0,t1)))return true;
  }
 }
 return false;
}
std::vector<uint32_t> GeometryHits(const Vector& a,const Vector& b,bool firstOnly=false){
 const auto candidates=geometryIndex->Query({std::min(a.x,b.x),std::min(a.y,b.y),std::max(a.x,b.x),std::max(a.y,b.y)});
 std::set<uint32_t> hits;for(auto candidate:candidates){const auto& solid=solids[candidate];
  if(SegmentSolid(a,b,solid)){hits.insert(solid.feature);if(firstOnly)break;}}
 return {hits.begin(),hits.end()};
}
class P01GeometryLossModel : public PropagationLossModel {
public:
 static TypeId GetTypeId(){static TypeId type=TypeId("ns3::P01GeometryLossModel").SetParent<PropagationLossModel>().AddConstructor<P01GeometryLossModel>()
  .AddAttribute("ReferenceDistance","Close-in reference distance",DoubleValue(1),MakeDoubleAccessor(&P01GeometryLossModel::referenceDistance),MakeDoubleChecker<double>(0))
  .AddAttribute("ReferenceLoss","Configured R1 reference loss",DoubleValue(0),MakeDoubleAccessor(&P01GeometryLossModel::referenceLoss),MakeDoubleChecker<double>())
  .AddAttribute("LosExponent","Geometric LOS exponent",DoubleValue(2.2),MakeDoubleAccessor(&P01GeometryLossModel::los),MakeDoubleChecker<double>(0))
  .AddAttribute("NlosExponent","Geometric NLOS exponent",DoubleValue(3),MakeDoubleAccessor(&P01GeometryLossModel::nlos),MakeDoubleChecker<double>(0));return type;}
private:
 double referenceDistance=1,referenceLoss=0,los=2.2,nlos=3;
 double DoCalcRxPower(double txPower,Ptr<MobilityModel> a,Ptr<MobilityModel> b) const override {
  const auto tx=a->GetPosition(),rx=b->GetPosition();const bool blocked=!GeometryHits(tx,rx,true).empty();
  if(blocked)++nlosEvaluations;else ++losEvaluations;
  const double distance=CalculateDistance(tx,rx);
  if(distance<=referenceDistance)return txPower;
  return txPower-referenceLoss-10*(blocked?nlos:los)*std::log10(distance/referenceDistance);
 }
 int64_t DoAssignStreams(int64_t) override{return 0;}
};
NS_OBJECT_ENSURE_REGISTERED(P01GeometryLossModel);
void SnapshotGeometry(int64_t observationTime){
 if(!geometryEnabled)return;
 const int64_t observedUntil=observationTime==0?-1:Simulator::Now().GetNanoSeconds();
 for(const auto& flow:flows){
  const auto tx=lives[flow.src].mobility->GetPosition(),rx=lives[flow.dst].mobility->GetPosition();const auto hits=GeometryHits(tx,rx);
  const double exponent=hits.empty()?losExponent:nlosExponent;
  std::cout<<"{\"record_type\":\"geometry_link\",\"time_ns\":"<<observationTime<<",\"observed_until_ns\":"<<observedUntil
   <<",\"flow_id\":\""<<flow.id<<"\",\"link_id\":\""<<flow.link<<"\",\"src_node_id\":\""<<lives[flow.src].id<<"\",\"dst_node_id\":\""<<lives[flow.dst].id
   <<"\",\"tx_enu_m\":["<<tx.x<<","<<tx.y<<","<<tx.z<<"],\"rx_enu_m\":["<<rx.x<<","<<rx.y<<","<<rx.z
   <<"],\"geometric_state\":\""<<(hits.empty()?"LOS":"NLOS")<<"\",\"building_feature_indices\":[";
  bool first=true;for(auto feature:hits){if(!first)std::cout<<",";first=false;std::cout<<feature;}
  std::cout<<"],\"distance_m\":"<<CalculateDistance(tx,rx)<<",\"path_loss_exponent\":"<<exponent
   <<",\"reference_distance_m\":"<<geometryReferenceDistance<<",\"reference_loss_db\":"<<geometryReferenceLoss<<"}\n";
 }
}
WifiStandard ResolveStandard(const std::string& name){
 if(name=="802.11b")return WIFI_STANDARD_80211b;
 if(name=="802.11g")return WIFI_STANDARD_80211g;
 if(name=="802.11n")return WIFI_STANDARD_80211n;
 if(name=="802.11ax")return WIFI_STANDARD_80211ax;
 throw std::runtime_error("undeclared radio standard for retained 2.4GHz band");
}
std::string StandardName(WifiStandard standard){
 switch(standard){
 case WIFI_STANDARD_80211b:return "802.11b";
 case WIFI_STANDARD_80211g:return "802.11g";
 case WIFI_STANDARD_80211n:return "802.11n";
 case WIFI_STANDARD_80211ax:return "802.11ax";
 default:throw std::runtime_error("loaded PHY has undeclared radio standard");}
}
void EmitLoadedRadio(uint32_t node,Ptr<WifiNetDevice> device,const RadioConfig& radio){
 auto phy=device->GetPhy();DoubleValue txPower,txGain,rxGain,sensitivity;
 phy->GetAttribute("TxPowerStart",txPower);phy->GetAttribute("TxGain",txGain);
 phy->GetAttribute("RxGain",rxGain);phy->GetAttribute("RxSensitivity",sensitivity);
 WifiModeValue data,control;auto manager=device->GetRemoteStationManager();
 manager->GetAttribute("DataMode",data);manager->GetAttribute("ControlMode",control);
 std::cout<<"{\"record_type\":\"radio\",\"node_id\":\""<<lives[node].id
  <<"\",\"standard\":\""<<StandardName(phy->GetStandard())<<"\",\"channel_number\":"<<unsigned(phy->GetChannelNumber())
  <<",\"channel_width_mhz\":"<<phy->GetChannelWidth()<<",\"frequency_mhz\":"<<phy->GetFrequency()
  <<",\"data_mode\":\""<<data.Get().GetUniqueName()<<"\",\"control_mode\":\""<<control.Get().GetUniqueName()
  <<"\",\"tx_power_dbm\":"<<txPower.Get()<<",\"tx_gain_db\":"<<txGain.Get()<<",\"rx_gain_db\":"<<rxGain.Get()
  <<",\"rx_sensitivity_dbm\":"<<sensitivity.Get()<<",\"configured_rx_noise_figure_db\":"<<radio.noise
  <<",\"rx_noise_figure_observation\":\"applied via WifiPhy::SetRxNoiseFigure; no public getter\",\"mac_queues\":[";
 bool first=true;for(const auto& queue:lives[node].queues){
  if(!first)std::cout<<",";first=false;
  std::cout<<"{\"max_packets\":"<<queue->GetMaxSize().GetValue()<<",\"max_delay_ns\":"<<queue->GetMaxDelay().GetNanoSeconds()<<"}";
 }
 std::cout<<"]}\n";
}


class PacketIdentity : public Tag {
public:
 uint64_t id=0;
 static TypeId GetTypeId(){ static TypeId t=TypeId("P01PacketIdentity").SetParent<Tag>().AddConstructor<PacketIdentity>(); return t; }
 TypeId GetInstanceTypeId() const override{return GetTypeId();}
 uint32_t GetSerializedSize() const override{return 8;}
 void Serialize(TagBuffer b)const override{b.WriteU64(id);}
 void Deserialize(TagBuffer b)override{id=b.ReadU64();}
 void Print(std::ostream& s)const override{s<<id;}
};
void EmitNetworkEvent(const char* type,uint64_t packet,uint32_t f,int64_t time){
 auto& flow=flows[f];
 std::cout<<"{\"record_type\":\"event\",\"event_id\":\"event-"<<eventSequence++<<"\",\"event_type\":\""<<type<<"\",\"time_ns\":"<<time
 <<",\"packet_id\":\"packet-"<<packet<<"\",\"link_id\":\""<<flow.link<<"\",\"flow_id\":\""<<flow.id
 <<"\",\"src_node_id\":\""<<lives[flow.src].id<<"\",\"dst_node_id\":\""<<lives[flow.dst].id<<"\",\"payload_bytes\":"<<flow.bytes<<"}\n";
}
void Receive(uint32_t f,Ptr<Socket> socket){
 Ptr<Packet> p;
 while((p=socket->Recv())){
  PacketIdentity tag;if(!p->PeekPacketTag(tag))throw std::runtime_error("RX packet has no producer identity");
  auto it=packets.find(tag.id);if(it==packets.end())throw std::runtime_error("RX without socket TX");
  auto& stamp=it->second;if(!stamp.accepted)throw std::runtime_error("RX without accepted TX");
  if(stamp.delivered)continue;
  stamp.delivered=true;EmitNetworkEvent("rx",tag.id,f,Simulator::Now().GetNanoSeconds());
 }
}
void Send(uint32_t f,uint32_t window){
 auto& flow=flows[f];auto& life=lives[flow.src];int64_t now=Simulator::Now().GetNanoSeconds();
 const auto [start,end]=flow.windows[window];
 if(now<start||now>=end||now<life.birth||now>=life.death||now>=horizon)return;
 uint64_t id=packetSequence++;auto p=Create<Packet>(flow.bytes);PacketIdentity tag;tag.id=id;p->AddPacketTag(tag);
 packets.emplace(id,Stamp{f,now,false});
 int result=flow.tx->Send(p);bool accepted=result==static_cast<int>(flow.bytes);
 packets.at(id).accepted=accepted;EmitNetworkEvent(accepted?"tx_accepted":"tx_rejected",id,f,now);
 std::cout<<"{\"record_type\":\"packet\",\"packet_id\":\"packet-"<<id<<"\",\"link_id\":\""<<flow.link<<"\",\"flow_id\":\""<<flow.id
 <<"\",\"src_node_id\":\""<<life.id<<"\",\"dst_node_id\":\""<<lives[flow.dst].id<<"\",\"first_tx_ns\":"<<now
 <<",\"accepted\":"<<(accepted?"true":"false")<<",\"payload_bytes\":"<<flow.bytes<<"}\n";
 if(now+flow.period<end&&now+flow.period<life.death&&now+flow.period<horizon)Simulator::Schedule(NanoSeconds(flow.period),&Send,f,window);
}
void CountDiagnostic(uint32_t node,const std::string& kind,const std::string& reason,Ptr<const Packet> packet){
 PacketIdentity tag;int64_t identity=-1;std::string scope="untagged_control_or_unidentified";
 if(packet->PeekPacketTag(tag)){
  auto found=packets.find(tag.id);
  if(found==packets.end())throw std::runtime_error("diagnostic tag has no socket TX identity");
  const auto& flow=flows[found->second.flow];
  if(node==flow.src||node==flow.dst){identity=static_cast<int64_t>(tag.id);scope="tagged_endpoint";}
  else scope="tagged_overheard";
 }
 auto& value=diagnostics[DiagnosticKey{node,identity,kind,reason,scope}];
 int64_t now=Simulator::Now().GetNanoSeconds();if(value.count==0)value.first=now;value.last=now;++value.count;
}
void PhyTxBegin(uint32_t node,Ptr<const Packet> packet,double power){
 CountDiagnostic(node,"phy_tx_begin","ACTUAL_MPDU_TRANSMISSION",packet);
 WifiMacHeader header;packet->PeekHeader(header);
 if(header.IsRetry())CountDiagnostic(node,"phy_tx_retry","MAC_HEADER_RETRY_BIT",packet);
}
void PhyTxDrop(uint32_t node,Ptr<const Packet> packet){CountDiagnostic(node,"phy_tx_drop","PHY_TX_DROP",packet);}
void PhyRxDrop(uint32_t node,Ptr<const Packet> packet,WifiPhyRxfailureReason reason){
 std::ostringstream text;text<<reason;CountDiagnostic(node,"phy_rx_drop",text.str(),packet);
}
void MacDrop(uint32_t node,WifiMacDropReason reason,Ptr<const WifiMpdu> mpdu){
 std::string text;
 switch(reason){
 case WIFI_MAC_DROP_FAILED_ENQUEUE:text="WIFI_MAC_DROP_FAILED_ENQUEUE";break;
 case WIFI_MAC_DROP_EXPIRED_LIFETIME:text="WIFI_MAC_DROP_EXPIRED_LIFETIME";break;
 case WIFI_MAC_DROP_REACHED_RETRY_LIMIT:text="WIFI_MAC_DROP_REACHED_RETRY_LIMIT";break;
 case WIFI_MAC_DROP_QOS_OLD_PACKET:text="WIFI_MAC_DROP_QOS_OLD_PACKET";break;
 default:throw std::runtime_error("undeclared MAC drop reason");}
 CountDiagnostic(node,"mac_drop",text,mpdu->GetPacket());
}
void MacResponseTimeout(uint32_t node,uint8_t reason,Ptr<const WifiMpdu> mpdu,const WifiTxVector& tx){
 CountDiagnostic(node,"mac_response_timeout","WIFI_TX_TIMER_REASON_"+std::to_string(reason),mpdu->GetPacket());
}
void ArpProtocolDrop(uint32_t node,Ptr<const Packet> packet){
 CountDiagnostic(node,"arp_protocol_drop","ARP_L3_DROP_CALLBACK_REASON_NOT_EXPOSED",packet);
}
void ArpCacheDrop(uint32_t node,Ptr<const Packet> packet){
 CountDiagnostic(node,"arp_cache_drop","ARP_CACHE_DROP_CALLBACK_REASON_NOT_EXPOSED",packet);
}
void EmitDiagnostics(){
 for(const auto& [key,value]:diagnostics){
  const auto& [node,packet,kind,reason,scope]=key;
  std::cout<<"{\"record_type\":\"diagnostic\",\"node_id\":\""<<lives[node].id<<"\",\"packet_id\":";
  if(packet<0)std::cout<<"null";else std::cout<<"\"packet-"<<packet<<"\"";
  std::cout<<",\"kind\":\""<<kind<<"\",\"reason\":\""<<reason<<"\",\"scope\":\""<<scope
    <<"\",\"count\":"<<value.count<<",\"first_time_ns\":"<<value.first<<",\"last_time_ns\":"<<value.last<<"}\n";
 }
}
void EmitRadioAction(uint32_t node,int64_t time,uint32_t opcode,uint32_t requestedChannel){
 std::cout<<"{\"record_type\":\"radio_action\",\"node_id\":\""<<lives[node].id
  <<"\",\"time_ns\":"<<time<<",\"opcode\":"<<opcode<<",\"requested_channel\":"<<requestedChannel
  <<",\"observed_channel\":"<<unsigned(lives[node].phy->GetChannelNumber())<<"}\n";
}
void ApplyRadioControl(uint32_t node,uint32_t opcode,uint32_t channel,uint32_t width,int64_t time){
 auto phy=lives[node].phy;
 switch(opcode){
 case 0:phy->SetOffMode();break;
 case 1:phy->ResumeFromOff();break;
 case 2:
  if(!phy->SetAttributeFailSafe("ChannelSettings",StringValue("{"+std::to_string(channel)+","+std::to_string(width)+",BAND_2_4GHZ,0}")))
   throw std::runtime_error("WifiPhy rejected the authored channel switch");
  break;
 default:throw std::runtime_error("undeclared radio control opcode");
 }
 EmitRadioAction(node,time,opcode,channel);
}
// ns-3 AdhocWifiMac normally initializes peer capabilities on Enqueue.
// A live channel change resets the station manager while old MPDUs can remain
// queued. Restore the SAME declared ad-hoc capabilities after that reset,
// without changing aggregation, queues, PHY rates or RF parameters.
void RestoreAdhocPeers(uint32_t node){
 auto mac=lives[node].mac;auto manager=mac->GetWifiRemoteStationManager();
 for(uint32_t peer=0;peer<lives.size();++peer){
  if(peer==node)continue;
  auto address=lives[peer].mac->GetAddress();
  if(mac->GetHtSupported(0)){
   manager->AddAllSupportedMcs(address);
   manager->AddStationHtCapabilities(address,lives[peer].mac->GetHtCapabilities(0));
  }
  manager->AddAllSupportedModes(address);manager->RecordDisassociated(address);
 }
 std::cout<<"{\"record_type\":\"radio_action\",\"node_id\":\""<<lives[node].id
  <<"\",\"time_ns\":"<<Simulator::Now().GetNanoSeconds()
  <<",\"opcode\":2,\"requested_channel\":"<<unsigned(lives[node].phy->GetChannelNumber())
  <<",\"observed_channel\":"<<unsigned(lives[node].phy->GetChannelNumber())
  <<",\"phase\":\"native_switch_completed\",\"adhoc_peer_capabilities_restored\":true}\n";
}
class RadioSwitchListener:public WifiPhyListener{
 uint32_t node;
public:
 explicit RadioSwitchListener(uint32_t value):node(value){}
 void NotifyRxStart(Time)override{}
 void NotifyRxEndOk()override{}
 void NotifyRxEndError(const WifiTxVector&)override{}
 void NotifyTxStart(Time,dBm_u)override{}
 void NotifyCcaBusyStart(Time,WifiChannelListType,const std::vector<Time>&)override{}
 void NotifySwitchingStart(Time duration)override{
  // FrameExchangeManager schedules the MAC/station-manager reset at the
  // END of this duration. Restore after that reset, before a later TX slot.
  Simulator::Schedule(duration+NanoSeconds(1),&RestoreAdhocPeers,node);
 }
 void NotifySleep()override{}
 void NotifyOff()override{}
 void NotifyWakeup()override{}
 void NotifyOn()override{}
};
std::vector<std::shared_ptr<RadioSwitchListener>> switchListeners;
void SnapshotQueues(int64_t observationTime){
 const int64_t observedUntil = observationTime == 0 ? -1 : Simulator::Now().GetNanoSeconds();
 for(const auto& life:lives){
  uint64_t count=0,bytes=0;
  for(const auto& queue:life.queues){count+=queue->GetNPackets();bytes+=queue->GetNBytes();}
  std::cout<<"{\"record_type\":\"queue_sample\",\"time_ns\":"<<observationTime
   <<",\"node_id\":\""<<life.id<<"\",\"queue_packets\":"<<count<<",\"queue_bytes\":"<<bytes
   <<",\"observed_until_ns\":"<<observedUntil<<"}\n";
 }
 SnapshotGeometry(observationTime);
}
int main(){try{
 auto started=std::chrono::steady_clock::now();std::cout<<std::setprecision(17);
 uint32_t n,m,seed,run;std::cin>>horizon>>n>>m>>seed>>run;
 RadioConfig radio;std::cin>>radio.channel>>radio.width>>radio.standard>>radio.dataMode>>radio.controlMode
  >>radio.txPower>>radio.txGain>>radio.rxGain>>radio.sensitivity>>radio.noise
  >>radio.exponent>>radio.referenceDistance>>radio.referenceLoss>>radio.maxPackets>>radio.maxDelayNs;
 if(!std::cin)throw std::runtime_error("provider radio input protocol malformed");
 std::cin>>geometryEnabled;
 if(geometryEnabled){uint32_t count;std::cin>>losExponent>>nlosExponent>>count;std::vector<GeoBox> boxes;
  for(uint32_t i=0;i<count;++i){Solid solid;uint32_t ringCount;std::cin>>solid.feature>>solid.base>>solid.roof>>ringCount;
   if(ringCount==0||solid.roof<solid.base)throw std::runtime_error("invalid building extrusion input");
   for(uint32_t r=0;r<ringCount;++r){uint32_t points;std::cin>>points;if(points<4)throw std::runtime_error("building ring must be closed with at least four points");std::vector<XY> ring(points);for(auto& point:ring)std::cin>>point.x>>point.y;solid.rings.push_back(std::move(ring));}
   double minX=solid.rings[0][0].x,maxX=minX,minY=solid.rings[0][0].y,maxY=minY;
   for(const auto& point:solid.rings[0]){minX=std::min(minX,static_cast<double>(point.x));maxX=std::max(maxX,static_cast<double>(point.x));minY=std::min(minY,static_cast<double>(point.y));maxY=std::max(maxY,static_cast<double>(point.y));}
   boxes.push_back({minX,minY,maxX,maxY});solids.push_back(std::move(solid));
  }
  geometryIndex=std::make_unique<GeometryIndex>(std::move(boxes));
 }
 if(!std::cin)throw std::runtime_error("provider geometry input protocol malformed");
 auto standard=ResolveStandard(radio.standard);
 Config::SetDefault("ns3::WifiMacQueue::MaxSize",QueueSizeValue(QueueSize(std::to_string(radio.maxPackets)+"p")));
 Config::SetDefault("ns3::WifiMacQueue::MaxDelay",TimeValue(NanoSeconds(radio.maxDelayNs)));
 RngSeedManager::SetSeed(seed);RngSeedManager::SetRun(run);
 NodeContainer nodes;nodes.Create(n);lives.resize(n);flows.resize(m);
 for(uint32_t i=0;i<n;++i){uint32_t count;std::cin>>lives[i].id>>lives[i].birth>>lives[i].death>>count;
  auto mobility=CreateObject<WaypointMobilityModel>();nodes.Get(i)->AggregateObject(mobility);lives[i].mobility=mobility;
  for(uint32_t j=0;j<count;++j){int64_t time;double x,y,z;std::cin>>time>>x>>y>>z;mobility->AddWaypoint(Waypoint(NanoSeconds(time),Vector(x,y,z)));}
 }
 YansWifiChannelHelper channel;channel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
 if(geometryEnabled)channel.AddPropagationLoss("ns3::P01GeometryLossModel","LosExponent",DoubleValue(losExponent),"NlosExponent",DoubleValue(nlosExponent),"ReferenceDistance",DoubleValue(radio.referenceDistance),"ReferenceLoss",DoubleValue(radio.referenceLoss));
 else channel.AddPropagationLoss("ns3::LogDistancePropagationLossModel","Exponent",DoubleValue(radio.exponent),"ReferenceDistance",DoubleValue(radio.referenceDistance),"ReferenceLoss",DoubleValue(radio.referenceLoss));
 auto shared=channel.Create();
 PointerValue lossPointer;shared->GetAttribute("PropagationLossModel",lossPointer);
 auto loss=lossPointer.Get<PropagationLossModel>();
 DoubleValue exponent,referenceDistance,referenceLoss;loss->GetAttribute("ReferenceDistance",referenceDistance);loss->GetAttribute("ReferenceLoss",referenceLoss);
 geometryReferenceDistance=referenceDistance.Get();geometryReferenceLoss=referenceLoss.Get();
 if(geometryEnabled){DoubleValue los,nlos;loss->GetAttribute("LosExponent",los);loss->GetAttribute("NlosExponent",nlos);
  std::cout<<"{\"record_type\":\"propagation\",\"model\":\""<<loss->GetInstanceTypeId().GetName()<<"\",\"los_exponent\":"<<los.Get()<<",\"nlos_exponent\":"<<nlos.Get()<<",\"polygon_part_count\":"<<solids.size()<<",\"reference_distance_m\":"<<referenceDistance.Get()<<",\"reference_loss_db\":"<<referenceLoss.Get()<<",\"applied_shadowing\":false,\"extra_fast_fading\":false}\n";
 }else{loss->GetAttribute("Exponent",exponent);
 std::cout<<"{\"record_type\":\"propagation\",\"model\":\""<<loss->GetInstanceTypeId().GetName()
  <<"\",\"exponent\":"<<exponent.Get()<<",\"reference_distance_m\":"<<referenceDistance.Get()
  <<",\"reference_loss_db\":"<<referenceLoss.Get()<<"}\n";
 }
 YansWifiPhyHelper phy;phy.SetChannel(shared);
 phy.Set("ChannelSettings",StringValue("{"+std::to_string(radio.channel)+","+std::to_string(radio.width)+",BAND_2_4GHZ,0}"));
 phy.Set("TxPowerStart",DoubleValue(radio.txPower));phy.Set("TxPowerEnd",DoubleValue(radio.txPower));phy.Set("TxPowerLevels",UintegerValue(1));
 phy.Set("TxGain",DoubleValue(radio.txGain));phy.Set("RxGain",DoubleValue(radio.rxGain));
 phy.Set("RxSensitivity",DoubleValue(radio.sensitivity));phy.Set("RxNoiseFigure",DoubleValue(radio.noise));
 WifiHelper wifi;wifi.SetStandard(standard);wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager","DataMode",StringValue(radio.dataMode),"ControlMode",StringValue(radio.controlMode));WifiMacHelper mac;mac.SetType("ns3::AdhocWifiMac");auto devices=wifi.Install(phy,mac,nodes);
 InternetStackHelper internet;internet.Install(nodes);Ipv4AddressHelper addresses;addresses.SetBase("10.1.0.0","255.255.0.0");auto ips=addresses.Assign(devices);
 for(uint32_t i=0;i<n;++i){
  auto arpProtocol=nodes.Get(i)->GetObject<ArpL3Protocol>();
  auto ipv4=nodes.Get(i)->GetObject<Ipv4L3Protocol>();
  const int32_t interface=ipv4->GetInterfaceForDevice(devices.Get(i));
  if(interface<0)throw std::runtime_error("radio device has no IPv4 interface");
  auto arpCache=ipv4->GetInterface(interface)->GetArpCache();
  if(!arpProtocol||!arpCache
     || !arpProtocol->TraceConnectWithoutContext("Drop",MakeBoundCallback(&ArpProtocolDrop,i))
     || !arpCache->TraceConnectWithoutContext("Drop",MakeBoundCallback(&ArpCacheDrop,i)))
   throw std::runtime_error("actual ARP drop trace connection failed");
  auto device=DynamicCast<WifiNetDevice>(devices.Get(i));lives[i].phy=device->GetPhy();auto deviceMac=device->GetMac();lives[i].mac=deviceMac;
  auto switchListener=std::make_shared<RadioSwitchListener>(i);switchListeners.push_back(switchListener);lives[i].phy->RegisterListener(switchListener);
  if(auto queue=deviceMac->GetTxopQueue(AC_BE_NQOS))lives[i].queues.push_back(queue);
  if(deviceMac->GetQosSupported()){for(AcIndex ac:edcaAcIndices){auto queue=deviceMac->GetTxopQueue(ac);if(!queue)throw std::runtime_error("QoS MAC has no declared AC queue");lives[i].queues.push_back(queue);}}
  if(lives[i].queues.empty())throw std::runtime_error("Wi-Fi device has no observable MAC queue");
  EmitLoadedRadio(i,device,radio);
  if(!lives[i].phy->TraceConnectWithoutContext("PhyTxBegin",MakeBoundCallback(&PhyTxBegin,i))
     || !lives[i].phy->TraceConnectWithoutContext("PhyTxDrop",MakeBoundCallback(&PhyTxDrop,i))
     || !lives[i].phy->TraceConnectWithoutContext("PhyRxDrop",MakeBoundCallback(&PhyRxDrop,i))
     || !deviceMac->TraceConnectWithoutContext("DroppedMpdu",MakeBoundCallback(&MacDrop,i))
     || !deviceMac->TraceConnectWithoutContext("MpduResponseTimeout",MakeBoundCallback(&MacResponseTimeout,i)))
   throw std::runtime_error("actual ns3 diagnostic trace connection failed");if(lives[i].birth>0){lives[i].phy->SetOffMode();Simulator::Schedule(NanoSeconds(lives[i].birth),&WifiPhy::ResumeFromOff,lives[i].phy);}if(lives[i].death<horizon)Simulator::Schedule(NanoSeconds(lives[i].death),&WifiPhy::SetOffMode,lives[i].phy);}
 // Initial snapshot is synchronous, before any t=0 application callbacks.
 for(int64_t tick=500000000;tick<=horizon;tick+=500000000)Simulator::Schedule(NanoSeconds(tick-1),&SnapshotQueues,tick);
 for(uint32_t f=0;f<m;++f){auto& flow=flows[f];uint32_t windowCount;std::cin>>flow.id>>flow.link>>flow.src>>flow.dst>>flow.bytes>>flow.period>>windowCount;
  for(uint32_t w=0;w<windowCount;++w){int64_t start,end;std::cin>>start>>end;if(start<lives[flow.src].birth||end>lives[flow.src].death||end>horizon||start>=end||flow.period<=0)throw std::runtime_error("invalid exact application window");flow.windows.emplace_back(start,end);}
  uint16_t port=20000+f;flow.rx=Socket::CreateSocket(nodes.Get(flow.dst),UdpSocketFactory::GetTypeId());if(flow.rx->Bind(InetSocketAddress(Ipv4Address::GetAny(),port))!=0)throw std::runtime_error("RX bind failed");flow.rx->SetRecvCallback(MakeBoundCallback(&Receive,f));
  flow.tx=Socket::CreateSocket(nodes.Get(flow.src),UdpSocketFactory::GetTypeId());if(flow.tx->Bind()!=0||flow.tx->Connect(InetSocketAddress(ips.GetAddress(flow.dst),port))!=0)throw std::runtime_error("TX persistent socket setup failed");
  for(uint32_t w=0;w<flow.windows.size();++w)Simulator::Schedule(NanoSeconds(flow.windows[w].first),&Send,f,w);
 }
 uint32_t controlCount;std::cin>>controlCount;
 if(!std::cin)throw std::runtime_error("provider radio control input protocol malformed");
 for(uint32_t c=0;c<controlCount;++c){int64_t time;uint32_t node,opcode,channel;
  std::cin>>time>>node>>opcode>>channel;
  if(!std::cin)throw std::runtime_error("provider radio control input protocol malformed");
  if(time<0||time>=horizon)throw std::runtime_error("radio control time outside authored horizon");
  if(node>=n)throw std::runtime_error("radio control references unknown node index");
  if(time<lives[node].birth||time>=lives[node].death)throw std::runtime_error("radio control outside exact owner lifetime");
  if(opcode>2)throw std::runtime_error("undeclared radio control opcode");
  if(opcode==2){if(channel!=1&&channel!=6)throw std::runtime_error("channel switch requires authored channel 1 or 6");}
  else if(channel!=0)throw std::runtime_error("non-switch radio opcodes require channel 0");
  Simulator::Schedule(NanoSeconds(time),&ApplyRadioControl,node,opcode,channel,radio.width,time);
 }
 SnapshotQueues(0);
 if(!std::cin)throw std::runtime_error("provider input protocol malformed");
 Simulator::Stop(NanoSeconds(horizon));Simulator::Run();EmitDiagnostics();Simulator::Destroy();struct rusage usage;getrusage(RUSAGE_SELF,&usage);
 double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
 std::cout<<"{\"record_type\":\"summary\",\"ns3_version\":\"3.48\",\"provider_version\":\"p01.ns3.frozen-wifi/v8-switch-completion\",\"wall_time_s\":"<<elapsed<<",\"peak_rss_kb\":"<<usage.ru_maxrss<<",\"packet_attempts\":"<<packets.size()<<",\"propagation_los_evaluations\":"<<losEvaluations<<",\"propagation_nlos_evaluations\":"<<nlosEvaluations<<"}\n";
 return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}}
