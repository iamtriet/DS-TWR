"""Combine BLE discovery with UWB presence; connected BLE devices may not advertise."""
def membership_plan(boards, origin, state, discovered, now):
    known={b['address'].upper() for b in boards}
    visible={b['address'].upper():b for b in discovered}
    additions=[b for key,b in visible.items() if key not in known]
    if not additions:return None
    last={int(n['id']):n['last'] for n in state.get('nodes',[])}
    keep=[boards[0]];removed=[]
    for node,board in enumerate(boards[1:],1):
        if board['address'].upper() in visible or now-last.get(node,float('-inf'))<10:
            keep.append(board)
        else:removed.append(board)
    keep.extend(sorted(additions,key=lambda b:b['name']))
    if not 4<=len(keep)<=18:return None
    return dict(boards=keep,removed=removed,additions=additions)
