"""The published models must fit the independently specified equipment envelope."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class IndustrialModelTests(unittest.TestCase):
    def test_generated_equipment_bounds_match_the_catalogue_envelope(self):
        spec=importlib.util.spec_from_file_location('industrial_models',ROOT/'scripts/build_industrial_models.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        expected={'inspect':(1,1.8,.8),'measure':(1.2,1.5,.8),'assemble':(1.6,2,.8),
                  'convey':(2,.95,.6),'mark':(.9,1.7,.75),'connect':(.6,1.2,.4)}
        with tempfile.TemporaryDirectory() as temporary:
            for key,envelope in expected.items():
                with self.subTest(product=key):
                    path=Path(temporary)/(key+'.glb');module.make(key).export(path)
                    raw=path.read_bytes();magic,version,length=struct.unpack_from('<4sII',raw)
                    self.assertEqual((magic,version,length),(b'glTF',2,len(raw)))
                    json_length=struct.unpack_from('<I',raw,12)[0];doc=json.loads(raw[20:20+json_length])
                    low=[float('inf')]*3;high=[float('-inf')]*3
                    for node in doc['nodes']:
                        self.assertEqual(node['rotation'],[0,0,0,1])
                        for axis in range(3):
                            low[axis]=min(low[axis],node['translation'][axis]-node['scale'][axis])
                            high[axis]=max(high[axis],node['translation'][axis]+node['scale'][axis])
                    for actual,want in zip([high[i]-low[i] for i in range(3)],envelope):
                        self.assertAlmostEqual(actual,want,places=3)
                    self.assertEqual(low[1],0)


if __name__=='__main__':unittest.main()
