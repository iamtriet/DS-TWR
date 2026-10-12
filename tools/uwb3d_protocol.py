"""U3D v1: little endian, COBS delimiter 0, CRC32/ISO-HDLC (zlib).

Radio timestamps are 40-bit DW time units; transport timestamps are gateway
monotonic microseconds. These two clocks must never be used interchangeably.
"""
import struct
import zlib
from dataclasses import dataclass
from collections import Counter

VERSION=1
DISCOVERY,STATUS,RANGE,TIMESTAMPS,IMU,NETWORK,HEARTBEAT,CONFIG_REQUEST,CONFIG_RESPONSE,ERROR=range(1,11)
HEADER=struct.Struct('<BBHIQ')
RANGE_BODY=struct.Struct('<HHiIQHB')
DISCOVERY_BODY=struct.Struct('<HQHHHI')
MAX_PAYLOAD=4096
MASK40=(1<<40)-1
DTU=1/(499.2e6*128)
C=299702547.


def node_id(n):
    if not isinstance(n,int) or not 0<n<65535: raise ValueError('Invalid node ID')
    return n


def cobs_encode(raw):
    out=bytearray([0]); code=1; mark=0
    for b in raw:
        if b:
            out.append(b); code+=1
            if code==255: out[mark]=code;mark=len(out);out.append(0);code=1
        else: out[mark]=code;mark=len(out);out.append(0);code=1
    out[mark]=code
    return bytes(out)


def cobs_decode(raw):
    out=bytearray();i=0
    while i<len(raw):
        count=raw[i];i+=1
        if not count or i+count-1>len(raw): raise ValueError('COBS length')
        out.extend(raw[i:i+count-1]);i+=count-1
        if count<255 and i<len(raw): out.append(0)
    return bytes(out)


@dataclass(frozen=True)
class Packet:
    kind:int
    sequence:int
    timestamp_us:int
    payload:bytes
    reset:bool=False


def encode(kind,sequence,timestamp_us,payload=b''):
    if kind not in range(1,11) or len(payload)>MAX_PAYLOAD: raise ValueError('Packet bounds')
    raw=HEADER.pack(VERSION,kind,len(payload),sequence&0xffffffff,timestamp_us)+payload
    return cobs_encode(raw+struct.pack('<I',zlib.crc32(raw)))+b'\0'


class Decoder:
    def __init__(self): self.buffer=bytearray();self.last=None;self.last_stamp=None;self.stats=Counter();self.discard=False
    def feed(self,chunk):
        packets=[]
        for b in chunk:
            if b:
                if not self.discard:self.buffer.append(b)
                if len(self.buffer)>MAX_PAYLOAD+64:
                    self.buffer.clear();self.discard=True;self.stats['oversized']+=1
                continue
            if self.discard:self.discard=False;continue
            if not self.buffer:continue
            raw=bytes(self.buffer);self.buffer.clear()
            try:
                data=cobs_decode(raw)
                if len(data)<HEADER.size+4:raise ValueError('short')
                version,kind,size,seq,stamp=HEADER.unpack_from(data)
                if version!=VERSION:raise ValueError('version')
                if kind not in range(1,11) or size>MAX_PAYLOAD or len(data)!=HEADER.size+size+4:raise ValueError('length')
                if zlib.crc32(data[:-4])!=struct.unpack_from('<I',data,len(data)-4)[0]:raise ValueError('crc')
                reset=kind==DISCOVERY and seq==0 and self.last_stamp is not None and stamp+1000000<self.last_stamp
                if reset:self.last=None;self.stats['resets']+=1
                if self.last is not None:
                    delta=(seq-self.last)&0xffffffff
                    if not delta:self.stats['duplicate']+=1;continue
                    if delta>=0x80000000:self.stats['old']+=1;continue
                    self.stats['dropped']+=delta-1
                self.last=seq;self.last_stamp=stamp;self.stats['accepted']+=1
                packets.append(Packet(kind,seq,stamp,data[HEADER.size:-4],reset))
            except (ValueError,struct.error) as exc:self.stats[str(exc)]+=1
        return packets


def range_payload(a,b,distance,sequence,effective_us,quality=None,nlos=None,valid=True):
    node_id(a);node_id(b)
    if a==b:raise ValueError('Self range')
    flags=int(valid)|(2 if nlos is not None else 0)|(4 if nlos else 0)
    return RANGE_BODY.pack(a,b,round(distance*1000),sequence&0xffffffff,effective_us,
                           65535 if quality is None else min(1000,max(0,round(quality*1000))),flags)


def decode_range(payload):
    if len(payload)!=RANGE_BODY.size:raise ValueError('range length')
    a,b,mm,seq,stamp,q,flags=RANGE_BODY.unpack(payload)
    node_id(a);node_id(b)
    if a==b or flags&~7 or (q>1000 and q!=65535):raise ValueError('range fields')
    if flags&1 and not 0<mm<=1000000:raise ValueError('distance bounds')
    return dict(a=a,b=b,distance=mm/1000,sequence=seq,effective_us=stamp,
                quality=None if q==65535 else q/1000,nlos=bool(flags&4) if flags&2 else None,valid=bool(flags&1))


def ds_distance(pt,pr,rt,rr,ft,fr):
    if any(not isinstance(t,int) or not 0<=t<=MASK40 for t in (pt,pr,rt,rr,ft,fr)):raise ValueError('timestamp width')
    ra=(rr-pt)&MASK40;rb=(fr-rt)&MASK40;da=(ft-rr)&MASK40;db=(rt-pr)&MASK40
    if any(not 0<t<.5/DTU for t in (ra,rb,da,db)):raise ValueError('timestamp order/age')
    # Integer products avoid intermediate integer overflow and cancellation.
    distance=(ra*rb-da*db)/(ra+rb+da+db)*DTU*C
    if not 0<=distance<=1000:raise ValueError('invalid ToF')
    return distance


@dataclass
class Broadcast:
    # Optional UART TIMESTAMPS diagnostic schema; NOT the on-air A7 MAC frame.
    node:int
    frame:int
    tx:int
    received:dict  # (source ID, superframe) -> local RX antenna timestamp

    def pack(self):
        node_id(self.node)
        if len(self.received)>30 or not 0<=self.tx<=MASK40:raise ValueError('broadcast bounds')
        raw=struct.pack('<BHI',1,self.node,self.frame)+self.tx.to_bytes(5,'little')+bytes([len(self.received)])
        for (peer,frame),stamp in sorted(self.received.items()):
            node_id(peer)
            if peer==self.node or not 0<=stamp<=MASK40:raise ValueError('RX timestamp')
            raw+=struct.pack('<HI',peer,frame)+stamp.to_bytes(5,'little')
        return raw

    @classmethod
    def unpack(cls,raw):
        if len(raw)<13 or raw[0]!=1 or raw[12]>30 or len(raw)!=13+11*raw[12]:raise ValueError('broadcast length/version')
        node,frame=struct.unpack_from('<HI',raw,1);node_id(node);rx={}
        for pos in range(13,len(raw),11):
            peer,sf=struct.unpack_from('<HI',raw,pos);node_id(peer)
            if peer==node or (peer,sf) in rx:raise ValueError('duplicate/self RX')
            rx[peer,sf]=int.from_bytes(raw[pos+6:pos+11],'little')
        return cls(node,frame,int.from_bytes(raw[7:12],'little'),rx)


class BroadcastLedger:
    """Bounded pipelined exchanges; no distances from absent timestamps."""
    def __init__(self):self.frames={};self.completed=set()
    def add(self,broadcast):
        b=broadcast;key=(b.node,b.frame)
        if key in self.frames: return []
        self.frames[key]=b
        if len(self.frames)>120:
            oldest=next(iter(self.frames));self.frames.pop(oldest)
            self.completed={k for k in self.completed if (k[0],k[2]) in self.frames}
        results=[]
        for (i,k),poll in list(self.frames.items()):
            final=self.frames.get((i,(k+1)&0xffffffff))
            if final is None:continue
            for j in {n for n,_ in self.frames if n>i}:
                token=(i,j,k)
                if token in self.completed:continue
                response=self.frames.get((j,k));report=self.frames.get((j,(k+1)&0xffffffff))
                if response is None or report is None:continue
                try:
                    d=ds_distance(poll.tx,response.received[i,k],response.tx,final.received[j,k],
                                  final.tx,report.received[i,(k+1)&0xffffffff])
                except (KeyError,ValueError):continue
                self.completed.add(token);results.append((i,j,k,d))
        return results


def timing_budget(nodes=30,slot_ms=3.,superframe_ms=100.,management_ms=10.,preamble=128):
    if not 2<=nodes<=30 or slot_ms<=0 or management_ms<0:raise ValueError('schedule bounds')
    payload=46+9*(nodes-1)+4*nodes # coordinator RX records + assignment table
    # Optimistic lower bound, excluding FEC, processing, SPI and clock guards.
    airtime_us=(preamble+8)*1.01763+21/0.85+(payload+2)*8/6.8
    return dict(payload_bytes=payload,airtime_lower_bound_us=airtime_us,
        arithmetic_fits=nodes*slot_ms+management_ms<=superframe_ms,
        current_radio_frame_fits=payload<=512,hardware_qualified=False,
        firmware_schedule_supported=slot_ms>=10 and nodes*slot_ms+20<=superframe_ms<=500,
        sparse_edges=6+4*(nodes-4) if nodes>=4 else nodes*(nodes-1)//2,
        uart_range_bytes_per_second=(6+4*(nodes-4) if nodes>=4 else nodes*(nodes-1)//2)*46*1000/superframe_ms,
        reason='Experimental extended-PHR delayed TX implemented; >=10 ms slots, <=500 ms period. RF/SPI/IRQ/clock guards need physical qualification. Airtime is a lower bound.')
