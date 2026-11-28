// The manifest lists transfers in topological order. A dependency is a
// transfer whose final ACK must arrive before this transfer may start.
struct Figure4Transfer {
    uint32_t src, dst, rail, step, bytes;
    uint16_t sport, dport;
    uint32_t pending;
    int32_t issueSuccessor = -1;
    std::vector<uint32_t> successors;
};

std::vector<Figure4Transfer> figure4Transfers;
uint32_t figure4Completed = 0;

void StartFigure4Transfer(uint32_t id) {
    const auto &f = figure4Transfers.at(id);
    auto src = n.Get(f.src);
    auto dst = n.Get(f.dst);
    RingProgress progress;
    progress.collectiveId = 0;
    progress.step = f.step;
    src->GetObject<RdmaDriver>()->AddQueuePair(
        f.bytes, 3, ringRails[f.src][f.rail].address, ringRails[f.dst][f.rail].address,
        f.sport, f.dport, has_win ? (global_t == 1 ? maxBdp : pairBdp[src][dst]) : 0,
        global_t == 1 ? maxRtt : pairRtt[src][dst], id, progress);
    if (f.issueSuccessor >= 0 && --figure4Transfers[f.issueSuccessor].pending == 0) {
        Simulator::ScheduleNow(&StartFigure4Transfer, f.issueSuccessor);
    }
}

bool ScheduleFigure4Collective(const std::string &file) {
    std::ifstream input(file);
    uint32_t count;
    if (!(input >> count) || count == 0 || count > 1000000) return false;
    figure4Transfers.resize(count);
    uint32_t id = 0;
    for (auto &f : figure4Transfers) {
        int32_t issueAfter;
        uint32_t dependencyCount;
        if (!(input >> f.src >> f.dst >> f.rail >> f.step >> f.bytes >>
              f.sport >> f.dport >> issueAfter >> dependencyCount) ||
            f.src == f.dst || f.src >= ringRails.size() || f.dst >= ringRails.size() ||
            f.rail >= ringRails[f.src].size() || f.rail >= ringRails[f.dst].size() ||
            f.step >= 64 || f.bytes == 0) {
            return false;
        }
        f.pending = dependencyCount;
        if (issueAfter != -1) {
            if (issueAfter < 0 || uint32_t(issueAfter) >= id ||
                figure4Transfers[issueAfter].src != f.src ||
                figure4Transfers[issueAfter].issueSuccessor != -1) return false;
            figure4Transfers[issueAfter].issueSuccessor = id;
            ++f.pending;
        }
        std::set<uint32_t> dependencies;
        for (uint32_t i = 0; i < dependencyCount; ++i) {
            uint32_t predecessor;
            if (!(input >> predecessor) || predecessor >= id ||
                !dependencies.insert(predecessor).second) return false;
            figure4Transfers[predecessor].successors.push_back(id);
        }
        ++id;
    }
    std::string extra;
    if (input >> extra) return false;
    Simulator::Schedule(Seconds(flowgen_start_time), [] {
        for (const auto &sw : symphonySwitches) sw->StartSymphonyCollective(0);
        for (uint32_t i = 0; i < figure4Transfers.size(); ++i) {
            if (figure4Transfers[i].pending == 0) Simulator::ScheduleNow(&StartFigure4Transfer, i);
        }
    });
    return true;
}

void CompleteFigure4Transfer(Ptr<RdmaQueuePair> qp) {
    const auto &f = figure4Transfers.at(qp->m_flow_id);
    std::cout << "FIGURE4_DAG_TRANSFER flow_id=" << qp->m_flow_id << " src=" << f.src
              << " dst=" << f.dst << " step=" << f.step << " bytes=" << qp->m_size
              << " tx_payload_bytes=" << qp->stat.txTotalBytes
              << " sport=" << qp->sport << " dport=" << qp->dport
              << " start_ns=" << qp->startTime.GetNanoSeconds()
              << " completion_ns=" << Simulator::Now().GetNanoSeconds() << '\n';
    for (uint32_t successor : f.successors) {
        if (--figure4Transfers[successor].pending == 0) {
            Simulator::ScheduleNow(&StartFigure4Transfer, successor);
        }
    }
    if (++figure4Completed == figure4Transfers.size()) {
        for (const auto &sw : symphonySwitches) sw->FinishSymphonyCollective(0);
        Simulator::Stop();
    }
}
