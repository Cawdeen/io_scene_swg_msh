"""Pure Python format tests: python -m unittest discover -s tests -v."""
import importlib.util
import bisect
import math
from pathlib import Path
import struct
import sys
import unittest

spec=importlib.util.spec_from_file_location('ans_format',Path(__file__).parents[1]/'io_scene_swg/ans_format.py')
ans=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=ans
spec.loader.exec_module(ans)


def chunk(tag,payload):
    return tag.encode()+struct.pack('>I',len(payload))+payload


def form(tag,*children):
    return chunk('FORM',tag.encode()+b''.join(children))


def compressed_fixture():
    pack=lambda fmt,*v:struct.pack('<'+fmt,*v)
    q0,q1=0,255  # z=255/511, with X/Y/Z format 254 (full range).
    rotation=chunk('QCHN',pack('h3BhIhI',2,254,254,254,0,q0,4,q1))
    return form('CKAT',form('0001',
        chunk('INFO',pack('f6h',30,4,1,1,0,1,1)),
        form('XFRM',chunk('XFIN',b'root\0'+pack('bhB3h',1,0,8,0,0,0))),
        form('AROT',rotation),chunk('SROT',b''),
        form('ATRN',chunk('CHNL',pack('hhfhf',2,0,0,4,1))),
        chunk('STRN',pack('f',0)),
        form('MSGS',chunk('INFO',pack('h',1)),chunk('MESG',pack('h',2)+b'footstep\0'+pack('2h',1,3))),
        chunk('LOCT',pack('fh',1,2)+pack('h3f',0,0,0,0)+pack('h3f',4,0,0,1)),rotation))


class AnsFormatTests(unittest.TestCase):
    def test_client_crc_reference_values(self):
        # Verified against the table-driven client sharedFoundation/Crc.cpp.
        for name,crc in [('123456789',0xfc891918),('root',0x212d6fae),
                         ('lthigh',0x329f30b6),('lwrist',0x7704ffff)]:
            self.assertEqual(ans.transform_name_key(name),(crc,name))
        self.assertEqual(ans.transform_name_key('ROOT'),ans.transform_name_key('root'))

    def test_client_binary_lookup_finds_every_exported_joint(self):
        track=ans.decode(compressed_fixture()).tracks['root']
        # Skeleton order deliberately differs from CRC order and lexical order.
        clip=ans.Clip(30,4,{name:track for name in
                          ['root','lthigh','lshin','lankle','lwrist','rring01','rring02']})
        records=ans.blocks(ans.encode(clip))[0].children[0].children
        xfrm=next(block for block in records if block.tag=='XFRM')
        names=[ans.Reader(block.payload).string() for block in xfrm.children]
        self.assertEqual(names,['root','lthigh','lwrist','lshin','rring01','rring02','lankle'])
        keys=[ans.transform_name_key(name) for name in names]
        for name in clip.tracks:
            index=bisect.bisect_left(keys,ans.transform_name_key(name))
            self.assertLess(index,len(names))
            self.assertEqual(names[index],name)

    def test_compressed_layout_and_masks(self):
        clip=ans.decode(compressed_fixture())
        self.assertEqual((clip.fps,clip.last_frame,clip.source_format),(30,4,'CKAT/0001'))
        q,t=clip.tracks['root'].sample(2)
        self.assertEqual(t,(0.5,0,0))
        expected_angle=math.asin(255/511)/2
        self.assertAlmostEqual(q[0],math.cos(expected_angle),places=7)
        self.assertAlmostEqual(q[3],math.sin(expected_angle),places=7)
        self.assertEqual(clip.messages,[{'name':'footstep','frames':[1,3]}])
        self.assertEqual(clip.locomotion_translation.sample(2),(0,0,0.5))
        self.assertIsNotNone(clip.locomotion_rotation)

    def test_all_compression_formats(self):
        for shift in range(7):
            count=1<<shift
            prefix=256-(1<<(shift+1))
            for index in range(count):
                q=ans.expand_quaternion(0,(prefix+index,254,254))
                base=-1+(index+1)*2/(count+1)
                self.assertAlmostEqual(q[1],base,places=7)
                self.assertAlmostEqual(sum(v*v for v in q),1,places=7)
        q=ans.expand_quaternion((1024|400)<<21 | (1024|200)<<10 | 100,(254,254,254))
        self.assertAlmostEqual(q[1],-400/1023)
        self.assertAlmostEqual(q[2],-200/1023)
        self.assertAlmostEqual(q[3],100/511)

    def test_bad_compressed_values(self):
        with self.assertRaises(ans.AnimationFormatError):ans.expand_quaternion(0,(0,254,254))
        with self.assertRaises(ans.AnimationFormatError):ans.expand_quaternion(1023<<21 | 1023<<10 | 511,(254,254,254))
        warnings=[]
        q=ans.expand_quaternion(1023<<21 | 18<<10,(254,254,254),warnings)
        self.assertAlmostEqual(sum(v*v for v in q),1,places=7)
        self.assertTrue(warnings)

    def test_kfat_roundtrip_and_integer_keyframes(self):
        original=ans.decode(compressed_fixture())
        payload=ans.encode(original)
        self.assertEqual(payload[8:12],b'KFAT')
        decoded=ans.decode(payload)
        for frame in (0,0.5,1,2.3,4):
            a,ta=original.tracks['root'].sample(frame)
            b,tb=decoded.tracks['root'].sample(frame)
            self.assertGreater(abs(sum(x*y for x,y in zip(a,b))),1-1e-6)
            self.assertEqual(ta,tb)
        self.assertEqual(decoded.messages,original.messages)
        self.assertEqual(decoded.average_speed,original.average_speed)
        self.assertEqual(decoded.locomotion_translation.keys,original.locomotion_translation.keys)

    def test_rejects_truncated_and_unsupported_files(self):
        payload=compressed_fixture()
        for size in (0,7,11,len(payload)-1):
            with self.assertRaises(ans.AnimationFormatError):ans.decode(payload[:size])
        with self.assertRaises(ans.AnimationFormatError):ans.decode(form('KFAT',form('0002')))

    def test_rejects_bad_channel_timing(self):
        with self.assertRaises(ans.AnimationFormatError):ans.Channel([(1,0),(1,1)])
        with self.assertRaises(ans.AnimationFormatError):ans.Channel([])
        with self.assertRaises(ans.AnimationFormatError):ans.Channel([(0,float('nan'))])

    def test_quaternion_hemisphere(self):
        q=ans.slerp((1,0,0,0),(-1,0,0,0),0.5)
        self.assertEqual(q,(1,0,0,0))


if __name__=='__main__':unittest.main()
