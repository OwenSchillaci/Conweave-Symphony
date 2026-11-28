#ifndef ECN_MARK_PROVENANCE_TAG_H
#define ECN_MARK_PROVENANCE_TAG_H

#include "ns3/packet.h"
#include "ns3/tag.h"

namespace ns3 {

// Simulator-only evidence of which mechanisms marked this packet.
class EcnMarkProvenanceTag : public Tag {
   public:
    enum Cause : uint8_t { Neither = 0, Queue = 1, Symphony = 2, Both = 3 };

    static TypeId GetTypeId();
    TypeId GetInstanceTypeId() const override;
    uint32_t GetSerializedSize() const override;
    void Serialize(TagBuffer buffer) const override;
    void Deserialize(TagBuffer buffer) override;
    void Print(std::ostream &out) const override;

    static uint8_t Read(Ptr<const Packet> packet);
    static uint8_t Accumulate(Ptr<Packet> packet, uint8_t marks);

   private:
    uint8_t m_marks = Neither;
};

}  // namespace ns3

#endif
