from pathlib import Path
import tempfile
import unittest

from deploy.platform_check import inspect_platform


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'proc/device-tree').mkdir(parents=True)
        (self.root / 'etc').mkdir()
        (self.root / 'proc/device-tree/model').write_text('NVIDIA Jetson Xavier NX Developer Kit\x00')
        (self.root / 'etc/nv_tegra_release').write_text('# R35 (release), REVISION: 4.1, GCID: 33958178')
        (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="20.04"\n')

    def test_xavier_jetpack5_supported(self):
        result = inspect_platform(self.root, 'aarch64', (3, 8))
        self.assertTrue(result['ok'])
        self.assertEqual(result['l4t'], '35.4.1')

    def test_wrong_board_or_old_jetpack_is_refused_before_install(self):
        (self.root / 'proc/device-tree/model').write_text('NVIDIA Jetson Nano')
        (self.root / 'etc/nv_tegra_release').write_text('# R32 (release), REVISION: 7.6')
        result = inspect_platform(self.root, 'aarch64', (3, 8))
        self.assertFalse(result['ok'])
        self.assertGreaterEqual(len(result['errors']), 2)

    def test_other_python_and_x86_cannot_use_arm_cp38_wheel(self):
        result = inspect_platform(self.root, 'x86_64', (3, 10))
        self.assertFalse(result['ok'])
        self.assertEqual(len(result['errors']), 2)


if __name__ == '__main__':
    unittest.main()
