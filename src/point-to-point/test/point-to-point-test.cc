#include "ns3/test.h"
#include "ns3/conweave-routing.h"
#include "ns3/ecn-mark-provenance-tag.h"
#include "ns3/drop-tail-queue.h"
#include "ns3/simulator.h"
#include "ns3/flow-stat-tag.h"
#include "ns3/ipv4-header.h"
#include "ns3/ppp-header.h"
#include "ns3/ring-progress-tag.h"
#include "ns3/seq-ts-header.h"
#include "ns3/switch-node.h"
#include "ns3/udp-header.h"
#include <sstream>
#include "ns3/point-to-point-net-device.h"
#include "ns3/point-to-point-channel.h"

namespace ns3 {

class PointToPointTest : public TestCase
{
public:
  PointToPointTest ();

  virtual void DoRun (void);

private:
  void SendOnePacket (Ptr<PointToPointNetDevice> device);
};

PointToPointTest::PointToPointTest ()
  : TestCase ("PointToPoint")
{
}

void
PointToPointTest::SendOnePacket (Ptr<PointToPointNetDevice> device)
{
  Ptr<Packet> p = Create<Packet> ();
  device->Send (p, device->GetBroadcast (), 0x800);
}


void
PointToPointTest::DoRun (void)
{
  Ptr<Node> a = CreateObject<Node> ();
  Ptr<Node> b = CreateObject<Node> ();
  Ptr<PointToPointNetDevice> devA = CreateObject<PointToPointNetDevice> ();
  Ptr<PointToPointNetDevice> devB = CreateObject<PointToPointNetDevice> ();
  Ptr<PointToPointChannel> channel = CreateObject<PointToPointChannel> ();

  devA->Attach (channel);
  devA->SetAddress (Mac48Address::Allocate ());
  devA->SetQueue (CreateObject<DropTailQueue> ());
  devB->Attach (channel);
  devB->SetAddress (Mac48Address::Allocate ());
  devB->SetQueue (CreateObject<DropTailQueue> ());

  a->AddDevice (devA);
  b->AddDevice (devB);

  Simulator::Schedule (Seconds (1.0), &PointToPointTest::SendOnePacket, this, devA);

  Simulator::Run ();

  Simulator::Destroy ();
}
static Ptr<Packet>
MakeSymphonyPacket (uint32_t collective, uint32_t step, uint32_t psn, bool last = false)
{
  Ptr<Packet> packet = Create<Packet> (1000);
  SeqTsHeader seq;
  seq.SetSeq (1000 * psn);
  packet->AddHeader (seq);
  packet->AddHeader (UdpHeader ());
  Ipv4Header ip;
  ip.SetProtocol (17);
  ip.SetPayloadSize (packet->GetSize ());
  packet->AddHeader (ip);
  PppHeader ppp;
  ppp.SetProtocol (0x0021);
  packet->AddHeader (ppp);
  RingProgress progress;
  progress.collectiveId = collective;
  progress.step = step;
  packet->AddPacketTag (RingProgressTag (progress));
  FlowStatTag stat;
  stat.SetType (last ? FlowStatTag::FLOW_END : FlowStatTag::FLOW_NOTEND);
  packet->AddPacketTag (stat);
  return packet;
}

class SymphonyAlphaTest : public TestCase {
public:
  SymphonyAlphaTest () : TestCase ("Symphony adaptive alpha windows and marking") {}

private:
  void PacketAt (Ptr<SwitchNode> sw, uint32_t collective, uint32_t step, uint32_t psn,
                 bool last = false)
  {
    sw->SymphonyMark (MakeSymphonyPacket (collective, step, psn, last), false);
  }

  void Window (Ptr<SwitchNode> sw, uint32_t total, bool outpacing)
  {
    for (uint32_t i = 0; i < total; ++i)
      {
        PacketAt (sw, 0, outpacing && i == total - 1 ? 1 : 0, 10);
      }
  }

  void Exercise (Ptr<SwitchNode> sw, Ptr<SwitchNode> other, uint32_t window, uint64_t expected)
  {
    if (window == 0)
      {
        Window (sw, 3, false);
        PacketAt (sw, 0, 1, 10, true);
      }
    else
      {
        Window (sw, window == 1 || window == 5 || window == 6 ? 4 : 2, window == 1);
      }
    NS_TEST_ASSERT_MSG_EQ (sw->m_symphonyProgress.at (0).alpha, expected, "alpha at rollover");
    NS_TEST_ASSERT_MSG_EQ (sw->m_symphonyProgress.at (1).alpha, 1, "other collective");
    NS_TEST_ASSERT_MSG_EQ (other->m_symphonyProgress.at (0).alpha, 1, "other switch");

    if (window == 1)
      {
        std::ostringstream trace;
        auto original = std::cout.rdbuf (trace.rdbuf ());
        PacketAt (other, 0, 0, 10);
        PacketAt (other, 0, 1, 10);
        PacketAt (sw, 1, 1, 10); // No reference PSN yet.
        PacketAt (sw, 0, 1, 10); // alpha 2, probability 0.02.
        PacketAt (sw, 0, 1, 1000); // Cap at 1.
        std::cout.rdbuf (original);
        const auto data = trace.str ();
        NS_TEST_ASSERT_MSG_EQ ((data.find ("probability=0.01 ") != std::string::npos), true, "fixed gain");
        NS_TEST_ASSERT_MSG_EQ ((data.find ("eligible=0 probability=0 ") != std::string::npos), true, "warmup");
        NS_TEST_ASSERT_MSG_EQ ((data.find ("probability=0.02 ") != std::string::npos), true, "scaled gain");
        NS_TEST_ASSERT_MSG_EQ ((data.find ("probability=1 ") != std::string::npos), true, "cap");
      }
    if (window == 0)
      {
        const auto &state = sw->m_symphonyProgress.at (0);
        NS_TEST_ASSERT_MSG_EQ (state.outpacing, 1, "LAST uses the old minimum");
        NS_TEST_ASSERT_MSG_EQ (state.stepMin, 2, "LAST advances minimum");
      }
    if (window == 7)
      {
        sw->FinishSymphonyCollective (0);
        sw->StartSymphonyCollective (2);
        NS_TEST_ASSERT_MSG_EQ (sw->m_symphonyProgress.count (0), 0, "finished state retired");
        NS_TEST_ASSERT_MSG_EQ (sw->m_symphonyProgress.at (2).alpha, 1, "new iteration");
      }
  }

  void DoRun () override
  {
    Ptr<SwitchNode> sw = CreateObject<SwitchNode> ();
    Ptr<SwitchNode> other = CreateObject<SwitchNode> ();
    sw->ConfigureSymphony (1, 1000, 4, 0.01, true, 2);
    other->ConfigureSymphony (1, 1000, 4, 0.01, false, 2);
    sw->StartSymphonyCollective (0);
    sw->StartSymphonyCollective (1);
    other->StartSymphonyCollective (0);

    const uint32_t windows[] = {0, 1, 2, 5, 6, 7};
    const uint64_t alphas[] = {1, 2, 3, 3, 2, 1};
    for (uint32_t i = 0; i < 6; ++i)
      {
        Simulator::Schedule (MicroSeconds (100 * windows[i]), &SymphonyAlphaTest::Exercise,
                             this, sw, other, windows[i], alphas[i]);
      }
    Simulator::Run ();
    Simulator::Destroy ();
  }
};

class SymphonyEgressTest : public TestCase
{
public:
  SymphonyEgressTest () : TestCase ("Symphony egress scope and independent queue marking") {}

private:
  void DoRun () override
  {
    auto sw = CreateObject<SwitchNode> ();
    sw->ConfigureSymphony (3, 1000, 4, 1.0, false, 0);
    sw->StartSymphonyCollective (7);
    sw->StartSymphonyCollective (8);
    auto earlier = MakeSymphonyPacket (7, 0, 10);
    auto later = MakeSymphonyPacket (7, 1, 10);
    NS_TEST_ASSERT_MSG_EQ (sw->SymphonyMark (earlier, false, 4), false, "aligned packet");
    sw->NotifySymphonyQueue (4, 3, earlier, true);
    NS_TEST_ASSERT_MSG_EQ (sw->SymphonyMark (later, false, 4), true, "earlier step same port");
    NS_TEST_ASSERT_MSG_EQ (sw->SymphonyMark (later, false, 5), false, "earlier step on other port");
    NS_TEST_ASSERT_MSG_EQ (sw->SymphonyMark (earlier, false, 4), false, "queued aligned packet");
    sw->NotifySymphonyQueue (4, 3, earlier, false);
    NS_TEST_ASSERT_MSG_EQ (sw->m_symphonyQueued.empty (), true, "empty entries removed");

    auto other = MakeSymphonyPacket (8, 0, 10);
    sw->NotifySymphonyQueue (4, 3, other, true);
    NS_TEST_ASSERT_MSG_EQ (sw->SymphonyMark (later, false, 4), false, "other job does not count");
    sw->NotifySymphonyQueue (4, 3, earlier, true);
    sw->FinishSymphonyCollective (7);
    NS_TEST_ASSERT_MSG_EQ (sw->CountEarlierQueued (4, 7, 1), 0, "finished job retired");
    NS_TEST_ASSERT_MSG_EQ (sw->CountEarlierQueued (4, 8, 1), 1, "other job retained");
    sw->StartSymphonyCollective (7);
    sw->SymphonyMark (earlier, false, 4);
    const bool symphony = sw->SymphonyMark (later, true, 4);
    NS_TEST_ASSERT_MSG_EQ (symphony, false, "ordinary ECN does not open the gate");
    sw->ApplyCongestionMarks (later, true, symphony);
    PppHeader ppp;
    Ipv4Header header;
    later->RemoveHeader (ppp);
    later->PeekHeader (header);
    NS_TEST_ASSERT_MSG_EQ (header.GetEcn (), Ipv4Header::CE, "ordinary CE retained");

    auto original = CreateObject<SwitchNode> ();
    original->ConfigureSymphony (2, 1000, 4, 1.0, false, 0);
    original->StartSymphonyCollective (7);
    original->SymphonyMark (earlier, false, 4);
    NS_TEST_ASSERT_MSG_EQ (original->SymphonyMark (MakeSymphonyPacket (7, 1, 10), false, 5),
                           true, "original marking does not require same-egress work");
    Simulator::Destroy ();
  }
};

class EcnMarkProvenanceTest : public TestCase
{
public:
  EcnMarkProvenanceTest () : TestCase ("ECN provenance accumulates without changing packet size") {}

private:
  void DoRun () override
  {
    using Marks = EcnMarkProvenanceTag;
    auto packet = Create<Packet> (1000);
    NS_TEST_EXPECT_MSG_EQ (Marks::Read (packet), Marks::Neither, "untagged packet");
    Marks::Accumulate (packet, Marks::Queue);
    Marks::Accumulate (packet, Marks::Queue);
    NS_TEST_EXPECT_MSG_EQ (Marks::Read (packet), Marks::Queue, "repeated marks are idempotent");

    auto copy = packet->Copy ();
    NS_TEST_EXPECT_MSG_EQ (Marks::Read (copy), Marks::Queue, "copied queue mark");
    Marks::Accumulate (copy, Marks::Symphony);
    NS_TEST_EXPECT_MSG_EQ (Marks::Read (copy), Marks::Both, "queue then Symphony");
    NS_TEST_EXPECT_MSG_EQ (Marks::Read (packet), Marks::Queue, "copy update is isolated");
    NS_TEST_EXPECT_MSG_EQ (copy->GetUid (), packet->GetUid (), "copy preserves lineage");
    NS_TEST_EXPECT_MSG_EQ (copy->GetSize (), 1000, "marks do not add wire bytes");

    auto symphony = Create<Packet> (1000);
    Marks::Accumulate (symphony, Marks::Symphony);
    NS_TEST_EXPECT_MSG_EQ (Marks::Read (symphony), Marks::Symphony, "Symphony alone");
    Marks::Accumulate (symphony, Marks::Queue);
    NS_TEST_EXPECT_MSG_EQ (Marks::Read (symphony), Marks::Both, "Symphony then queue");
  }
};

class ConWeaveSharedCeTest : public TestCase
{
public:
  explicit ConWeaveSharedCeTest (bool queueOnly = false)
    : TestCase (queueOnly ? "ConWeave separates queue NOTIFYs while preserving transport CE"
                          : "ConWeave preserves Symphony tags, reorders data and consumes shared CE"),
      m_queueOnly (queueOnly) {}

private:
  bool m_queueOnly;
  Ptr<ConWeaveRouting> m_destination;
  std::vector<uint32_t> m_sequence;
  std::vector<uint32_t> m_notifyPaths;
  std::vector<uint8_t> m_deliveryEcn;

  void ForwardAtSpine (Ptr<Packet> packet, CustomHeader &header, uint32_t port, uint32_t queue)
  {
    ConWeaveDataTag path;
    NS_TEST_ASSERT_MSG_EQ (packet->PeekPacketTag (path), true, "ConWeave metadata survived");
    NS_TEST_ASSERT_MSG_EQ (path.GetHopCount (), 1, "spine advanced the ConWeave hop");
    NS_TEST_ASSERT_MSG_EQ (port, (path.GetPhase () == 1 ? 8u : 7u), "directed to the intended port");
    m_destination->RouteInput (packet, header);
  }

  void ObserveDestination (Ptr<Packet> packet, CustomHeader &header)
  {
    ConWeaveNotifyTag notify;
    if (packet->PeekPacketTag (notify))
      {
        m_notifyPaths.push_back (notify.GetPathId ());
        return;
      }
    ConWeaveReplyTag reply;
    if (packet->PeekPacketTag (reply))
      {
        return;
      }
    RingProgressTag progress;
    FlowStatTag stat;
    NS_TEST_ASSERT_MSG_EQ (packet->PeekPacketTag (progress), true, "ring progress survives forwarding");
    NS_TEST_ASSERT_MSG_EQ (progress.GetProgress ().collectiveId, 7, "collective ID unchanged");
    NS_TEST_ASSERT_MSG_EQ (progress.GetProgress ().step, 3, "logical step unchanged");
    NS_TEST_ASSERT_MSG_EQ (packet->PeekPacketTag (stat), true, "flow boundary metadata survives");
    NS_TEST_ASSERT_MSG_EQ (stat.GetType (), (header.udp.seq == 3000 ?
                         FlowStatTag::FLOW_END : FlowStatTag::FLOW_NOTEND), "last-write flag unchanged");
    // Test injection maps these four sequence numbers to all four cause masks.
    NS_TEST_ASSERT_MSG_EQ (EcnMarkProvenanceTag::Read (packet), header.udp.seq / 1000,
                           "provenance survives switching and the VOQ");
    m_sequence.push_back (header.udp.seq);
    m_deliveryEcn.push_back (header.GetIpv4EcnBits ());
  }

  void Send (Ptr<ConWeaveRouting> spine, uint32_t psn, uint32_t phase,
             bool tail, bool queueMark, bool symphonyMark)
  {
    auto packet = MakeSymphonyPacket (7, 3, psn, psn == 3);
    PppHeader ppp;
    Ipv4Header ip;
    packet->RemoveHeader (ppp);
    packet->RemoveHeader (ip);
    ip.SetSource (Settings::node_id_to_ip (0));
    ip.SetDestination (Settings::node_id_to_ip (4));
    packet->AddHeader (ip);
    packet->AddHeader (ppp);
    const uint8_t local = (queueMark ? EcnMarkProvenanceTag::Queue : 0) |
                          (symphonyMark ? EcnMarkProvenanceTag::Symphony : 0);
    EcnMarkProvenanceTag::Accumulate (packet, local);
    SwitchNode::ApplyCongestionMarks (packet, queueMark, symphonyMark);
    // A later unmarked hop must preserve CE set upstream.
    SwitchNode::ApplyCongestionMarks (packet, false, false);

    ConWeaveDataTag path;
    path.SetPathId (phase == 1 ? (8u << 8) | 2u : (7u << 8) | 1u);
    path.SetHopCount (0);
    path.SetEpoch (1);
    path.SetPhase (phase);
    path.SetTimestampTx (tail ? 200 : 100);
    path.SetTimestampTail (200);
    path.SetFlagData (tail ? ConWeaveDataTag::TAIL : ConWeaveDataTag::DATA);
    packet->AddPacketTag (path);

    CustomHeader header (CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
    packet->PeekHeader (header);
    spine->RouteInput (packet, header);
  }

  void DoRun () override
  {
    const auto hostMap = Settings::hostIp2SwitchId;
    const bool previousMode = ConWeaveRouting::m_queueOnlyNotify;
    ConWeaveRouting::m_queueOnlyNotify = m_queueOnly;
    Settings::hostIp2SwitchId[Settings::node_id_to_ip (0).Get ()] = 12;
    Settings::hostIp2SwitchId[Settings::node_id_to_ip (4).Get ()] = 13;

    auto spine = CreateObject<ConWeaveRouting> ();
    spine->SetSwitchInfo (false, 14);
    spine->SetSwitchSendCallback (MakeCallback (&ConWeaveSharedCeTest::ForwardAtSpine, this));
    m_destination = CreateObject<ConWeaveRouting> ();
    m_destination->SetSwitchInfo (true, 13);
    m_destination->SetSwitchSendToDevCallback (
        MakeCallback (&ConWeaveSharedCeTest::ObserveDestination, this));

    Send (spine, 0, 0, false, false, false);
    Send (spine, 2, 1, false, false, true);
    Send (spine, 3, 1, false, true, true);
    NS_TEST_EXPECT_MSG_EQ (m_sequence.size (), 1, "new-path packets wait for TAIL");
    NS_TEST_EXPECT_MSG_EQ (m_destination->GetVolumeVOQ (), 2, "two packets buffered");
    Send (spine, 1, 0, true, true, false);
    Simulator::Stop (MicroSeconds (1));
    Simulator::Run ();

    const std::vector<uint32_t> expectedSequence {0, 1000, 2000, 3000};
    const uint32_t newPath = (8u << 8) | 2u;
    const uint32_t oldPath = (7u << 8) | 1u;
    const auto expectedNotifyPaths = m_queueOnly
        ? std::vector<uint32_t> {newPath, oldPath}
        : std::vector<uint32_t> {newPath, newPath, oldPath};
    const std::vector<uint8_t> expectedEcn {0, 3, 3, 3};
    const bool inOrder = m_sequence == expectedSequence;
    const bool expectedNotifications = m_notifyPaths == expectedNotifyPaths;
    const bool cePreserved = m_deliveryEcn == expectedEcn;
    NS_TEST_EXPECT_MSG_EQ (inOrder, true, "TAIL precedes buffered packets delivered in FIFO order");
    NS_TEST_EXPECT_MSG_EQ (expectedNotifications, true, "only eligible marks generate NOTIFY");
    NS_TEST_EXPECT_MSG_EQ (cePreserved, true, "CE survives forwarding and VOQ flush");
    NS_TEST_EXPECT_MSG_EQ (m_destination->GetNumVOQ (), 0, "VOQ drains");
    Simulator::Destroy ();
    Settings::hostIp2SwitchId = hostMap;
    ConWeaveRouting::m_queueOnlyNotify = previousMode;
  }
};

//-----------------------------------------------------------------------------
class PointToPointTestSuite : public TestSuite
{
public:
  PointToPointTestSuite ();
};

PointToPointTestSuite::PointToPointTestSuite ()
  : TestSuite ("devices-point-to-point", UNIT)
{
  AddTestCase (new PointToPointTest);
  AddTestCase (new SymphonyAlphaTest);
  AddTestCase (new SymphonyEgressTest);
  AddTestCase (new EcnMarkProvenanceTest);
  AddTestCase (new ConWeaveSharedCeTest);
  AddTestCase (new ConWeaveSharedCeTest (true));
}

static PointToPointTestSuite g_pointToPointTestSuite;

} // namespace ns3
