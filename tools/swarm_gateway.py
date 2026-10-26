"""BLE provisioning for one passive sniffer + mobile nodes; sniffer ID is zero."""
from uwb3d_gateway import GatewayFleet
class SwarmFleet(GatewayFleet):
    async def open_session(self,board):
        import asyncio
        for attempt in range(3):
            session=self.session(board)
            try:
                await session.open();return session
            except asyncio.CancelledError:
                await session.close();raise
            except Exception:
                await session.close()
                if attempt==2:raise
                self.emit('gateway',f'Retrying BLE connection: {board["name"]}')
                await asyncio.sleep(2)

    async def close_led_sessions(self,nodes=None):
        for node in list(self.led_sessions) if nodes is None else list(nodes):
            cached=self.led_sessions.pop(node,None)
            if cached:await cached[0].close()

    async def manual_led(self,nodes,command):
        import re,asyncio,time
        from led_protocol import wire_command,logical_status,command_ack
        wire=wire_command(command);nodes=list(dict.fromkeys(nodes));results=[]
        async def send(node):
            started=time.monotonic();warm=False
            try:
                if node not in self.identities:raise ValueError('Unknown node identity')
                cached=self.led_sessions.get(node)
                if cached:
                    session,uid=cached
                    client=getattr(session,'client',None)
                    if uid!=self.identities[node] or getattr(session,'closed',False) or getattr(session,'desynchronized',False) or (client is not None and not client.is_connected):
                        await self.close_led_sessions([node]);cached=None
                if cached:session=cached[0];warm=True
                else:
                    board=self.boards[0 if node==self.origin else node]
                    session=await self.open_session(board)
                    # Register immediately so failures/cancellation also close it.
                    self.led_sessions[node]=(session,self.identities[node])
                    info=await session.command('info')
                    uid=next((m[1] for line in info if (m:=re.search(r'FICR ID=([0-9a-fA-F]{16})',line))),None)
                    if not uid or uid.lower()!=self.identities[node].lower():raise ValueError('Hardware UID mismatch')
                    caps=await session.command('led aux status')
                    if not any(line.startswith('AUX_LED v=1 ') for line in caps):raise RuntimeError('Manual LED requires firmware 1.2.7')
                reply=await session.command(wire)
                if command=='led aux status':
                    line=next((logical_status(line) for line in reply if line.startswith('AUX_LED v=1 ')),None)
                    if not line:raise RuntimeError('Missing LED status')
                else:line=command_ack(command)
                elapsed=round((time.monotonic()-started)*1000)
                result=f'{node:04X}: {line} elapsed_ms={elapsed} connection={"warm" if warm else "new"}'
                self.emit('gateway',result);return result
            except BaseException as exc:
                await self.close_led_sessions([node])
                if isinstance(exc,asyncio.CancelledError):raise
                return f'{node:04X}: FAILED: {exc}'
        async with self.control_lock:
            # Bound retained Windows BLE connections to two. Larger selections
            # are sent in pairs; repeat commands to the same pair are warm.
            for start in range(0,len(nodes),2):
                batch=nodes[start:start+2]
                await self.close_led_sessions(set(self.led_sessions)-set(batch))
                results.extend(await asyncio.gather(*(send(n) for n in batch)))
        return results

    async def stop(self):
        async with self.control_lock:
            await self.close_led_sessions()
            return await super().stop()

    async def recover(self,node,restart_running=False):
        async with self.control_lock:
            await self.close_led_sessions([node])
            return await self._recover(node,restart_running)

    async def _recover(self,node,restart_running=False):
        import re
        version=self.versions.get(node,'0.0.0')
        if tuple(map(int,version.split('.'))) < (1,2,3):
            raise RuntimeError('Non-disruptive recovery requires firmware 1.2.3')
        board=self.boards[0 if node==self.origin else node]
        session=await self.open_session(board)
        try:
            info=await session.command('info')
            uid=next((m[1] for line in info if (m:=re.search(r'FICR ID=([0-9a-fA-F]{16})',line))),None)
            if not uid or uid.lower()!=self.identities.get(node,'').lower():raise RuntimeError('Recovery hardware UID mismatch')
            status=await session.command('swarm status')
            running=any('SWARM running=1' in line for line in status)
            if running:
                radio=await session.command('uwb status')
                failed=any('UWB ID=' in line and 'ready=0' in line for line in radio)
                if not failed and not restart_running:return False
                self.emit('gateway',f'Node {node:04X}: UWB stream absent or radio fault; reinitializing')
                await session.command('uwb stop')
            elif not any('SWARM running=0' in line for line in status):raise RuntimeError('Unknown swarm state')
            await session.command('uwb init')
            await session.command(f'swarm configure {node} {self.count} 53574131')
            if node==self.origin:await session.command('swarm collector 1')
            await session.command('swarm start')
            self.emit('gateway',f'Restored swarm node {node:04X}: {board["name"]}')
            return True
        finally:await session.close()

    def __init__(self,boards,emit,origin=0):
        if not 4<=len(boards)<=18:raise ValueError('UART swarm requires 1 sniffer + 3–17 mobile nodes; 30 need a faster transport')
        import asyncio
        self.control_lock=asyncio.Lock();self.led_sessions={}
        self.boards=boards;self.emit=emit;self.started=[];self.identities={};self.failures=[];self.versions={}
        if origin and origin!=len(boards):raise ValueError('Active collector must use the last ID; mobile IDs stay unchanged')
        self.origin=origin;self.count=len(boards) if origin else len(boards)-1
        if self.count>17:raise ValueError('UART supports at most 17 transmitting nodes')
        from control_backend import Session
        self.factory=Session
    async def configure(self):
        import re
        # Start RX-only collector first, then highest node IDs; lowest ID last.
        for index in [0,*range(len(self.boards)-1,0,-1)]:
            node=self.origin if index==0 else index
            board=self.boards[index];session=await self.open_session(board)
            if board not in self.started:self.started.append(board)
            self.emit('gateway',f"Swarm: configuring {'passive sniffer' if index==0 else f'mobile {index}'} {board['name']}")
            try:
                await session.command('uwb stop')
                caps=await session.command('uwb capabilities')
                if not any('SWARM broadcast_DS_TWR=1' in line for line in caps):raise RuntimeError(f"{board['name']}: install swarm 1.2.2 firmware first")
                info=await session.command('info')
                uid=next((m[1] for line in info if (m:=re.search(r'FICR ID=([0-9a-fA-F]{16})',line))),None)
                if not uid or uid in self.identities.values():raise ValueError('Missing/duplicate board UID')
                self.identities[node]=uid
                self.versions[node]=next((m[1] for line in info if (m:=re.search(r'PCB_VERSION=([0-9.]+)',line))),'unknown')
                version=self.versions[node]
                if not re.fullmatch(r'\d+\.\d+\.\d+',version) or tuple(map(int,version.split('.'))) < (1,2,2):
                    raise RuntimeError(f"{board['name']}: install swarm 1.2.2+ with corrected delayed-TX scheduling")
                await session.command('uwb init')
                if index==0 and self.origin and tuple(map(int,version.split('.'))) < (1,2,6):raise RuntimeError('Active collector requires firmware 1.2.6+')
                await session.command(f'swarm configure {node} {self.count} 53574131')
                if index==0 and self.origin:await session.command('swarm collector 1')
                await session.command('swarm start')
            finally:await session.close()
        self.emit('gateway','Active collector is the stationary position origin; heading remains relative' if self.origin else 'Anchor-free swarm active; sniffer has no UWB TX; body calibration absent')
