import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ofdl.common import AppError
from ofdl.downloads import Options
from ofdl.paths import local_output_path


class OutputPathTests(unittest.TestCase):
    def test_plain_path_is_expanded_and_resolved(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(local_output_path(folder), Path(folder).resolve())

    def test_file_url_becomes_a_local_path(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(local_output_path('file://' + folder), Path(folder).resolve())

    def test_colon_in_a_relative_name_is_not_treated_as_a_url(self):
        self.assertEqual(local_output_path('export:2026'), (Path.cwd() / 'export:2026').resolve())

    def test_smb_url_is_resolved_through_kio_fuse(self):
        expected = Path('/run/user/1000/kio-fuse-test/smb/nas/share')
        with patch('ofdl.paths._kio_fuse', return_value=expected) as mount:
            self.assertEqual(local_output_path('smb://Noa3@synology.local/docker'), expected)
        mount.assert_called_once_with('smb://Noa3@synology.local/docker')

    def test_pathlib_mangled_smb_url_is_rebuilt(self):
        # The GUI builds Path(output) first, which turns "smb://" into "smb:/".
        expected = Path('/run/user/1000/kio-fuse-test/smb/nas/share')
        with patch('ofdl.paths._kio_fuse', return_value=expected) as mount:
            self.assertEqual(local_output_path(Path('smb://Noa3@synology.local/docker')), expected)
        mount.assert_called_once_with('smb://Noa3@synology.local/docker')

    def test_unavailable_kio_fuse_explains_the_fix(self):
        with patch('ofdl.paths._kio_fuse', return_value=None):
            with self.assertRaisesRegex(AppError, 'network URL.*mounted folder'):
                local_output_path('smb://Noa3@synology.local/docker')

    def test_web_url_is_rejected(self):
        with self.assertRaisesRegex(AppError, 'not a web address'):
            local_output_path('https://example.com/folder')

    def test_unknown_url_scheme_is_rejected(self):
        with self.assertRaisesRegex(AppError, 'not a URL'):
            local_output_path('custom://host/folder')

    def test_options_uses_the_same_resolution(self):
        expected = Path('/run/user/1000/kio-fuse-test/smb/nas/share')
        with patch('ofdl.paths._kio_fuse', return_value=expected):
            options = Options(output_dir='smb://Noa3@synology.local/docker', profiles=['creator']).validate()
        self.assertEqual(options.output_dir, expected)
        self.assertEqual(options.profiles, ['creator'])

    def test_options_refuses_an_unmounted_share_before_starting(self):
        with patch('ofdl.paths._kio_fuse', return_value=None):
            with self.assertRaisesRegex(AppError, 'network URL'):
                Options(output_dir='smb://Noa3@synology.local/docker', profiles=['creator']).validate()


if __name__ == '__main__':
    unittest.main()
