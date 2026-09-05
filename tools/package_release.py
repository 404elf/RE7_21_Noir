"""Create the exact Windows asset expected by the manual updater; never overwrite."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path, help='Built RE7_21_Noir directory')
args = parser.parse_args()
root = args.directory.resolve()
if root.name != 'RE7_21_Noir' or not (root/'RE7_21_Noir.exe').is_file():
    raise SystemExit('Pass the built RE7_21_Noir directory')
metadata = json.loads((root/'version.json').read_text(encoding='utf-8'))
output = root.parent/'RE7_21_Noir-Windows.zip'
with ZipFile(output, 'x', ZIP_DEFLATED) as archive:
    for path in root.rglob('*'):
        if path.is_file():
            archive.write(path, path.relative_to(root.parent))
with ZipFile(output) as archive:
    assert archive.testzip() is None
print(f'Validated release v{metadata["version"]}: {output}')
