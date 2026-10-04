#!/usr/bin/env python3
"""Fetch public pinned artifacts and image manifests for an operator-reviewed lock.

Never contacts a managed cluster; never reads registry credentials. A lock is
provenance, not a signature, vulnerability scan or deployment acceptance.
"""
import argparse
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ACCEPT = ', '.join(['application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json'])


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'pcloud-lock/1', **(headers or {})})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read(), dict(r.headers)


def image_lock(image):
    registry, rest = image.split('/', 1)
    repository, tag = rest.rsplit(':', 1)
    if registry not in ('docker.io', 'quay.io', 'ghcr.io') or tag in ('latest', 'master'):
        raise ValueError('Use a reviewed release tag on docker.io, quay.io or ghcr.io')
    host = 'registry-1.docker.io' if registry == 'docker.io' else registry
    url = f'https://{host}/v2/{repository}/manifests/{tag}'
    headers = {'Accept': ACCEPT}
    try:
        body, response = fetch(url, headers)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        challenge = exc.headers.get('WWW-Authenticate', '')
        if not challenge.lower().startswith('bearer '):
            raise ValueError('Unsupported public registry authentication') from exc
        fields = dict(re.findall(r'(\w+)="([^"]+)"', challenge))
        realm = fields.pop('realm')
        if urllib.parse.urlparse(realm).scheme != 'https':
            raise ValueError('Registry token endpoint must use HTTPS')
        fields.setdefault('scope', f'repository:{repository}:pull')
        raw, _ = fetch(realm + '?' + urllib.parse.urlencode(fields))
        token = json.loads(raw)
        headers['Authorization'] = 'Bearer ' + (token.get('token') or token['access_token'])
        body, response = fetch(url, headers)
    actual = 'sha256:' + hashlib.sha256(body).hexdigest()
    reported = next((v for k, v in response.items() if k.lower() == 'docker-content-digest'), actual)
    if actual != reported:
        raise ValueError('Registry digest differs from downloaded manifest')
    return {'source': image, 'image': f'{registry}/{repository}@{actual}',
            'manifest_sha256': actual, 'manifest_url': url}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--images', nargs='+', default=[])
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    records = {}
    for image in a.images:
        records[image] = image_lock(image)
        print(f'Locked {image}: {records[image]["manifest_sha256"]}')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
