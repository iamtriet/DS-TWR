"""One-time BLE setup; radio timing and aggregation then run on mobile node 1.

No OTA, LED, actuator or IMU commands are issued by this workflow.
"""
import re
from control_backend import Session


class GatewayFleet:
    def __init__(self,boards,emit,slot_ms=10,period_ms=500,network_id=0x43594231,factory=Session):
        if not 2<=len(boards)<=30:raise ValueError('Select 2–30 nodes')
        if len({b['name'] for b in boards})!=len(boards) or len({b['address'] for b in boards})!=len(boards):raise ValueError('Duplicate board')
        if slot_ms<10 or period_ms<len(boards)*slot_ms+20 or period_ms>500:raise ValueError('Experimental slot/superframe bounds')
        self.boards=boards;self.emit=emit;self.slot_ms=slot_ms;self.period_ms=period_ms;self.network_id=network_id
        self.factory=factory;self.started=[];self.identities={};self.failures=[]
    def session(self,board):return self.factory(board['name'],self.emit,address=board['address'])
    async def configure(self):
        # Followers listen first; the coordinator starts only once all are ready.
        for index in [*range(1,len(self.boards)),0]:
            board=self.boards[index];session=self.session(board)
            if board not in self.started:self.started.append(board)
            self.emit('gateway',f"Configuring {index+1}/{len(self.boards)}: {board['name']}")
            try:
                await session.open()
                await session.command('uwb stop')
                caps=await session.command('uwb capabilities')
                if not any('experimental_sparse_BP=1' in line for line in caps):
                    raise RuntimeError(f"{board['name']}: install candidate firmware with experimental_sparse_BP support first")
                info=await session.command('info')
                version=next((tuple(map(int,m.groups())) for line in info if (m:=re.search(r'PCB_VERSION=(\d+)\.(\d+)\.(\d+)',line))),None)
                if version is None or version<(1,0,18):
                    raise RuntimeError(f"{board['name']}: gateway requires firmware 1.0.18+ (fresh DW clock and continuous RX fixes)")
                uid=next((m[1] for line in info if (m:=re.search(r'FICR ID=([0-9a-fA-F]{16})',line))),None)
                if uid is None:raise RuntimeError('Missing hardware identity')
                if uid in self.identities.values():raise RuntimeError('Duplicate hardware UID')
                self.identities[index+1]=uid
                await session.command('uwb init')
                await session.command(f'net configure {index+1} {len(self.boards)} {self.slot_ms} {self.period_ms} {self.network_id:X}')
                await session.command('net start experimental')
            finally:await session.close()
        self.emit('gateway','Experimental sparse BP active; timing is on the boards, UART gateway is node 1')
    async def recover(self,node):
        """Restore a verified board that rebooted/stopped; leave running radios alone."""
        board=self.boards[node-1];session=self.session(board)
        try:
            await session.open();info=await session.command('info')
            uid=next((m[1] for line in info if (m:=re.search(r'FICR ID=([0-9a-fA-F]{16})',line))),None)
            if uid is None or uid.lower()!=self.identities.get(node,'').lower():raise RuntimeError('Recovery hardware UID mismatch')
            status=await session.command('net status')
            if any('running=1' in line for line in status):return False
            if not any('running=0' in line for line in status):raise RuntimeError('Recovery: unknown radio state')
            await session.command('uwb init')
            await session.command(f'net configure {node} {len(self.boards)} {self.slot_ms} {self.period_ms} {self.network_id:X}')
            await session.command('net start experimental')
            self.emit('gateway',f'Recovered node {node:04X}: {board["name"]}')
            return True
        finally:await session.close()
    async def stop(self):
        remaining=[];errors=[]
        # Stop the clock first so followers cannot acquire another transmit slot.
        ordered=sorted(self.started,key=lambda b:self.boards.index(b))
        for board in ordered:
            session=self.session(board)
            try:
                await session.open();await session.command('uwb stop')
            except Exception as exc:remaining.append(board);errors.append(f"{board['name']}: {exc}")
            finally:await session.close()
        self.started=remaining;self.failures=errors
        return errors
