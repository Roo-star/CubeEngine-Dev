import io
import unittest
from PIL import Image
from srtp.sprite_geometry import extrude_rgba


class SpriteGeometryTests(unittest.TestCase):
    def test_transparent_hole_and_original_colours_survive(self):
        im=Image.new('RGBA',(3,3),(30,100,200,255)); im.putpixel((1,1),(0,0,0,0))
        out=io.BytesIO(); im.save(out,format='PNG')
        mesh=extrude_rgba(out.getvalue(),depth=.4)
        self.assertEqual(mesh['opaque_pixels'],8)
        self.assertEqual(len(mesh['triangles']),64) # front/back + outer and inner boundary
        self.assertEqual(set(mesh['colors']),{(30/255,100/255,200/255,1)})
        self.assertEqual({v[2] for v in mesh['vertices']},{-.2,.2})
        other=extrude_rgba(out.getvalue(),depth=.4,axis='x')
        self.assertEqual({v[0] for v in other['vertices']},{-.2,.2})

    def test_no_silent_downsampling_or_empty_solid(self):
        for size,color in [((256,256),(1,2,3,255)),((2,2),(0,0,0,0))]:
            im=Image.new('RGBA',size,color); out=io.BytesIO(); im.save(out,format='PNG')
            with self.assertRaises(ValueError): extrude_rgba(out.getvalue(),depth=.2)


if __name__=='__main__': unittest.main()
