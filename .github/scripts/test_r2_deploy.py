import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name('r2_deploy.py')
SPEC = importlib.util.spec_from_file_location('r2_deploy', SCRIPT_PATH)
r2_deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r2_deploy)


class UploadGroupTest(unittest.TestCase):
    def test_overwrites_changed_file_when_size_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            upload_root = root / 'upload'
            bucket_root = root / 'bucket'
            source = upload_root / 'Shows/2018/Maniac.html'
            destination = bucket_root / 'Shows/2018/Maniac.html'
            source.parent.mkdir(parents=True)
            destination.parent.mkdir(parents=True)
            source.write_bytes(b'new!')
            destination.write_bytes(b'old!')

            def fake_aws(args, dry_run=False):
                self.assertEqual(args[:3], ['s3', 'cp', str(upload_root)])
                self.assertIn('--recursive', args)
                self.assertNotIn('--size-only', args)
                shutil.copy2(source, destination)

            group = {
                'root': upload_root,
                'content_type': 'text/html; charset=utf-8',
                'content_encoding': 'gzip',
                'count': 1,
            }
            with mock.patch.object(r2_deploy, 'aws', side_effect=fake_aws):
                r2_deploy.upload_group(
                    group,
                    'site-bucket',
                    'https://example.r2.cloudflarestorage.com',
                    False,
                )

            self.assertEqual(destination.read_bytes(), b'new!')


if __name__ == '__main__':
    unittest.main()
