import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from swarm_membership import membership_plan
class MembershipTests(unittest.TestCase):
 def setUp(self):self.b=[dict(name='PCB_'+str(i),address=str(i)) for i in range(5)]
 def test_connected_members_retained_without_advertisements(self):
  p=membership_plan(self.b[:4],4,{'nodes':[dict(id=i,last=99) for i in (1,2,3)]},[self.b[4]],100)
  self.assertEqual(p['boards'],self.b);self.assertEqual(p['removed'],[])
 def test_powered_off_removed_only_when_admitting_new_board(self):
  state={'nodes':[dict(id=i,last=99 if i<3 else 0) for i in (1,2,3)]}
  p=membership_plan(self.b[:4],4,state,[self.b[4]],100)
  self.assertEqual(p['boards'],self.b[:3]+[self.b[4]]);self.assertEqual(p['removed'],[self.b[3]])
  self.assertIsNone(membership_plan(self.b[:4],4,state,[],100))
 def test_existing_advertisement_does_not_restart(self):
  self.assertIsNone(membership_plan(self.b,5,{},self.b,100))
if __name__=='__main__':unittest.main()
