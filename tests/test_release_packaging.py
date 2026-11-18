"""Release ZIP must carry matching runtime notices and owner-selected terms."""
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from zipfile import ZipFile

package = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'packaging/release_zip.py'))['package']


class ReleasePackagingTests(unittest.TestCase):
    def fixture(self, root):
        binary = bytearray(70)
        binary[:2] = b'MZ'
        binary[60:64] = (64).to_bytes(4, 'little')
        binary[64:] = b'PE\0\0\x64\x86'
        (root / 'PixelAgent.exe').write_bytes(binary)
        notices = root / 'THIRD-PARTY-NOTICES'
        notices.mkdir()
        license_text = b'Test dependency license text\n'
        (notices / 'LICENSE.txt').write_bytes(license_text)
        (notices / 'README.md').write_text('Test notices\n')
        (notices / 'manifest.json').write_text(json.dumps({
            'executable_sha256': hashlib.sha256(binary).hexdigest(),
            'license_files': [{'file': 'LICENSE.txt', 'sha256': hashlib.sha256(license_text).hexdigest()}]}))
        return notices

    def test_matching_notices_and_terms_are_inside_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notices = self.fixture(root)
            (notices / 'STALE-LICENSE.txt').write_text('An old build artifact')
            archive = package(root)
            with ZipFile(archive) as zipped:
                self.assertEqual(set(zipped.namelist()), {'PixelAgent.exe', 'START-HERE.txt',
                    'LICENSE', 'DISTRIBUTION.md', 'THIRD-PARTY-NOTICES/README.md',
                    'THIRD-PARTY-NOTICES/manifest.json', 'THIRD-PARTY-NOTICES/LICENSE.txt'})
                self.assertEqual(zipped.read('THIRD-PARTY-NOTICES/LICENSE.txt'), b'Test dependency license text\n')
            self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest(), archive.with_suffix('.sha256').read_text())

    def test_executable_changed_after_notice_collection_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            with (root / 'PixelAgent.exe').open('ab') as stream:
                stream.write(b'changed')
            with self.assertRaisesRegex(ValueError, 'notices do not match'):
                package(root)

    def test_notice_changed_after_collection_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notices = self.fixture(root)
            (notices / 'LICENSE.txt').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'notice text changed'):
                package(root)


if __name__ == '__main__':
    unittest.main()
