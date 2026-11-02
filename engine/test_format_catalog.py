"""Actual tiny professional-codec/image round trips plus catalogue contracts."""
from fractions import Fraction
import tempfile
from pathlib import Path
import unittest

import av
import numpy as np

import format_catalog as catalog


class CatalogTests(unittest.TestCase):
    def test_profiles_are_unique_independent_and_hardware_is_not_faked(self):
        rows = catalog.profiles()
        self.assertEqual(len(rows), len({p['id'] for p in rows}))
        self.assertTrue(all(not p['hardware_verified'] for p in rows))
        profile = catalog.get_profile('lossless')
        profile['options']['level'] = 'broken'
        self.assertEqual(catalog.get_profile('lossless')['options']['level'], '3')
        self.assertFalse(catalog.get_profile('prores')['lossless'])
        self.assertFalse(catalog.get_profile('hevc')['lossless'])
        self.assertFalse(next(p for p in rows if p['id'] == 'nvenc-lossless')['rgb_lossless'])

    def test_broadcast_geometry_is_explicit(self):
        p = {'width':1920,'height':1080,'fps':'24000/1001'}
        self.assertEqual(catalog.get_profile('dnxhd',p)['options']['b'], '175000000')
        for mode in ('dnxhd','mxf-mpeg2'):
            with self.assertRaises(ValueError):
                catalog.get_profile(mode, {'width':7680,'height':4320,'fps':'24/1'})
        with self.assertRaises(ValueError):
            catalog.get_profile('h264', {'width':321,'height':180,'fps':'24/1'})
        catalog.get_profile('lossless', {'width':321,'height':181,'fps':'24/1'})
        with self.assertRaisesRegex(ValueError, 'HDR colorimetry'):
            catalog.get_profile('uncompressed', {'width':7680,'height':4320,'fps':'24/1','color_space':'bt2020-pq'})

    def test_native_codec_and_sdk_support_are_distinct(self):
        result = catalog.capabilities()
        sdk = [row for row in result['formats'] if row['category']=='SDK / package']
        self.assertTrue(sdk)
        self.assertTrue(all(not row['import_available'] and row['reason'] for row in sdk))
        self.assertTrue({'.mxf','.exr','.dpx','.tiff','.wav','.m2ts'} <= set(result['extensions']))
        self.assertFalse(set(catalog.safe_demuxer_names()) & catalog.BLOCKED_DEMUXERS)

    def test_actual_professional_export_profiles_decode(self):
        project = {'width':320,'height':180,'fps':'24/1'}
        gradient = np.linspace(0,65535,320,dtype=np.uint16)[None,:,None]
        image = np.broadcast_to(gradient,(180,320,3)).copy()
        image[:,:,1] = np.flip(image[:,:,1],axis=1)
        image[:,:,2] //= 2
        with tempfile.TemporaryDirectory(prefix='editor-format-proof-') as directory:
            for name in ('lossless','prores','prores-proxy','prores-lt','prores-422',
                         'prores-4444','prores-xq','dnxhr','dnxhr-444','mxf','cineform',
                         'jpeg2000','uncompressed','png-sequence','tiff-sequence','dpx-sequence'):
                with self.subTest(profile=name):
                    availability = next(p for p in catalog.profiles() if p['id'] == name)
                    if not availability['available'] and name != 'lossless':
                        self.skipTest(availability['reason'])
                    p = catalog.get_profile(name,project)
                    extension = p.get('sequence') or p['extension']
                    path = Path(directory)/(name+extension)
                    container_format = 'image2' if p.get('sequence') else p['container']
                    with av.open(str(path),'w',format=container_format) as output:
                        stream = output.add_stream(p['encoder'],rate=24)
                        stream.width,stream.height = 320,180
                        stream.pix_fmt = p['pixel_format']
                        stream.options = p['options']
                        stream.time_base = stream.codec_context.time_base = Fraction(1,24)
                        stream.codec_context.max_b_frames = 0
                        stream.codec_context.thread_count = 2
                        frame = av.VideoFrame.from_ndarray(image,format='rgb48le')
                        frame.pts,frame.time_base = 0,Fraction(1,24)
                        for packet in stream.encode(frame): output.mux(packet)
                        for packet in stream.encode(): output.mux(packet)
                    self.assertGreater(path.stat().st_size,0)
                    with av.open(str(path)) as source:
                        decoded = list(source.decode(video=0))
                    self.assertEqual(len(decoded),1)
                    self.assertEqual((decoded[0].width,decoded[0].height),(320,180))
                    if p['rgb_lossless']:
                        actual = decoded[0].to_ndarray(format='rgb48le')
                        self.assertTrue(np.array_equal(actual,image),f'{name} did not preserve RGB16 samples')


if __name__=='__main__':
    unittest.main(verbosity=2)
