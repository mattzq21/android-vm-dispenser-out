import importlib.machinery
import importlib.util
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('dispenser_version_test', str(Path(__file__).with_name('android-vm-dispenser')))
spec = importlib.util.spec_from_loader(loader.name, loader)
dispenser = importlib.util.module_from_spec(spec)
loader.exec_module(dispenser)


class BaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'base'
        self.base.mkdir()
        self.image = self.root / 'sdk/system-images/custom-image'
        self.image.mkdir(parents=True)
        (self.base / 'config.ini').write_text('image.sysdir.1=system-images/custom-image/\ntarget=android-34\n')
        (self.image / 'source.properties').write_text('AndroidVersion.ApiLevel=35\n')
        self.config = dict(base=str(self.base), sdk=str(self.root / 'sdk'), audio_input=False,
                           port_start=5570, capacity=1, memory_mb=4096, cpus=2, startup_timeout_sec=10)

    def test_version_comes_from_image_not_stale_template_or_directory_name(self):
        self.assertEqual(dispenser.base_api_level(self.config), 35)

    def test_absolute_image_path(self):
        (self.base / 'config.ini').write_text(f'image.sysdir.1={self.image}\n')
        (self.image / 'source.properties').write_text('AndroidVersion.ApiLevel=36\n')
        self.assertEqual(dispenser.base_api_level(self.config), 36)

    def test_missing_or_invalid_version_fails(self):
        for text in ('', 'AndroidVersion.ApiLevel=invalid', 'AndroidVersion.ApiLevel=0'):
            with self.subTest(text=text):
                (self.image / 'source.properties').write_text(text)
                with self.assertRaisesRegex(RuntimeError, 'AndroidVersion.ApiLevel'):
                    dispenser.base_api_level(self.config)
        (self.image / 'source.properties').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Cannot read Android version'):
            dispenser.base_api_level(self.config)

    def make_dispenser(self):
        with patch.object(dispenser, 'STATE', self.root):
            return dispenser.Dispenser(self.config, self.root)

    def test_start_writes_image_version_to_both_emulator_configs(self):
        service = self.make_dispenser()
        folder = self.root / 'device'
        folder.mkdir()
        (folder / 'config.ini').write_text((self.base / 'config.ini').read_text())
        record = dict(id='device', disk=str(folder / 'userdata.img'), qmp=str(folder / 'qmp'))
        def spawn(*args, **kwargs):
            (folder / 'qemu.pid').write_text('123')
            return SimpleNamespace(pid=123)
        with patch.object(dispenser.subprocess, 'Popen', side_effect=spawn):
            service.start(record)
        for path in (folder / 'config.ini', self.root / 'device.ini'):
            self.assertIn('target=android-35\n', path.read_text())
            self.assertNotIn('target=android-34', path.read_text())

    def test_ready_accepts_base_version_and_rejects_wrong_version(self):
        service = self.make_dispenser()
        conn, peer = socket.socketpair()
        self.addCleanup(conn.close)
        self.addCleanup(peer.close)
        for level in (35, 34):
            with self.subTest(level=level):
                def run(command, **kwargs):
                    output = f'1\n{level}\n'.encode() if 'getprop' in command[-1] else b'0\n'
                    return SimpleNamespace(returncode=0, stdout=output)
                with patch.object(dispenser, 'alive', return_value=True), patch.object(dispenser.subprocess, 'run', side_effect=run):
                    if level == 35:
                        service.ready({'slot': 0}, conn)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'does not match base image API 35'):
                            service.ready({'slot': 0}, conn)


if __name__ == '__main__':
    unittest.main()
