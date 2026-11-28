/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
/*
 * Copyright (c) 2023 NUS
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation;
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 *
 * Authors: Chahwan Song <skychahwan@gmail.com>
 */

#include <ns3/assert.h>
#include <ns3/rdma-client-helper.h>
#include <ns3/rdma-client.h>
#include <ns3/rdma-driver.h>
#include <ns3/rdma.h>
#include <ns3/sim-setting.h>
#include <ns3/switch-node.h>
#include <time.h>

#include <algorithm>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <tuple>
#include <sstream>
#include <unordered_map>
#include <vector>

#include "ns3/applications-module.h"
#include "ns3/broadcom-node.h"
#include "ns3/conga-routing.h"
#include "ns3/conweave-voq.h"
#include "ns3/core-module.h"
#include "ns3/error-model.h"
#include "ns3/flow-id-num-tag.h"
#include "ns3/global-route-manager.h"
#include "ns3/internet-module.h"
#include "ns3/ipv4-static-routing-helper.h"
#include "ns3/letflow-routing.h"
#include "ns3/packet.h"
#include "ns3/point-to-point-helper.h"
#include "ns3/qbb-helper.h"
#include "ns3/qbb-net-device.h"
#include "ns3/rdma-hw.h"
#include "ns3/settings.h"

using namespace ns3;
using namespace std;

NS_LOG_COMPONENT_DEFINE("GENERIC_SIMULATION");

/*------Load balancing parameters-----*/
// mode for load balancer, 0: flow ECMP, 2: DRILL, 3: Conga, 6: Letflow, 9: ConWeave
uint32_t lb_mode = 0;

// Conga params (based on paper recommendation)
Time conga_flowletTimeout = MicroSeconds(100);  // 100us
Time conga_dreTime = MicroSeconds(50);
Time conga_agingTime = MicroSeconds(500);
uint32_t conga_quantizeBit = 3;
double conga_alpha = 0.2;

// Letflow params
Time letflow_flowletTimeout = MicroSeconds(100);  // 100us
Time letflow_agingTime = MilliSeconds(2);  // just to clear the unused map entries for simul speed

// Conweave params
Time conweave_extraReplyDeadline = MicroSeconds(4);       // additional term to reply deadline
Time conweave_pathPauseTime = MicroSeconds(8);            // time to send packets to congested path
Time conweave_txExpiryTime = MicroSeconds(1000);          // waiting time for CLEAR
Time conweave_extraVOQFlushTime = MicroSeconds(32);       // extra for uncertainty
Time conweave_defaultVOQWaitingTime = MicroSeconds(500);  // default flush timer if no history
bool conweave_pathAwareRerouting = true;

/*------------------------ simulation variables -----------------------------*/
uint64_t one_hop_delay = 1000;  // nanoseconds
uint32_t cc_mode = 1;           // mode for congestion control, 1: DCQCN
bool enable_qcn = true, enable_pfc = true, use_dynamic_pfc_threshold = true;
uint32_t packet_payload_size = 1000, l2_chunk_size = 0, l2_ack_interval = 0;
double pause_time = 5;  // PFC pause, microseconds
double flowgen_start_time = 2.0, flowgen_stop_time = 2.5, simulator_extra_time = 0.1;
// queue length monitoring time is not used in this simulator
// uint32_t qlen_dump_interval = 100000000, qlen_mon_interval = 1000;  // ns
uint64_t qlen_mon_start;               // ns
uint64_t qlen_mon_end;                 // ns
uint32_t switch_mon_interval = 10000;  // ns
uint64_t cnp_mon_start;                // ns
uint64_t cnp_monitor_bucket = 100000;  // ns
uint64_t irn_mon_start;                // ns
uint64_t irn_monitor_bucket = 100000;  // ns

FILE *pfc_file = NULL;
FILE *fct_output = NULL;
FILE *flow_input_stream = NULL;
FILE *cnp_output = NULL;
FILE *est_error_output = NULL;
FILE *voq_output = NULL;
FILE *voq_detail_output = NULL;
FILE *uplink_output = NULL;
FILE *conn_output = NULL;

std::string data_rate, link_delay, topology_file, flow_file;
std::string flow_input_file = "flow.txt";
std::string fct_output_file = "fct.txt";
std::string pfc_output_file = "pfc.txt";
std::string cnp_output_file = "cnp.txt";
std::string qlen_mon_file = "qlen.txt";
std::string voq_mon_file = "voq.txt";
std::string voq_mon_detail_file = "voq_detail.txt";
std::string uplink_mon_file = "uplink.txt";
std::string conn_mon_file = "conn.txt";
std::string est_error_output_file = "est_error.txt";

// CC params
double alpha_resume_interval = 55, rp_timer = 300, ewma_gain = 1 / 16;
double rate_decrease_interval = 4;
uint32_t fast_recovery_times = 1;
std::string rate_ai, rate_hai, min_rate = "100Mb/s";
std::string dctcp_rate_ai = "1000Mb/s";

bool clamp_target_rate = false, l2_back_to_zero = false;
double error_rate_per_link = 0.0;
uint32_t has_win = 1;
uint32_t global_t = 1;
uint32_t mi_thresh = 5;
bool var_win = false, fast_react = true;
bool multi_rate = true;
bool sample_feedback = false;
double u_target = 0.95;
uint32_t int_multi = 1;
bool rate_bound = true;
unordered_map<uint64_t, uint32_t> rate2kmax, rate2kmin;
unordered_map<uint64_t, double> rate2pmax;
unordered_map<uint32_t, Ptr<SwitchNode>> idxNodeToR;  // Id -> Ptr

// config of link-down scenario, ACK priority, and buffer
uint64_t link_down_time = 0;
uint32_t link_down_A = 0, link_down_B = 0;
uint32_t buffer_size = 0;  // 0 to set buffer size automatically

// Added from Here
double load = 10.0;
int enable_irn = 0;
int random_seed = 1;  // change this randomly if you want random expt

uint64_t maxRtt, maxBdp;

// app parameters
struct Interface {
    uint32_t idx;
    bool up;
    uint64_t delay;
    uint64_t bw;

    Interface() : idx(0), up(false) {}
};
map<Ptr<Node>, map<Ptr<Node>, Interface>> nbr2if;
// Mapping destination to next hop for each node: <node, <dest, <nexthop0, ...> > >
map<Ptr<Node>, map<Ptr<Node>, vector<Ptr<Node>>>> nextHop;
map<Ptr<Node>, map<Ptr<Node>, uint64_t>> pairDelay;
map<Ptr<Node>, map<Ptr<Node>, uint64_t>> pairTxDelay;
map<Ptr<Node>, map<Ptr<Node>, uint64_t>> pairBw;
map<Ptr<Node>, map<Ptr<Node>, uint64_t>> pairBdp;
map<Ptr<Node>, map<Ptr<Node>, uint64_t>> pairRtt;

// for uplink/Downlink monitoring at TOR switches (load balance performance)
std::map<uint32_t, std::vector<uint32_t>> torId2UplinkIf;
std::map<uint32_t, std::vector<uint32_t>> torId2DownlinkIf;

// input files
std::ifstream topof, flowf;
NodeContainer n;                         // node container
struct RingRail {
    Ipv4Address address;
    uint32_t hostPort;
    uint32_t tor;
    uint32_t torPort;
    std::string rate;
};
bool ringDedicatedRails = false;
bool figure4WorkloadTelemetry = false;
bool symphonyScopeTrace = false;
bool figure4FixedWindow = false;
std::string profileFBackgroundFile;
bool profileFMarkProvenance = false;
bool profileFPilotDetail = false;
bool profileFQueueOnlyNotify = false;
std::string figure4Manifest;
constexpr uint32_t kRingRails = 4;
std::vector<std::vector<RingRail>> ringRails;
struct RailPortStats {
    uint64_t bytes = 0;
    uint64_t data = 0;
    uint64_t feedback = 0;
};
std::map<std::pair<uint32_t, uint32_t>, RailPortStats> railPortStats;

void TraceRailPort(RailPortStats *stats, Ipv4Address address, bool host,
                   Ptr<const Packet> packet) {
    stats->bytes += packet->GetSize();
    if (address == Ipv4Address()) return;
    CustomHeader header(CustomHeader::L2_Header | CustomHeader::L3_Header);
    packet->PeekHeader(header);
    const bool data = header.l3Prot == 0x11;
    const bool feedback = header.l3Prot == 0xFC || header.l3Prot == 0xFD;
    if (!(data || feedback)) return;
    NS_ABORT_MSG_IF((host ? header.sip : header.dip) != address.Get(),
                    "Ring packet used the wrong dedicated rail");
    stats->data += data;
    stats->feedback += feedback;
}

std::vector<Ipv4Address> serverAddress;  // server address

// flow generator
std::unordered_map<uint32_t, uint32_t> flows_per_host;
uint32_t flow_id = 0;
std::unordered_map<uint32_t, uint16_t> portNumber;
std::unordered_map<uint32_t, uint16_t> dportNumber;
uint16_t *port_per_host;

// Scheduling input flows from flow.txt
struct FlowInput {
    uint32_t src, dst, pg, maxPacketCount, port;
    double start_time;
    uint32_t idx;
};
FlowInput flow_input = {0};  // global variable
uint32_t flow_num;

struct RingJob {
    std::vector<uint32_t> hosts = {0, 16, 32, 48};
    std::vector<int64_t> delayNs;
    uint32_t iterations = 1;
    uint32_t iteration = 0;
    uint32_t collectiveId = 0;
    uint64_t flowIdBase = 0;
    std::vector<std::pair<uint16_t, uint16_t>> figure4Ports;
    std::vector<uint32_t> nextStep;
    std::vector<std::vector<uint8_t>> ready;
    uint64_t completed = 0;
    Time start;

    uint32_t Ranks() const { return hosts.size(); }
    uint32_t PhaseSteps() const { return Ranks() - 1; }
    uint32_t Steps() const { return 2 * PhaseSteps(); }
    uint64_t Transfers() const { return uint64_t(Ranks()) * Steps(); }
};

// Only the ring directives reserve the remainder of their line for arguments.
bool ReadRingArguments(std::istream &config, std::vector<int64_t> &values) {
    std::string line;
    std::getline(config, line);
    std::istringstream fields(line.substr(0, line.find('#')));
    std::string token;
    while (fields >> token) {
        std::istringstream number(token);
        int64_t value;
        char trailing;
        if (!(number >> value) || value < 0 || (number >> trailing)) {
            return false;
        }
        values.push_back(value);
    }
    return true;
}

bool symphonyTwoFlow = false;
constexpr uint32_t twoFlowBytes = 16 * 1024 * 1024;
constexpr uint32_t twoFlowReceiver = 2;
constexpr uint32_t twoFlowSwitch = 3;
uint32_t twoFlowCompleted = 0;

bool ringAllReduce = false;
uint32_t symphonyMode = 0;
uint32_t symphonyWarmup = 4;
double symphonyGain = 0.01;
bool symphonyAdaptiveAlpha = true;
uint32_t symphonyMinSamples = 32;
std::vector<Ptr<SwitchNode>> symphonySwitches;
uint32_t ringChunkBytes = 65536;
std::vector<int64_t> ringDelayNs;
std::unordered_map<uint32_t, std::vector<int64_t>> ringJobDelays;
std::vector<RingJob> ringJobs;
std::unordered_map<uint32_t, uint32_t> ringActiveCollectives;  // collective ID -> job ID
uint32_t ringNextCollectiveId = 0;
uint64_t ringNextFlowId = 0;
uint32_t ringFinishedJobs = 0;
uint64_t ringExpectedTransfers = 0;
uint64_t ringCompletedTransfers = 0;

uint32_t figure4Trace = 0;  // 1: summaries; 2: representative traces.
bool figure4Late = false;
std::vector<Ptr<SwitchNode>> figure4Switches;

struct Figure4EgressBin {
    int64_t bin = -1;
    uint64_t steps = 0;
    uint32_t queueMax = 0;
    uint64_t wireBytes = 0;
    int32_t firstFlow = -1;
    int32_t otherFlow = -1;
    int64_t firstNs = 0;
    int64_t otherNs = 0;
};

std::map<std::pair<uint32_t, uint32_t>, Figure4EgressBin> figure4Egress;

void EmitFigure4Bin(uint32_t sw, uint32_t port, const Figure4EgressBin &value) {
    if (value.otherFlow >= 0 || (symphonyScopeTrace && value.steps != 0)) {
        std::cout << "FIGURE4_EGRESS switch=" << sw << " port=" << port
                  << " bin=" << value.bin << " steps=" << value.steps
                  << " queue_bytes=" << value.queueMax << " flow_a=" << value.firstFlow
                  << " flow_b=" << value.otherFlow << " time_a_ns=" << value.firstNs
                  << " time_b_ns=" << value.otherNs;
        if (symphonyScopeTrace) std::cout << " data_wire_bytes=" << value.wireBytes;
        std::cout << '\n';
    }
}

void TraceFigure4Egress(QbbNetDevice *dev, Figure4EgressBin *state,
                        Ptr<const Packet> packet, uint32_t queue) {
    RingProgressTag tag;
    if (queue == 0 || !packet->PeekPacketTag(tag)) {
        return;
    }
    const int64_t now = Simulator::Now().GetNanoSeconds();
    const int64_t bin = now / 100000;
    if (state->bin != bin) {
        EmitFigure4Bin(dev->GetNode()->GetId(), dev->GetIfIndex(), *state);
        *state = Figure4EgressBin{};
        state->bin = bin;
    }
    const uint64_t bit = uint64_t(1) << tag.GetProgress().step;
    state->queueMax = std::max(state->queueMax, dev->GetQueue()->GetNBytesTotal());
    if (!(state->steps & bit) && state->otherFlow < 0) {
        FlowIDNUMTag flow;
        if (packet->PeekPacketTag(flow)) {
            if (state->firstFlow < 0) {
                state->firstFlow = flow.GetId();
                state->firstNs = now;
            } else {
                state->otherFlow = flow.GetId();
                state->otherNs = now;
            }
        }
    }
    state->steps |= bit;
    state->wireBytes += packet->GetSize();
}

bool ringEgressTrace = false;
std::map<uint32_t, int64_t> ringJobStartOffsets;
std::map<std::tuple<int64_t, uint32_t, uint32_t>, uint64_t> ringEgressBytes;

void TraceRingEgress(uint32_t dst, Ptr<const Packet> packet, uint32_t queue) {
    RingProgressTag tag;
    if (queue == 0 || !packet->PeekPacketTag(tag)) {
        return;
    }
    const auto job = ringActiveCollectives.find(tag.GetProgress().collectiveId);
    if (job == ringActiveCollectives.end()) {
        return;
    }
    CustomHeader header(CustomHeader::L2_Header | CustomHeader::L3_Header |
                        CustomHeader::L4_Header);
    const uint32_t headerSize = packet->PeekHeader(header);
    if (header.l3Prot == 17) {
        const int64_t bin = Simulator::Now().GetNanoSeconds() / 100000;
        ringEgressBytes[std::make_tuple(bin, job->second, dst)] += packet->GetSize() - headerSize;
    }
}

bool ConnectRingEgressTraces() {
    std::set<uint32_t> hosts;
    for (const auto &job : ringJobs) {
        hosts.insert(job.hosts.begin(), job.hosts.end());
    }
    for (uint32_t host : hosts) {
        uint32_t connected = 0;
        Ptr<Node> endpoint = n.Get(host);
        for (uint32_t i = 0; i < endpoint->GetNDevices(); ++i) {
            auto device = DynamicCast<QbbNetDevice>(endpoint->GetDevice(i));
            if (!device || !device->GetChannel()) {
                continue;
            }
            auto channel = device->GetChannel();
            for (uint32_t j = 0; j < channel->GetNDevices(); ++j) {
                auto peer = DynamicCast<QbbNetDevice>(channel->GetDevice(j));
                if (peer && peer->GetNode()->GetNodeType() == 1 &&
                    peer->TraceConnectWithoutContext("QbbDequeue",
                        MakeBoundCallback(&TraceRingEgress, host))) {
                    ++connected;
                }
            }
        }
        if (connected != 1) {
            std::cerr << "Ring egress telemetry requires one traced link per receiver\n";
            return false;
        }
    }
    return true;
}

void TraceTwoFlowEgress(Ptr<const Packet> packet, uint32_t queue) {
    if (queue == 0) {
        return;
    }
    RingProgressTag tag;
    if (!packet->PeekPacketTag(tag)) {
        return;
    }
    CustomHeader header(CustomHeader::L2_Header | CustomHeader::L3_Header |
                        CustomHeader::L4_Header);
    const uint32_t headerSize = packet->PeekHeader(header);
    if (header.l3Prot == 17) {
        std::cout << "TWO_FLOW_EGRESS step=" << tag.GetProgress().step
                  << " time_ns=" << Simulator::Now().GetNanoSeconds()
                  << " bytes=" << packet->GetSize() - headerSize << '\n';
    }
}

void StartTwoFlowTransfer(uint32_t step) {
    Ptr<Node> src = n.Get(step);
    Ptr<Node> dst = n.Get(twoFlowReceiver);
    RingProgress progress;
    progress.step = step;
    std::cout << "TWO_FLOW_START step=" << step
              << " time_ns=" << Simulator::Now().GetNanoSeconds() << '\n';
    src->GetObject<RdmaDriver>()->AddQueuePair(
        twoFlowBytes, 3, serverAddress[step], serverAddress[twoFlowReceiver],
        portNumber[step]++, dportNumber[twoFlowReceiver]++,
        has_win ? (global_t == 1 ? maxBdp : pairBdp[src][dst]) : 0,
        global_t == 1 ? maxRtt : pairRtt[src][dst], step, progress);
}

bool ScheduleTwoFlow() {
    if (n.GetN() != 4 || n.Get(twoFlowSwitch)->GetNodeType() != 1 ||
        flowgen_start_time < 0 || flowgen_stop_time <= flowgen_start_time + 0.0067) {
        std::cerr << "Invalid topology or time window for the two-flow experiment\n";
        return false;
    }

    Ptr<Node> sw = n.Get(twoFlowSwitch);
    Ptr<QbbNetDevice> egress;
    for (uint32_t host = 0; host <= twoFlowReceiver; ++host) {
        Ptr<Node> endpoint = n.Get(host);
        Ptr<QbbNetDevice> link;
        for (uint32_t i = 0; i < sw->GetNDevices(); ++i) {
            Ptr<QbbNetDevice> dev = DynamicCast<QbbNetDevice>(sw->GetDevice(i));
            if (!dev || !dev->GetChannel()) {
                continue;
            }
            Ptr<Channel> channel = dev->GetChannel();
            for (uint32_t j = 0; j < channel->GetNDevices(); ++j) {
                if (channel->GetDevice(j)->GetNode() == endpoint) {
                    link = dev;
                }
            }
        }
        if (endpoint->GetNodeType() != 0 || !link ||
            link->GetDataRate().GetBitRate() != 10000000000ull) {
            std::cerr << "Two-flow topology requires three hosts directly linked to switch 3 at 10 Gb/s\n";
            return false;
        }
        if (host == twoFlowReceiver) {
            egress = link;
        }
    }
    if (!egress || !egress->TraceConnectWithoutContext("QbbDequeue", MakeCallback(&TraceTwoFlowEgress))) {
        std::cerr << "Could not trace the shared receiver-facing egress\n";
        return false;
    }
    for (const auto &sw : symphonySwitches) {
        sw->StartSymphonyCollective(0);
    }
    const Time start = Seconds(flowgen_start_time);
    Simulator::Schedule(start, &StartTwoFlowTransfer, 1u);
    Simulator::Schedule(start + MicroSeconds(6700), &StartTwoFlowTransfer, 0u);
    return true;
}

#include "figure4-collective.h"
#include "figure4-telemetry.h"
#include "profile-f.h"

void RingStepReady(uint32_t jobId, uint32_t rank, uint32_t step) {
    auto &job = ringJobs[jobId];
    job.ready[rank][step] = true;
    uint32_t src = job.hosts[rank];
    uint32_t dst = job.hosts[(rank + 1) % job.Ranks()];
    Ptr<Node> srcNode = n.Get(src);
    Ptr<Node> dstNode = n.Get(dst);
    auto driver = srcNode->GetObject<RdmaDriver>();
    auto &next = job.nextStep[rank];

    // A ready chunk may overtake another chunk. Issue sends in local step order.
    while (next < job.Steps() && job.ready[rank][next]) {
        int32_t flowId = job.flowIdBase + uint64_t(rank) * job.Steps() + next;
        RingProgress progress;
        progress.collectiveId = figure4Trace ? 0 : job.collectiveId;
        const auto ports = figure4Trace ? job.figure4Ports[rank * job.Steps() + next]
                                        : std::make_pair(portNumber[src]++, dportNumber[dst]++);
        progress.step = next++;
        const Ipv4Address source = ringDedicatedRails ? ringRails[src][jobId].address
                                                     : serverAddress[src];
        const Ipv4Address destination = ringDedicatedRails ? ringRails[dst][jobId].address
                                                          : serverAddress[dst];
        driver->AddQueuePair(
            ringChunkBytes, 3, source, destination, ports.first,
            ports.second, has_win ? (figure4FixedWindow ? 14000 :
                                     (global_t == 1 ? maxBdp : pairBdp[srcNode][dstNode])) : 0,
            figure4FixedWindow ? 11200 : (global_t == 1 ? maxRtt : pairRtt[srcNode][dstNode]),
            flowId, progress);
    }
}

void StartRingIteration(uint32_t jobId) {
    auto &job = ringJobs[jobId];
    job.start = Simulator::Now();
    job.completed = 0;
    job.collectiveId = ringNextCollectiveId++;
    job.flowIdBase = ringNextFlowId;
    ringNextFlowId += job.Transfers();
    ringActiveCollectives.emplace(job.collectiveId, jobId);
    for (const auto &sw : symphonySwitches) {
        if (!figure4Trace || jobId == 0) {
            sw->StartSymphonyCollective(job.collectiveId);
        }
    }
    for (uint32_t rank = 0; rank < job.Ranks(); ++rank) {
        job.nextStep[rank] = 0;
        for (auto &ready : job.ready[rank]) {
            ready = false;
        }
        RingStepReady(jobId, rank, 0);
    }
}

void CompleteRingTransfer(Ptr<RdmaQueuePair> q) {
    // Figure 4 reserves equal, contiguous flow-ID blocks for its four channels.
    const uint32_t collectiveId = figure4Trace
        ? q->m_flow_id / ringJobs.front().Transfers() : q->m_ringProgress.collectiveId;
    uint32_t jobId = ringActiveCollectives.at(collectiveId);
    auto &job = ringJobs[jobId];
    uint32_t rank = (q->m_flow_id - job.flowIdBase) / job.Steps();
    uint32_t transferStep = q->m_ringProgress.step;
    bool allGather = transferStep >= job.PhaseSteps();
    uint32_t step = transferStep % job.PhaseSteps();
    uint32_t chunk = (rank + job.Ranks() - step + (allGather ? 1 : 0)) % job.Ranks();
    uint32_t dstRank = (rank + 1) % job.Ranks();
    Time now = Simulator::Now();
    std::cout << "RING_TRANSFER job=" << jobId << " iteration=" << job.iteration
              << " ranks=" << job.Ranks()
              << " flow_id=" << q->m_flow_id << " rank=" << rank
              << " phase=" << (allGather ? "all-gather" : "reduce-scatter") << " step=" << step
              << " chunk=" << chunk << " src=" << job.hosts[rank] << " dst=" << job.hosts[dstRank]
              << " sport=" << q->sport << " dport=" << q->dport << " bytes=" << q->m_size
              << " start_ns=" << q->startTime.GetNanoSeconds()
              << " completion_ns=" << now.GetNanoSeconds();
    if (figure4Trace) {
        std::cout << " collective_id=" << q->m_ringProgress.collectiveId;
    }
    std::cout << '\n';

    // This ACK proves delivery. Enqueue the dependent send after QP cleanup,
    // even when this rank's processing delay is zero.
    if (transferStep + 1 < job.Steps()) {
        Simulator::Schedule(NanoSeconds(job.delayNs[dstRank]), &RingStepReady, jobId, dstRank,
                            transferStep + 1);
    }
    ++ringCompletedTransfers;
    if (++job.completed == job.Transfers()) {
        ringActiveCollectives.erase(collectiveId);
        for (const auto &sw : symphonySwitches) {
            if (!figure4Trace || ringFinishedJobs + 1 == ringJobs.size()) {
                sw->FinishSymphonyCollective(figure4Trace ? 0 : collectiveId);
            }
        }
        std::cout << "RING_COMPLETE job=" << jobId << " iteration=" << job.iteration
                  << " ranks=" << job.Ranks() << " transfers=" << job.completed << " start_ns=" << job.start.GetNanoSeconds()
                  << " completion_ns=" << now.GetNanoSeconds()
                  << " duration_ns=" << (now - job.start).GetNanoSeconds() << '\n';
        if (++job.iteration < job.iterations) {
            Simulator::ScheduleNow(&StartRingIteration, jobId);
        } else if (++ringFinishedJobs == ringJobs.size() && !profileFBackgroundPending) {
            Simulator::Stop();
        }
    }
}

bool ScheduleRingJobs() {
    if (flowgen_start_time < 0 || flowgen_stop_time <= flowgen_start_time ||
        l2_ack_interval == 0) {
        std::cerr << "Ring requires a valid flow generation time window and nonzero L2_ACK_INTERVAL\n";
        return false;
    }
    if (figure4Trace && (ringJobs.size() != 4 || !ringJobStartOffsets.empty() ||
                        ringEgressTrace ||
                        std::any_of(ringJobs.begin(), ringJobs.end(), [](const RingJob &job) {
                            return job.Ranks() != 32 || job.iterations != 1;
                        }))) {
        std::cerr << "Figure 4 requires four simultaneous single-iteration 32-rank channels\n";
        return false;
    }
    if (ringJobs.empty()) {
        ringJobs.emplace_back();
    }

    for (const auto &entry : ringJobDelays) {
        if (entry.first >= ringJobs.size()) {
            std::cerr << "RING_JOB_DELAY_NS refers to unknown job " << entry.first << '\n';
            return false;
        }
    }
    std::vector<uint64_t> transfersPerHost(n.GetN());
    for (uint32_t jobId = 0; jobId < ringJobs.size(); ++jobId) {
        auto &job = ringJobs[jobId];
        std::set<uint32_t> distinctHosts(job.hosts.begin(), job.hosts.end());
        if (job.hosts.size() < 2 || job.hosts.size() > n.GetN() ||
            distinctHosts.size() != job.hosts.size()) {
            std::cerr << "Each RING_JOB requires at least two distinct hosts in the topology\n";
            return false;
        }
        auto delay = ringJobDelays.find(jobId);
        job.delayNs = delay == ringJobDelays.end() ? ringDelayNs : delay->second;
        if (job.delayNs.empty()) {
            job.delayNs.resize(job.Ranks());
        } else if (job.delayNs.size() != job.Ranks()) {
            std::cerr << "Processing delay count must match rank count for job " << jobId << '\n';
            return false;
        }
        const uint64_t availableFlowIds = uint64_t(INT32_MAX) + 1 - ringExpectedTransfers;
        if (2ull * job.PhaseSteps() > availableFlowIds / job.iterations / job.Ranks()) {
            std::cerr << "Ring jobs exceed the RDMA flow ID range\n";
            return false;
        }
        ringExpectedTransfers += uint64_t(job.iterations) * job.Transfers();
        for (uint32_t rank = 0; rank < job.Ranks(); ++rank) {
            uint32_t src = job.hosts[rank];
            uint32_t dst = job.hosts[(rank + 1) % job.Ranks()];
            if (src >= n.GetN() || dst >= n.GetN() || n.Get(src)->GetNodeType() != 0 ||
                n.Get(dst)->GetNodeType() != 0 || pairBw[n.Get(src)][n.Get(dst)] == 0) {
                std::cerr << "Ring requires connected end hosts. Invalid edge "
                          << src << " -> " << dst << '\n';
                return false;
            }
            transfersPerHost[src] += uint64_t(job.iterations) * job.Steps();
            // A ring host sends and receives the same number of transfers.
            if (transfersPerHost[src] > UINT16_MAX + 1u - portNumber[src] ||
                transfersPerHost[src] > UINT16_MAX + 1u - dportNumber[src]) {
                std::cerr << "Ring jobs would reuse RDMA ports on host " << src << '\n';
                return false;
            }
        }
    }
    for (auto &job : ringJobs) {
        job.nextStep.resize(job.Ranks());
        job.ready.assign(job.Ranks(), std::vector<uint8_t>(job.Steps()));
    }
    for (const auto &entry : ringJobStartOffsets) {
        if (entry.first >= ringJobs.size() ||
            NanoSeconds(entry.second) >= Seconds(flowgen_stop_time - flowgen_start_time)) {
            std::cerr << "Invalid RING_JOB_START_OFFSET_NS job or offset\n";
            return false;
        }
    }
    if (figure4Trace) {
        for (auto &job : ringJobs) {
            for (uint32_t rank = 0; rank < job.Ranks(); ++rank) {
                const uint32_t src = job.hosts[rank];
                const uint32_t dst = job.hosts[(rank + 1) % job.Ranks()];
                for (uint32_t step = 0; step < job.Steps(); ++step) {
                    job.figure4Ports.emplace_back(portNumber[src]++, dportNumber[dst]++);
                }
            }
        }
        if (figure4Late) {
            Simulator::Schedule(Seconds(flowgen_start_time) + MilliSeconds(500), [] {
                std::cout << "FIGURE4_ACTIVATE time_ns=" << Simulator::Now().GetNanoSeconds()
                          << " active_channels=" << ringActiveCollectives.size() << '\n';
            });
        }

    }
    if (ringEgressTrace && !ConnectRingEgressTraces()) {
        return false;
    }
    for (uint32_t jobId = 0; jobId < ringJobs.size(); ++jobId) {
        Simulator::Schedule(Seconds(flowgen_start_time) + NanoSeconds(ringJobStartOffsets[jobId]),
                            &StartRingIteration, jobId);
    }
    return true;
}

/**
 * Read flow input from file "flowf"
 */
void ReadFlowInput() {
    if (flow_input.idx < flow_num) {
        flowf >> flow_input.src >> flow_input.dst >> flow_input.pg >> flow_input.maxPacketCount >>
            flow_input.start_time;
        assert(n.Get(flow_input.src)->GetNodeType() == 0 &&
               n.Get(flow_input.dst)->GetNodeType() == 0);
    } else {
        std::cout << "*** input flow is over the prefixed number -- flow number : " << flow_num
                  << std::endl;
        std::cout << "*** flow_input.idx : " << flow_input.idx << std::endl;
        std::cout << "*** THIS IS THE LAST FLOW TO SEND :) " << std::endl;
    }
}

/**
 * Scheduling flows given in /config/L_XX....txt file
 */
void ScheduleFlowInputs(FILE *infile) {
    NS_LOG_DEBUG("ScheduleFlowInputs at " << Simulator::Now());
    while (flow_input.idx < flow_num && Seconds(flow_input.start_time) == Simulator::Now()) {
        uint32_t pg, src, dst, sport, dport, maxPacketCount, target_len;
        pg = flow_input.pg;
        src = flow_input.src;
        dst = flow_input.dst;

        // src port
        sport = portNumber[src];  // get a new port number
        portNumber[src] = portNumber[src] + 1;

        // dst port
        dport = dportNumber[dst];
        dportNumber[dst] = dportNumber[dst] + 1;

        target_len = flow_input.maxPacketCount;  // this is actually not packet-count, but bytes
        if (target_len == 0) {
            target_len = 1;
        }
        assert(n.Get(src)->GetNodeType() == 0 && n.Get(dst)->GetNodeType() == 0);

        /**
         * Turn on if you want to record all input streams into output file for logging.
         * But, the input stream can be found in config. We do not recommend to do this
         * as it consumes storage resource, redundantly.
         */
        if (0) {  // logging input streams to "XXXX_out_in.txt"
            /************************
             * record flow's 4-tuple
             ************************/
            fprintf(infile, "%u %u %u %u %u %lu\n", src, dst, sport, dport, target_len,
                    (uint64_t)(flow_input.start_time * (uint64_t)1000000000));
            fflush(infile);

            /***********    FCT Tracking    **************/
            UdpServerHelper server0(dport);
            server0.SetAttribute("FlowSize", UintegerValue(target_len));
            server0.SetAttribute("irn", BooleanValue(enable_irn));
            server0.SetAttribute("StatHostSrc", UintegerValue(src));
            server0.SetAttribute("StatHostDst", UintegerValue(dst));
            server0.SetAttribute("StatRxLen", UintegerValue(target_len));
            server0.SetAttribute("StatFlowID", UintegerValue(flow_input.idx));
            server0.SetAttribute("Port", UintegerValue(dport));

            ApplicationContainer apps0s = server0.Install(n.Get(dst));  // DST
            apps0s.Start(Seconds(Time(0)));
            apps0s.Stop(Seconds(100.0));
        }  // end of logging input streams

        if (pairRtt.find(n.Get(src)) == pairRtt.end() ||
            pairRtt[n.Get(src)].find(n.Get(dst)) == pairRtt[n.Get(src)].end()) {
            std::cerr << "pairRtt src: " << src << " -> dst: " << dst
                      << " ==> cannot be found from database" << std::endl;
            assert(false);
        }

        RdmaClientHelper clientHelper(
            pg, serverAddress[src], serverAddress[dst], sport, dport, target_len,
            has_win ? (global_t == 1 ? maxBdp : pairBdp[n.Get(src)][n.Get(dst)]) : 0,
            global_t == 1 ? maxRtt : pairRtt[n.Get(src)][n.Get(dst)]);
        clientHelper.SetAttribute("StatFlowID", IntegerValue(flow_input.idx));

        ApplicationContainer appCon = clientHelper.Install(n.Get(src));  // SRC
        appCon.Start(Seconds(Time(0)));
        appCon.Stop(Seconds(100.0));

        flow_input.idx++;
        ReadFlowInput();
    }

    // schedule the next time to run this function
    if (flow_input.idx < flow_num) {
        Simulator::Schedule(Seconds(flow_input.start_time) - Simulator::Now(), &ScheduleFlowInputs,
                            infile);
    } else {  // no more flows, close the file
        flowf.close();
    }
}

/**
 * @brief CNP frequency monitoring (timestamp nodeId ECN OoO Total)
 */
void cnp_freq_monitoring(FILE *fout, Ptr<RdmaHw> rdmahw) {
    if (rdmahw->cnp_total > 0) {
        // flush
        fprintf(fout, "%lu %u %u %u %u\n", Simulator::Now().GetNanoSeconds(),
                rdmahw->m_node->GetId(), rdmahw->cnp_by_ecn, rdmahw->cnp_by_ooo, rdmahw->cnp_total);
        fflush(fout);

        // initialize
        rdmahw->cnp_by_ecn = 0;
        rdmahw->cnp_by_ooo = 0;
        rdmahw->cnp_total = 0;
    }

    // recursive callback
    Simulator::Schedule(NanoSeconds(cnp_monitor_bucket), &cnp_freq_monitoring, fout, rdmahw);
}

/**
 * @brief TOR Switch monitoring
 * - VOQ number and uplink throughput at switches
 * - the number of active connections at RNICS
 */
void periodic_monitoring(FILE *fout_voq, FILE *fout_voq_detail, FILE *fout_uplink, FILE *fout_conn,
                         uint32_t *lb_mode) {
    uint32_t lb_mode_val = *lb_mode;
    uint64_t now = Simulator::Now().GetNanoSeconds();
    for (const auto &tor2If : torId2UplinkIf) {  // for each TOR switches
        Ptr<Node> node = n.Get(tor2If.first);    // tor id
        auto swNode = DynamicCast<SwitchNode>(node);
        assert(swNode->m_isToR == true);  // sanity check

        if (lb_mode_val == 9) {  // Conweave
            // monitor VOQ number per switch <time, ToRId, #VOQ, #Pkts>
            uint32_t nVOQ = swNode->m_mmu->m_conweaveRouting.GetNumVOQ();
            uint32_t nVolumeVOQ = swNode->m_mmu->m_conweaveRouting.GetVolumeVOQ();
            fprintf(fout_voq, "%lu,%u,%u,%u\n", now, tor2If.first, nVOQ, nVolumeVOQ);

            // monitor VOQ per destination IP <time, dstip, #VOQ, #Pkts>
            std::unordered_map<uint32_t, std::pair<uint32_t, uint32_t>> dip_to_nvoq_npkt;
            for (auto voq : swNode->m_mmu->m_conweaveRouting.GetVOQMap()) {
                auto &nvoq_npkt = dip_to_nvoq_npkt[voq.second.getDIP()];
                nvoq_npkt.first += 1;
                nvoq_npkt.second += voq.second.getQueueSize();
            }
            for (auto x : dip_to_nvoq_npkt) {
                fprintf(fout_voq_detail, "%lu,%u,%u,%u\n", now, x.first, x.second.first,
                        x.second.second);
            }
        }

        // common: monitor TOR's uplink to measure load balancing performance
        for (const auto &iface : tor2If.second) {
            // monitor uplink txBytes <time, ToRId, OutDev, Bytes>
            uint64_t uplink_txbyte = swNode->GetTxBytesOutDev(iface);
            fprintf(fout_uplink, "%lu,%u,%u,%lu\n", now, tor2If.first, iface, uplink_txbyte);
        }
    }

    // common: get number of concurrent connections at each server
    for (uint32_t i = 0; i < Settings::node_num; i++) {
        if (n.Get(i)->GetNodeType() == 0) {  // is server
            Ptr<Node> server = n.Get(i);
            Ptr<RdmaDriver> rdmaDriver = server->GetObject<RdmaDriver>();
            Ptr<RdmaHw> rdmaHw = rdmaDriver->m_rdma;
            // monitor total/active QP number <time, serverId, #ExistingQP, #ActiveQP>
            uint64_t nQP = rdmaHw->m_qpMap.size();
            uint64_t nActiveQP = 0;
            for (auto qp : rdmaHw->m_qpMap) {
                if (qp.second->GetBytesLeft() > 0) {  // conns with bytes left
                    nActiveQP++;
                }
            }
            fprintf(fout_conn, "%lu,%u,%lu,%lu\n", now, i, nQP, nActiveQP);
        }
    }

    if (Simulator::Now() < Seconds(flowgen_stop_time + 0.05)) {
        // recursive callback
        Simulator::Schedule(NanoSeconds(switch_mon_interval), &periodic_monitoring, fout_voq,
                            fout_voq_detail, fout_uplink, fout_conn, lb_mode);  // every 10us
    }
    return;
}

/**
 * @brief Conga timeout number recording
 */
void conga_history_print() {
    std::cout << "\n------------CONGA History---------------" << std::endl;
    std::cout << "Number of flowlet's timeout:" << CongaRouting::nFlowletTimeout
              << "Conga's timeout: " << conga_flowletTimeout << std::endl;
}

/**
 * @brief Letflow timeout number recording
 */
void letflow_history_print() {
    std::cout << "\n------------Letflow History---------------" << std::endl;
    std::cout << "Number of flowlet's timeout:" << LetflowRouting::nFlowletTimeout
              << "\nLetflow's timeout: " << letflow_flowletTimeout << std::endl;
}

/**
 * @brief Conweave rerouting/VOQ number recording
 */
void conweave_history_print() {
    // Conweave params
    std::cout << "\n------ConWeave parameters-----" << std::endl;
    std::cout << "Param - extraReplyDeadline:" << conweave_extraReplyDeadline << std::endl;
    std::cout << "Param - extraVOQFlushTime:" << conweave_extraVOQFlushTime << std::endl;
    std::cout << "Param - txExpiryTime:" << conweave_txExpiryTime << std::endl;
    std::cout << "Param - defaultVOQWaitingTime:" << conweave_defaultVOQWaitingTime << std::endl;
    std::cout << "Param - pathPauseTime:" << conweave_pathPauseTime << std::endl;
    std::cout << "Param - pathAwareRerouting:" << conweave_pathAwareRerouting << std::endl;

    std::cout << "\n------------ConWeave History---------------" << std::endl;
    std::cout << "Number of INIT's Reply sent (RTT_REPLY):" << ConWeaveRouting::m_nReplyInitSent
              << "\nNumber of Timely RTT_REPLY (INIT's Reply):" << ConWeaveRouting::m_nTimelyInitReplied
              << "\nNumber of TAIL's Reply Sent (CLEAR):" << ConWeaveRouting::m_nReplyTailSent
              << "\nNumber of Timely CLEAR (TAIL's Reply):" << ConWeaveRouting::m_nTimelyTailReplied
              << "\nNumber of NOTIFY Sent:" << ConWeaveRouting::m_nNotifySent
              << "\nNumber of Rerouting:" << ConWeaveRouting::m_nReRoute
              << "\nNumber of OoO enqueued pkts:" << ConWeaveRouting::m_nOutOfOrderPkts
              << "\nNumber of VOQ Flush Total:" << ConWeaveRouting::m_nFlushVOQTotal
              << "\nNumber of VOQ Flush From History:" << ConWeaveRouting::m_historyVOQSize.size()
              << "\nNumber of VOQ Flush by TAIL:" << ConWeaveRouting::m_nFlushVOQByTail
              << std::endl;

    std::cout << "--------------------------" << std::endl;

    /** VOQ: Sanity check*/
    for (size_t ToRId = 0; ToRId < Settings::node_num; ToRId++) {
        Ptr<Node> node = n.Get(ToRId);
        if (node->GetNodeType() == 1) {  // switches
            auto swNode = DynamicCast<SwitchNode>(n.Get(ToRId));
            if (swNode->m_isToR) {  // TOR switch
                uint32_t num_remained_voq = swNode->m_mmu->m_conweaveRouting.GetNumVOQ();
                if (num_remained_voq > 0) {
                    printf("*******************************\n");
                    printf("*** WARNING - Tor Sw (%lu) - VOQ (num=%u) is not flushed yet!! ***\n",
                           ToRId, num_remained_voq);
                    printf(
                        " -- Probably the history print is too early so simulation might not be "
                        "finished?");
                    printf("********************************\n");
                }
            }
        }
    }

    /** Get ConWeave Flush Time Estimation Error */
    if (0) {
        // sanity check - extraVOQFlushTime must be large enough to get accuracy
        assert(conweave_extraVOQFlushTime >= MicroSeconds(128) && "PARAMETER ERROR!!");

        std::cout << "\n--------------------------" << std::endl;
        std::cout << "Extracting ConWeave Estimation Error Data..." << std::endl;
        est_error_output = fopen(est_error_output_file.c_str(), "w");
        for (auto x : ConWeaveVOQ::m_flushEstErrorhistory) {
            fprintf(est_error_output, "%d\n", x);
        }
        ConWeaveVOQ::m_flushEstErrorhistory.clear();
        std::cout << "---------D O N E---------" << std::endl;
    }
}

/**
 * @brief When one RDMA is finished, so does (1) QP, (2) RxQP, (3) write it on file fct.txt.
 */
void qp_finish(FILE *fout, Ptr<RdmaQueuePair> q) {
    uint32_t sid = Settings::ip_to_node_id(q->sip), did = Settings::ip_to_node_id(q->dip);
    uint64_t base_rtt = pairRtt[n.Get(sid)][n.Get(did)];
    uint64_t b = pairBw[n.Get(sid)][n.Get(did)];
    uint32_t total_bytes =
        q->m_size + ((q->m_size - 1) / packet_payload_size + 1) *
                        (CustomHeader::GetStaticWholeHeaderSize() -
                         IntHeader::GetStaticSize());  // translate to the minimum bytes required
                                                       // (with header but no INT)
    uint64_t standalone_fct = base_rtt + total_bytes * 8000000000lu / b;

    // XXX: remove rxQP from the receiver
    Ptr<Node> dstNode = n.Get(did);
    Ptr<RdmaDriver> rdma = dstNode->GetObject<RdmaDriver>();
    rdma->m_rdma->DeleteRxQp(q->sip.Get(), q->sport, q->dport, q->m_pg);

    // fprintf(fout, "%lu QP complete\n", Simulator::Now().GetTimeStep());
    fprintf(fout, "%u %u %u %u %lu %lu %lu %lu\n", Settings::ip_to_node_id(q->sip),
            Settings::ip_to_node_id(q->dip), q->sport, q->dport, q->m_size,
            q->startTime.GetTimeStep(), (Simulator::Now() - q->startTime).GetTimeStep(),
            standalone_fct);

    // for debugging
    NS_LOG_DEBUG("%u %u %u %u %lu %lu %lu %lu\n" %
                 (Settings::ip_to_node_id(q->sip), Settings::ip_to_node_id(q->dip), q->sport,
                  q->dport, q->m_size, q->startTime.GetTimeStep(),
                  (Simulator::Now() - q->startTime).GetTimeStep(), standalone_fct));
    Settings::cnt_finished_flows++;
    fflush(fout);
    if (!profileFBackgroundFile.empty() && q->m_flow_id >= profileFBackgroundId) {
        std::cout << "PROFILE_F_BACKGROUND_COMPLETE flow_id=" << q->m_flow_id
                  << " time_ns=" << Simulator::Now().GetNanoSeconds() << '\n';
        if (--profileFBackgroundPending == 0 && ringFinishedJobs == ringJobs.size()) {
            Simulator::Stop();
        }
    } else if (!figure4Manifest.empty()) {
        CompleteFigure4Transfer(q);
    } else if (ringAllReduce) {
        CompleteRingTransfer(q);
    } else if (symphonyTwoFlow) {
        std::cout << "TWO_FLOW_COMPLETE step=" << q->m_ringProgress.step
                  << " time_ns=" << Simulator::Now().GetNanoSeconds()
                  << " bytes=" << q->m_size << '\n';
        if (++twoFlowCompleted == 2) {
            for (const auto &sw : symphonySwitches) {
                sw->FinishSymphonyCollective(0);
            }
            Simulator::Stop();
        }
    }
}

/**
 * @brief PFC event logging
 */
void get_pfc(FILE *fout, Ptr<QbbNetDevice> dev, uint32_t type) {
    // time, nodeID, nodeType, Interface's Idx, 0:resume, 1:pause
    fprintf(fout, "%lu %u %u %u %u\n", Simulator::Now().GetTimeStep(), dev->GetNode()->GetId(),
            dev->GetNode()->GetNodeType(), dev->GetIfIndex(), type);
}

/*******************************************************************/
#if (false)

/**
 * @brief Qlen monitoring at switches (output: qlen.txt), I think "periodically"...
 *
 */
struct QlenDistribution {
    vector<uint32_t> cnt;  // cnt[i] is the number of times that the queue len is i KB
    void add(uint32_t qlen) {
        uint32_t kb = qlen / 1000;
        if (cnt.size() < kb + 1) cnt.resize(kb + 1);
        cnt[kb]++;
    }
};

map<uint32_t, map<uint32_t, QlenDistribution>> queue_result;
void monitor_buffer(FILE *qlen_output, NodeContainer *n) {
    /*******************************************************************/
    /************************** UNUSED NOW *****************************/
    /*******************************************************************/
    for (uint32_t i = 0; i < n->GetN(); i++) {
        if (n->Get(i)->GetNodeType() == 1) {  // is switch
            Ptr<SwitchNode> sw = DynamicCast<SwitchNode>(n->Get(i));
            if (queue_result.find(i) == queue_result.end()) queue_result[i];
            for (uint32_t j = 1; j < sw->GetNDevices(); j++) {
                uint32_t size = 0;
                for (uint32_t k = 0; k < SwitchMmu::qCnt; k++)
                    size += sw->m_mmu->egress_bytes[j][k];
                queue_result[i][j].add(size);
            }
        }
    }
    if (Simulator::Now().GetTimeStep() % qlen_dump_interval == 0) {
        fprintf(qlen_output, "time: %lu\n", Simulator::Now().GetTimeStep());
        for (auto &it0 : queue_result) {
            for (auto &it1 : it0.second) {
                fprintf(qlen_output, "%u %u", it0.first, it1.first);
                auto &dist = it1.second.cnt;
                for (uint32_t i = 0; i < dist.size(); i++) fprintf(qlen_output, " %u", dist[i]);
                fprintf(qlen_output, "\n");
            }
        }
        fflush(qlen_output);
    }
    if (Simulator::Now().GetTimeStep() < qlen_mon_end)
        Simulator::Schedule(NanoSeconds(qlen_mon_interval), &monitor_buffer, qlen_output, n);
}
#endif
/*******************************************************************/

/**
 * @brief Stop simulation in the middle (when almost all flows are done).
 * This function allows to finish simulation quickly when all messages are sent.
 */
void stop_simulation_middle() {
    uint32_t target_flow_num = flow_num - 0;  // can be lower than flownum
    if (Settings::cnt_finished_flows >= target_flow_num) {
        std::cout << "\n*** Simulator is enforced to be finished, finished so far: "
                  << Settings::cnt_finished_flows << "/ total: " << target_flow_num
                  << ", Time:" << Simulator::Now() << std::endl;

        // schedule conga timeout monitor
        if (lb_mode == 3) {  // CONGA
            conga_history_print();
        }
        if (lb_mode == 6) {  // LETFLOW
            letflow_history_print();
        }
        if (lb_mode == 9) {  // CONWEAVE
            conweave_history_print();
        }
        Simulator::Stop(NanoSeconds(1));  // finish soon, stop this schedule (NECESSARY!)
        return;
    }

    Simulator::Schedule(MicroSeconds(100), &stop_simulation_middle);  // check every 100us
}

/**
 * @brief Calculate edge-to-edge delays, TX delays, and bandwidths
 */
void CalculateRoute(Ptr<Node> host) {
    // queue for the BFS.
    vector<Ptr<Node>> q;
    // Distance from the host to each node.
    map<Ptr<Node>, int> dis;
    map<Ptr<Node>, uint64_t> delay;
    map<Ptr<Node>, uint64_t> txDelay;
    map<Ptr<Node>, uint64_t> bw;
    // init BFS.
    q.push_back(host);
    dis[host] = 0;
    delay[host] = 0;
    txDelay[host] = 0;
    bw[host] = 0xfffffffffffffffflu;

    // BFS.
    for (int i = 0; i < (int)q.size(); i++) {
        Ptr<Node> now = q[i];
        int d = dis[now];
        for (auto it = nbr2if[now].begin(); it != nbr2if[now].end(); it++) {
            // skip down link
            if (!it->second.up) continue;
            Ptr<Node> next = it->first;
            // If 'next' have not been visited.
            if (dis.find(next) == dis.end()) {
                dis[next] = d + 1;
                delay[next] = delay[now] + it->second.delay;  // maybe nanoseconds?
                txDelay[next] = txDelay[now] + packet_payload_size * 1000000000lu * 8 /
                                                   it->second.bw;  // maybe nanoseconds?
                bw[next] = std::min(bw[now], it->second.bw);
                // we only enqueue switch, because we do not want packets to go through host as
                // middle point
                if (next->GetNodeType() == 1) {
                    q.push_back(next);
                }
            }
            // if 'now' is on the shortest path from 'next' to 'host'.
            if (d + 1 == dis[next]) {
                nextHop[next][host].push_back(now);
            }
        }
    }
    for (auto it : delay) {
        pairDelay[it.first][host] = it.second;
    }
    for (auto it : txDelay) {
        pairTxDelay[it.first][host] = it.second;
    }
    for (auto it : bw) {
        pairBw[it.first][host] = it.second;
    }
}
void CalculateRoutes(NodeContainer &n) {
    for (int i = 0; i < (int)n.GetN(); i++) {
        Ptr<Node> node = n.Get(i);
        if (node->GetNodeType() == 0) {
            CalculateRoute(node);
        }
    }
}

/**
 * @brief Set the Routing Entries object
 */
void SetRoutingEntries() {
    // For each node.
    for (auto i = nextHop.begin(); i != nextHop.end(); i++) {
        Ptr<Node> node = i->first;
        auto &table = i->second;
        for (auto j = table.begin(); j != table.end(); j++) {
            // The destination node.
            Ptr<Node> dst = j->first;
            if (ringDedicatedRails) {
                for (const auto &rail : ringRails[dst->GetId()]) {
                    auto address = rail.address;
                    for (const auto &next : j->second) {
                        uint32_t port = nbr2if[node][next].idx;
                        if (node->GetNodeType() == 1) {
                            if (next == dst) port = rail.torPort;
                            DynamicCast<SwitchNode>(node)->AddTableEntry(address, port);
                        } else {
                            node->GetObject<RdmaDriver>()->m_rdma->AddTableEntry(address, port);
                        }
                    }
                }
                continue;
            }
            // The IP address of the dst.
            Ipv4Address dstAddr = dst->GetObject<Ipv4>()->GetAddress(1, 0).GetLocal();
            // The next hops towards the dst.
            vector<Ptr<Node>> nexts = j->second;
            for (int k = 0; k < (int)nexts.size(); k++) {
                Ptr<Node> next = nexts[k];
                uint32_t interface = nbr2if[node][next].idx;
                if (node->GetNodeType() == 1)
                    DynamicCast<SwitchNode>(node)->AddTableEntry(dstAddr, interface);
                else {
                    node->GetObject<RdmaDriver>()->m_rdma->AddTableEntry(dstAddr, interface);
                }
            }
        }
    }
}
/**
 * @brief take down the link between a and b, and redo the routing
 */
void TakeDownLink(NodeContainer n, Ptr<Node> a, Ptr<Node> b) {
    if (!nbr2if[a][b].up) return;
    // take down link between a and b
    nbr2if[a][b].up = nbr2if[b][a].up = false;
    nextHop.clear();
    CalculateRoutes(n);
    // clear routing tables
    for (uint32_t i = 0; i < n.GetN(); i++) {
        if (n.Get(i)->GetNodeType() == 1)
            DynamicCast<SwitchNode>(n.Get(i))->ClearTable();
        else
            n.Get(i)->GetObject<RdmaDriver>()->m_rdma->ClearTable();
    }
    DynamicCast<QbbNetDevice>(a->GetDevice(nbr2if[a][b].idx))->TakeDown();
    DynamicCast<QbbNetDevice>(b->GetDevice(nbr2if[b][a].idx))->TakeDown();
    // reset routing table
    SetRoutingEntries();

    // redistribute qp on each host
    for (uint32_t i = 0; i < n.GetN(); i++) {
        if (n.Get(i)->GetNodeType() == 0)
            n.Get(i)->GetObject<RdmaDriver>()->m_rdma->RedistributeQp();
    }
}

uint64_t get_nic_rate(NodeContainer &n) {
    uint64_t avg_nic_rate = 0;
    uint64_t n_servers = 0;
    for (uint32_t i = 0; i < n.GetN(); i++) {
        if (n.Get(i)->GetNodeType() == 0) {
            avg_nic_rate +=
                DynamicCast<QbbNetDevice>(n.Get(i)->GetDevice(1))->GetDataRate().GetBitRate();
            n_servers += 1;
        }
    }
    return avg_nic_rate / n_servers;
}

/************************************************************************/
//                                                                      //
//                                M A I N                               //
//                                                                      //
/************************************************************************/

int main(int argc, char *argv[]) {
    uint32_t *workload_cdf = nullptr;
    clock_t begint, endt;
    begint = clock();
#ifndef PGO_TRAINING
    if (argc > 1)
#else
    if (true)
#endif
    {
        // Read the configuration file
        std::ifstream conf;
#ifndef PGO_TRAINING
        conf.open(argv[1]);
#else
        conf.open(PATH_TO_PGO_CONFIG);
#endif
        while (!conf.eof()) {
            std::string key;
            conf >> key;
            if (key.compare("FLOW_INPUT_FILE") == 0) {
                std::string v;
                conf >> v;
                flow_input_file = v;
                std::cerr << "FLOW_INPUT_FILE\t\t\t" << flow_input_file << "\n";
            } else if (key.compare("CNP_OUTPUT_FILE") == 0) {
                std::string v;
                conf >> v;
                cnp_output_file = v;
                std::cerr << "CNP_OUTPUT_FILE\t\t\t" << cnp_output_file << "\n";
            } else if (key.compare("EST_ERROR_MON_FILE") == 0) {
                std::string v;
                conf >> v;
                est_error_output_file = v;
                std::cerr << "EST_ERROR_MON_FILE\t\t\t" << est_error_output_file << "\n";
            } else if (key.compare("LB_MODE") == 0) {
                uint32_t v;
                conf >> v;
                lb_mode = v;
                std::cerr << "LB_MODE\t\t\t" << lb_mode << "\n";
            } else if (key.compare("SW_MONITORING_INTERVAL") == 0) {
                uint32_t v;
                conf >> v;
                switch_mon_interval = v;
                std::cerr << "SW_MONITORING_INTERVAL\t\t\t" << switch_mon_interval << "\n";
            } else if (key.compare("CONWEAVE_TX_EXPIRY_TIME") == 0) {
                uint32_t v;
                conf >> v;
                conweave_txExpiryTime = Time(MicroSeconds(v));
                std::cerr << "CONWEAVE_TX_EXPIRY_TIME\t\t\t" << conweave_txExpiryTime << "\n";
            } else if (key.compare("CONWEAVE_REPLY_TIMEOUT_EXTRA") == 0) {
                uint32_t v;
                conf >> v;
                conweave_extraReplyDeadline = Time(MicroSeconds(v));
                std::cerr << "CONWEAVE_REPLY_TIMEOUT_EXTRA\t\t\t" << conweave_extraReplyDeadline
                          << "\n";
            } else if (key.compare("CONWEAVE_EXTRA_VOQ_FLUSH_TIME") == 0) {
                uint32_t v;
                conf >> v;
                conweave_extraVOQFlushTime = Time(MicroSeconds(v));
                std::cerr << "CONWEAVE_EXTRA_VOQ_FLUSH_TIME\t\t\t" << conweave_extraVOQFlushTime
                          << "\n";
            } else if (key.compare("CONWEAVE_PATH_PAUSE_TIME") == 0) {
                uint32_t v;
                conf >> v;
                conweave_pathPauseTime = Time(MicroSeconds(v));
                std::cerr << "CONWEAVE_PATH_PAUSE_TIME\t\t\t" << conweave_pathPauseTime << "\n";
            } else if (key.compare("CONWEAVE_DEFAULT_VOQ_WAITING_TIME") == 0) {
                uint32_t v;
                conf >> v;
                conweave_defaultVOQWaitingTime = Time(MicroSeconds(v));
                std::cerr << "CONWEAVE_DEFAULT_VOQ_WAITING_TIME\t\t\t"
                          << conweave_defaultVOQWaitingTime << "\n";
            } else if (key.compare("ENABLE_PFC") == 0) {
                uint32_t v;
                conf >> v;
                enable_pfc = v;
                if (enable_pfc)
                    std::cerr << "ENABLE_PFC\t\t\t"
                              << "Yes"
                              << "\n";
                else
                    std::cerr << "ENABLE_PFC\t\t\t"
                              << "No"
                              << "\n";
            } else if (key.compare("ENABLE_QCN") == 0) {
                uint32_t v;
                conf >> v;
                enable_qcn = v;
                if (enable_qcn)
                    std::cerr << "ENABLE_QCN\t\t\t"
                              << "Yes"
                              << "\n";
                else
                    std::cerr << "ENABLE_QCN\t\t\t"
                              << "No"
                              << "\n";
            } else if (key.compare("USE_DYNAMIC_PFC_THRESHOLD") == 0) {
                uint32_t v;
                conf >> v;
                use_dynamic_pfc_threshold = v;
                if (use_dynamic_pfc_threshold)
                    std::cerr << "USE_DYNAMIC_PFC_THRESHOLD\t"
                              << "Yes"
                              << "\n";
                else
                    std::cerr << "USE_DYNAMIC_PFC_THRESHOLD\t"
                              << "No"
                              << "\n";
            } else if (key.compare("CLAMP_TARGET_RATE") == 0) {
                uint32_t v;
                conf >> v;
                clamp_target_rate = v;
                if (clamp_target_rate)
                    std::cerr << "CLAMP_TARGET_RATE\t\t"
                              << "Yes"
                              << "\n";
                else
                    std::cerr << "CLAMP_TARGET_RATE\t\t"
                              << "No"
                              << "\n";
            } else if (key.compare("PAUSE_TIME") == 0) {
                double v;
                conf >> v;
                pause_time = v;
                std::cerr << "PAUSE_TIME\t\t\t" << pause_time << "\n";
            } else if (key.compare("DATA_RATE") == 0) {
                std::string v;
                conf >> v;
                data_rate = v;
                std::cerr << "DATA_RATE\t\t\t" << data_rate << "\n";
            } else if (key.compare("LINK_DELAY") == 0) {
                std::string v;
                conf >> v;
                link_delay = v;
                std::cerr << "LINK_DELAY\t\t\t" << link_delay << "\n";
            } else if (key.compare("PACKET_PAYLOAD_SIZE") == 0) {
                uint32_t v;
                conf >> v;
                packet_payload_size = v;
                std::cerr << "PACKET_PAYLOAD_SIZE\t\t" << packet_payload_size << "\n";
            } else if (key.compare("L2_CHUNK_SIZE") == 0) {
                uint32_t v;
                conf >> v;
                l2_chunk_size = v;
                std::cerr << "L2_CHUNK_SIZE\t\t\t" << l2_chunk_size << "\n";
            } else if (key.compare("L2_ACK_INTERVAL") == 0) {
                uint32_t v;
                conf >> v;
                l2_ack_interval = v;
                std::cerr << "L2_ACK_INTERVAL\t\t\t" << l2_ack_interval << "\n";
            } else if (key.compare("L2_BACK_TO_ZERO") == 0) {
                uint32_t v;
                conf >> v;
                l2_back_to_zero = v;
                if (l2_back_to_zero)
                    std::cerr << "L2_BACK_TO_ZERO\t\t\t"
                              << "Yes"
                              << "\n";
                else
                    std::cerr << "L2_BACK_TO_ZERO\t\t\t"
                              << "No"
                              << "\n";
            } else if (key.compare("TOPOLOGY_FILE") == 0) {
                std::string v;
                conf >> v;
                topology_file = v;
                std::cerr << "TOPOLOGY_FILE\t\t\t" << topology_file << "\n";
            } else if (key.compare("FLOW_FILE") == 0) {
                std::string v;
                conf >> v;
                flow_file = v;
                std::cerr << "FLOW_FILE\t\t\t" << flow_file << "\n";
            } else if (key == "RING_FIGURE4") {
                if (!(conf >> figure4Trace) || figure4Trace > 2) {
                    std::cerr << "RING_FIGURE4 requires 0, 1 or 2\n";
                    return 1;
                }
            } else if (key == "RING_FIGURE4_LATE") {
                if (!(conf >> figure4Late)) {
                    std::cerr << "RING_FIGURE4_LATE requires 0 or 1\n";
                    return 1;
                }
            } else if (key == "SYMPHONY_TWO_FLOW") {
                if (!(conf >> symphonyTwoFlow)) {
                    std::cerr << "SYMPHONY_TWO_FLOW requires 0 or 1\n";
                    return 1;
                }
            } else if (key == "SYMPHONY_SCOPE_TRACE") {
                if (!(conf >> symphonyScopeTrace)) return 1;
            } else if (key == "SYMPHONY_MODE") {
                if (!(conf >> symphonyMode) || symphonyMode > 3) {
                    std::cerr << "SYMPHONY_MODE requires 0 (off), 1 (log), 2 (original), or 3 (egress-aware)\n";
                    return 1;
                }
            } else if (key == "SYMPHONY_WARMUP_PACKETS") {
                int64_t packets;
                if (!(conf >> packets) || packets < 0 || packets > UINT32_MAX) {
                    std::cerr << "SYMPHONY_WARMUP_PACKETS requires a nonnegative 32-bit count\n";
                    return 1;
                }
                symphonyWarmup = packets;
            } else if (key == "SYMPHONY_ADAPTIVE_ALPHA") {
                if (!(conf >> symphonyAdaptiveAlpha)) {
                    std::cerr << "SYMPHONY_ADAPTIVE_ALPHA requires 0 or 1\n";
                    return 1;
                }
            } else if (key == "SYMPHONY_MIN_SAMPLE_PACKETS") {
                int64_t packets;
                if (!(conf >> packets) || packets < 0 || packets > UINT32_MAX) {
                    std::cerr << "SYMPHONY_MIN_SAMPLE_PACKETS requires a nonnegative 32-bit count\n";
                    return 1;
                }
                symphonyMinSamples = packets;
            } else if (key == "SYMPHONY_GAIN") {
                if (!(conf >> symphonyGain) || !(symphonyGain > 0 && symphonyGain <= 1)) {
                    std::cerr << "SYMPHONY_GAIN requires a number in (0, 1]\n";
                    return 1;
                }
            } else if (key == "PROFILE_F_QUEUE_ONLY_NOTIFY") {
                if (!(conf >> profileFQueueOnlyNotify)) return 1;
            } else if (key == "PROFILE_F_PILOT_DETAIL") {
                if (!(conf >> profileFPilotDetail)) return 1;
            } else if (key == "PROFILE_F_MARK_PROVENANCE") {
                if (!(conf >> profileFMarkProvenance)) return 1;
            } else if (key == "PROFILE_F_BACKGROUND") {
                if (!(conf >> profileFBackgroundFile)) return 1;
            } else if (key == "RING_ALL_REDUCE") {
                if (!(conf >> ringAllReduce)) {
                    std::cerr << "RING_ALL_REDUCE requires 0 or 1\n";
                    return 1;
                }
            } else if (key == "RING_EGRESS_TRACE") {
                if (!(conf >> ringEgressTrace)) {
                    std::cerr << "RING_EGRESS_TRACE requires 0 or 1\n";
                    return 1;
                }
            } else if (key == "RING_JOB_START_OFFSET_NS") {
                std::vector<int64_t> args;
                if (!ReadRingArguments(conf, args) || args.size() != 2 ||
                    args.front() > UINT32_MAX ||
                    !ringJobStartOffsets.emplace(args[0], args[1]).second) {
                    std::cerr << "RING_JOB_START_OFFSET_NS requires job ID and offset in ns\n";
                    return 1;
                }
            } else if (key == "RING_CHUNK_BYTES") {
                int64_t bytes;
                if (!(conf >> bytes) || bytes <= 0 || bytes > UINT32_MAX) {
                    std::cerr << "RING_CHUNK_BYTES requires a positive 32-bit byte count\n";
                    return 1;
                }
                ringChunkBytes = bytes;
            } else if (key == "RING_PROCESSING_DELAY_NS" || key == "RING_JOB_DELAY_NS" ||
                       key == "RING_JOB") {
                std::vector<int64_t> args;
                if (!ReadRingArguments(conf, args)) {
                    std::cerr << key << " requires nonnegative integers\n";
                    return 1;
                }
                if (key == "RING_PROCESSING_DELAY_NS" && !args.empty()) {
                    ringDelayNs = std::move(args);
                } else if (key == "RING_JOB_DELAY_NS" && args.size() >= 2 &&
                           args.front() <= UINT32_MAX &&
                           ringJobDelays.emplace(args.front(),
                               std::vector<int64_t>(args.begin() + 1, args.end())).second) {
                    // Resolve the job index after all directives have been read.
                } else if (key == "RING_JOB" && args.size() >= 3 && args.back() > 0 &&
                           *std::max_element(args.begin(), args.end()) <= UINT32_MAX) {
                    RingJob job;
                    job.hosts.assign(args.begin(), args.end() - 1);
                    job.iterations = args.back();
                    ringJobs.push_back(std::move(job));
                } else {
                    std::cerr << "Invalid arguments or duplicate job delay for " << key << '\n';
                    return 1;
                }
            } else if (key.compare("FLOWGEN_START_TIME") == 0) {
                double v;
                conf >> v;
                flowgen_start_time = v;
                qlen_mon_start = v;
                qlen_mon_end = v;
                cnp_mon_start = v;
                irn_mon_start = v;
                std::cerr << "FLOWGEN_START_TIME\t\t" << flowgen_start_time << "\n";
            } else if (key.compare("FLOWGEN_STOP_TIME") == 0) {
                double v;
                conf >> v;
                flowgen_stop_time = v;
                std::cerr << "FLOWGEN_STOP_TIME\t\t" << flowgen_stop_time << "\n";
            } else if (key.compare("ALPHA_RESUME_INTERVAL") == 0) {
                double v;
                conf >> v;
                alpha_resume_interval = v;
                std::cerr << "ALPHA_RESUME_INTERVAL\t\t" << alpha_resume_interval << "\n";
            } else if (key.compare("RP_TIMER") == 0) {
                double v;
                conf >> v;
                rp_timer = v;
                std::cerr << "RP_TIMER\t\t\t" << rp_timer << "\n";
            } else if (key.compare("EWMA_GAIN") == 0) {
                double v;
                conf >> v;
                ewma_gain = v;
                std::cerr << "EWMA_GAIN\t\t\t" << ewma_gain << "\n";
            } else if (key.compare("FAST_RECOVERY_TIMES") == 0) {
                uint32_t v;
                conf >> v;
                fast_recovery_times = v;
                std::cerr << "FAST_RECOVERY_TIMES\t\t" << fast_recovery_times << "\n";
            } else if (key.compare("RATE_AI") == 0) {
                std::string v;
                conf >> v;
                rate_ai = v;
                std::cerr << "RATE_AI\t\t\t\t" << rate_ai << "\n";
            } else if (key.compare("RATE_HAI") == 0) {
                std::string v;
                conf >> v;
                rate_hai = v;
                std::cerr << "RATE_HAI\t\t\t" << rate_hai << "\n";
            } else if (key.compare("ERROR_RATE_PER_LINK") == 0) {
                double v;
                conf >> v;
                error_rate_per_link = v;
                std::cerr << "ERROR_RATE_PER_LINK\t\t" << error_rate_per_link << "\n";
            } else if (key.compare("CC_MODE") == 0) {
                conf >> cc_mode;
                std::cerr << "CC_MODE\t\t" << cc_mode << '\n';
            } else if (key.compare("RATE_DECREASE_INTERVAL") == 0) {
                double v;
                conf >> v;
                rate_decrease_interval = v;
                std::cerr << "RATE_DECREASE_INTERVAL\t\t" << rate_decrease_interval << "\n";
            } else if (key.compare("MIN_RATE") == 0) {
                conf >> min_rate;
                std::cerr << "MIN_RATE\t\t" << min_rate << "\n";
            } else if (key.compare("FCT_OUTPUT_FILE") == 0) {
                conf >> fct_output_file;
                std::cerr << "FCT_OUTPUT_FILE\t\t" << fct_output_file << '\n';
            } else if (key.compare("HAS_WIN") == 0) {
                conf >> has_win;
                std::cerr << "HAS_WIN\t\t" << has_win << "\n";
            } else if (key.compare("GLOBAL_T") == 0) {
                conf >> global_t;
                std::cerr << "GLOBAL_T\t\t" << global_t << '\n';
            } else if (key.compare("MI_THRESH") == 0) {
                conf >> mi_thresh;
                std::cerr << "MI_THRESH\t\t" << mi_thresh << '\n';
            } else if (key.compare("VAR_WIN") == 0) {
                uint32_t v;
                conf >> v;
                var_win = v;
                std::cerr << "VAR_WIN\t\t" << v << '\n';
            } else if (key.compare("FAST_REACT") == 0) {
                uint32_t v;
                conf >> v;
                fast_react = v;
                std::cerr << "FAST_REACT\t\t" << v << '\n';
            } else if (key.compare("U_TARGET") == 0) {
                conf >> u_target;
                std::cerr << "U_TARGET\t\t" << u_target << '\n';
            } else if (key.compare("INT_MULTI") == 0) {
                conf >> int_multi;
                std::cerr << "INT_MULTI\t\t\t\t" << int_multi << '\n';
            } else if (key.compare("RATE_BOUND") == 0) {
                uint32_t v;
                conf >> v;
                rate_bound = v;
                std::cerr << "RATE_BOUND\t\t" << rate_bound << '\n';
            } else if (key.compare("DCTCP_RATE_AI") == 0) {
                conf >> dctcp_rate_ai;
                std::cerr << "DCTCP_RATE_AI\t\t\t\t" << dctcp_rate_ai << "\n";
            } else if (key.compare("PFC_OUTPUT_FILE") == 0) {
                conf >> pfc_output_file;
                std::cerr << "PFC_OUTPUT_FILE\t\t\t\t" << pfc_output_file << '\n';
            } else if (key == "RING_FIGURE4_MANIFEST") {
                conf >> figure4Manifest;
            } else if (key == "RING_FIGURE4_WORKLOAD_TELEMETRY") {
                conf >> figure4WorkloadTelemetry;
            } else if (key == "RING_FIGURE4_FIXED_WINDOW") {
                conf >> figure4FixedWindow;
            } else if (key == "RING_DEDICATED_RAILS") {
                conf >> ringDedicatedRails;
            } else if (key.compare("LINK_DOWN") == 0) {
                conf >> link_down_time >> link_down_A >> link_down_B;
                std::cerr << "LINK_DOWN\t\t\t\t" << link_down_time << ' ' << link_down_A << ' '
                          << link_down_B << '\n';
            } else if (key.compare("KMAX_MAP") == 0) {
                int n_k;
                conf >> n_k;
                std::cerr << "KMAX_MAP\t\t\t\t";
                for (int i = 0; i < n_k; i++) {
                    uint64_t rate;
                    uint32_t k;
                    conf >> rate >> k;
                    rate2kmax[rate] = k;
                    std::cerr << ' ' << rate << ' ' << k;
                }
                std::cerr << '\n';
            } else if (key.compare("KMIN_MAP") == 0) {
                int n_k;
                conf >> n_k;
                std::cerr << "KMIN_MAP\t\t\t\t";
                for (int i = 0; i < n_k; i++) {
                    uint64_t rate;
                    uint32_t k;
                    conf >> rate >> k;
                    rate2kmin[rate] = k;
                    std::cerr << ' ' << rate << ' ' << k;
                }
                std::cerr << '\n';
            } else if (key.compare("PMAX_MAP") == 0) {
                int n_k;
                conf >> n_k;
                std::cerr << "PMAX_MAP\t\t\t\t";
                for (int i = 0; i < n_k; i++) {
                    uint64_t rate;
                    double p;
                    conf >> rate >> p;
                    rate2pmax[rate] = p;
                    std::cerr << ' ' << rate << ' ' << p;
                }
                std::cerr << '\n';
            } else if (key.compare("BUFFER_SIZE") == 0) {
                conf >> buffer_size;
                std::cerr << "BUFFER_SIZE\t\t\t\t" << buffer_size << '\n';
            } else if (key.compare("QLEN_MON_FILE") == 0) {
                conf >> qlen_mon_file;
                std::cerr << "QLEN_MON_FILE\t\t\t\t" << qlen_mon_file << '\n';
            } else if (key.compare("VOQ_MON_FILE") == 0) {
                conf >> voq_mon_file;
                std::cerr << "VOQ_MON_FILE\t\t\t\t" << voq_mon_file << '\n';
            } else if (key.compare("VOQ_MON_DETAIL_FILE") == 0) {
                conf >> voq_mon_detail_file;
                std::cerr << "VOQ_MON_DETAIL_FILE\t\t\t\t" << voq_mon_detail_file << '\n';
            } else if (key.compare("UPLINK_MON_FILE") == 0) {
                conf >> uplink_mon_file;
                std::cerr << "UPLINK_MON_FILE\t\t\t\t" << uplink_mon_file << '\n';
            } else if (key.compare("CONN_MON_FILE") == 0) {
                conf >> conn_mon_file;
                std::cerr << "CONN_MON_FILE\t\t\t\t" << conn_mon_file << '\n';
            } else if (key.compare("QLEN_MON_START") == 0) {
                conf >> qlen_mon_start;
                std::cerr << "QLEN_MON_START\t\t\t\t" << qlen_mon_start << '\n';
            } else if (key.compare("QLEN_MON_END") == 0) {
                conf >> qlen_mon_end;
                std::cerr << "QLEN_MON_END\t\t\t\t" << qlen_mon_end << '\n';
            } else if (key.compare("MULTI_RATE") == 0) {
                int v;
                conf >> v;
                multi_rate = v;
                std::cerr << "MULTI_RATE\t\t\t\t" << multi_rate << '\n';
            } else if (key.compare("SAMPLE_FEEDBACK") == 0) {
                int v;
                conf >> v;
                sample_feedback = v;
                std::cerr << "SAMPLE_FEEDBACK\t\t\t\t" << sample_feedback << '\n';
            } else if (key.compare("LOAD") == 0) {
                double v;
                conf >> v;
                load = v;
                std::cerr << "LOAD\t\t\t" << load << "\n";
            } else if (key.compare("ENABLE_IRN") == 0) {
                bool v;
                conf >> v;
                enable_irn = v;
                std::cerr << "ENABLE_IRN\t\t" << enable_irn << "\n";
            } else if (key.compare("RANDOM_SEED") == 0) {
                int v;
                conf >> v;
                random_seed = v;
                std::cerr << "RANDOM_SEED\t\t\t" << random_seed << "\n";
            }

            fflush(stdout);
        }
        conf.close();

    } else {
        std::cerr << "Error: require a config file\n";
        fflush(stdout);
        return 1;
    }

    /******************* READING CONFIG FILE IS DONE ***********************/

    /**
     * Activate ns3 logging
     */
    LogComponentEnable("GENERIC_SIMULATION", LOG_LEVEL_DEBUG);

    /**
     * @brief Random seed setup
     */
    NS_LOG_INFO("Initialize random seed: " << random_seed);
    srand((unsigned)random_seed);
    SeedManager::SetSeed(random_seed);

    /**
     * @brief PFC/QCN setup
     */
    bool dynamicth = use_dynamic_pfc_threshold;
    Config::SetDefault("ns3::QbbNetDevice::PauseTime", UintegerValue(pause_time));
    Config::SetDefault("ns3::QbbNetDevice::QcnEnabled", BooleanValue(enable_qcn));
    Config::SetDefault("ns3::QbbNetDevice::DynamicThreshold", BooleanValue(dynamicth));
    Config::SetDefault("ns3::QbbNetDevice::QbbEnabled", BooleanValue(enable_pfc));

    if ((profileFMarkProvenance || profileFPilotDetail) && profileFBackgroundFile.empty()) {
        std::cerr << "Profile F marking and pilot detail traces require Profile F\n";
        return 1;
    }
    if (!profileFBackgroundFile.empty() &&
        (!ringAllReduce || figure4Trace || ringDedicatedRails || symphonyTwoFlow ||
         cc_mode != 1 || enable_irn || !enable_qcn || link_down_time > 0 ||
         (lb_mode != 0 && lb_mode != 9) || (symphonyMode != 0 && symphonyMode != 2))) {
        std::cerr << "Profile F requires ECMP or ConWeave with DCQCN and optional original Symphony\n";
        return 1;
    }
    if ((figure4Trace && (!ringAllReduce || enable_irn || lb_mode != 0 || cc_mode != 1 ||
                          !enable_qcn)) || (figure4Late && (!figure4Trace || symphonyMode != 2))) {
        std::cerr << "Figure 4 requires ring ECMP/DCQCN and IRN disabled\n";
        return 1;
    }
    if ((!figure4Manifest.empty() || figure4WorkloadTelemetry || figure4FixedWindow) &&
        (!figure4Trace || !ringDedicatedRails || figure4Late)) {
        std::cerr << "Figure 4 workload options require dedicated rails and immediate start\n";
        return 1;
    }
    if (ringDedicatedRails && (!figure4Trace || link_down_time > 0)) {
        std::cerr << "Dedicated rails require Figure 4 without LINK_DOWN\n";
        return 1;
    }
    if (symphonyTwoFlow && (ringAllReduce || symphonyAdaptiveAlpha || enable_irn)) {
        std::cerr << "The two-flow experiment requires fixed alpha with ring scheduling and IRN disabled\n";
        return 1;
    }
    // Profile F permits ConWeave to consume the same CE bit as the receiver.
    const bool profileFSharedCe = !profileFBackgroundFile.empty() && lb_mode == 9 &&
                                  symphonyMode == 2;
    if (profileFQueueOnlyNotify && !profileFSharedCe) {
        std::cerr << "PROFILE_F_QUEUE_ONLY_NOTIFY requires Profile F with ConWeave and original Symphony\n";
        return 1;
    }
    if (symphonyMode != 0 || symphonyTwoFlow) {
        if ((!ringAllReduce && !symphonyTwoFlow) || (lb_mode != 0 && !profileFSharedCe) ||
            cc_mode != 1 || !enable_qcn || l2_ack_interval == 0 || packet_payload_size == 0) {
            std::cerr << "Symphony requires a ring or two-flow scenario with DCQCN, QCN, ACKs, "
                         "positive payload size, and ECMP or Profile F shared CE\n";
            return 1;
        }
        std::cout << "SYMPHONY_CONFIG mode=" << symphonyMode << " gain=" << symphonyGain
                  << " warmup_packets=" << symphonyWarmup
                  << " adaptive_alpha=" << symphonyAdaptiveAlpha
                  << " min_sample_packets=" << symphonyMinSamples << '\n';
        RdmaHw::EnableSymphonyTrace(figure4Trace == 2 ? 10000 : figure4Trace ? 0 : UINT32_MAX);
    }

    if (symphonyScopeTrace && !figure4Trace) {
        std::cerr << "Symphony scope telemetry requires Figure 4 tracing\n";
        return 1;
    }
    if (symphonyScopeTrace || profileFPilotDetail) {
        RdmaHw::SetSymphonyScopeTrace(true);
        RdmaHw::EnableSymphonyTrace();
    }

    if (cc_mode != 1 && lb_mode == 9) {
        std::cout << "Currently, ConWeave supports only DCQCN congestion control for RDMA. \nIf "
                     "you want to extend, the reordering delay at DstTor must be considered."
                  << std::endl;
        exit(1);
    }

    /**
     * @brief INT header setup
     */
    IntHop::multi = int_multi;
    // IntHeader::mode
    if (cc_mode == 7)  // timely, use ts
        IntHeader::mode = 1;
    else if (cc_mode == 3)  // hpcc, use int
        IntHeader::mode = 0;
    else  // others, no extra header
        IntHeader::mode = 5;

    /**
     * @brief open topology config, input-flows config.
     */
    topof.open(topology_file.c_str());
    uint32_t node_num, switch_num, link_num;
    topof >> node_num >> switch_num >> link_num;
    if (!ringAllReduce && !symphonyTwoFlow) {
        flowf.open(flow_file.c_str());
        flowf >> flow_num;
    }

    /*-------Parameter of Settings-------*/
    Settings::node_num = node_num;
    Settings::host_num = node_num - switch_num;
    Settings::switch_num = switch_num;
    Settings::lb_mode = lb_mode;
    Settings::packet_payload = packet_payload_size;
    // Settings::MTU = packet_payload_size + 48;  // for simplicity
    /*------------------------------------*/

    std::vector<uint32_t> node_type(node_num, 0);
    for (uint32_t i = 0; i < switch_num; i++) {
        uint32_t sid;
        topof >> sid;
        node_type[sid] = 1;
    }
    for (uint32_t i = 0; i < node_num; i++) {
        if (node_type[i] == 0)
            n.Add(CreateObject<Node>());
        else {
            Ptr<SwitchNode> sw = CreateObject<SwitchNode>();
            n.Add(sw);
            sw->SetAttribute("EcnEnabled", BooleanValue(enable_qcn));
            sw->SetSymphonyScopeTrace(symphonyScopeTrace);
            if (figure4Trace) {
                const int64_t activation = Seconds(flowgen_start_time).GetNanoSeconds() +
                                           (figure4Late ? 500000000 : 0);
                sw->ConfigureFigure4(figure4Trace, activation);
                sw->SetEcmpSeed(uint32_t(random_seed) * 0x9e3779b9u + sw->GetId());
                figure4Switches.push_back(sw);
            }
            if (symphonyMode != 0) {
                sw->ConfigureSymphony(symphonyMode, packet_payload_size, symphonyWarmup, symphonyGain,
                                      symphonyAdaptiveAlpha, symphonyMinSamples);
                symphonySwitches.push_back(sw);
            }
        }
    }
    NS_LOG_INFO("Create nodes.");

    /*----------------------------------------*/

    InternetStackHelper internet;
    internet.Install(n);  // aggregate ipv4, ipv6, udp, tcp, etc

    //
    // Assign IP to each server
    //
    for (uint32_t i = 0; i < node_num; i++) {
        if (n.Get(i)->GetNodeType() == 0) {  // is server
            serverAddress.resize(i + 1);
            serverAddress[i] = Settings::node_id_to_ip(i);
        }
    }

    NS_LOG_INFO("Create channels.");

    //
    // Explicitly create the channels required by the topology.
    //
    Ptr<RateErrorModel> rem = CreateObject<RateErrorModel>();
    Ptr<UniformRandomVariable> uv = CreateObject<UniformRandomVariable>();
    rem->SetRandomVariable(uv);
    uv->SetStream(50);
    rem->SetAttribute("ErrorRate", DoubleValue(error_rate_per_link));
    rem->SetAttribute("ErrorUnit", StringValue("ERROR_UNIT_PACKET"));

    pfc_file = fopen(pfc_output_file.c_str(), "w");

    if (ringDedicatedRails) ringRails.resize(node_num);
    QbbHelper qbb;
    Ipv4AddressHelper ipv4;
    std::vector<std::pair<uint32_t, uint32_t>> link_pairs;  // src, dst link pairs
    for (uint32_t i = 0; i < link_num; i++) {
        uint32_t src, dst;
        std::string data_rate, link_delay;
        double error_rate;
        topof >> src >> dst >> data_rate >> link_delay >> error_rate;

        /** ASSUME: fixed one-hop delay across network */
        assert(std::to_string(one_hop_delay) + "ns" == link_delay);

        link_pairs.push_back(std::make_pair(src, dst));
        Ptr<Node> snode = n.Get(src), dnode = n.Get(dst);

        qbb.SetDeviceAttribute("DataRate", StringValue(data_rate));
        qbb.SetChannelAttribute("Delay", StringValue(link_delay));

        if (error_rate > 0) {
            Ptr<RateErrorModel> rem = CreateObject<RateErrorModel>();
            Ptr<UniformRandomVariable> uv = CreateObject<UniformRandomVariable>();
            rem->SetRandomVariable(uv);
            uv->SetStream(50);
            rem->SetAttribute("ErrorRate", DoubleValue(error_rate));
            rem->SetAttribute("ErrorUnit", StringValue("ERROR_UNIT_PACKET"));
            qbb.SetDeviceAttribute("ReceiveErrorModel", PointerValue(rem));
        } else {
            qbb.SetDeviceAttribute("ReceiveErrorModel", PointerValue(rem));
        }

        fflush(stdout);

        // Assigne server IP
        // Note: this should be before the automatic assignment below (ipv4.Assign(d)),
        // because we want our IP to be the primary IP (first in the IP address list),
        // so that the global routing is based on our IP
        NetDeviceContainer d = qbb.Install(snode, dnode);
        if (ringDedicatedRails && (snode->GetNodeType() == 0 || dnode->GetNodeType() == 0)) {
            const uint32_t hostEnd = snode->GetNodeType() == 0 ? 0 : 1;
            auto hostDevice = d.Get(hostEnd);
            auto torDevice = d.Get(1 - hostEnd);
            const uint32_t host = hostDevice->GetNode()->GetId();
            const uint32_t tor = torDevice->GetNode()->GetId();
            auto &rails = ringRails[host];
            if (torDevice->GetNode()->GetNodeType() != 1 || rails.size() >= kRingRails ||
                (!rails.empty() && (rails.front().tor != tor || rails.front().rate != data_rate))) {
                std::cerr << "Invalid dedicated rail layout for host " << host << '\n';
                return 1;
            }
            Ipv4Address address(serverAddress[host].Get() + rails.size());
            auto ip = hostDevice->GetNode()->GetObject<Ipv4>();
            const auto interface = ip->AddInterface(hostDevice);
            ip->AddAddress(interface, Ipv4InterfaceAddress(address, Ipv4Mask(0xff000000)));
            rails.push_back({address, hostDevice->GetIfIndex(), tor, torDevice->GetIfIndex(),
                             data_rate});
        } else if (snode->GetNodeType() == 0) {
            Ptr<Ipv4> ipv4 = snode->GetObject<Ipv4>();
            ipv4->AddInterface(d.Get(0));
            ipv4->AddAddress(1, Ipv4InterfaceAddress(serverAddress[src], Ipv4Mask(0xff000000)));
        }
        if (!ringDedicatedRails && dnode->GetNodeType() == 0) {
            Ptr<Ipv4> ipv4 = dnode->GetObject<Ipv4>();
            ipv4->AddInterface(d.Get(1));
            ipv4->AddAddress(1, Ipv4InterfaceAddress(serverAddress[dst], Ipv4Mask(0xff000000)));
        }

        // used to create a graph of the topology
        nbr2if[snode][dnode].idx = DynamicCast<QbbNetDevice>(d.Get(0))->GetIfIndex();
        nbr2if[snode][dnode].up = true;
        nbr2if[snode][dnode].delay =
            DynamicCast<QbbChannel>(DynamicCast<QbbNetDevice>(d.Get(0))->GetChannel())
                ->GetDelay()
                .GetTimeStep();
        nbr2if[snode][dnode].bw = DynamicCast<QbbNetDevice>(d.Get(0))->GetDataRate().GetBitRate();
        nbr2if[dnode][snode].idx = DynamicCast<QbbNetDevice>(d.Get(1))->GetIfIndex();
        nbr2if[dnode][snode].up = true;
        nbr2if[dnode][snode].delay =
            DynamicCast<QbbChannel>(DynamicCast<QbbNetDevice>(d.Get(1))->GetChannel())
                ->GetDelay()
                .GetTimeStep();
        nbr2if[dnode][snode].bw = DynamicCast<QbbNetDevice>(d.Get(1))->GetDataRate().GetBitRate();

        // This is just to set up the connectivity between nodes. The IP addresses are useless
        char ipstring[16];
        Ipv4Address x;
        sprintf(ipstring, "10.%d.%d.0", i / 254 + 1, i % 254 + 1);
        ipv4.SetBase(ipstring, "255.255.255.0");
        ipv4.Assign(d);

        // setup PFC trace
        DynamicCast<QbbNetDevice>(d.Get(0))->TraceConnectWithoutContext(
            "QbbPfc", MakeBoundCallback(&get_pfc, pfc_file, DynamicCast<QbbNetDevice>(d.Get(0))));
        DynamicCast<QbbNetDevice>(d.Get(1))->TraceConnectWithoutContext(
            "QbbPfc", MakeBoundCallback(&get_pfc, pfc_file, DynamicCast<QbbNetDevice>(d.Get(1))));
    }

    std::cout << "(AVG) NIC RATE: " << get_nic_rate(n) << std::endl;

    /* Get IP address <-> NodeID pairs */
    Ipv4Address empty_ip;
    for (uint32_t i = 0; i < node_num; ++i) {
        if (n.Get(i)->GetNodeType() == 0) {  // is server
            if (serverAddress[i].IsEqual(empty_ip)) {
                printf("XXX ERROR %d\n", i);
                printf("size of serverAddress: %lu", serverAddress.size());
                NS_FATAL_ERROR("An end-host belongs to no link");
            }
        }
        if (ringDedicatedRails && n.Get(i)->GetNodeType() == 0) {
            if (ringRails[i].size() != kRingRails) {
                std::cerr << "Expected four dedicated rails for host " << i << '\n';
                return 1;
            }
            for (const auto &rail : ringRails[i]) Settings::hostIp2IdMap[rail.address.Get()] = i;
        }
        Settings::hostId2IpMap[i] = serverAddress[i].Get();
        Settings::hostIp2IdMap[serverAddress[i].Get()] = i;
    }

    // config switch
    for (uint32_t i = 0; i < node_num; i++) {
        if (n.Get(i)->GetNodeType() == 1) {  // is switch
            Ptr<SwitchNode> sw = DynamicCast<SwitchNode>(n.Get(i));
            uint32_t shift = 3;  // by default 1/8
            for (uint32_t j = 1; j < sw->GetNDevices(); j++) {
                Ptr<QbbNetDevice> dev = DynamicCast<QbbNetDevice>(sw->GetDevice(j));
                // set ecn
                uint64_t rate = dev->GetDataRate().GetBitRate();
                NS_ASSERT_MSG(rate2kmin.find(rate) != rate2kmin.end(),
                              "must set kmin for each link speed");
                NS_ASSERT_MSG(rate2kmax.find(rate) != rate2kmax.end(),
                              "must set kmax for each link speed");
                NS_ASSERT_MSG(rate2pmax.find(rate) != rate2pmax.end(),
                              "must set pmax for each link speed");
                assert(rate2kmin.find(rate) != rate2kmin.end() &&
                       rate2kmax.find(rate) != rate2kmax.end() &&
                       rate2pmax.find(rate) != rate2pmax.end());
                sw->m_mmu->ConfigEcn(j, rate2kmin[rate], rate2kmax[rate], rate2pmax[rate]);
                // set pfc
                uint64_t delay =
                    DynamicCast<QbbChannel>(dev->GetChannel())->GetDelay().GetTimeStep();
                uint32_t headroom = rate * delay / 8 / 1000000000 * 2 + 2 * sw->m_mmu->MTU;
                sw->m_mmu->ConfigHdrm(j, headroom);
            }
            sw->m_mmu->ConfigNPort(sw->GetNDevices() - 1);
            sw->m_mmu->ConfigBufferSize(buffer_size * 1024 *
                                        1024);  // default 0, specify in run.py!!
            sw->m_mmu->node_id = sw->GetId();
            NS_LOG_INFO("Node %u : Broadcom switch (%u ports / %gMB MMU)\n" %
                        (i, sw->GetNDevices() - 1, sw->m_mmu->GetMmuBufferBytes() / 1000000.));
        }
    }

    fct_output = fopen(fct_output_file.c_str(), "w");
    flow_input_stream = fopen(flow_input_file.c_str(), "w");
    if (cc_mode == 1) {
        cnp_output = fopen(cnp_output_file.c_str(), "w");
    }

    /**
     * @brief install RDMA driver (Mellanox parameters)
     *
     * [ClampTargetRate]clamp_tgt_rate (false) - when receiving a CNP, the target rate is always
     *updated to be the current rate
     *[-]clamp_tgt_rate_after_time_inc (true) - when receiving a CNP, the target rate is updated to
     *be the current rate also if the last rate increase event was due to the timer, and not only
     *due to the byte counter
     * [-]initial_alpha_value(1023) -
     * [RateDecreaseInterval]rate_reduce_monitor_period(4) - Minimal interval for rate reduction for
     *a flow. If a CNP is received during the interval, the flow rate is reduced at the beginning of
     *the next rate_reduce_monitor_period interval to (1-Alpha/Gd)*CurrentRate. rpg_gd is given as
     *log2(Gd), where Gd may only be powers of 2.
     * [-]rpg_gd(11) - If an CNP is received, the flow rate is reduced at the beginning of the next
     *rate_reduce_monitor_period interval to (1-Alpha/Gd)*CurrentRate.
     * -> in this simulator, (alpha / gd) ~ 0.5 setup, initially. We do not need rpg_gd parameter.
     * [RateOnFirstCnp]rate_to_set_on_first_cnp(0) - The rate that is set for the flow, upon first
     *CNP received, in Mbps. [RPTimer]rpg_time_reset(300us) - Time counter for rate increase event
     *[FastRecoveryTimes]rpg_threshold(1) - Number of rate increase events for switching between
     *Fast Recovery, Active Increase, Hyper Active Increase modes.
     * [AlphaResumInterval]dce_tcp_rtt(1) - Window for sampling of moving average calculation of
     *alpha
     * [-]dce_tcp_g(1019) - Weight of the new sampling in moving average calculation of alpha
     * [-]rpg_byte_reset(32767) - Byte counter for rate increase event
     * [-]rpg_min_dec_fac(50) -  Maximal factor by which the rate can be reduced (2 means that the
     *new rate can be divided by 2 at maximum)
     */

    // manually type BDP
    std::map<std::string, uint32_t> topo2bdpMap;
    topo2bdpMap[std::string("leaf_spine_128_100G_OS2")] = 104000;  // RTT=8320
    topo2bdpMap[std::string("fat_k8_100G_OS2")] = 156000;      // RTT=12480 --> all 100G links

    // topology_file
    bool found_topo2bdpMap = false;
    uint32_t irn_bdp_lookup = 0;
    for (auto pair : topo2bdpMap) {
        if (topology_file.find(pair.first) !=
            std::string::npos) {  // if topology file string includes the word
            irn_bdp_lookup = pair.second;
            found_topo2bdpMap = true;
            break;
        }
    }
    if (!symphonyTwoFlow && !figure4Trace && profileFBackgroundFile.empty() && !found_topo2bdpMap) {
        std::cout << __FILE__ << "(" << __LINE__ << ")"
                  << " ERROR - topo2bdpMap has no matched item with " << topology_file << std::endl;
        assert(false);
    }

    // rdmaHw config
    for (uint32_t i = 0; i < node_num; i++) {
        if (n.Get(i)->GetNodeType() == 0) {  // is server
            // create RdmaHw
            Ptr<RdmaHw> rdmaHw = CreateObject<RdmaHw>();
            rdmaHw->SetAttribute("ClampTargetRate", BooleanValue(clamp_target_rate));
            rdmaHw->SetAttribute("AlphaResumInterval", DoubleValue(alpha_resume_interval));
            rdmaHw->SetAttribute("RPTimer", DoubleValue(rp_timer));
            rdmaHw->SetAttribute("FastRecoveryTimes", UintegerValue(fast_recovery_times));
            rdmaHw->SetAttribute("EwmaGain", DoubleValue(ewma_gain));
            rdmaHw->SetAttribute("RateAI", DataRateValue(DataRate(rate_ai)));
            rdmaHw->SetAttribute("RateHAI", DataRateValue(DataRate(rate_hai)));
            rdmaHw->SetAttribute("L2BackToZero", BooleanValue(l2_back_to_zero));
            rdmaHw->SetAttribute("L2ChunkSize", UintegerValue(l2_chunk_size));
            rdmaHw->SetAttribute("L2AckInterval", UintegerValue(l2_ack_interval));
            rdmaHw->SetAttribute("CcMode", UintegerValue(cc_mode));
            rdmaHw->SetAttribute("RateDecreaseInterval", DoubleValue(rate_decrease_interval));
            rdmaHw->SetAttribute("MinRate", DataRateValue(DataRate(min_rate)));
            rdmaHw->SetAttribute("Mtu", UintegerValue(packet_payload_size));
            rdmaHw->SetAttribute("MiThresh", UintegerValue(mi_thresh));
            rdmaHw->SetAttribute("VarWin", BooleanValue(var_win));
            rdmaHw->SetAttribute("FastReact", BooleanValue(fast_react));
            rdmaHw->SetAttribute("MultiRate", BooleanValue(multi_rate));
            rdmaHw->SetAttribute("SampleFeedback", BooleanValue(sample_feedback));
            rdmaHw->SetAttribute("TargetUtil", DoubleValue(u_target));
            rdmaHw->SetAttribute("RateBound", BooleanValue(rate_bound));
            rdmaHw->SetAttribute("DctcpRateAI", DataRateValue(DataRate(dctcp_rate_ai)));
            rdmaHw->SetAttribute("IrnEnable", BooleanValue(enable_irn));
            // topo2bdpMap (e.g., longest BDP 25000: 8us * 25Gbps)
            rdmaHw->SetAttribute("IrnRtoHigh", TimeValue(MicroSeconds(320)));  // 1930
            rdmaHw->SetAttribute("IrnRtoLow", TimeValue(MicroSeconds(100)));   // 454
            rdmaHw->SetAttribute("IrnBdp", UintegerValue(irn_bdp_lookup));
            // Monitoring CNP Marking frequency of DCQCN
            if (cc_mode == 1) {
                Simulator::Schedule(NanoSeconds(cnp_mon_start), &cnp_freq_monitoring, cnp_output,
                                    rdmaHw);
            }

            // create and install RdmaDriver
            Ptr<RdmaDriver> rdma = CreateObject<RdmaDriver>();
            Ptr<Node> node = n.Get(i);
            rdma->SetNode(node);
            rdma->SetRdmaHw(rdmaHw);

            node->AggregateObject(rdma);
            rdma->Init();
            if (ringDedicatedRails) {
                for (const auto &rail : ringRails[i]) {
                    rdmaHw->BindRailAddress(rail.address, rail.hostPort);
                }
            }
            rdma->TraceConnectWithoutContext("QpComplete",
                                             MakeBoundCallback(qp_finish, fct_output));
        }
    }

    /**
     * @brief setup switch's CcMode and ACK with high priority
     */
    for (uint32_t i = 0; i < node_num; i++) {
        if (n.Get(i)->GetNodeType() == 1) {  // switch
            Ptr<SwitchNode> sw = DynamicCast<SwitchNode>(n.Get(i));
            sw->SetAttribute("CcMode", UintegerValue(cc_mode));
            sw->SetAttribute("AckHighPrio", UintegerValue(1));
        }
    }

    /**
     * @brief setup routing
     */
    CalculateRoutes(n);
    SetRoutingEntries();

    /**
     * @brief get BDP and delay
     */
    maxRtt = maxBdp = 0;
    fprintf(stderr, "node_num=%d\n", node_num);
    for (uint32_t i = 0; i < node_num; i++) {
        if (n.Get(i)->GetNodeType() != 0) continue;
        for (uint32_t j = i + 1; j < node_num; j++) {
            if (n.Get(j)->GetNodeType() != 0) continue;
            uint64_t delay = pairDelay[n.Get(i)][n.Get(j)];
            uint64_t txDelay = pairTxDelay[n.Get(i)][n.Get(j)];
            uint64_t rtt = delay * 2 + txDelay;
            uint64_t bw = pairBw[n.Get(i)][n.Get(j)];
            uint64_t bdp = rtt * bw / 1000000000 / 8;
            pairBdp[n.Get(i)][n.Get(j)] = bdp;
            pairBdp[n.Get(j)][n.Get(i)] = bdp;
            pairRtt[n.Get(i)][n.Get(j)] = rtt;
            pairRtt[n.Get(j)][n.Get(i)] = rtt;

            if (bdp > maxBdp) maxBdp = bdp;
            if (rtt > maxRtt) maxRtt = rtt;
        }
    }
    fprintf(stderr, "maxRtt: %lu, maxBdp: %lu\n", maxRtt, maxBdp);
    assert(symphonyTwoFlow || figure4Trace || !profileFBackgroundFile.empty() ||
           maxBdp == irn_bdp_lookup);

    std::cout << "Configuring switches" << std::endl;
    /* config ToR Switch */
    for (auto &pair : link_pairs) {
        Ptr<Node> probably_host = n.Get(pair.first);
        Ptr<Node> probably_switch = n.Get(pair.second);

        // host-switch link
        if (probably_host->GetNodeType() == 0 && probably_switch->GetNodeType() == 1) {
            Ptr<SwitchNode> sw = DynamicCast<SwitchNode>(probably_switch);
            sw->m_isToR = true;
            uint32_t hostIP = serverAddress[pair.first].Get();
            sw->m_isToR_hostIP.insert(hostIP);
            if (idxNodeToR.find(sw->GetId()) == idxNodeToR.end()) {
                idxNodeToR[sw->GetId()] = sw;
            };
        }
    }

    /* config load balancer's switches using ToR-to-ToR routing */
    if (lb_mode == 3 || lb_mode == 6 || lb_mode == 9) {  // Conga, Letflow, Conweave
        NS_LOG_INFO("Configuring Load Balancer's Switches");
        for (auto &pair : link_pairs) {
            Ptr<Node> probably_host = n.Get(pair.first);
            Ptr<Node> probably_switch = n.Get(pair.second);

            // host-switch link
            if (probably_host->GetNodeType() == 0 && probably_switch->GetNodeType() == 1) {
                Ptr<SwitchNode> sw = DynamicCast<SwitchNode>(probably_switch);
                uint32_t hostIP = serverAddress[pair.first].Get();
                Settings::hostIp2SwitchId[hostIP] = sw->GetId();  // hostIP -> connected switch's ID
            }
        }

        // Conga: m_congaFromLeafTable, m_congaToLeafTable, m_congaRoutingTable
        // Letflow: m_letflowRoutingTable
        // Conweave: m_ConWeaveRoutingTable, m_rxToRId2BaseRTT
        for (auto i = nextHop.begin(); i != nextHop.end(); i++) {  // every node
            if (i->first->GetNodeType() == 1) {                    // switch
                Ptr<Node> nodeSrc = i->first;
                Ptr<SwitchNode> swSrc = DynamicCast<SwitchNode>(nodeSrc);  // switch
                uint32_t swSrcId = swSrc->GetId();

                if (swSrc->m_isToR) {
                    // printf("--- ToR Switch %d\n", swSrcId);

                    auto table1 = i->second;
                    for (auto j = table1.begin(); j != table1.end(); j++) {
                        Ptr<Node> dst = j->first;  // dst
                        uint32_t dstIP = Settings::hostId2IpMap[dst->GetId()];
                        uint32_t swDstId = Settings::hostIp2SwitchId[dstIP];  // Rx(dst)ToR

                        if (swSrcId == swDstId) {
                            continue;  // if in the same pod, then skip
                        }

                        if (lb_mode == 3) {
                            // initialize `m_congaFromLeafTable` and `m_congaToLeafTable`
                            swSrc->m_mmu->m_congaRouting
                                .m_congaFromLeafTable[swDstId];  // dynamically will be added in
                                                                 // conga
                            swSrc->m_mmu->m_congaRouting.m_congaToLeafTable[swDstId];
                        }

                        // construct paths
                        uint32_t pathId;
                        uint8_t path_ports[4] = {0, 0, 0, 0};  // interface is always large than 0
                        vector<Ptr<Node>> nexts1 = j->second;
                        for (auto next1 : nexts1) {
                            uint32_t outPort1 = nbr2if[nodeSrc][next1].idx;
                            auto nexts2 = nextHop[next1][dst];
                            if (nexts2.size() == 1 && nexts2[0]->GetId() == swDstId) {
                                // this destination has 2-hop distance
                                uint32_t outPort2 = nbr2if[next1][nexts2[0]].idx;
                                // printf("[IntraPod-2hop] %d (%d)-> %d (%d) -> %d -> %d\n",
                                // nodeSrc->GetId(), outPort1, next1->GetId(), outPort2,
                                // nexts2[0]->GetId(), dst->GetId());
                                path_ports[0] = (uint8_t)outPort1;
                                path_ports[1] = (uint8_t)outPort2;
                                pathId = *((uint32_t *)path_ports);
                                if (lb_mode == 3) {
                                    swSrc->m_mmu->m_congaRouting.m_congaRoutingTable[swDstId]
                                        .insert(pathId);
                                }
                                if (lb_mode == 6) {
                                    swSrc->m_mmu->m_letflowRouting.m_letflowRoutingTable[swDstId]
                                        .insert(pathId);
                                }
                                if (lb_mode == 9) {
                                    swSrc->m_mmu->m_conweaveRouting.m_ConWeaveRoutingTable[swDstId]
                                        .insert(pathId);
                                    swSrc->m_mmu->m_conweaveRouting.m_rxToRId2BaseRTT[swDstId] =
                                        one_hop_delay * 4;
                                }
                                continue;
                            }

                            for (auto next2 : nexts2) {
                                uint32_t outPort2 = nbr2if[next1][next2].idx;
                                auto nexts3 = nextHop[next2][dst];
                                if (nexts3.size() == 1 && nexts3[0]->GetId() == swDstId) {
                                    // this destination has 3-hop distance
                                    uint32_t outPort3 = nbr2if[next2][nexts3[0]].idx;
                                    // printf("[IntraPod-3hop] %d (%d)-> %d (%d) -> %d (%d) -> %d ->
                                    // %d\n", nodeSrc->GetId(), outPort1, next1->GetId(), outPort2,
                                    // next2->GetId(), outPort3, nexts3[0]->GetId(), dst->GetId());
                                    path_ports[0] = (uint8_t)outPort1;
                                    path_ports[1] = (uint8_t)outPort2;
                                    path_ports[2] = (uint8_t)outPort3;
                                    pathId = *((uint32_t *)path_ports);
                                    if (lb_mode == 3) {
                                        swSrc->m_mmu->m_congaRouting.m_congaRoutingTable[swDstId]
                                            .insert(pathId);
                                    }
                                    if (lb_mode == 6) {
                                        swSrc->m_mmu->m_letflowRouting
                                            .m_letflowRoutingTable[swDstId]
                                            .insert(pathId);
                                    }
                                    if (lb_mode == 9) {
                                        swSrc->m_mmu->m_conweaveRouting
                                            .m_ConWeaveRoutingTable[swDstId]
                                            .insert(pathId);
                                        swSrc->m_mmu->m_conweaveRouting.m_rxToRId2BaseRTT[swDstId] =
                                            one_hop_delay * 6;
                                    }
                                    continue;
                                }

                                for (auto next3 : nexts3) {
                                    uint32_t outPort3 = nbr2if[next2][next3].idx;
                                    auto nexts4 = nextHop[next3][dst];
                                    if (nexts4.size() == 1 && nexts4[0]->GetId() == swDstId) {
                                        // this destination has 4-hop distance
                                        uint32_t outPort4 = nbr2if[next3][nexts4[0]].idx;
                                        // printf("[IntraPod-4hop] %d (%d)-> %d (%d) -> %d (%d) ->
                                        // %d (%d) -> %d -> %d\n", nodeSrc->GetId(), outPort1,
                                        // next1->GetId(), outPort2, next2->GetId(), outPort3,
                                        // next3->GetId(), outPort4, nexts4[0]->GetId(),
                                        // dst->GetId());
                                        path_ports[0] = (uint8_t)outPort1;
                                        path_ports[1] = (uint8_t)outPort2;
                                        path_ports[2] = (uint8_t)outPort3;
                                        path_ports[3] = (uint8_t)outPort4;
                                        pathId = *((uint32_t *)path_ports);
                                        if (lb_mode == 3) {
                                            swSrc->m_mmu->m_congaRouting
                                                .m_congaRoutingTable[swDstId]
                                                .insert(pathId);
                                        }
                                        if (lb_mode == 6) {
                                            swSrc->m_mmu->m_letflowRouting
                                                .m_letflowRoutingTable[swDstId]
                                                .insert(pathId);
                                        }
                                        if (lb_mode == 9) {
                                            swSrc->m_mmu->m_conweaveRouting
                                                .m_ConWeaveRoutingTable[swDstId]
                                                .insert(pathId);
                                            swSrc->m_mmu->m_conweaveRouting
                                                .m_rxToRId2BaseRTT[swDstId] = one_hop_delay * 8;
                                        }
                                        continue;
                                    } else {
                                        printf("Too large topology?\n");
                                        assert(false);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // m_outPort2BitRateMap - only for Conga
        for (auto i = nextHop.begin(); i != nextHop.end(); i++) {  // every node
            if (i->first->GetNodeType() == 1) {                    // switch
                Ptr<Node> node = i->first;
                Ptr<SwitchNode> sw = DynamicCast<SwitchNode>(node);  // switch
                uint32_t swId = sw->GetId();

                auto table = i->second;
                for (auto j = table.begin(); j != table.end(); j++) {
                    Ptr<Node> dst = j->first;  // dst
                    uint32_t dstIP = Settings::hostId2IpMap[dst->GetId()];
                    uint32_t swDstId = Settings::hostIp2SwitchId[dstIP];

                    for (auto next : j->second) {
                        uint32_t outPort = nbr2if[node][next].idx;
                        uint64_t bw = nbr2if[node][next].bw;
                        sw->m_mmu->m_congaRouting.SetLinkCapacity(outPort, bw);
                        // printf("Node: %d, interface: %d, bw: %lu\n", swId, outPort, bw);
                    }
                }
            }
        }

        // Constant setup, and switchInfo
        for (auto i = nextHop.begin(); i != nextHop.end(); i++) {  // every node
            if (i->first->GetNodeType() == 1) {
                Ptr<Node> node = i->first;
                Ptr<SwitchNode> sw = DynamicCast<SwitchNode>(node);  // switch
                NS_LOG_INFO("Switch Info - ID:%u, ToR:%d\n" % (sw->GetId(), sw->m_isToR));
                if (lb_mode == 3) {
                    sw->m_mmu->m_congaRouting.SetConstants(conga_dreTime, conga_agingTime,
                                                           conga_flowletTimeout, conga_quantizeBit,
                                                           conga_alpha);
                    sw->m_mmu->m_congaRouting.SetSwitchInfo(sw->m_isToR, sw->GetId());
                }
                if (lb_mode == 6) {
                    sw->m_mmu->m_letflowRouting.SetConstants(letflow_agingTime,
                                                             letflow_flowletTimeout);
                    sw->m_mmu->m_letflowRouting.SetSwitchInfo(sw->m_isToR, sw->GetId());
                }
                if (lb_mode == 9) {
                    sw->m_mmu->m_conweaveRouting.SetConstants(
                        conweave_extraReplyDeadline, conweave_extraVOQFlushTime,
                        conweave_txExpiryTime, conweave_defaultVOQWaitingTime,
                        conweave_pathPauseTime, conweave_pathAwareRerouting);
                    sw->m_mmu->m_conweaveRouting.SetSwitchInfo(sw->m_isToR, sw->GetId());
                }
            }
        }

        // schedule conga timeout monitor
        if (lb_mode == 3) {  // CONGA
            Simulator::Schedule(Seconds(flowgen_stop_time + simulator_extra_time),
                                conga_history_print);
        }
        if (lb_mode == 6) {  // LETFLOW
            Simulator::Schedule(Seconds(flowgen_stop_time + simulator_extra_time),
                                letflow_history_print);
        }
        if (lb_mode == 9) {  // CONWEAVE
            Simulator::Schedule(Seconds(flowgen_stop_time + simulator_extra_time),
                                conweave_history_print);
        }
    }

    // populate routing tables (although we use our custom impl in switch_node.cc)
    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    // maintain port number for each host
    for (uint32_t i = 0; i < node_num; i++) {
        if (n.Get(i)->GetNodeType() == 0) {
            portNumber[i] = 10000;  // each host use port number from 10000
            dportNumber[i] = 100;
        }
    }

    flow_input.idx = 0;
    port_per_host = new uint16_t[node_num - switch_num];
    if (!figure4Manifest.empty()) {
        if (!ScheduleFigure4Collective(figure4Manifest)) {
            std::cerr << "Invalid Figure 4 transfer manifest\n";
            return 1;
        }
    } else if (ringAllReduce) {
        if (!ScheduleRingJobs()) {
            return 1;
        }
        if (!profileFBackgroundFile.empty() && !ScheduleProfileFBackground()) {
            std::cerr << "Invalid Profile F workload or missing telemetry\n";
            return 1;
        }
    } else if (symphonyTwoFlow) {
        if (!ScheduleTwoFlow()) {
            return 1;
        }
    } else if (flow_num > 0) {
        // generate flows
        ReadFlowInput();
        Simulator::Schedule(Seconds(0), &ScheduleFlowInputs, flow_input_stream);
    }

    topof.close();

    // schedule link down
    if (link_down_time > 0) {
        Simulator::Schedule(Seconds(flowgen_start_time) + MicroSeconds(link_down_time),
                            &TakeDownLink, n, n.Get(link_down_A), n.Get(link_down_B));
    }

    if (lb_mode == 9) {
        voq_output = fopen(voq_mon_file.c_str(), "w");                // specific to ConWeave
        voq_detail_output = fopen(voq_mon_detail_file.c_str(), "w");  // specific to ConWeave
    }

    uplink_output = fopen(uplink_mon_file.c_str(), "w");  // common
    conn_output = fopen(conn_mon_file.c_str(), "w");      // common

    // update torId2UplinkIf, torId2DownlinkIf
    for (size_t ToRId = 0; ToRId < Settings::node_num; ToRId++) {
        Ptr<Node> node = n.Get(ToRId);
        if (node->GetNodeType() == 1) {  // switches
            auto swNode = DynamicCast<SwitchNode>(n.Get(ToRId));
            if (swNode->m_isToR) {  // TOR switch
                for (auto &nextNodeIf : nbr2if[node]) {
                    if (nextNodeIf.first->GetNodeType() ==
                        1) {  // nextNode is switch (i.e., uplink)
                        auto &vec = torId2UplinkIf[ToRId];
                        vec.push_back(
                            nextNodeIf.second.idx);  // record this uplink port (outDev index)
                        // printf("Sw %lu - uplink port %u\n", ToRId, nextNodeIf.second.idx);  //
                        // debugging
                    } else {
                        auto &vec = torId2DownlinkIf[ToRId];
                        vec.push_back(
                            nextNodeIf.second.idx);  // record this downlink port (outDev index)
                        // printf("Sw %lu - downlink port %u\n", ToRId, nextNodeIf.second.idx);  //
                        // debugging
                    }
                }
            }
        }
    }
    Simulator::Schedule(Seconds(flowgen_start_time), &periodic_monitoring, voq_output,
                        voq_detail_output, uplink_output, conn_output, &lb_mode);

    //
    // Now, do the actual simulation.
    //
    std::cout << "------------------------------------------" << std::endl;
    std::cout << "Running Simulation.\n";
    fflush(stdout);
    NS_LOG_INFO("Run Simulation.");
    if (ringAllReduce || symphonyTwoFlow) {
        Simulator::Stop(Seconds(flowgen_stop_time));
    } else {
        Simulator::Schedule(Seconds(flowgen_start_time),
                            &stop_simulation_middle);  // check every 100us
        Simulator::Stop(Seconds(flowgen_stop_time + 10.0));
    }
    if (figure4WorkloadTelemetry) {
        SwitchNode::SetFigure4MarkObserver(MakeCallback(&Figure4MarkSample));
    }
    if (figure4Trace) {
        std::map<std::pair<uint32_t, uint32_t>, Ipv4Address> accessAddresses;
        for (uint32_t host = 0; host < ringRails.size(); ++host) {
            for (const auto &rail : ringRails[host]) {
                accessAddresses[{host, rail.hostPort}] = rail.address;
                accessAddresses[{rail.tor, rail.torPort}] = rail.address;
            }
        }
        for (uint32_t node = 0; node < n.GetN(); ++node) {
            for (uint32_t port = 0; port < n.Get(node)->GetNDevices(); ++port) {
                auto device = DynamicCast<QbbNetDevice>(n.Get(node)->GetDevice(port));
                if (!device) continue;
                const auto key = std::make_pair(node, port);
                device->TraceConnectWithoutContext(
                    "PhyTxBegin", MakeBoundCallback(&TraceRailPort, &railPortStats[key],
                                                    accessAddresses[key],
                                                    n.Get(node)->GetNodeType() == 0));
                if (figure4Trace == 2 && n.Get(node)->GetNodeType() == 1) {
                    device->TraceConnectWithoutContext("QbbDequeue",
                        MakeBoundCallback(&TraceFigure4Egress, PeekPointer(device), &figure4Egress[key]));
                }
                if (figure4WorkloadTelemetry) {
                    if (n.Get(node)->GetNodeType() == 0) {
                        device->TraceConnectWithoutContext("RdmaQpDequeue",
                                                           MakeCallback(&Figure4RateSample));
                    } else {
                        device->TraceConnectWithoutContext("PhyTxBegin",
                            MakeBoundCallback(&Figure4QueueSample, PeekPointer(device)));
                    }
                }
            }
        }
    }
    Simulator::Run();
    if (!profileFBackgroundFile.empty()) {
        PrintProfileFTelemetry();
    }
    if (figure4WorkloadTelemetry) {
        PrintFigure4Telemetry();
        SwitchNode::SetFigure4MarkObserver(Callback<void, uint32_t, uint32_t, bool, bool>());
    }
    for (const auto &entry : railPortStats) {
        std::cout << "FIGURE4_RAIL_PORT node=" << entry.first.first
                  << " port=" << entry.first.second << " bytes=" << entry.second.bytes
                  << " data=" << entry.second.data << " feedback=" << entry.second.feedback
                  << '\n';
    }
    for (const auto &entry : figure4Egress) {
        EmitFigure4Bin(entry.first.first, entry.first.second, entry.second);
    }
    for (const auto &sw : figure4Switches) {
        std::cout << "FIGURE4_MARKS switch=" << sw->GetId()
                  << " count=" << sw->GetFigure4Marks() << '\n';
    }
    figure4Switches.clear();
    for (const auto &entry : ringEgressBytes) {
        std::cout << "RING_EGRESS bin=" << std::get<0>(entry.first)
                  << " job=" << std::get<1>(entry.first)
                  << " dst=" << std::get<2>(entry.first)
                  << " bytes=" << entry.second << '\n';
    }


    /*-----------------------------------------------------------------------------*/
    /*----- we don't need below. Just we can enforce to close this simulation. -----*/
    /*-----------------------------------------------------------------------------*/
    symphonySwitches.clear();
    Simulator::Destroy();
    NS_LOG_INFO("Total number of packets: " << RdmaHw::nAllPkts);
    NS_LOG_INFO("Done.");
    endt = clock();
    std::cerr << (double)(endt - begint) / CLOCKS_PER_SEC << "\n";
    if (profileFBackgroundPending) {
        std::cerr << "Profile F background transfers timed out\n";
        return 1;
    }
    if (symphonyTwoFlow && twoFlowCompleted != 2) {
        std::cerr << "Two-flow experiment timed out before both flows finished\n";
        return 1;
    }
    if (!figure4Manifest.empty() && figure4Completed != figure4Transfers.size()) {
        std::cerr << "Figure 4 manifest timed out after " << figure4Completed << " transfers\n";
        return 1;
    }
    if (figure4Manifest.empty() && ringAllReduce && ringFinishedJobs != ringJobs.size()) {
        std::cerr << "Ring timed out after " << ringCompletedTransfers << '/' << ringExpectedTransfers
                  << " transfers and " << ringFinishedJobs << '/' << ringJobs.size() << " jobs\n";
        return 1;
    }
}
