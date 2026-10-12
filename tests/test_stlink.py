from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from stlink import validate_location
from first_flash import provision, tcl_path


class StlinkTests(unittest.TestCase):
    def test_explicit_usb_port(self):
        self.assertEqual(validate_location("1-5.2"), "1-5.2")
        for bad in ("", "COM8", "1-5; shutdown", "1-5\n", "{1-5}"):
            with self.assertRaises(ValueError): validate_location(bad)

    def test_tcl_path_quoting(self):
        self.assertIn("folder with spaces", tcl_path("folder with spaces/image.hex"))
        self.assertTrue(tcl_path("image.hex").startswith("{"))
        for bad in ("a}b.hex", "a{b.hex", "a\nb.hex"):
            with self.assertRaises(ValueError): tcl_path(bad)

    @patch("first_flash.run_openocd")
    @patch("first_flash.list_probes", return_value=[{"location": "1-6"}])
    def test_missing_selected_probe_never_falls_back(self, probes, run):
        with self.assertRaisesRegex(ValueError, "no longer connected"):
            provision(Path("openocd.exe"), Path("scripts"), "1-5", Path("combined.hex"), Path("unused"), {})
        run.assert_not_called()


if __name__ == "__main__": unittest.main()
