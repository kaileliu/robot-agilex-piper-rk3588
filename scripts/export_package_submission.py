#!/usr/bin/env python3
"""Export the ignore-filtered source without touching the live deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile
from catalog_common import ROOT, source_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/piper-rk3588-source.tar.gz')
    args = parser.parse_args()
    files = source_files()
    credentials = re.compile(r'(?:sk-[A-Za-z0-9_-]{15,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKID[A-Za-z0-9]{16,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')
    hashes = {}
    for rel in files:
        path = ROOT/rel
        if any(part in ('rbnx-build','rbnx-boot','__pycache__','.venv') for part in Path(rel).parts) or '.local.' in path.name:
            raise SystemExit(f'Private or generated file in publication: {rel}')
        if path.is_symlink():
            hashes[rel] = {'symlink':str(path.readlink())}
            continue
        data = path.read_bytes()
        # Scan all text, including YAML, documentation and scripts. Values are
        # deliberately not printed when a match is found.
        if b'\0' not in data:
            text = data.decode('utf-8',errors='replace')
            if credentials.search(text):
                raise SystemExit(f'Credential-like text in {rel}; inspect locally')
            config_text = text if path.suffix in ('.yaml', '.yml', '.env') else ''
            for match in re.finditer(r'(?im)^\s*[\w.-]*(?:api_key|secret_key|access_token)\s*[:=]\s*["\x27]?([^\n"\x27#]+)', config_text):
                value=match[1].strip().rstrip(',')
                if value and not any(token in value for token in ('<','>','$','None','null','YOUR_','os.','env.','getenv','config','cfg','str(','self.','api_key','secret_key','access_token')):
                    raise SystemExit(f'Possible literal credential in {rel}; inspect locally')
        hashes[rel] = hashlib.sha256(data).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output,'w:gz',dereference=False) as archive:
        for rel in files:
            archive.add(ROOT/rel,arcname=rel,recursive=False)
    report={'files':len(files),'archive':args.output.name,
            'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
            'source_sha256':hashes}
    report_path=args.output.with_suffix('.inventory.json')
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    print(f'Exported {len(files)} files: {args.output}')
    print(f'Inventory and SHA256: {report_path}')

if __name__=='__main__':
    main()
