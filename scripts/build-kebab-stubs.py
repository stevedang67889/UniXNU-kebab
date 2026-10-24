#!/usr/bin/env python3
"""Compile the kebab weak probe stubs into linkable archives."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / 'scripts' / 'kebab_stubs.c'
out = ROOT / 'build' / 'kebab-stubs'
out.mkdir(parents=True, exist_ok=True)
obj = out / 'kebab_stubs.o'
lib = out / 'libKebabStubs.a'

subprocess.run(['clang', '-target', 'arm64-apple-ios17.0', '-mkernel', '-O2',
                '-fno-stack-protector', '-c', str(src), '-o', str(obj)], check=True)
if lib.exists():
    lib.unlink()
subprocess.run(['ar', 'rcs', str(lib), str(obj)], check=True)
print(f'built {lib}')
