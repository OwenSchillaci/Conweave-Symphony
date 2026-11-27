#ifndef RING_PROGRESS_TAG_H
#define RING_PROGRESS_TAG_H

#include "ns3/tag.h"

namespace ns3 {

struct RingProgress {
    uint32_t collectiveId = 0;
    uint32_t step = UINT32_MAX;

    bool IsPresent() const { return step != UINT32_MAX; }
};

class RingProgressTag : public Tag {
  public:
    RingProgressTag() = default;
    explicit RingProgressTag(RingProgress progress) : m_progress(progress) {}

    static TypeId GetTypeId() {
        static TypeId tid = TypeId("ns3::RingProgressTag")
                                .SetParent<Tag>()
                                .AddConstructor<RingProgressTag>();
        return tid;
    }
    TypeId GetInstanceTypeId() const override { return GetTypeId(); }
    uint32_t GetSerializedSize() const override { return 8; }
    void Serialize(TagBuffer buffer) const override {
        buffer.WriteU32(m_progress.collectiveId);
        buffer.WriteU32(m_progress.step);
    }
    void Deserialize(TagBuffer buffer) override {
        m_progress.collectiveId = buffer.ReadU32();
        m_progress.step = buffer.ReadU32();
    }
    void Print(std::ostream &os) const override {
        os << "collective_id=" << m_progress.collectiveId << " step=" << m_progress.step;
    }
    RingProgress GetProgress() const { return m_progress; }

  private:
    RingProgress m_progress;
};

}  // namespace ns3

#endif
