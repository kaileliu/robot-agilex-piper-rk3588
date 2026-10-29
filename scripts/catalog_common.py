"""Shared publication inventory; git applies the project's ignore rules."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def source_files(root=ROOT):
    with tempfile.TemporaryDirectory(prefix='piper-index-') as tmp:
        subprocess.run(['git', 'init', '-q', tmp], check=True)
        result = subprocess.run(['git', f'--git-dir={tmp}/.git', f'--work-tree={root}',
                                 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                                check=True, capture_output=True)
    files = []
    for item in result.stdout.split(b'\0'):
        if not item:
            continue
        rel = item.decode().rstrip('/')
        path = root / rel
        # Git represents nested checkouts by their directory. Expand their
        # source with their own ignore rules, never export their .git data.
        if path.is_dir() and not path.is_symlink():
            files.extend(f'{rel}/{child}' for child in source_files(path))
        else:
            files.append(rel)
    files = sorted(set(files))
    for rel in files:
        path = root / rel
        if path.is_symlink() and not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'External symlink cannot be published: {rel}')
        if not path.is_symlink() and path.stat().st_size >= 100 * 1024 * 1024:
            raise ValueError(f'File exceeds GitHub 100 MiB limit: {rel}')
    return files
