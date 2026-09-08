import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from updater import install, release_info, unpack, version


def package(extra=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name, content in {'version.json': json.dumps({'product': 'RE7_21_Noir', 'version': '1.4.0'}),
                              'RE7_21_Noir.exe': 'test fixture, never executed', 'config.json': '{"new":true}',
                              **(extra or {})}.items():
            archive.writestr('RE7_21_Noir/'+name, content)
    return data.getvalue()


class UpdateTests(unittest.TestCase):
    def test_version_and_release_selection(self):
        data = dict(tag_name='v1.4.0', assets=[dict(name='RE7_21_Noir-Windows.zip', size=100,
                    digest='sha256:'+'a'*64, browser_download_url='https://github.com/owner/repo/releases/download/v1.4.0/app.zip')])
        fetch = lambda url: io.BytesIO(json.dumps(data).encode())
        selected = release_info({'repository': 'owner/repo'}, '1.3.0', fetch)
        self.assertEqual(selected['tag'], 'v1.4.0')
        self.assertIsNone(release_info({'repository': 'owner/repo'}, '1.4.0', fetch))
        data['assets'][0]['digest'] = None
        with self.assertRaisesRegex(ValueError, 'missing_digest'):
            release_info({'repository': 'owner/repo'}, '1.3.0', fetch)
        with self.assertRaises(ValueError):
            version('v1.4.0/../../')

    def test_install_keeps_old_files_and_copies_settings_with_verified_defaults(self):
        data = package()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = '{"my_config":42}'
            (root/'config.json').write_text(original)
            (root/'RE7_21_Noir.exe').write_text('old')
            (root/'presets').mkdir()
            (root/'presets/custom.json').write_text('{"custom":1}')
            release = dict(tag='v1.4.0', url='mock', size=len(data), digest=hashlib.sha256(data).hexdigest())
            exe = install(root, release, lambda url: io.BytesIO(data))
            self.assertTrue(exe.is_file())
            self.assertEqual((root/'RE7_21_Noir.exe').read_text(), 'old')
            self.assertEqual((exe.parent/'config.json').read_text(), original)
            self.assertEqual((exe.parent/'presets/custom.json').read_text(), '{"custom":1}')
            self.assertEqual((exe.parent/'package-defaults/config.json').read_text(), '{"new":true}')

    def test_checksum_failure_does_not_change_installation(self):
        data = package()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config.json').write_text('old')
            with self.assertRaisesRegex(ValueError, 'checksum_failed'):
                install(root, dict(tag='v1.4.0', url='mock', size=len(data), digest='0'*64), lambda url: io.BytesIO(data))
            self.assertEqual((root/'config.json').read_text(), 'old')

    def test_reject_unsafe_zip_paths_and_symlinks(self):
        for name in ('../outside.txt', 'RE7_21_Noir/../../outside.txt', 'RE7_21_Noir/a:stream', 'RE7_21_Noir/CON.txt', '/absolute.txt'):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr(name, 'x')
            stream.seek(0)
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
                unpack(stream, directory)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            item = zipfile.ZipInfo('RE7_21_Noir/link')
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, '../elsewhere')
        stream.seek(0)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            unpack(stream, directory)
