"""Build the source archive and the checksum manifest for a release.

Player packages come from tools/package_release.py. This script adds the two
artifacts that used to be produced by hand: the source archive and the
SHA256SUMS.txt covering every zip in the release directory.
"""
import argparse
import hashlib
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'RE7_21_Noir-source'
EXCLUDED_DIRS = {'.git', '.venv', '__pycache__', 'build', 'dist', 'logs', 'userdata', 'config-backups', 'certs', 'updates'}
EXCLUDED_SUFFIXES = {'.pyc', '.spec', '.log', '.zip', '.exe', '.pem', '.key', '.pfx', '.p12'}

parser = argparse.ArgumentParser()
parser.add_argument('release', type=Path, help='Release directory holding the packaged player build')
args = parser.parse_args();release = args.release.resolve()
if not release.is_dir():raise SystemExit(f'Not a release directory: {release}')

def wanted(path):
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in relative.parts):return False
    return path.suffix.lower() not in EXCLUDED_SUFFIXES

archive = release/'RE7_21_Noir-Source.zip'
if archive.exists():raise SystemExit(f'Already exists, remove it first: {archive}')
with ZipFile(archive, 'x', ZIP_DEFLATED) as output:
    for path in sorted(ROOT.rglob('*')):
        if path.is_file() and wanted(path):
            output.write(path, f'{PREFIX}/{path.relative_to(ROOT).as_posix()}')
with ZipFile(archive) as check:
    assert check.testzip() is None, 'Corrupt source archive'
print('Packaged:', archive, f'({len(ZipFile(archive).namelist())} entries)')

targets = sorted(path for path in release.glob('*.zip'))
if not targets:raise SystemExit('No release archives to checksum')
manifest = release/'SHA256SUMS.txt'
lines = []
for target in targets:
    digest = hashlib.sha256()
    with target.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):digest.update(block)
    lines.append(f'{digest.hexdigest()}  {target.name}')
manifest.write_text('\n'.join(lines)+'\n', encoding='utf-8')
print('Checksums:', manifest)
for line in lines:print(' ', line)
