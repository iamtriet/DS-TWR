"""Compile actual portable firmware sources and run host-tool unit tests."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
output = ROOT / "build/tests"
output.mkdir(parents=True, exist_ok=True)
exe = output / ("test_core.exe" if os.name == "nt" else "test_core")
compiler = [os.environ["CC"]] if "CC" in os.environ else [sys.executable, "-m", "ziglang", "cc"]
subprocess.run([*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Itests/mocks", "-Iinclude",
                "tests/test_core.c", "src/validation_math.c", "src/uwb_frame.c", "src/tag_table.c",
                "src/ranging.c", "-o", str(exe), "-lm"], check=True)
subprocess.run([str(exe)], check=True)
listener = output / ("test_anchor_listener.exe" if os.name == "nt" else "test_anchor_listener")
subprocess.run([*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Itests/uwb_mocks", "-Iinclude",
                "tests/test_anchor_listener.c", "src/uwb_app.c", "src/uwb_frame.c", "-o", str(listener)], check=True)
subprocess.run([str(listener)], check=True)
wire = output / ("test_uwb3d_wire.exe" if os.name == "nt" else "test_uwb3d_wire")
subprocess.run([*compiler,"-std=c11","-Wall","-Wextra","-Werror","-Iinclude",
                "tests/test_uwb3d_wire.c","src/uwb3d_wire.c","src/validation_math.c","-o",str(wire),"-lm"],check=True)
from uwb3d_protocol import Decoder,decode_range
golden=bytes.fromhex(subprocess.check_output([str(wire)],text=True).strip())
decoded=Decoder().feed(golden)
assert len(decoded)==1 and decoded[0].sequence==5 and decode_range(decoded[0].payload)['distance']==1.234
network = output / ("test_uwb_network.exe" if os.name == "nt" else "test_uwb_network")
subprocess.run([*compiler,"-std=c11","-Wall","-Wextra","-Werror","-Itests/network_mocks","-Itests/uwb_mocks","-Iinclude",
    "tests/test_uwb_network.c","src/uwb_network.c","src/uwb3d_wire.c","src/validation_math.c","-o",str(network),"-lm"],check=True)
subprocess.run([str(network)],check=True)
reference = output / ("test_reference_app.exe" if os.name == "nt" else "test_reference_app")
subprocess.run([*compiler,"-std=c11","-Wall","-Wextra","-Iinclude","-Ivendor/dw3000",
    "tests/test_reference_app.c","vendor/albifache/app/app_utils.c","vendor/albifache/mac/mac.c","-o",str(reference),"-lm"],check=True)
subprocess.run([str(reference)],check=True)
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], check=True)
