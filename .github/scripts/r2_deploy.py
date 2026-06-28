#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

COMPRESSIBLE_EXTENSIONS = {
    '.css',
    '.html',
    '.js',
    '.json',
    '.map',
    '.svg',
    '.txt',
    '.webmanifest',
    '.xml',
}

CONTENT_TYPES = {
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.txt': 'text/plain; charset=utf-8',
    '.webmanifest': 'application/manifest+json; charset=utf-8',
    '.xml': 'application/xml; charset=utf-8',
}


def human_size(size):
    units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
    n = float(size)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} {unit}'
        n /= 1024


def sha256_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def content_type_for(path):
    ext = path.suffix.lower()
    if ext in CONTENT_TYPES:
        return CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or 'application/octet-stream'


def should_compress(path):
    return path.suffix.lower() in COMPRESSIBLE_EXTENSIONS


def prepare_file(source, destination, compress):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not compress:
        shutil.copy2(source, destination)
        return False

    with source.open('rb') as src, destination.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=6, mtime=0) as gz:
            shutil.copyfileobj(src, gz, length=1024 * 1024)
    return True


def aws(args, dry_run=False):
    command = ['aws', *args]
    if dry_run:
        return
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, command)



def aws_json(args):
    command = ['aws', *args]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, command)
    return json.loads(result.stdout or '{}')


def list_bucket_keys(bucket, endpoint_url):
    keys = set()
    token = None
    while True:
        args = [
            's3api', 'list-objects-v2',
            '--bucket', bucket,
            '--endpoint-url', endpoint_url,
        ]
        if token:
            args.extend(['--continuation-token', token])
        data = aws_json(args)
        for item in data.get('Contents', []):
            keys.add(item['Key'])
        token = data.get('NextContinuationToken')
        if not token:
            return keys


def load_manifest(path):
    if not path.exists():
        return {'version': 1, 'files': {}}
    with path.open() as f:
        return json.load(f)


def save_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write('\n')
    tmp.replace(path)


def delete_keys(keys, bucket, endpoint_url, dry_run):
    keys = sorted(keys)
    for i in range(0, len(keys), 1000):
        chunk = keys[i:i + 1000]
        payload = {'Objects': [{'Key': key} for key in chunk], 'Quiet': True}
        with tempfile.NamedTemporaryFile('w', delete=False) as f:
            json.dump(payload, f)
            payload_path = f.name
        try:
            aws([
                's3api', 'delete-objects',
                '--bucket', bucket,
                '--delete', f'file://{payload_path}',
                '--endpoint-url', endpoint_url,
            ], dry_run=dry_run)
        finally:
            try:
                os.unlink(payload_path)
            except FileNotFoundError:
                pass


def group_name(entry):
    encoding = entry.get('content_encoding') or 'identity'
    value = encoding + '__' + entry['content_type']
    return ''.join(c if c.isalnum() else '_' for c in value)


def link_or_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def build_changed_upload_trees(upload_entries, staging):
    upload_root = staging.parent / (staging.name + '-changed')
    if upload_root.exists():
        shutil.rmtree(upload_root)
    upload_root.mkdir(parents=True)

    groups = {}
    for entry in upload_entries:
        group = group_name(entry)
        group_root = upload_root / group
        link_or_copy(Path(entry['staged_path']), group_root / entry['key'])
        groups.setdefault(group, {
            'root': group_root,
            'content_type': entry['content_type'],
            'content_encoding': entry.get('content_encoding'),
            'count': 0,
        })
        groups[group]['count'] += 1
    return groups


def sync_group(group, bucket, endpoint_url, dry_run):
    args = [
        's3', 'sync', str(group['root']), f's3://{bucket}',
        '--endpoint-url', endpoint_url,
        '--content-type', group['content_type'],
        '--no-progress',
        '--only-show-errors',
        '--size-only',
    ]
    if group.get('content_encoding'):
        args.extend(['--content-encoding', group['content_encoding']])
    print(f"Uploading {group['count']} files as {group['content_type']}"
          + (f" ({group['content_encoding']})" if group.get('content_encoding') else ''))
    aws(args, dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(description='Deploy a static site to R2 using a local manifest.')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--staging', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--endpoint-url', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    source = args.source
    staging = args.staging
    if not source.is_dir():
        raise SystemExit(f'source directory does not exist: {source}')

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    manifest_exists = args.manifest.exists()
    old_manifest = load_manifest(args.manifest)
    old_files = old_manifest.get('files', {})
    new_files = {}
    upload_entries = []
    source_total = 0
    staged_total = 0
    largest_source_files = []

    for path in sorted(p for p in source.rglob('*') if p.is_file()):
        rel = path.relative_to(source).as_posix()
        compressed = should_compress(path)
        staged_path = staging / rel
        source_size = path.stat().st_size
        source_total += source_size
        largest_source_files.append((source_size, rel))

        compressed = prepare_file(path, staged_path, compressed)
        staged_size = staged_path.stat().st_size
        staged_total += staged_size

        record = {
            'sha256': sha256_file(staged_path),
            'size': staged_size,
            'source_size': source_size,
            'content_type': content_type_for(path),
        }
        if compressed:
            record['content_encoding'] = 'gzip'
        new_files[rel] = record

        old_record = old_files.get(rel)
        comparable_record = {k: v for k, v in record.items() if k != 'source_size'}
        comparable_old = {k: v for k, v in old_record.items() if k != 'source_size'} if old_record else None
        if comparable_record != comparable_old:
            upload_entries.append({
                'key': rel,
                'staged_path': str(staged_path),
                **record,
            })

    if manifest_exists:
        delete_candidates = set(old_files) - set(new_files)
    elif args.dry_run:
        delete_candidates = set()
    else:
        print('No R2 manifest found; listing bucket once to find stale keys')
        delete_candidates = list_bucket_keys(args.bucket, args.endpoint_url) - set(new_files)

    print(f'Source files: {len(new_files)}')
    print(f'Source size: {human_size(source_total)}')
    print(f'Staged upload size: {human_size(staged_total)}')
    if source_total:
        saved = 100 - (staged_total / source_total * 100)
        print(f'Gzip reduction: {saved:.1f}%')
    print('Largest source files:')
    for size, rel in sorted(largest_source_files, reverse=True)[:20]:
        print(f'  {human_size(size):>10}  {rel}')
    print(f'Changed uploads: {len(upload_entries)}')
    print(f'Deleted keys: {len(delete_candidates)}')

    groups = build_changed_upload_trees(upload_entries, staging)
    if args.dry_run:
        print(f'Dry run: skipping {len(groups)} grouped R2 syncs')
    else:
        for group in groups.values():
            sync_group(group, args.bucket, args.endpoint_url, False)
    if delete_candidates and not args.dry_run:
        delete_keys(delete_candidates, args.bucket, args.endpoint_url, False)

    manifest = {
        'version': 1,
        'files': new_files,
    }
    if not args.dry_run:
        save_manifest(args.manifest, manifest)


if __name__ == '__main__':
    main()
