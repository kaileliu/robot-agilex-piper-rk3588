"""Shared publication allowlist, additionally filtered by Git ignore rules."""
from pathlib import Path
import fnmatch
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def source_files(root=ROOT):
    root = Path(root).resolve()
    policy_path = root / 'catalog/publication.json'
    policy = json.loads(policy_path.read_text()) if policy_path.is_file() else None
    def included(rel):
        if policy is None:
            return True
        return (any(rel.startswith(p) if p.endswith('/') else rel == p
                    for p in policy['include'])
                and not any(rel.startswith(p) if p.endswith('/') else fnmatch.fnmatchcase(rel, p)
                            for p in policy['exclude']))
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
        if not included(rel):
            continue
        path = root / rel
        # Git represents nested checkouts by their directory. Expand their
        # source with their own ignore rules, never export their .git data.
        if path.is_dir() and not path.is_symlink():
            files.extend(f'{rel}/{child}' for child in source_files(path))
        else:
            files.append(rel)
    files = sorted({rel for rel in files if included(rel)})
    for rel in files:
        path = root / rel
        if path.is_symlink() and not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'External symlink cannot be published: {rel}')
        if path.is_symlink() and path.resolve().relative_to(root).as_posix() not in files:
            raise ValueError(f'Symlink target absent from publication: {rel}')
        if not path.is_symlink() and path.stat().st_size >= 100 * 1024 * 1024:
            raise ValueError(f'File exceeds GitHub 100 MiB limit: {rel}')
    return files


if __name__ == '__main__':
    published = set(source_files())
    tracked = set(subprocess.check_output(
        ['git', '-C', str(ROOT), 'ls-files', '-z']).decode().strip('\0').split('\0'))
    unexpected = sorted(tracked - published)
    if unexpected:
        raise SystemExit('Tracked files outside publication inventory:\n' + '\n'.join(unexpected))
    print(f'PASS: {len(tracked)} tracked files match publication policy')
