"""Control the separately built albifache TDMA/STS DS-TWR reference image."""
import re
import secrets
from control_backend import Session
from uwb3d_gateway import GatewayFleet


class ReferenceFleet(GatewayFleet):
    def __init__(self,boards,emit,period_ms=200,tag=0,anchor_mask=0,network_id=0x4359,factory=Session):
        super().__init__(boards,emit,period_ms=500,factory=factory)
        n=len(boards)
        if not 2<=n<=9:raise ValueError('Reference firmware supports 2–9 nodes (at most 8 anchors)')
        if not 150<=period_ms<=5000:raise ValueError('Reference period must be 150–5000 ms')
        if not 0<=tag<=n or not 0<=anchor_mask<(1<<n):raise ValueError('Invalid reference roles')
        if anchor_mask and (not (anchor_mask & ~(1<<(tag-1))) if tag else anchor_mask.bit_count()<2):
            raise ValueError('Every selected tag needs at least one different anchor')
        if not 0<=network_id<65535:raise ValueError('Invalid reference PAN')
        self.period_ms=period_ms;self.tag=tag;self.anchor_mask=anchor_mask;self.network_id=network_id

    async def configure(self):
        self.identities.clear()
        # Read every UID before starting anyone; coordinator gets a verified roster.
        for i,board in enumerate(self.boards):
            session=self.session(board)
            if board not in self.started:self.started.append(board)
            self.emit('gateway',f"Reference preflight {i+1}/{len(self.boards)}: {board['name']}")
            try:
                await session.open();await session.command('uwb stop')
                caps=await session.command('uwb capabilities')
                if not any('reference_tdma=1' in line for line in caps):
                    raise RuntimeError(f"{board['name']}: install build/releases/reference-1.1.0/reference.signed.bin through Boards & OTA first")
                info=await session.command('info')
                uid=next((m[1] for line in info if (m:=re.search(r'FICR ID=([0-9a-fA-F]{16})',line))),None)
                if uid is None or uid in self.identities.values():raise RuntimeError('Missing or duplicate hardware UID')
                self.identities[i+1]=uid
            finally:await session.close()
        # Fresh keys on every start avoid reusing an AES epoch across sessions.
        # BLE provisioning is a bench mechanism, not authenticated key management.
        aes,sts=secrets.token_hex(16),secrets.token_hex(16)
        for i in [*range(1,len(self.boards)),0]:
            board=self.boards[i];session=self.session(board)
            self.emit('gateway',f"Starting reference {i+1}/{len(self.boards)}: {board['name']}")
            try:
                await session.open();await session.command('uwb init')
                await session.command(f'ref configure {i+1} {len(self.boards)} {self.period_ms} {self.network_id:X}')
                await session.command(f'ref keys {aes} {sts}')
                if i==0:
                    for node,uid in self.identities.items():await session.command(f'ref peer {node} {uid}')
                    await session.command(f'ref roles {self.tag} {self.anchor_mask:X}')
                await session.command('ref start experimental')
            finally:await session.close()
        self.emit('gateway','Reference TDMA DS-TWR active; STS/AES enabled, security and RF timing unqualified')
