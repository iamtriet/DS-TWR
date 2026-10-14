"""Quick unit test for dstwr_live parser."""
import sys, struct
sys.path.insert(0, 'tools')
import matplotlib; matplotlib.use('Agg')
from dstwr_live import SnifferParser, LiveDSTWRAssembler, LivePipeline, crc32_compute

print('Import OK')

# Test ASCII text parsing
p = SnifferParser()
p.feed(b'[DSTWR] Started: master=1, sync=2\r\n')
assert len(p.text_lines) == 1
print('Text parse OK:', p.text_lines[0])

# Build a synthetic binary sniffer report
payload = bytearray()
payload.append(0x01)  # report type
payload.append(0x03)  # source node 3
payload.extend(b'\x00\x01\x02\x03\x04')  # tx_ts
payload.extend(b'\x10\x11\x12\x13\x14')  # sniffer_rx_ts
payload.extend(struct.pack('<I', 42))     # sf_counter = 42
payload.append(2)  # 2 replies

# Reply 1: peer=1, reply_time=0x12345678, nlos=10
payload.append(1)
payload.extend(struct.pack('<I', 0x12345678))
payload.append(10)

# Reply 2: peer=2, reply_time=0xAABBCCDD, nlos=5
payload.append(2)
payload.extend(struct.pack('<I', 0xAABBCCDD))
payload.append(5)

crc = crc32_compute(bytes(payload))

# Build frame: [0xAA] [len_lo len_hi] [payload] [CRC32]
frame = bytearray()
frame.append(0xAA)
frame.extend(struct.pack('<H', len(payload)))
frame.extend(payload)
frame.extend(struct.pack('<I', crc))

p.feed(bytes(frame))
assert len(p.reports) == 1
r = p.reports[0]
print(f"Binary parse OK: src={r['source_id']}, sf={r['sf_counter']}, replies={len(r['replies'])}")
assert r['source_id'] == 3
assert r['sf_counter'] == 42
assert len(r['replies']) == 2
assert r['replies'][0] == (1, 0x12345678, 10)
assert r['replies'][1] == (2, 0xAABBCCDD, 5)

# Test interleaved: ASCII then binary then ASCII
p2 = SnifferParser()
p2.feed(b'hello world\r\n')
p2.feed(bytes(frame))
p2.feed(b'goodbye\r\n')
assert len(p2.text_lines) == 2
assert len(p2.reports) == 1
print('Interleave test OK')

# Test pipeline creation
pipeline = LivePipeline(n_nodes=6, arena_size=3.0)
print('Pipeline created OK')

print('\nAll tests PASSED')
