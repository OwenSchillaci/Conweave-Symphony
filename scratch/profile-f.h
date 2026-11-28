// Profile F background flows use the normal RDMA QP and queue admission paths.
struct ProfileFBackground {
    uint32_t src, srcTor, spine, dstTor, dst, sport, dport, bytes;
    int64_t startNs;
};

const int32_t profileFBackgroundId = 1000000;
const int64_t profileFBinNs = 100000;
uint32_t profileFBackgroundPending = 0;

struct ProfileFPortBin {
    uint64_t wireBytes = 0, collectiveBytes = 0, backgroundBytes = 0;
    uint64_t backgroundFeedbackBytes = 0, queueSum = 0, samples = 0;
    uint32_t queueMax = 0, queueMarks = 0, symphonyMarks = 0, bothMarks = 0;
};
using ProfileFPortKey = std::tuple<uint32_t, uint32_t, int64_t>;
std::map<ProfileFPortKey, ProfileFPortBin> profileFPorts;
std::map<std::pair<uint32_t, uint32_t>, uint32_t> profileFPeers;
std::map<std::tuple<int32_t, uint32_t, uint32_t>, uint64_t> profileFPathBytes; // (flow, source ToR, spine)
// First packet physically transmitted for each ConWeave flow, epoch, phase and port.
std::map<std::tuple<int32_t, uint32_t, uint32_t, uint32_t>, int64_t> profileFPhaseTx;

ProfileFPortBin &ProfileFCurrentBin(uint32_t node, uint32_t port) {
    return profileFPorts[std::make_tuple(node, port,
        Simulator::Now().GetNanoSeconds() / profileFBinNs)];
}

void ProfileFSamplePort(QbbNetDevice *device, Ptr<const Packet> packet) {
    auto &bin = ProfileFCurrentBin(device->GetNode()->GetId(), device->GetIfIndex());
    bin.wireBytes += packet->GetSize();
    const uint32_t queued = device->GetQueue()->GetNBytesTotal();
    bin.queueSum += queued;
    bin.queueMax = std::max(bin.queueMax, queued);
    ++bin.samples;
    CustomHeader header(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
    packet->PeekHeader(header);
    FlowIDNUMTag flow;
    if (!packet->PeekPacketTag(flow)) return;
    if ((header.l3Prot == 0xFC || header.l3Prot == 0xFD) &&
        flow.GetId() >= profileFBackgroundId) {
        bin.backgroundFeedbackBytes += packet->GetSize();
    }
    if (header.l3Prot == 0x11) {
        if (flow.GetId() >= profileFBackgroundId) {
            bin.backgroundBytes += packet->GetSize();
        } else {
            bin.collectiveBytes += packet->GetSize();
            auto sw = DynamicCast<SwitchNode>(device->GetNode());
            const uint32_t peer = profileFPeers.at({sw->GetId(), device->GetIfIndex()});
            if (sw->m_isToR_hostIP.count(header.sip) && n.Get(peer)->GetNodeType() == 1) {
                const int32_t id = flow.GetId();
                profileFPathBytes[{id, sw->GetId(), peer}] += packet->GetSize();
                ConWeaveDataTag tag;
                if (packet->PeekPacketTag(tag)) {
                    const auto now = Simulator::Now().GetNanoSeconds();
                    const auto observed = profileFPhaseTx.emplace(
                        std::make_tuple(id, tag.GetEpoch(), tag.GetPhase(), device->GetIfIndex()), now);
                    if (profileFMarkProvenance && observed.second) {
                        std::cout << "CW_PROV_TX time_ns=" << now << " switch=" << sw->GetId()
                                  << " flow_id=" << id << " epoch=" << tag.GetEpoch()
                                  << " phase=" << tag.GetPhase() << " port=" << device->GetIfIndex()
                                  << " path=" << tag.GetPathId() << " data_uid=" << packet->GetUid()
                                  << '\n';
                    }
                }
            }
        }
    }
}

void ProfileFMark(uint32_t node, uint32_t port, bool queue, bool symphony) {
    auto &bin = ProfileFCurrentBin(node, port);
    bin.queueMarks += queue;
    bin.symphonyMarks += symphony;
    bin.bothMarks += queue && symphony;
}

bool ConnectProfileFTelemetry() {
    for (uint32_t node = 0; node < n.GetN(); ++node) {
        auto endpoint = n.Get(node);
        for (const auto &link : nbr2if[endpoint]) {
            uint32_t port = link.second.idx;
            auto device = DynamicCast<QbbNetDevice>(endpoint->GetDevice(port));
            profileFPeers.emplace(std::make_pair(node, port), link.first->GetId());
            if (endpoint->GetNodeType() == 1) {
                if (!device->TraceConnectWithoutContext("PhyTxBegin",
                        MakeBoundCallback(&ProfileFSamplePort, PeekPointer(device)))) return false;
            } else {
                if (!device->TraceConnectWithoutContext("RdmaQpDequeue",
                        MakeCallback(&Figure4RateSample))) return false;
            }
        }
    }
    SwitchNode::SetFigure4MarkObserver(MakeCallback(&ProfileFMark));
    ConWeaveRouting::m_traceProfileF = true;
    ConWeaveRouting::m_traceMarkProvenance = profileFMarkProvenance;
    ConWeaveRouting::m_queueOnlyNotify = profileFQueueOnlyNotify;
    ConWeaveRouting::m_traceProfileFPilotDetail = profileFPilotDetail;
    return true;
}

void PrintProfileFTelemetry() {
    for (const auto &entry : profileFPorts) {
        const auto &key = entry.first;
        const auto &bin = entry.second;
        const auto port = std::make_pair(std::get<0>(key), std::get<1>(key));
        std::cout << "PROFILE_F_PORT node=" << port.first << " port=" << port.second
                  << " peer=" << profileFPeers.at(port) << " start_ns=" << std::get<2>(key) * profileFBinNs
                  << " wire_bytes=" << bin.wireBytes << " collective_bytes=" << bin.collectiveBytes
                  << " background_bytes=" << bin.backgroundBytes
                  << " background_feedback_bytes=" << bin.backgroundFeedbackBytes
                  << " samples=" << bin.samples
                  << " queue_sum=" << bin.queueSum << " queue_max=" << bin.queueMax
                  << " queue_marks=" << bin.queueMarks << " symphony_marks=" << bin.symphonyMarks
                  << " both_marks=" << bin.bothMarks << '\n';
    }
    for (const auto &entry : profileFPathBytes) {
        std::cout << "PROFILE_F_FLOW_PATH flow_id=" << std::get<0>(entry.first)
                  << " switch=" << std::get<1>(entry.first)
                  << " spine=" << std::get<2>(entry.first)
                  << " wire_bytes=" << entry.second << '\n';
    }
    for (const auto &entry : profileFPhaseTx) {
        std::cout << "PROFILE_F_PHASE_TX flow_id=" << std::get<0>(entry.first)
                  << " epoch=" << std::get<1>(entry.first)
                  << " phase=" << std::get<2>(entry.first)
                  << " port=" << std::get<3>(entry.first) << " time_ns=" << entry.second << '\n';
    }
    PrintFigure4Telemetry();
    uint64_t unflushedOooNotifications = 0;
    for (auto host = n.Begin(); host != n.End(); ++host) {
        if ((*host)->GetNodeType() == 0) {
            unflushedOooNotifications += (*host)->GetObject<RdmaDriver>()->m_rdma->cnp_by_ooo;
        }
    }
    std::cout << "PROFILE_F_CNP_TAIL ooo=" << unflushedOooNotifications << '\n';
    if (lb_mode == 9) {
        std::cout << "PROFILE_F_CW_FEEDBACK init_replies=" << ConWeaveRouting::m_nReplyInitSent
                  << " timely_init_replies=" << ConWeaveRouting::m_nTimelyInitReplied
                  << " tail_replies=" << ConWeaveRouting::m_nReplyTailSent << '\n';
        for (auto it = n.Begin(); it != n.End(); ++it) {
            auto node = DynamicCast<SwitchNode>(*it);
            if (node && node->m_isToR) {
                auto &routing = node->m_mmu->m_conweaveRouting;
                std::cout << "PROFILE_F_CW_END switch=" << node->GetId()
                          << " voqs=" << routing.GetNumVOQ() << '\n';
            }
        }
    }
    SwitchNode::SetFigure4MarkObserver(Callback<void, uint32_t, uint32_t, bool, bool>());
    ConWeaveRouting::m_traceProfileF = false;
    ConWeaveRouting::m_traceMarkProvenance = false;
    ConWeaveRouting::m_queueOnlyNotify = false;
    ConWeaveRouting::m_traceProfileFPilotDetail = false;
}

void StartProfileFBackground(ProfileFBackground f, int32_t flowId) {
    auto src = n.Get(f.src);
    auto dst = n.Get(f.dst);
    std::cout << "PROFILE_F_BACKGROUND_START flow_id=" << flowId
              << " src=" << f.src << " dst=" << f.dst << " sport=" << f.sport
              << " dport=" << f.dport << " spine=" << f.spine << " bytes=" << f.bytes
              << " time_ns=" << Simulator::Now().GetNanoSeconds() << '\n';
    src->GetObject<RdmaDriver>()->AddQueuePair(
        f.bytes, 3, serverAddress[f.src], serverAddress[f.dst], f.sport, f.dport,
        has_win ? (global_t == 1 ? maxBdp : pairBdp[src][dst]) : 0,
        global_t == 1 ? maxRtt : pairRtt[src][dst], flowId);
}

bool ScheduleProfileFBackground() {
    std::ifstream in(profileFBackgroundFile);
    if (!in || ringJobs.size() != 1 || ringJobs.front().Ranks() != 8 ||
        ringJobs.front().iterations != 1) return false;
    std::set<uint32_t> ranks(ringJobs.front().hosts.begin(), ringJobs.front().hosts.end());
    std::set<std::pair<uint32_t, uint32_t>> sourcePorts, destinationPorts;
    std::string line;
    while (std::getline(in, line)) {
        std::istringstream row(line);
        row >> std::ws;
        if (row.eof() || row.peek() == '#') continue;
        ProfileFBackground f;
        std::string trailing;
        if (!(row >> f.src >> f.srcTor >> f.spine >> f.dstTor >> f.dst
                  >> f.sport >> f.dport >> f.bytes >> f.startNs) || row >> trailing ||
            !f.bytes || f.startNs < 0 || f.startNs >= Seconds(flowgen_stop_time).GetNanoSeconds() ||
            !f.sport || f.sport > UINT16_MAX || !f.dport || f.dport > UINT16_MAX ||
            ranks.count(f.src) || ranks.count(f.dst) ||
            !sourcePorts.emplace(f.src, f.sport).second ||
            !destinationPorts.emplace(f.dst, f.dport).second) return false;
        const uint32_t path[] = {f.src, f.srcTor, f.spine, f.dstTor, f.dst};
        if (std::set<uint32_t>(path, path + 5).size() != 5) return false;
        for (uint32_t i = 0; i < 5; ++i) {
            if (path[i] >= n.GetN() ||
                n.Get(path[i])->GetNodeType() != (i != 0 && i != 4) ||
                (i != 0 && !nbr2if[n.Get(path[i - 1])].count(n.Get(path[i])))) return false;
        }
        for (uint32_t i = 1; i < 4; ++i) {
            auto &links = nbr2if[n.Get(path[i])];
            SwitchNode::PinExperimentFlow(path[i], serverAddress[f.src].Get(),
                serverAddress[f.dst].Get(), f.sport, f.dport, links.at(n.Get(path[i + 1])).idx, false);
            SwitchNode::PinExperimentFlow(path[i], serverAddress[f.dst].Get(),
                serverAddress[f.src].Get(), f.dport, f.sport, links.at(n.Get(path[i - 1])).idx, true);
        }
        Simulator::Schedule(NanoSeconds(f.startNs), &StartProfileFBackground, f,
                            profileFBackgroundId + profileFBackgroundPending++);
    }
    return ConnectProfileFTelemetry();
}
