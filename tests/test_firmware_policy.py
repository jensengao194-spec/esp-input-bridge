"""Compile and exercise the same pairing gate included in both real firmwares."""
import shutil
import subprocess
from pathlib import Path


def test_native_pairing_security_policy(tmp_path):
    compiler = shutil.which("c++")
    assert compiler, "A C++ compiler is required for the firmware policy test"
    source = tmp_path/"policy.cpp"
    source.write_text(r'''
#include "pairing_policy.h"
#include <cassert>
int main() {
    PairingPolicy p;
    assert(!p.active(0) && !p.claim(7,0)); // Closed after every boot.
    assert(p.open(100));
    assert(p.claim(7,101));
    assert(!p.claim(8,102)); // A second connection cannot take the window.
    assert(!p.open(103)); // No silent extension or replacement.
    assert(p.owns(7) && !p.owns(8));
    assert(!p.active(120100) && !p.claim(8,120100));
    p.close();
    assert(!p.owns(7) && !p.claim(7,120101));
    assert(p.open(0xfffffff0u)); // millis() wrap is safe.
    assert(p.active(32) && !p.active(120000));
    for (int mask=0; mask<8; ++mask) {
        assert(PairingPolicy::trusted(mask&1,mask&2,mask&4,16)==(mask==7));
        assert(!PairingPolicy::trusted(mask&1,mask&2,mask&4,7));
    }
}
''')
    binary = tmp_path/"policy"
    subprocess.run([compiler, "-std=c++17", "-I", str(Path(__file__).resolve().parents[1]/"firmware/include"),
                    str(source), "-o", str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)
