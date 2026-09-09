"""Manual GitHub release updates, verified and installed side by side."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import stat
import threading
import urllib.request
import urllib.error
import uuid
import zipfile

MAX_ZIP = 300*1024*1024


def version(value):
    found = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    if not found:
        raise ValueError('Version must be major.minor.patch')
    return tuple(map(int, found.groups()))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        'User-Agent': 'RE7-21-Noir-Updater', 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'}), timeout=20)


def release_info(config, current, fetch=request):
    repo = config.get('repository', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('configure_repository')
    with fetch(f'https://api.github.com/repos/{repo}/releases/latest') as response:
        data = json.loads(response.read(2*1024*1024))
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('invalid_release')
    tag = data['tag_name']
    if version(tag) <= version(current):
        return None
    asset = next((a for a in data.get('assets', []) if a['name'] == config.get('asset_name', 'RE7_21_Noir-Windows.zip')), None)
    if not asset:
        raise ValueError('missing_asset')
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('missing_digest')
    url = asset['browser_download_url']
    if not url.startswith(f'https://github.com/{repo}/releases/download/'):
        raise ValueError('invalid_download')
    if not 0 < asset['size'] <= MAX_ZIP:
        raise ValueError('invalid_size')
    return dict(tag=tag, url=url, digest=digest[7:].lower(), size=asset['size'])


def unpack(archive, destination):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as bundle:
        files = bundle.infolist()
        if len(files) > 5000 or sum(f.file_size for f in files) > 1024**3:
            raise ValueError('archive_too_large')
        seen = set()
        for item in files:
            parts = PurePosixPath(item.filename).parts
            if not parts or parts[0] != 'RE7_21_Noir' or '\\' in item.filename:
                raise ValueError('unsafe_archive')
            for part in parts:
                if part in ('.', '..') or ':' in part or part.endswith((' ', '.')) or re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', part):
                    raise ValueError('unsafe_archive')
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('unsafe_archive')
            key = item.filename.casefold().rstrip('/')
            if key in seen:
                raise ValueError('duplicate_archive_path')
            seen.add(key)
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('unsafe_archive')
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)


def preserve(source, target, backup):
    if not source.is_file():
        return
    if target.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        if sha(target) != sha(backup):
            raise ValueError('backup_failed')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if sha(source) != sha(target):
        raise ValueError('copy_failed')


def install(root, release, fetch=request):
    version(release['tag'])
    root = Path(root)
    stage = root/'updates'/(release['tag']+'-'+uuid.uuid4().hex[:8])
    stage.mkdir(parents=True, exist_ok=False)
    archive = stage/'release.zip'
    size = 0
    with fetch(release['url']) as source, archive.open('xb') as output:
        while True:
            chunk = source.read(1024*1024)
            if not chunk:
                break
            size += len(chunk)
            if size > release['size'] or size > MAX_ZIP:
                raise ValueError('invalid_size')
            output.write(chunk)
    if size != release['size'] or sha(archive) != release['digest']:
        raise ValueError('checksum_failed')
    unpack(archive, stage)
    package = stage/'RE7_21_Noir'
    metadata = read_json(package/'version.json')
    if metadata.get('product') != 'RE7_21_Noir' or version(metadata.get('version')) != version(release['tag']):
        raise ValueError('wrong_product')
    exe = package/'RE7_21_Noir.exe'
    if not exe.is_file():
        raise ValueError('missing_executable')
    for name in ('config.json', 'audio.json', 'timer.json', 'updates.json','network.json','room-server.json'):
        preserve(root/name, package/name, package/'package-defaults'/name)
    sound_root = root/'sounds'
    if sound_root.is_dir():
        for source in sound_root.rglob('*'):
            if source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(sound_root.resolve()):
                relative = source.relative_to(root)
                preserve(source, package/relative, package/'package-defaults'/relative)
    preset_root=root/'presets'
    if preset_root.is_dir():
        for source in preset_root.glob('*.json'):
            if source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(preset_root.resolve()):
                relative=source.relative_to(root)
                preserve(source,package/relative,package/'package-defaults'/relative)
    return exe


class Updater:
    def __init__(self, root):
        self.root = Path(root)
        self.events = queue.Queue()
        self.busy = False
        self.release = None
        self.executable = None
        self.status = 'idle'
        self.error = ''
        try:
            self.current = read_json(self.root/'version.json')['version']
        except (OSError, ValueError, KeyError):
            self.current = '1.4.5'

    def start(self, download=False):
        if self.busy:
            return
        self.busy = True
        self.status = 'downloading' if download else 'checking'
        def work():
            try:
                if download:
                    if self.release is None:
                        raise ValueError('no_release')
                    self.events.put(('installed', install(self.root, self.release)))
                else:
                    result = release_info(read_json(self.root/'updates.json'), self.current)
                    self.events.put(('available' if result else 'current', result))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            event, value = self.events.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        self.status = event
        if event == 'available':
            self.release = value
        elif event == 'installed':
            self.executable = value
        elif event == 'error':
            self.error = value
