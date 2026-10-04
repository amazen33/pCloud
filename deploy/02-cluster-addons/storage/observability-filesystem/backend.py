#!/usr/bin/env python3
"""Standalone monolithic LGTM filesystem handover and isolated conformance."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent
OWNER = 'observability-filesystem'
COMPONENTS = {'loki': '3.7.8', 'tempo': '2.10.8', 'mimir': '2.17.11'}
MOUNT = '/var/lib/pcloud/observability'
IMAGE_SOURCE = 'docker.io/library/python:3.14.8-alpine'


class CheckRejected(ValueError): pass


def require(condition, message):
    if not condition: raise CheckRejected(message)


def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate(c):
    try: jsonschema.Draft202012Validator(read_json(ROOT / 'site.schema.json')).validate(c)
    except jsonschema.ValidationError: raise CheckRejected('Backend site schema rejected') from None
    volumes = list(c['volumes'].values())
    require(len({v['claim'] for v in volumes}) == 3, 'Each component requires its own claim')
    require(sum(v['size_gib'] for v in volumes) <= c['max_total_gib'], 'Claims exceed reviewed size budget')
    require(all(('uid' in v) == (c['profile'] == 'supplied-filesystem') for v in volumes),
            'Supplied claims require explicit UIDs; installed claims must not supply UIDs')
    if c['profile'] == 'supplied-filesystem':
        require(len({v['uid'] for v in volumes}) == 3, 'Supplied claim UIDs must be distinct')
    return c


def meta(c, name, namespaced=True):
    return {'name': name, **({'namespace': c['namespace']} if namespaced else {}),
            'labels': {'pcloud.io/package': OWNER}}


def claim(c, component, name=None, size=None):
    v = c['volumes'][component]
    obj = {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': meta(c, name or v['claim']),
           'spec': {'storageClassName': c['storage_class'], 'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem',
                    'resources': {'requests': {'storage': str(size or v['size_gib']) + 'Gi'}}}}
    obj['metadata']['labels']['pcloud.io/backend-component'] = component
    return obj


def namespace(c, run=None):
    labels = {'pcloud.io/backend-owner': OWNER}
    if run: labels['pcloud.io/run'] = run
    for mode in ('enforce', 'audit', 'warn'):
        labels['pod-security.kubernetes.io/' + mode] = 'restricted'
        labels['pod-security.kubernetes.io/' + mode + '-version'] = 'v1.35'
    return {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': c['namespace'], 'labels': labels}}


def render(c):
    return [] if c['profile'] == 'supplied-filesystem' else [namespace(c), *[claim(c, x) for x in COMPONENTS]]


def fragments():
    loki = MOUNT + '/loki'; tempo = MOUNT + '/tempo'; mimir = MOUNT + '/mimir'
    return {
        'loki': {'common': {'path_prefix': loki, 'storage': {'filesystem': {
                    'chunks_directory': loki + '/chunks', 'rules_directory': loki + '/rules'}}},
                 'schema_config': {'configs': [{'from': '2024-01-01', 'store': 'tsdb', 'object_store': 'filesystem',
                    'schema': 'v13', 'index': {'prefix': 'index_', 'period': '24h'}}]},
                 'storage_config': {'tsdb_shipper': {'active_index_directory': loki + '/index', 'cache_location': loki + '/index-cache'}},
                 'ingester': {'wal': {'enabled': True, 'dir': loki + '/wal'}},
                 'compactor': {'working_directory': loki + '/compactor'}, 'ruler': {'rule_path': loki + '/ruler-cache'}},
        'tempo': {'storage': {'trace': {'backend': 'local', 'wal': {'path': tempo + '/wal'}, 'local': {'path': tempo + '/blocks'}}},
                  'metrics_generator': {'storage': {'path': tempo + '/generator-wal'},
                                        'traces_storage': {'path': tempo + '/generator-traces'}}},
        'mimir': {'blocks_storage': {'backend': 'filesystem', 'filesystem': {'dir': mimir + '/blocks'},
                    'tsdb': {'dir': mimir + '/tsdb'}, 'bucket_store': {'sync_dir': mimir + '/tsdb-sync'}},
                  'compactor': {'data_dir': mimir + '/compactor'}, 'ruler': {'rule_path': mimir + '/ruler-cache'},
                  'ruler_storage': {'backend': 'filesystem', 'filesystem': {'dir': mimir + '/rules'}},
                  'alertmanager_storage': {'backend': 'filesystem', 'filesystem': {'dir': mimir + '/alertmanager-bucket'}},
                  'alertmanager': {'data_dir': mimir + '/alertmanager-runtime'}}}


def capability(c):
    return {'api': 'pcloud.observability-backend/v1', 'profile': c['profile'], 'namespace': c['namespace'],
            'mount_path': MOUNT, 'run_as_uid': 10001, 'run_as_group': 10001, 'fs_group': 10001,
            'fs_group_change_policy': 'OnRootMismatch', 'volumes': c['volumes'], 'components': COMPONENTS,
            'fragments_sha256': hashlib.sha256(json.dumps(fragments(), sort_keys=True).encode()).hexdigest(),
            'deployment_mode': 'one-monolithic-instance-per-component', 'object_store_api': None,
            'replication': False, 'backup': False, 'quota_enforced': False,
            'acceptance': 'filesystem-conformance-plus-M4-ingest-query-recovery-required'}


def digest(c):
    h = hashlib.sha256()
    for p in sorted(ROOT.rglob('*')):
        relative = p.relative_to(ROOT)
        if p.is_file() and (p.name in ('backend.py', 'site.schema.json', 'capability.schema.json', 'images.lock.json', 'UPSTREAM.json')
                            or relative.parts[0] in ('scripts', 'upstream')):
            h.update(relative.as_posix().encode()); h.update(p.read_bytes())
    h.update(json.dumps(c, sort_keys=True).encode()); h.update(json.dumps(fragments(), sort_keys=True).encode())
    h.update(yaml.safe_dump_all(render(c), sort_keys=False).encode())
    return h.hexdigest()


def run(argv, data=None):
    result = subprocess.run(argv, input=data, text=True, capture_output=True, timeout=60)
    require(result.returncode == 0, 'Command failed; sensitive diagnostic output suppressed')
    require(len(result.stdout) <= 8 * 1024**2, 'Command output exceeded bound')
    return result.stdout


def kube(c, *argv, data=None):
    return run(['kubectl', '--context', c['context'], '--request-timeout=30s', *argv], data)


def get(c, kind, name=None, ns=None):
    return json.loads(kube(c, 'get', kind, *([name] if name else []), *(['-n', ns] if ns else []), '-o', 'json'))


def claim_matches(c, component, pvc):
    desired = claim(c, component)['spec']; actual = pvc['spec']
    require(all(actual.get(k) == desired[k] for k in ('storageClassName', 'accessModes'))
            and actual.get('volumeMode', 'Filesystem') == 'Filesystem', 'Unexpected claim class/access/mode')
    # Quantity normalization permits an equivalent Kubernetes canonical value.
    size = actual['resources']['requests']['storage']
    factors = {'Gi': 1024**3, 'Mi': 1024**2, 'Ki': 1024, 'G': 10**9, 'M': 10**6, 'K': 1000}
    match = re.fullmatch(r'([0-9]+)(Gi|Mi|Ki|G|M|K)?', size)
    require(match and int(match[1]) * factors.get(match[2], 1) == c['volumes'][component]['size_gib'] * 1024**3,
            'Claim capacity differs from reviewed request')
    if c['profile'] == 'supplied-filesystem':
        require(pvc['metadata']['uid'] == c['volumes'][component]['uid'], 'Supplied claim UID changed')
    else:
        require(pvc['metadata'].get('labels', {}).get('pcloud.io/package') == OWNER
                and pvc['metadata']['labels'].get('pcloud.io/backend-component') == component, 'Foreign claim owner/component')


def preflight(c):
    server = json.loads(kube(c, 'version', '-o', 'json'))['serverVersion']['gitVersion']
    require(re.match(r'^v1\.35\.', server), 'Supported cluster is Kubernetes 1.35')
    sc = get(c, 'storageclass', c['storage_class'])
    require(sc.get('provisioner') == c['provisioner'] and sc.get('reclaimPolicy') == 'Retain'
            and sc.get('volumeBindingMode') == 'WaitForFirstConsumer', 'Retain/WFFC storage capability required')
    for worker in sorted({v['worker'] for v in c['volumes'].values()}):
        node = get(c, 'node', worker); labels = node['metadata'].get('labels', {})
        require(labels.get('kubernetes.io/hostname') == worker
                and not any(k in labels for k in ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master'))
                and not node.get('spec', {}).get('unschedulable') and any(x['type'] == 'Ready' and x['status'] == 'True'
                for x in node.get('status', {}).get('conditions', [])), 'Ready schedulable worker required')
    raw = kube(c, 'get', 'namespace', c['namespace'], '--ignore-not-found', '-o', 'json')
    namespace_exists = bool(raw)
    if raw:
        ns = json.loads(raw); labels = ns['metadata'].get('labels', {})
        require(all(labels.get('pod-security.kubernetes.io/' + m) == 'restricted' for m in ('enforce', 'audit', 'warn')),
                'Restricted backend namespace required')
        require(all(labels.get('pod-security.kubernetes.io/' + m + '-version') == 'v1.35' for m in ('enforce', 'audit', 'warn')),
                'Backend Pod Security version differs from reviewed contract')
        if c['profile'] == 'lab-filesystem':
            require(labels.get('pcloud.io/backend-owner') == OWNER, 'Existing namespace has foreign backend owner')
    else: require(c['profile'] == 'lab-filesystem', 'Supplied namespace must exist')
    for component, v in c['volumes'].items():
        raw = kube(c, 'get', 'pvc', v['claim'], '-n', c['namespace'], '--ignore-not-found', '-o', 'json') if namespace_exists else ''
        if raw: claim_matches(c, component, json.loads(raw))
        else: require(c['profile'] == 'lab-filesystem', 'Supplied claim must exist')
    return {'status': 'PASS', 'read_only': True, 'capacity': 'size-budget-is-not-measured-host-capacity'}


def bound_volume(c, pvc, worker):
    require(pvc.get('status', {}).get('phase') == 'Bound', 'Bound claim required')
    pv = get(c, 'pv', pvc['spec']['volumeName']); spec = pv['spec']
    require(spec.get('persistentVolumeReclaimPolicy') == 'Retain' and spec.get('storageClassName') == c['storage_class']
            and spec.get('volumeMode', 'Filesystem') == 'Filesystem' and spec.get('accessModes') == ['ReadWriteOnce'],
            'Unexpected volume class/reclaim/access/mode')
    require(spec.get('claimRef', {}).get('uid') == pvc['metadata']['uid']
            and spec['claimRef'].get('namespace') == pvc['metadata']['namespace'], 'Volume/claim UID binding changed')
    require(pv['metadata'].get('annotations', {}).get('pv.kubernetes.io/provisioned-by') == c['provisioner'],
            'Unexpected volume provisioner')
    terms = spec.get('nodeAffinity', {}).get('required', {}).get('nodeSelectorTerms', [])
    require(terms and all(any(e.get('key') == 'kubernetes.io/hostname' and e.get('operator') == 'In'
            and e.get('values') == [worker] for e in t.get('matchExpressions', [])) for t in terms),
            'Exclusive expected worker affinity required')
    require(not any(k in spec for k in ('nfs', 'azureFile', 'cephfs')), 'Network filesystem is outside the lab contract')
    return pv


def live(c):
    preflight(c); pending = []; bound = []
    for component, v in c['volumes'].items():
        pvc = get(c, 'pvc', v['claim'], c['namespace']); claim_matches(c, component, pvc)
        if pvc.get('status', {}).get('phase') == 'Pending': pending.append(component); continue
        bound_volume(c, pvc, v['worker']); bound.append(component)
    return {'status': 'INCOMPLETE', 'read_only': True, 'bound_components': bound, 'awaiting_first_consumer': pending,
            'remaining': ['authorized-POSIX-probe', 'M4-component-ingest-query-and-recovery']}


def authorize(c, args):
    require(args.allow_cluster_changes, 'Mutation requires --allow-cluster-changes')
    review = read_json(args.review)
    require(set(review) == {'revision', 'artifact_digest'} and re.fullmatch('[0-9a-f]{40}', review['revision'])
            and review['artifact_digest'] == digest(c), 'Reviewed revision/digest rejected')
    return review


def probe_pod(c, component, run_id, action):
    uid = 10002 if action == 'deny' else 10001
    image = read_json(ROOT / 'images.lock.json')[IMAGE_SOURCE]['image']
    security = {'runAsNonRoot': True, 'runAsUser': uid, 'runAsGroup': uid, 'seccompProfile': {'type': 'RuntimeDefault'}}
    if action != 'deny': security.update({'fsGroup': 10001, 'fsGroupChangePolicy': 'OnRootMismatch'})
    obj = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta(c, component + '-' + action),
           'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'securityContext': security,
                    'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                        {'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [c['volumes'][component]['worker']]},
                            {'key': 'node-role.kubernetes.io/control-plane', 'operator': 'DoesNotExist'},
                            {'key': 'node-role.kubernetes.io/master', 'operator': 'DoesNotExist'}]}]}}},
                    'containers': [{'name': 'probe', 'image': image, 'command': ['python3', '-c',
                        (ROOT / 'scripts/posix-probe.py').read_text(encoding='utf-8')],
                        'args': [action, '--root', '/volume', '--run', run_id, '--component', component, '--uid', str(uid),
                            '--min-bytes', str(c['minimum_free_bytes']), '--min-inodes', str(c['minimum_free_inodes']),
                            '--reserve', str(c['reserve_fraction'])],
                        'resources': {'requests': {'cpu': '50m', 'memory': '32Mi'}, 'limits': {'cpu': '250m', 'memory': '128Mi'}},
                        'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                        'volumeMounts': [{'name': 'data', 'mountPath': '/volume', 'readOnly': action == 'deny'}]}],
                    'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': c['volumes'][component]['claim'], 'readOnly': action == 'deny'}}]}}
    obj['metadata']['labels']['pcloud.io/run'] = run_id
    return obj


def create(c, obj):
    return json.loads(kube(c, 'create', '-f', '-', '-o', 'json', data=json.dumps(obj)))


def delete_uid(c, obj):
    meta = obj['metadata']; require(meta.get('uid'), 'Object deletion requires original UID')
    resource = {'Pod': 'pods', 'PersistentVolumeClaim': 'persistentvolumeclaims', 'Namespace': 'namespaces'}[obj['kind']]
    path = '/api/v1/' + (('namespaces/' + meta['namespace'] + '/') if meta.get('namespace') else '') + resource + '/' + meta['name']
    kube(c, 'delete', '--raw', path, '-f', '-', data=json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions',
         'preconditions': {'uid': meta['uid']}, 'propagationPolicy': 'Foreground'}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        raw = kube(c, 'get', obj['kind'], meta['name'], *(['-n', meta['namespace']] if meta.get('namespace') else []),
                   '--ignore-not-found', '-o', 'json')
        if not raw: return
        require(json.loads(raw)['metadata']['uid'] == meta['uid'], 'Resource replaced; preserve replacement')
        time.sleep(1)
    raise CheckRejected('Deletion not confirmed within bounded wait')


def execute_pod(c, component, run_id, action):
    obj = create(c, probe_pod(c, component, run_id, action)); deadline = time.monotonic() + 300
    try:
        while time.monotonic() < deadline:
            current = get(c, 'pod', obj['metadata']['name'], c['namespace'])
            require(current['metadata']['uid'] == obj['metadata']['uid'], 'Probe pod replaced')
            phase = current.get('status', {}).get('phase')
            require(phase != 'Failed', 'POSIX probe pod failed')
            if phase == 'Succeeded':
                require(current['spec'].get('nodeName') == c['volumes'][component]['worker'], 'Probe ran on unexpected worker')
                report = json.loads(kube(c, 'logs', '-n', c['namespace'], obj['metadata']['name'], '-c', 'probe', '--limit-bytes=4096'))
                require(report.get('status') == 'PASS', 'POSIX probe rejected filesystem')
                return report
            time.sleep(2)
        raise CheckRejected('Probe did not complete within bounded wait')
    finally:
        delete_uid(c, obj)


def namespace_empty(c):
    for resource in kube(c, 'api-resources', '--verbs=list', '--namespaced=true', '-o', 'name').split():
        if '/' in resource: continue
        items = json.loads(kube(c, 'get', resource, '-n', c['namespace'], '-o', 'json'))['items']
        for obj in items:
            allowed = (obj['kind'], obj['metadata']['name']) in [('ConfigMap', 'kube-root-ca.crt'), ('ServiceAccount', 'default')]
            require(allowed or obj['kind'] == 'Event', 'Unknown namespace object preserved')


def smoke(c, args):
    review = authorize(c, args); preflight(c)
    run_id = uuid.uuid4().hex[:12]
    test = {**c, 'namespace': 'pcloud-backend-test-' + run_id, 'profile': 'lab-filesystem',
            'volumes': {x: {'claim': x + '-probe', 'worker': v['worker'], 'size_gib': 1} for x, v in c['volumes'].items()}}
    ns = None; claims = []; initialized = set(); retained = []; checks = []; failed = False; cleanup_failed = False
    report = {'status': 'FAIL', 'revision': review['revision'], 'artifact_digest': review['artifact_digest'], 'run_id': run_id,
              'test_namespace': test['namespace'], 'checks': checks, 'retained_test_volumes': retained}
    try:
        ns = create(test, namespace(test, run_id))
        for component in COMPONENTS:
            pvc = create(test, claim(test, component)); claims.append((component, pvc))
            checks.append({'component': component, 'write': execute_pod(test, component, run_id, 'write')['checks']})
            initialized.add(component)
            pvc = get(test, 'pvc', test['volumes'][component]['claim'], test['namespace'])
            pv = bound_volume(test, pvc, test['volumes'][component]['worker'])
            retained.append({'pv': pv['metadata']['name'], 'pv_uid': pv['metadata']['uid'], 'claim_uid': pvc['metadata']['uid']})
            read = execute_pod(test, component, run_id, 'read')
            expected_hash = hashlib.sha256(('pcloud-backend:' + run_id + ':' + component).encode()).hexdigest()
            require(read.get('marker_sha256') == expected_hash, 'Persistent remount marker mismatch')
            execute_pod(test, component, run_id, 'deny')
            checks[-1].update({'persistent_remount': 'PASS', 'other_uid_read_denied': 'PASS'})
        report['status'] = 'INCOMPLETE'
        report['remaining'] = ['operator-disposition-of-retained-synthetic-PVs', 'M4-ingest-query-and-component-recovery']
    except Exception:
        failed = True
    finally:
        for component, original in reversed(claims):
            try:
                current = get(test, 'pvc', original['metadata']['name'], test['namespace'])
                require(current['metadata']['uid'] == original['metadata']['uid'], 'Test claim replaced; preserve it')
                pv = bound_volume(test, current, test['volumes'][component]['worker'])
                if component in initialized:
                    execute_pod(test, component, run_id, 'cleanup')
                    # Recheck Retain immediately before releasing the claim; never delete a PV.
                    again = bound_volume(test, get(test, 'pvc', original['metadata']['name'], test['namespace']), test['volumes'][component]['worker'])
                    require(again['metadata']['uid'] == pv['metadata']['uid'], 'Test volume replaced; preserve it')
                    delete_uid(test, original)
                else:
                    raise CheckRejected('Unconfirmed partial probe files; preserve claim for operator')
            except Exception: cleanup_failed = True
        if ns:
            try: namespace_empty(test); delete_uid(test, ns)
            except Exception: cleanup_failed = True
        report['cleanup'] = 'FAIL-preserved-or-uncertain-resources' if cleanup_failed else 'INCOMPLETE-retained-test-PVs-require-operator-disposition'
        if failed or cleanup_failed: report['status'] = 'FAIL'
        emit(report, args.output)
    require(not failed and not cleanup_failed, 'Filesystem conformance/cleanup failed; inspect run-owned evidence')
    return report


def uninstall_check(c):
    require(c['profile'] == 'lab-filesystem', 'Supplied data is not owned for uninstall')
    for component, v in c['volumes'].items():
        raw = kube(c, 'get', 'pvc', v['claim'], '-n', c['namespace'], '--ignore-not-found', '-o', 'json')
        require(not raw, 'Permanent backend claims present; preserve data')
    for pv in get(c, 'pv')['items']:
        require(pv.get('spec', {}).get('claimRef', {}).get('namespace') != c['namespace'], 'Retained backend PV present; preserve it')
    return {'status': 'INCOMPLETE', 'read_only': True, 'remaining': ['operator-data-disposition-review']}


def emit(report, output=None):
    raw = json.dumps(report, indent=2) + '\n'
    if output:
        p = Path(output).absolute()
        require(all(not (parent / '.git').exists() for parent in [p.parent, *p.parent.parents]), 'Reports must be outside repositories')
        p.write_text(raw, encoding='utf-8')
    print(raw, end='')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['render', 'capability', 'fragments', 'digest', 'preflight', 'live', 'smoke', 'uninstall-check'])
    p.add_argument('--site', required=True); p.add_argument('--review'); p.add_argument('--output')
    p.add_argument('--allow-cluster-changes', action='store_true'); a = p.parse_args()
    try:
        c = validate(read_json(a.site))
        if a.command == 'render': print(yaml.safe_dump_all(render(c), sort_keys=False), end=''); return 0
        if a.command == 'digest': print(digest(c)); return 0
        if a.command == 'fragments': result = fragments()
        elif a.command == 'capability': result = capability(c)
        elif a.command == 'smoke':
            require(a.review is not None, 'Review file required'); result = smoke(c, a)
        else: result = globals()[a.command.replace('-', '_')](c)
        if a.command != 'smoke': emit(result, a.output)
        return 3 if result.get('status') == 'INCOMPLETE' else 0
    except CheckRejected as exc: print('FAIL: ' + str(exc), file=sys.stderr); return 1
    except Exception: print('FAIL: operation rejected; sensitive diagnostic bodies suppressed', file=sys.stderr); return 1


if __name__ == '__main__': sys.exit(main())
