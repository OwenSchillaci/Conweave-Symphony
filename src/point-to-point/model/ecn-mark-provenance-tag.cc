#include "ecn-mark-provenance-tag.h"

namespace ns3 {

NS_OBJECT_ENSURE_REGISTERED(EcnMarkProvenanceTag);

TypeId EcnMarkProvenanceTag::GetTypeId() {
    static TypeId tid = TypeId("ns3::EcnMarkProvenanceTag")
        .SetParent<Tag>()
        .AddConstructor<EcnMarkProvenanceTag>();
    return tid;
}

TypeId EcnMarkProvenanceTag::GetInstanceTypeId() const { return GetTypeId(); }

uint32_t EcnMarkProvenanceTag::GetSerializedSize() const { return 1; }

void EcnMarkProvenanceTag::Serialize(TagBuffer buffer) const { buffer.WriteU8(m_marks); }

void EcnMarkProvenanceTag::Deserialize(TagBuffer buffer) { m_marks = buffer.ReadU8(); }

void EcnMarkProvenanceTag::Print(std::ostream &out) const { out << unsigned(m_marks); }

uint8_t EcnMarkProvenanceTag::Read(Ptr<const Packet> packet) {
    EcnMarkProvenanceTag tag;
    packet->PeekPacketTag(tag);
    return tag.m_marks;
}

uint8_t EcnMarkProvenanceTag::Accumulate(Ptr<Packet> packet, uint8_t marks) {
    EcnMarkProvenanceTag tag;
    packet->RemovePacketTag(tag);
    tag.m_marks |= marks;
    if (tag.m_marks != Neither) packet->AddPacketTag(tag);
    return tag.m_marks;
}

}  // namespace ns3
