"""The official source ZIP must ship the OBS script and preserve its bytes."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from package_release import package


class MarkerPackageTests(unittest.TestCase):
    def test_release_contains_obs_script_and_marker_support(self):
        root=Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp:
            result=package(root,Path(temp)/'release.zip')
            self.assertFalse(result['uploaded'])
            with zipfile.ZipFile(result['zip']) as archive:
                for rel in ['obs/apex-marker.lua','scripts/marker_scope.py',
                            'scripts/test_marker_scope.py','scripts/test_marker_audit.py',
                            'scripts/test_obs_marker.py','references/obs-markers.md']:
                    self.assertEqual(archive.read('apex-clipper/'+rel),(root/rel).read_bytes())
                self.assertFalse(any('/.local/' in name or '/__pycache__/' in name for name in archive.namelist()))


if __name__=='__main__':
    unittest.main()
