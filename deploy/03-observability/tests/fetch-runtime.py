#!/usr/bin/env python3
"""Optional Linux runtime validation setup: public immutable images, no container daemon."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def fetch(url, token=None, accept=None):
    headers = {'User-Agent': 'pcloud-runtime-validation/1'}
    if token: headers['Authorization'] = 'Bearer ' + token
    if accept: headers['Accept'] = accept
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--components', nargs='+', choices=['loki', 'tempo', 'mimir', 'grafana', 'collector', 'gateway'])
    args = p.parse_args()
    target = args.directory.resolve()
    if not target.is_relative_to(Path(tempfile.gettempdir()).resolve()) or target.exists():
        raise ValueError('Use a new Linux temporary-directory child outside repositories')
    # Public binaries must be traversable by the restricted runtime UID.
    target.mkdir(mode=0o755)
    records = json.loads((ROOT/'images.lock.json').read_text())
    names = ['loki', 'tempo', 'mimir', 'grafana', 'collector', 'gateway']
    wanted = {'loki': {'usr/bin/loki'}, 'tempo': {'tempo'}, 'mimir': {'bin/mimir'}, 'collector': {'otelcol-contrib'},
              'grafana': {'usr/share/grafana/bin/grafana', 'usr/share/grafana/conf/defaults.ini'}, 'gateway': {'usr/sbin/nginx'}}
    source_names = {'otel/opentelemetry-collector-contrib': 'collector', 'nginxinc/nginx-unprivileged': 'gateway', **{'grafana/'+x:x for x in ('loki','tempo','mimir','grafana')}}
    for source, record in records.items():
        name = source_names[source.split('/',1)[1].rsplit(':',1)[0]]
        if args.components and name not in args.components: continue
        repo = source.split('/', 1)[1].rsplit(':', 1)[0]
        with fetch('https://auth.docker.io/token?service=registry.docker.io&scope=repository:' + repo + ':pull') as r: token = json.load(r)['token']
        base = 'https://registry-1.docker.io/v2/' + repo
        with fetch(base+'/manifests/'+record['linux_amd64']['manifest_sha256'], token,
                   'application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json') as r: raw = r.read()
        if 'sha256:'+hashlib.sha256(raw).hexdigest() != record['linux_amd64']['manifest_sha256']: raise ValueError('Manifest checksum mismatch')
        manifest = json.loads(raw); extracted = set(); home = target/name; home.mkdir()
        for layer in manifest['layers']:
            with tempfile.TemporaryFile() as blob, fetch(base+'/blobs/'+layer['digest'], token) as r:
                h = hashlib.sha256(); size = 0
                while chunk := r.read(1024**2):
                    size += len(chunk)
                    if size > 1024**3: raise ValueError('Layer exceeds validation bound')
                    h.update(chunk); blob.write(chunk)
                if 'sha256:'+h.hexdigest() != layer['digest']: raise ValueError('Layer checksum mismatch')
                blob.seek(0)
                with tarfile.open(fileobj=blob, mode='r:*') as archive:
                    links = []
                    for member in archive:
                        path = member.name.removeprefix('./')
                        library = name == 'gateway' and (path.startswith('usr/lib/') or path.startswith('lib/'))
                        grafana_asset = name == 'grafana' and path.startswith('usr/share/grafana/')
                        if path not in wanted[name] and not library and not grafana_asset: continue
                        if library and member.issym():
                            links.append((path, member.linkname)); continue
                        if not member.isfile(): continue
                        dest = (home/path).resolve()
                        if not dest.is_relative_to(home.resolve()) or member.size > 512*1024**2: raise ValueError('Unsafe archive entry')
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as source_file, dest.open('wb') as output:
                            while chunk := source_file.read(1024**2): output.write(chunk)
                        dest.chmod(member.mode & 0o755); extracted.add(path)
                    for path, link in links:
                        dest = home/path
                        endpoint = home/link.lstrip('/') if link.startswith('/') else dest.parent/link
                        if not endpoint.resolve().is_relative_to(home.resolve()): raise ValueError('Unsafe library symlink')
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if not dest.exists() and not dest.is_symlink(): dest.symlink_to(endpoint.relative_to(home).as_posix() if dest.parent == home else
                            os.path.relpath(endpoint, dest.parent))
            print('Verified layer', name, layer['digest'], flush=True)
        if not wanted[name] <= extracted: raise ValueError('Image did not contain required runtime files')
        print('PASS runtime files:', name, flush=True)
    (target/'image-lock.json').write_text(json.dumps(records, sort_keys=True))
    print('Runtime directory:', target)


if __name__ == '__main__': main()
