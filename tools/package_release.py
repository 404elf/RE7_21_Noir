"""Build a clean player archive and a legacy-updater compatibility archive."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

parser=argparse.ArgumentParser()
parser.add_argument('directory',type=Path)
args=parser.parse_args();root=args.directory.resolve()
if root.name!='RE7_21_Noir' or not (root/'RE7_21_Noir.exe').is_file():raise SystemExit('Pass the built player directory')
metadata=(root/'_internal/version.json').read_bytes()
allowed={'RE7_21_Noir.exe','开始游戏.txt'}
assert all(p.is_dir() or p.name in allowed for p in root.iterdir()),'Unexpected player root file'
assert not any(p.name.startswith(('test_','ai-','AI-v')) for p in root.rglob('*')),'Test artifact in player package'
for name,legacy in [('RE7-21-Windows.zip',False),('RE7_21_Noir-Windows.zip',True)]:
    output=root.parent/name
    with ZipFile(output,'x',ZIP_DEFLATED) as archive:
        for path in root.rglob('*'):
            if path.is_file() and 'userdata' not in path.relative_to(root).parts:
                archive.write(path,path.relative_to(root.parent))
        if legacy:archive.writestr('RE7_21_Noir/version.json',metadata)
    with ZipFile(output) as archive:assert archive.testzip() is None
    print('Validated:',output)
