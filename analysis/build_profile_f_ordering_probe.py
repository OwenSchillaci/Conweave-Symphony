#!/usr/bin/env python3
"""Build a separate log-only Profile F ordering probe over existing ns-3 objects."""

import argparse
import concurrent.futures
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "src/point-to-point/model"

PROBES = {
    "rdma-hw": [
        ('''    bool cnp_check = false;
    int x = ReceiverCheckSeq(ch.udp.seq, rxQp, payload_size, cnp_check);''',
         '''    const auto auditExpected = rxQp->ReceiverNextExpectedSeq;
    bool cnp_check = false;
    int x = ReceiverCheckSeq(ch.udp.seq, rxQp, payload_size, cnp_check);
    std::cout << "ORDER_RX time_ns=" << Simulator::Now().GetNanoSeconds()
              << " flow_id=" << rxQp->m_flow_id << " uid=" << p->GetUid()
              << " seq=" << ch.udp.seq << " bytes=" << payload_size
              << " expected=" << auditExpected
              << " next=" << rxQp->ReceiverNextExpectedSeq
              << " action=" << x << " gap_cnp=" << cnp_check
              << " ce=" << unsigned(ecnbits) << '\\n';'''),
        ('''        newp->AddHeader(head);
        AddHeader(newp, 0x800);''',
         '''        std::cout << "ORDER_ACK_TX time_ns=" << Simulator::Now().GetNanoSeconds()
                  << " flow_id=" << rxQp->m_flow_id << " uid=" << newp->GetUid()
                  << " data_uid=" << p->GetUid() << " ack=" << seqh.GetSeq()
                  << " nack=" << (x == 2) << " gap_cnp=" << cnp_check << '\\n';
        newp->AddHeader(head);
        AddHeader(newp, 0x800);'''),
        ('''        if (qp->IsFinished()) {
            QpComplete(qp);''',
         '''        std::cout << "ORDER_ACK_RX time_ns=" << Simulator::Now().GetNanoSeconds()
                  << " flow_id=" << qp->m_flow_id << " uid=" << p->GetUid()
                  << " ack=" << seq << " snd_una=" << qp->snd_una
                  << " snd_nxt=" << qp->snd_nxt << " nack=" << (ch.l3Prot == 0xFD)
                  << " cnp=" << unsigned(cnp) << '\\n';
        if (qp->IsFinished()) {
            QpComplete(qp);'''),
        ('''    } else if (ch.l3Prot == 0xFD)  // NACK
        RecoverQueue(qp);''',
         '''    } else if (ch.l3Prot == 0xFD) {  // NACK
        std::cout << "ORDER_REWIND time_ns=" << Simulator::Now().GetNanoSeconds()
                  << " flow_id=" << qp->m_flow_id << " reason=nack"
                  << " before=" << qp->snd_nxt << " after=" << qp->snd_una << '\\n';
        RecoverQueue(qp);
    }'''),
        ('''    qp->m_ipid++;

    // return
    return p;''',
         '''    qp->m_ipid++;

    std::cout << "ORDER_TX time_ns=" << Simulator::Now().GetNanoSeconds()
              << " flow_id=" << qp->m_flow_id << " uid=" << p->GetUid()
              << " seq=" << seq << " bytes=" << payload_size << '\\n';
    // return
    return p;'''),
        ('''    RecoverQueue(qp);
    dev->TriggerTransmit();
}

void RdmaHw::UpdateNextAvail''',
         '''    std::cout << "ORDER_REWIND time_ns=" << Simulator::Now().GetNanoSeconds()
              << " flow_id=" << qp->m_flow_id << " reason=rto"
              << " before=" << qp->snd_nxt << " after=" << qp->snd_una << '\\n';
    RecoverQueue(qp);
    dev->TriggerTransmit();
}

void RdmaHw::UpdateNextAvail'''),
    ],
    "conweave-routing": [
        ('''            p->AddPacketTag(conweaveDataTag);''',
         '''            if (conweaveDataTag.GetFlagData() == ConWeaveDataTag::TAIL) {
                FlowIDNUMTag auditFlow;
                p->PeekPacketTag(auditFlow);
                std::cout << "ORDER_TAIL time_ns=" << now.GetNanoSeconds()
                          << " flow_id=" << auditFlow.GetId() << " uid=" << p->GetUid()
                          << " seq=" << ch.udp.seq << " path=" << tx_md.currPath
                          << " epoch=" << tx_md.epoch << '\\n';
            }
            p->AddPacketTag(conweaveDataTag);'''),
        ('''                rx_md.pkt_ecnbits = ch.GetIpv4EcnBits(); /* ECN bits */''',
         '''                rx_md.pkt_ecnbits = ch.GetIpv4EcnBits(); /* ECN bits */
                FlowIDNUMTag auditFlow;
                p->PeekPacketTag(auditFlow);
                std::cout << "ORDER_TOR_RX time_ns=" << now.GetNanoSeconds()
                          << " flow_id=" << auditFlow.GetId() << " uid=" << p->GetUid()
                          << " seq=" << ch.udp.seq << " path=" << rx_md.pkt_pathId
                          << " epoch=" << rx_md.pkt_epoch << " known_epoch=" << rxEntry._epoch
                          << " phase=" << rx_md.pkt_phase << " flag=" << rx_md.pkt_flagData
                          << " source_time_ns=" << rx_md.pkt_timestamp_Tx
                          << " tail_source_time_ns=" << rx_md.pkt_timestamp_TAIL
                          << " flow_key=" << rx_md.pkt_flowkey << '\\n';'''),
    ],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-build", default=ROOT / "build", type=Path)
    parser.add_argument("--headers", type=Path, help="directory containing ns3 headers, defaults to base build")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    base, output = args.base_build.resolve(), args.output.resolve()
    include = (args.headers or base).resolve()
    output.mkdir(parents=True)
    (output / "scratch").mkdir()

    sources = {}
    for name in ("switch-node", "conweave-routing", "conweave-voq",
                 "ecn-mark-provenance-tag", "rdma-hw", "network-load-balance"):
        source = ROOT / "scratch/network-load-balance.cc" if name == "network-load-balance" else MODEL / (name + ".cc")
        text = source.read_text()
        for old, new in PROBES.get(name, ()):
            if old not in text:
                raise ValueError(f"ordering probe anchor missing in {source}")
            text = text.replace(old, new, 1)
        path = output / source.name
        path.write_text(text)
        sources[name] = path

    def compile_one(task):
        name, source = task
        subprocess.run(["g++", "-O2", "-w", "-std=gnu++11", "-fPIC",
                        f"-I{include}", f"-I{MODEL}", f"-I{ROOT / 'scratch'}",
                        "-DHAVE_PACKET_H=1", "-DHAVE_IF_TUN_H=1",
                        "-c", str(source), "-o", str(output / (name + ".o"))],
                       cwd=ROOT, check=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(compile_one, sources.items()))
    objects = [p for p in (base / "src/point-to-point").rglob("*.1.o")
               if p.name[:-len(".cc.1.o")] not in sources]
    # Use the same module variant as the simulator linked in the base build.
    deps = subprocess.check_output(
        ["readelf", "-d", str(base / "scratch/network-load-balance")], text=True)
    libraries = re.findall(r"\[(libns3[^\]]+)\]", deps)
    p2p, = [lib for lib in libraries if "-point-to-point-" in lib and "-layout-" not in lib]
    module_deps = [lib for lib in libraries if re.search(r"-(internet|mpi|network|core)-", lib)]
    subprocess.run(["g++", "-shared", f"-Wl,-soname,{p2p}", "-o", str(output / p2p),
                    *[str(output / (name + ".o")) for name in sources if name != "network-load-balance"],
                    *map(str, objects), f"-L{base}", *["-l:" + lib for lib in module_deps]],
                   cwd=ROOT, check=True)
    subprocess.run(["g++", "-o", str(output / "scratch/network-load-balance"),
                    str(output / "network-load-balance.o"), f"-L{output}", f"-L{base}",
                    f"-Wl,-rpath-link,{output}", f"-Wl,-rpath-link,{base}",
                    *["-l:" + lib for lib in libraries], "-pthread"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
