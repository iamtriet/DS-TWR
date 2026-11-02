import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from uart_discovery import parse_identity,probe_port

class DiscoveryTests(unittest.TestCase):
 def test_identity_uses_low_ficr_word_for_ble_name(self):
  self.assertEqual(parse_identity(b'noise\x00 PCB_VERSION=1.2.9\r\nFICR ID=c992073e09ec3021 | flash=512\n'),dict(name='PCB_09EC3021',uid='C992073E09EC3021',version='1.2.9'))
  self.assertIsNone(parse_identity(b'PCB_09EC3021'))
  self.assertIsNone(parse_identity(b'FICR ID=c992073e09ec3021'))
 def test_probe_is_read_only_and_closes_port(self):
  instances=[]
  class Port:
   def __init__(self,**kwargs):self.baud=kwargs['baudrate'];self.is_open=False;self.writes=[];instances.append(self)
   def open(self):self.is_open=True;self.assertions=(self.dtr,self.rts)
   def reset_input_buffer(self):pass
   def write(self,data):self.writes.append(data)
   def flush(self):pass
   def read(self,count):return b'PCB_VERSION=1.2.9\nFICR ID=c992073e09ec3021\n'
   def close(self):self.is_open=False
  with patch('serial.Serial',Port):row=probe_port('COM9')
  self.assertEqual(row['name'],'PCB_09EC3021');self.assertEqual(row['baud'],1000000)
  self.assertEqual(instances[0].writes,[b'\ninfo\n']);self.assertEqual(instances[0].assertions,(False,False));self.assertFalse(instances[0].is_open)
if __name__=='__main__':unittest.main()
