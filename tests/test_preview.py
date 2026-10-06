import io
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

try:
    from ofdl.preview import build_preview, PreviewResult
except ImportError:
    build_preview = PreviewResult = None


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_image_preview_is_small_png_without_modifying_source(self):
        self.assertIsNotNone(build_preview, 'Preview helper is not implemented')
        path = self.root/'large.jpg'
        Image.new('RGB',(2400,1600),(12,34,56)).save(path,quality=90)
        before = path.read_bytes()
        result = build_preview(path,'photo',max_size=(320,240))
        self.assertIsInstance(result, PreviewResult)
        self.assertEqual(result.kind,'image')
        self.assertTrue(result.png)
        image = Image.open(io.BytesIO(result.png))
        self.assertLessEqual(image.width,320)
        self.assertLessEqual(image.height,240)
        self.assertEqual(path.read_bytes(),before)

    def test_video_without_ffmpeg_returns_lightweight_placeholder(self):
        self.assertIsNotNone(build_preview, 'Preview helper is not implemented')
        path = self.root/'clip.mp4'
        path.write_bytes(b'not-decoded')
        with patch('ofdl.preview.shutil.which', return_value=None):
            result = build_preview(path,'video',max_size=(320,240))
        self.assertEqual(result.kind,'video')
        self.assertIsNone(result.png)
        self.assertIn('ffmpeg',result.message.lower())

    def test_missing_file_returns_unavailable_without_exception(self):
        self.assertIsNotNone(build_preview, 'Preview helper is not implemented')
        result = build_preview(self.root/'missing.jpg','photo')
        self.assertIsNone(result.png)
        self.assertIn('not available',result.message.lower())
