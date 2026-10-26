"""Logical colours -> firmware 1.2.7 pin labels, corrected by observed LED colours."""
import re

def wire_command(command):
    if command in ('led aux status','led aux off') or re.fullmatch(r'led aux uv [01] [01]',command):return command
    if not re.fullmatch(r'led aux rgb [01] [01] [01] [01] [01] [01]',command):raise ValueError('Unsupported LED command')
    r1,g1,b1,r2,g2,b2=command.split()[3:]
    # Old R emits blue, old G emits red, old B emits green.
    # The unavailable old R1 output is consequently physical BLUE 1.
    if b1!='0':raise ValueError('Blue 1 is unavailable (DW3110 GPIO3)')
    return 'led aux rgb '+' '.join((b1,r1,g1,b2,r2,g2))

def logical_status(line):
    fields=dict(re.findall(r'(RGB_[RGB][12])=(\w+)',line))
    if not fields:return line
    for side in ('1','2'):
        for logical,wire in (('R','G'),('G','B'),('B','R')):
            line=re.sub(r'RGB_'+logical+side+r'=\w+',f'RGB_{logical}{side}={fields.get("RGB_"+wire+side,"unknown")}',line)
    return line

def command_ack(command):
    # An acknowledged SET, not independent physical/light readback.
    if command=='led aux off':return 'LED_ACK profile=off UV_L=0 UV_R=0'
    if command.startswith('led aux uv '):
        left,right=command.split()[3:];return f'LED_ACK profile=uv UV_L={left} UV_R={right}'
    if command.startswith('led aux rgb '):
        return 'LED_ACK profile=rgb '+' '.join(f'{name}={value}' for name,value in zip(('RGB_R1','RGB_G1','RGB_B1','RGB_R2','RGB_G2','RGB_B2'),command.split()[3:]))
    raise ValueError('Read commands require actual readback')
