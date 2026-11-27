#include "ns3/test.h"
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
class SymphonyAlphaTest : public TestCase {
public:
  SymphonyAlphaTest () : TestCase ("Symphony adaptive alpha windows and marking") {}

private:
  void PacketAt (Ptr<SwitchNode> sw, uint32_t collective, uint32_t step, uint32_t psn,
                 bool last = false)
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
    sw->SymphonyMark (packet, false);
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
}

static PointToPointTestSuite g_pointToPointTestSuite;

} // namespace ns3
