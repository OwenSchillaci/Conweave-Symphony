#ifndef SWITCH_NODE_H
#define SWITCH_NODE_H

#include <ns3/node.h>

#include <unordered_map>
#include <unordered_set>

#include "qbb-net-device.h"
#include "switch-mmu.h"

namespace ns3 {

class Packet;

class SwitchNode : public Node {
    static const unsigned qCnt = 8;    // Number of queues/priorities used
    static const unsigned pCnt = 128;  // port 0 is not used so + 1	// Number of ports used
    uint32_t m_ecmpSeed;
    std::unordered_map<uint32_t, std::vector<int> >
        m_rtTable;  // map from ip address (u32) to possible ECMP port (index of dev)

    // monitor uplinks
    uint64_t m_txBytes[pCnt];  // counter of tx bytes, for HPCC

   protected:
    bool m_ecnEnabled;
    uint32_t m_ccMode;
    uint32_t m_ackHighPrio;  // set high priority for ACK/NACK

   private:
    friend class SymphonyAlphaTest;
    friend class SymphonyEgressTest;
    friend class ConWeaveSharedCeTest;

    struct SymphonyProgress {
        uint32_t stepMin = 0;
        uint32_t psnRec = 0;
        int64_t window = -1;
        uint64_t alpha = 1;
        uint64_t total = 0;
        uint64_t outpacing = 0;
        bool activated = false;
    };
    uint32_t m_symphonyMode = 0;
    uint32_t m_symphonyPayload = 0;
    uint32_t m_symphonyWarmup = 0;
    double m_symphonyGain = 0;
    bool m_symphonyAdaptiveAlpha = true;
    uint32_t m_symphonyMinSamples = 32;
    Ptr<UniformRandomVariable> m_symphonyRandom;
    std::unordered_map<uint32_t, SymphonyProgress> m_symphonyProgress;

    uint32_t m_figure4Trace = 0;
    int64_t m_figure4ActivationNs = 0;
    uint32_t m_figure4DequeueRecords = 0;
    uint64_t m_figure4Marks = 0;

    bool SymphonyMark(Ptr<Packet> p, bool queueMark, uint32_t egress = 0);
    uint32_t CountEarlierQueued(uint32_t port, uint32_t collective, uint32_t step) const;
    static void ApplyCongestionMarks(Ptr<Packet> p, bool queueMark, bool symphonyMark);

    int GetOutDev(Ptr<Packet>, CustomHeader &ch);
    void SendToDev(Ptr<Packet> p, CustomHeader &ch);
    void SendToDevContinue(Ptr<Packet> p, CustomHeader &ch);
    static uint32_t EcmpHash(const uint8_t *key, size_t len, uint32_t seed);
    void CheckAndSendPfc(uint32_t inDev, uint32_t qIndex);
    void CheckAndSendResume(uint32_t inDev, uint32_t qIndex);

    /* Sending packet to Egress port */
    void DoSwitchSend(Ptr<Packet> p, CustomHeader &ch, uint32_t outDev, uint32_t qIndex);

    /*----- Load balancer -----*/
    // Flow ECMP (lb_mode = 0)
    uint32_t DoLbFlowECMP(Ptr<const Packet> p, const CustomHeader &ch,
                          const std::vector<int> &nexthops);
    // DRILL (lb_mode = 2)
    uint32_t DoLbDrill(Ptr<const Packet> p, const CustomHeader &ch,
                       const std::vector<int> &nexthops);     // choose egress port
    uint32_t m_drill_candidate;                               // always 2 (power of two)
    std::map<uint32_t, uint32_t> m_previousBestInterfaceMap;  // <dip, previousBestInterface>
    uint32_t CalculateInterfaceLoad(uint32_t interface);      // Get the load of a interface
    // Conga (lb_mode = 3)
    uint32_t DoLbConga(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // Conga (lb_mode = 6)
    uint32_t DoLbLetflow(Ptr<Packet> p, CustomHeader &ch, const std::vector<int> &nexthops);
    // ConWeave (lb_mode = 9)
    uint32_t DoLbConWeave(Ptr<const Packet> p, const CustomHeader &ch,
                           const std::vector<int> &nexthops);  // dummy

   public:
    // Ptr<BroadcomNode> m_broadcom;
    Ptr<SwitchMmu> m_mmu;
    bool m_isToR;                                 // true if ToR switch
    std::unordered_set<uint32_t> m_isToR_hostIP;  // host's IP connected to this ToR

    static TypeId GetTypeId(void);
    SwitchNode();
    static void PinExperimentFlow(uint32_t node, uint32_t sip, uint32_t dip,
                                  uint16_t sport, uint16_t dport, uint32_t egress,
                                  bool feedback);
    void SetEcmpSeed(uint32_t seed);
    void AddTableEntry(Ipv4Address &dstAddr, uint32_t intf_idx);
    void ClearTable();
    bool SwitchReceiveFromDevice(Ptr<NetDevice> device, Ptr<Packet> packet, CustomHeader &ch);
    void SwitchNotifyDequeue(uint32_t ifIndex, uint32_t qIndex, Ptr<Packet> p);
    void ConfigureSymphony(uint32_t mode, uint32_t payload, uint32_t warmup, double gain,
                           bool adaptiveAlpha = true, uint32_t minSamples = 32);
    static void SetFigure4MarkObserver(Callback<void, uint32_t, uint32_t, bool, bool> observer);
    void SetSymphonyScopeTrace(bool enabled) { m_symphonyScopeTrace = enabled; }
    void NotifySymphonyQueue(uint32_t port, uint32_t queue, Ptr<const Packet> p, bool enqueue);
    void ConfigureFigure4(uint32_t trace, int64_t activationNs);
    uint64_t GetFigure4Marks() const { return m_figure4Marks; }
    void StartSymphonyCollective(uint32_t collective);
    void FinishSymphonyCollective(uint32_t collective);
    uint64_t GetTxBytesOutDev(uint32_t outdev);

   private:
    bool m_symphonyScopeTrace = false;
    // Each entry holds packets currently waiting on one port for one collective.
    std::map<std::pair<uint32_t, uint32_t>, std::map<uint32_t, uint32_t>> m_symphonyQueued;
};

} /* namespace ns3 */

#endif /* SWITCH_NODE_H */
