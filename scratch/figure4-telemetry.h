struct Figure4PortSample {
    uint64_t queueSum = 0;
    uint64_t packets = 0;
    uint32_t queueMax = 0;
    uint64_t queueMarks = 0;
    uint64_t symphonyMarks = 0;
    uint64_t bothMarks = 0;
};

struct Figure4FlowSample {
    uint64_t rateSum = 0;
    uint64_t packets = 0;
    uint64_t minRate = UINT64_MAX;
    uint64_t maxRate = 0;
    uint64_t bytes = 0;
};

using Figure4Port = std::pair<uint32_t, uint32_t>;
std::map<Figure4Port, Figure4PortSample> figure4PortSamples;
std::map<std::pair<int32_t, uint64_t>, Figure4FlowSample> figure4FlowSamples;

void Figure4MarkSample(uint32_t node, uint32_t port, bool queue, bool symphony) {
    auto &sample = figure4PortSamples[{node, port}];
    sample.queueMarks += queue;
    sample.symphonyMarks += symphony;
    sample.bothMarks += queue && symphony;
}

void Figure4QueueSample(QbbNetDevice *device, Ptr<const Packet>) {
    auto &sample = figure4PortSamples[{device->GetNode()->GetId(), device->GetIfIndex()}];
    const uint32_t bytes = device->GetQueue()->GetNBytesTotal();
    sample.queueSum += bytes;
    sample.queueMax = std::max(sample.queueMax, bytes);
    ++sample.packets;
}

void Figure4RateSample(Ptr<const Packet> packet, Ptr<RdmaQueuePair> qp) {
    const uint64_t bin = Simulator::Now().GetNanoSeconds() / 1000000;
    auto &sample = figure4FlowSamples[{qp->m_flow_id, bin}];
    const uint64_t rate = qp->m_rate.GetBitRate();
    sample.rateSum += rate;
    sample.minRate = std::min(sample.minRate, rate);
    sample.maxRate = std::max(sample.maxRate, rate);
    sample.bytes += packet->GetSize();
    ++sample.packets;
}

void PrintFigure4Telemetry() {
    for (const auto &entry : figure4PortSamples) {
        const auto &s = entry.second;
        std::cout << "FIGURE4_PORT_SAMPLE node=" << entry.first.first
                  << " port=" << entry.first.second << " packets=" << s.packets
                  << " queue_sum=" << s.queueSum << " queue_max=" << s.queueMax
                  << " queue_marks=" << s.queueMarks << " symphony_marks=" << s.symphonyMarks
                  << " both_marks=" << s.bothMarks << '\n';
    }
    for (const auto &entry : figure4FlowSamples) {
        const auto &s = entry.second;
        std::cout << "FIGURE4_FLOW_SAMPLE flow_id=" << entry.first.first
                  << " bin_ms=" << entry.first.second << " bytes=" << s.bytes
                  << " packets=" << s.packets << " rate_sum=" << s.rateSum
                  << " min_rate=" << s.minRate << " max_rate=" << s.maxRate << '\n';
    }
}
