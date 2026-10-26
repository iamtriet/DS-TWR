import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from region_overlay import rounded_mask,components,render_regions

class RegionOverlayTests(unittest.TestCase):
 def test_gap_and_hole_stay_empty(self):
  rects=[(x*20,y*20,x*20+19,y*20+19) for x in range(3) for y in range(3) if (x,y)!=(1,1)]
  mask=rounded_mask((100,70),rects+[(80,0,99,19)],4)
  self.assertEqual(mask.getpixel((30,30)),0);self.assertEqual(mask.getpixel((70,10)),0)
  self.assertGreater(mask.getpixel((10,10)),0)
 def test_scan_palette_and_unknown_frontier(self):
  image,labels=render_regions(300,200,100,.5,{'OBSERVED PASSAGE':{(0,0)},'SUSPECTED OBSTRUCTION':{(-2,0)},'FRONTIER':{(2,0)}})
  self.assertEqual(image.getpixel((175,75)),(255,255,255,255))
  self.assertEqual(image.getpixel((275,75))[3],0)
  self.assertEqual(image.getpixel((75,75))[3],0)
  self.assertTrue(any(r>200 and g<100 and b<150 and a>0 for r,g,b,a in image.getdata()))
  self.assertEqual(labels,[])
 def test_components_and_no_evidence_mutation(self):
  cells={(0,0),(1,0),(4,0)};self.assertEqual(sorted(map(len,components(cells))),[1,2])
  image,labels=render_regions(400,300,100,.25,{'OBSERVED PASSAGE':cells})
  self.assertEqual(cells,{(0,0),(1,0),(4,0)});self.assertEqual(labels,[])
  self.assertEqual(image.mode,'RGBA')

if __name__=='__main__':unittest.main()
