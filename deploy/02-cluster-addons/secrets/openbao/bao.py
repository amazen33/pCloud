#!/usr/bin/env python3
"""Standalone lab secrets lifecycle. Reports contain no API bodies or credentials."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent
VERSION = '2.7.1'
OWNER = 'openbao-lab'
AUDIT_PATH = '/var/lib/bao/audit/events.jsonl'
MAX_JSON = 2 * 1024 * 1024
MAX_SNAPSHOT = 128 * 1024 * 1024


class CheckRejected(ValueError):
    """Messages must be constant or constructed from controlled identifiers."""


def require(condition, message):
    if not condition:
        raise CheckRejected(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate(c):
    try:
        jsonschema.Draft202012Validator(read_json(ROOT / 'site.schema.json')).validate(c)
    except jsonschema.ValidationError:
        raise CheckRejected('Site schema rejected configuration') from None
    for key in ('endpoint', 'kubernetes_host'):
        if key not in c:
            continue
        p = urllib.parse.urlsplit(c[key])
        require(p.scheme == 'https' and p.hostname and not p.username and not p.password
                and not p.query and not p.fragment and p.path in ('', '/'), 'HTTPS origin required')
        try:
            require(p.port is None or 1 <= p.port <= 65535, 'Invalid endpoint port')
        except ValueError:
            raise CheckRejected('Invalid endpoint port') from None
    require(c['kv_mount'] not in ('sys', 'identity', 'cubbyhole', 'secret')
            and c['auth_mount'] != 'token', 'Reserved mount name')
    if c['purpose'] == 'recovery':
        require(c['profile'] == 'openbao-raft-lab'
                and c['namespace'].startswith('pcloud-secrets-test-'), 'Recovery requires isolated installed target')
    if c['profile'] == 'openbao-raft-lab':
        for key in ('operator_cidrs', 'api_cidrs'):
            for cidr in c[key]:
                try:
                    net = ipaddress.ip_network(cidr, strict=True)
                except ValueError:
                    raise CheckRejected('Invalid CIDR') from None
                require(net.prefixlen > 0 and not net.is_multicast, 'Unrestricted or multicast CIDR rejected')
        def quantity(s):
            if s.endswith('m'): return float(s[:-1]) / 1000
            if s.endswith('Mi'): return int(s[:-2]) * 1024**2
            if s.endswith('Gi'): return int(s[:-2]) * 1024**3
            return float(s)
        for key in ('cpu', 'memory'):
            require(quantity(c['resources']['limits'][key]) >= quantity(c['resources']['requests'][key]),
                    'Resource limit below request')
        require(quantity(c['resources']['requests']['memory']) >= 256 * 1024**2,
                'Lab server requires at least 256Mi memory request')
    return c


def labels(c):
    return {'pcloud.io/package': OWNER, 'app.kubernetes.io/name': 'openbao',
            'pcloud.io/purpose': c['purpose']}


def object_(c, kind, name, spec=None, **other):
    api = {'StatefulSet': 'apps/v1', 'NetworkPolicy': 'networking.k8s.io/v1',
           'ClusterRole': 'rbac.authorization.k8s.io/v1',
           'ClusterRoleBinding': 'rbac.authorization.k8s.io/v1'}.get(kind, 'v1')
    meta = {'name': name, 'labels': labels(c)}
    if kind not in ('Namespace', 'ClusterRole', 'ClusterRoleBinding'):
        meta['namespace'] = c['namespace']
    obj = {'apiVersion': api, 'kind': kind, 'metadata': meta, **other}
    if spec is not None: obj['spec'] = spec
    return obj


def render(c):
    if c['profile'] == 'supplied-vault-api': return []
    ns = c['namespace']
    selector = {'app.kubernetes.io/name': 'openbao', 'pcloud.io/package': OWNER}
    config = {'ui': False, 'disable_mlock': True, 'cluster_name': 'pcloud-lab-' + ns,
              'api_addr': f'https://openbao.{ns}.svc:8200',
              'cluster_addr': f'https://openbao-0.openbao-internal.{ns}.svc:8201',
              'storage': {'raft': {'path': '/var/lib/bao/data', 'node_id': 'openbao-0'}},
              'listener': {'tcp': {'address': '0.0.0.0:8200', 'cluster_address': '0.0.0.0:8201',
                                  'tls_min_version': 'tls12', 'tls_cert_file': '/etc/bao/tls/tls.crt',
                                  'tls_key_file': '/etc/bao/tls/tls.key'}},
              'default_lease_ttl': '15m', 'max_lease_ttl': '1h'}
    namespace = object_(c, 'Namespace', ns)
    namespace['metadata']['labels'].update({f'pod-security.kubernetes.io/{mode}': 'restricted'
                                          for mode in ('enforce', 'audit', 'warn')})
    namespace['metadata']['labels'].update({f'pod-security.kubernetes.io/{mode}-version': 'v1.35'
                                          for mode in ('enforce', 'audit', 'warn')})
    role = ns + '-reviewer'
    image = read_json(ROOT / 'images.lock.json')['docker.io/openbao/openbao:2.7.1']['image']
    container = {'name': 'openbao', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                 'command': ['/usr/bin/bao'], 'args': ['server', '-config=/etc/bao/config/server.json'],
                 'ports': [{'name': 'https', 'containerPort': 8200}, {'name': 'raft', 'containerPort': 8201}],
                 'resources': c['resources'],
                 'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                     'capabilities': {'drop': ['ALL']}},
                 'startupProbe': {'tcpSocket': {'port': 'https'}, 'periodSeconds': 5, 'failureThreshold': 60},
                 'livenessProbe': {'tcpSocket': {'port': 'https'}, 'periodSeconds': 10, 'failureThreshold': 6},
                 'readinessProbe': {'httpGet': {'scheme': 'HTTPS', 'port': 'https',
                                              'path': '/v1/sys/health?standbyok=true'},
                                    'periodSeconds': 5},
                 'volumeMounts': [{'name': name, 'mountPath': path, 'readOnly': ro} for name, path, ro in
                                  [('config', '/etc/bao/config', True), ('tls', '/etc/bao/tls', True),
                                   ('reviewer', '/var/run/secrets/kubernetes.io/serviceaccount', True),
                                   ('data', '/var/lib/bao/data', False), ('audit', '/var/lib/bao/audit', False),
                                   ('tmp', '/tmp', False)]]}
    pod = {'metadata': {'labels': selector}, 'spec': {'serviceAccountName': 'openbao',
           'automountServiceAccountToken': False, 'terminationGracePeriodSeconds': 60,
           'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000,
                               'fsGroup': 1000, 'fsGroupChangePolicy': 'OnRootMismatch',
                               'seccompProfile': {'type': 'RuntimeDefault'}},
           'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
               {'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': c['workers']},
                                     {'key': 'node-role.kubernetes.io/control-plane', 'operator': 'DoesNotExist'},
                                     {'key': 'node-role.kubernetes.io/master', 'operator': 'DoesNotExist'}]}]}}},
           'containers': [container],
           'volumes': [{'name': 'config', 'configMap': {'name': 'openbao-config'}},
                       {'name': 'tls', 'secret': {'secretName': c['tls_secret'], 'defaultMode': 288}},
                       {'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}},
                       {'name': 'reviewer', 'projected': {'defaultMode': 288, 'sources': [
                           {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 3600}},
                           {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}},
                           {'downwardAPI': {'items': [{'path': 'namespace', 'fieldRef': {'fieldPath': 'metadata.namespace'}}]}}]}}]}}
    claims = [{'metadata': {'name': name, 'labels': labels(c)}, 'spec': {
        'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem', 'storageClassName': c['storage_class'],
        'resources': {'requests': {'storage': c[name + '_size']}}}} for name in ('data', 'audit')]
    stateful = object_(c, 'StatefulSet', 'openbao', {'replicas': 1, 'serviceName': 'openbao-internal',
        'selector': {'matchLabels': selector}, 'template': pod, 'volumeClaimTemplates': claims,
        'persistentVolumeClaimRetentionPolicy': {'whenDeleted': 'Retain', 'whenScaled': 'Retain'}})
    services = [object_(c, 'Service', name, {'selector': selector,
                'ports': [{'name': 'https', 'port': 8200, 'targetPort': 'https'}], **extra})
                for name, extra in [('openbao', {}), ('openbao-internal', {'clusterIP': 'None', 'publishNotReadyAddresses': True})]]
    network = object_(c, 'NetworkPolicy', 'openbao', {
        'podSelector': {'matchLabels': selector}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [{'namespaceSelector': {'matchLabels': {'pcloud.io/secrets-access': 'true'}}}]
                            + [{'ipBlock': {'cidr': v}} for v in c['operator_cidrs']],
                     'ports': [{'protocol': 'TCP', 'port': 8200}]}],
        'egress': [{'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                           'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                    'ports': [{'protocol': p, 'port': 53} for p in ('UDP', 'TCP')]},
                   {'to': [{'ipBlock': {'cidr': v}} for v in c['api_cidrs']],
                    'ports': [{'protocol': 'TCP', 'port': p} for p in sorted({443, c['api_port']})]}]})
    return [namespace, object_(c, 'ServiceAccount', 'openbao', automountServiceAccountToken=False),
            object_(c, 'ClusterRole', role, rules=[{'apiGroups': ['authentication.k8s.io'],
                    'resources': ['tokenreviews'], 'verbs': ['create']}]),
            object_(c, 'ClusterRoleBinding', role, roleRef={'apiGroup': 'rbac.authorization.k8s.io',
                    'kind': 'ClusterRole', 'name': role}, subjects=[{'kind': 'ServiceAccount', 'name': 'openbao', 'namespace': ns}]),
            object_(c, 'ConfigMap', 'openbao-config', data={'server.json': json.dumps(config, sort_keys=True)}),
            *services, stateful, network]


def manifest(c): return yaml.safe_dump_all(render(c), sort_keys=False)


def digest(c):
    h = hashlib.sha256()
    for p in sorted(ROOT.rglob('*')):
        rel = p.relative_to(ROOT)
        if p.is_file() and (p.name in ('bao.py', 'site.schema.json', 'capability.schema.json', 'images.lock.json', 'UPSTREAM.json')
                            or rel.parts[0] == 'upstream'):
            h.update(rel.as_posix().encode()); h.update(p.read_bytes())
    h.update(json.dumps(c, sort_keys=True).encode()); h.update(manifest(c).encode())
    ca = Path(c['ca_file'])
    h.update(ca.read_bytes() if ca.is_file() else b'CA-NOT-PRESENT')
    return h.hexdigest()


def capability(c):
    return {'api': 'pcloud.secrets/v1', 'profile': c['profile'], 'endpoint': c['endpoint'],
            'kv_v2_mount': c['kv_mount'], 'kubernetes_auth_mount': c['auth_mount'],
            'tls_verification': 'trusted-ca-and-hostname',
            'credential_delivery': 'projected-service-account-jwt; scoped-short-lived-token',
            'ha': False, 'acceptance': 'requires-live-conformance-and-recovery-evidence'}


def run(argv, data=None):
    result = subprocess.run(argv, input=data, capture_output=True, text=True, timeout=120)
    require(result.returncode == 0, 'Command failed; sensitive command output suppressed')
    require(len(result.stdout) <= 8 * MAX_JSON, 'Command response too large')
    return result.stdout


def kube(c, *argv, data=None):
    return run(['kubectl', '--context', c['context'], '--request-timeout=30s', *argv], data)


def get(c, kind, name=None, namespace=None):
    argv = ['get', kind] + ([name] if name else [])
    if namespace: argv += ['-n', namespace]
    return json.loads(kube(c, *argv, '-o', 'json'))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


class Client:
    def __init__(self, c, token=None):
        self.origin = c['endpoint'].rstrip('/')
        self.token = token
        context = ssl.create_default_context(cafile=c['ca_file'])
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def open(self, method, path, body=None, token=None):
        require(re.fullmatch(r'[a-zA-Z0-9_./?=&-]+', path) and '..' not in path
                and not path.startswith('/'), 'Invalid API path')
        headers = {'Content-Type': 'application/json'}
        credential = self.token if token is None else token
        if credential: headers['X-Vault-Token'] = credential
        data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.origin + '/v1/' + path, data=data, method=method, headers=headers)
        try: return self.opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc: return exc

    def request(self, method, path, body=None, token=None, allowed=(200, 204)):
        with self.open(method, path, body, token) as response:
            require(response.status in allowed, 'API returned an unexpected status')
            raw = response.read(MAX_JSON + 1)
            require(len(raw) <= MAX_JSON, 'API response too large')
            return json.loads(raw) if raw else {}


def admin(c):
    token = os.environ.get('PCLOUD_BAO_TOKEN')
    require(bool(token) and not re.search(r'\s', token), 'PCLOUD_BAO_TOKEN required')
    return Client(c, token)


def health(client):
    h = client.request('GET', 'sys/health', token='', allowed=(200, 429, 472, 473, 501, 503))
    require(h.get('initialized') is True and h.get('sealed') is False and h.get('standby') is False,
            'Server must be initialized, unsealed and active')
    return h


def preflight(c):
    version = json.loads(kube(c, 'version', '-o', 'json'))['serverVersion']['gitVersion']
    require(re.match(r'^v1\.35\.', version), 'Supported lab cluster is Kubernetes 1.35')
    if c['profile'] == 'supplied-vault-api':
        get(c, 'namespace', c['namespace'])
        return {'status': 'PASS', 'read_only': True}
    sc = get(c, 'storageclass', c['storage_class'])
    require(sc.get('provisioner') == c['storage_provisioner'] and sc.get('reclaimPolicy') == 'Retain'
            and sc.get('volumeBindingMode') == 'WaitForFirstConsumer', 'Retain/WFFC storage capability required')
    for name in c['workers']:
        node = get(c, 'node', name)
        ls = node['metadata'].get('labels', {})
        require(not any(k in ls for k in ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master')),
                'Worker-only storage placement required')
        require(ls.get('kubernetes.io/hostname') == name and not node.get('spec', {}).get('unschedulable')
                and any(v['type'] == 'Ready' and v['status'] == 'True' for v in node.get('status', {}).get('conditions', [])),
                'Ready schedulable worker required')
    # Only JSONPath metadata and key names; never return the TLS private-key bytes.
    secret = kube(c, 'get', 'secret', c['tls_secret'], '-n', c['namespace'],
                  '-o', 'go-template={{.type}}|{{range $k,$v := .data}}{{$k}}|{{end}}')
    require(secret.startswith('kubernetes.io/tls|') and 'tls.crt|' in secret and 'tls.key|' in secret,
            'Precreated TLS Secret required')
    for obj in render(c):
        # --ignore-not-found distinguishes absence without swallowing permission/transport failures.
        ns = obj['metadata'].get('namespace')
        raw = kube(c, 'get', obj['kind'], obj['metadata']['name'], *(['-n', ns] if ns else []),
                   '--ignore-not-found', '-o', 'json')
        if raw.strip():
            existing = json.loads(raw)
            require(existing['metadata'].get('labels', {}).get('pcloud.io/package') == OWNER,
                    'Existing installation resource has a foreign owner')
    return {'status': 'PASS', 'read_only': True, 'capacity': 'operator-measurement-required'}


def live(c):
    preflight(c)
    api = admin(c)
    h = health(api)
    if c['profile'] == 'openbao-raft-lab':
        require(h.get('version') == VERSION, 'Unexpected installed OpenBao version')
        for desired_object in render(c):
            m = desired_object['metadata']
            actual = get(c, desired_object['kind'], m['name'], m.get('namespace'))
            require(contains(actual, desired_object), 'Installation resource differs from reviewed manifest')
            if desired_object['kind'] == 'ClusterRole':
                require(actual['rules'] == desired_object['rules'], 'TokenReview role contains unexpected permissions')
        sts = get(c, 'statefulset', 'openbao', c['namespace'])
        desired = next(o for o in render(c) if o['kind'] == 'StatefulSet')['spec']
        require(sts['spec']['replicas'] == 1 and contains(sts['spec']['template'], desired['template']),
                'Server pod template differs from reviewed manifest')
        ps = sts['spec']['template']['spec']
        require(not ps.get('initContainers') and not ps.get('ephemeralContainers')
                and not any(ps.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC', 'shareProcessNamespace'))
                and not ps['containers'][0].get('env') and not ps['containers'][0].get('envFrom'),
                'Unexpected server execution settings')
        require(sts.get('status', {}).get('readyReplicas') == 1, 'One ready server required')
        for name in ('data', 'audit'):
            pvc = get(c, 'pvc', name + '-openbao-0', c['namespace'])
            require(pvc.get('status', {}).get('phase') == 'Bound', 'Persistent claim not bound')
            pv = get(c, 'pv', pvc['spec']['volumeName'])
            require(pv['spec'].get('persistentVolumeReclaimPolicy') == 'Retain', 'Retained volumes required')
    mounts = api.request('GET', 'sys/mounts')['data']
    require(mounts.get(c['kv_mount'] + '/', {}).get('type') == 'kv'
            and mounts[c['kv_mount'] + '/'].get('options', {}).get('version') == '2', 'KV v2 capability required')
    auth = api.request('GET', 'sys/auth')['data']
    require(auth.get(c['auth_mount'] + '/', {}).get('type') == 'kubernetes', 'Kubernetes authentication required')
    audits = api.request('GET', 'sys/audit')['data']
    require(bool(audits) and all(a.get('options', {}).get('log_raw', 'false') in ('false', False)
                              for a in audits.values()), 'HMAC audit logging required')
    if c['profile'] == 'openbao-raft-lab':
        a = audits.get('pcloud-file/', {})
        require(a.get('type') == 'file' and a.get('options', {}).get('file_path') == AUDIT_PATH,
                'Persistent file audit device required')
    return {'status': 'PASS', 'read_only': True, 'checks': ['health', 'kv-v2', 'kubernetes-auth', 'audit'],
            'acceptance': 'smoke-restart-and-isolated-restore-still-required'}


def contains(actual, desired):
    """API defaults are allowed; every declared value must still match."""
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in desired.items())
    if isinstance(desired, list):
        return isinstance(actual, list) and len(actual) == len(desired) and all(contains(a, d) for a, d in zip(actual, desired))
    return actual == desired


def authorize(c, args, extra=(), mutation=True):
    if mutation: require(args.allow_cluster_changes, 'Mutation requires --allow-cluster-changes')
    review = read_json(args.review)
    require(set(review) == {'revision', 'artifact_digest', *extra} and re.fullmatch(r'[0-9a-f]{40}', review['revision'])
            and review['artifact_digest'] == digest(c), 'Review revision/digest rejected')
    # The caller attests the source revision; uncommitted work must be reviewed as a separate digest.
    return review


def bootstrap(c, args):
    authorize(c, args)
    require(c['profile'] == 'openbao-raft-lab' and c['purpose'] == 'platform', 'Bootstrap is installed platform only')
    preflight(c); api = admin(c); health(api)
    audits = api.request('GET', 'sys/audit')['data']
    desired = {'file_path': AUDIT_PATH, 'log_raw': 'false', 'format': 'json'}
    if 'pcloud-file/' in audits:
        a = audits['pcloud-file/']
        require(a.get('type') == 'file' and all(a.get('options', {}).get(k) == v for k, v in desired.items()),
                'Audit device conflicts; preserve existing device')
    else:
        api.request('PUT', 'sys/audit/pcloud-file', {'type': 'file', 'description': OWNER, 'options': desired})
    for category, name, body in [('mounts', c['kv_mount'], {'type': 'kv', 'options': {'version': '2'}}),
                                 ('auth', c['auth_mount'], {'type': 'kubernetes'})]:
        current = api.request('GET', 'sys/' + category)['data'].get(name + '/')
        if current:
            require(current.get('type') == body['type'] and current.get('description') == OWNER
                    and (category != 'mounts' or current.get('options', {}).get('version') == '2'),
                    'Mount ownership/configuration conflict')
        else:
            api.request('POST', 'sys/' + category + '/' + name, {**body, 'description': OWNER})
    api.request('POST', c['kv_mount'] + '/config', {'cas_required': True, 'max_versions': 10})
    # Omit reviewer JWT/CA: plugin continuously rereads the short-lived projected local files.
    api.request('POST', 'auth/' + c['auth_mount'] + '/config',
                {'kubernetes_host': c['kubernetes_host'], 'disable_local_ca_jwt': False})
    return {'status': 'PASS', 'checks': ['persistent-hmac-audit', 'kv-v2-cas', 'local-projected-reviewer'],
            'application_roles': 'owned-by-consumers; none-created'}


def delete_uid(c, obj):
    meta = obj['metadata']
    require(meta.get('uid'), 'Missing created object UID')
    kind = {'Namespace': 'namespaces', 'ServiceAccount': 'serviceaccounts', 'Pod': 'pods'}[obj['kind']]
    path = '/api/v1/' + (('namespaces/' + meta['namespace'] + '/') if 'namespace' in meta else '') + kind + '/' + meta['name']
    kube(c, 'delete', '--raw', path, '-f', '-', data=json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions',
         'preconditions': {'uid': meta['uid']}, 'propagationPolicy': 'Foreground'}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        raw = kube(c, 'get', obj['kind'], meta['name'], *(['-n', meta['namespace']] if 'namespace' in meta else []),
                   '--ignore-not-found', '-o', 'json')
        if not raw.strip(): return
        require(json.loads(raw)['metadata']['uid'] == meta['uid'], 'Deleted resource replaced; preserve replacement')
        time.sleep(1)
    raise CheckRejected('Test object deletion not confirmed within bounded wait')


def namespace_empty(c, ns):
    resources = kube(c, 'api-resources', '--verbs=list', '--namespaced=true', '-o', 'name').split()
    for resource in resources:
        if '/' in resource: continue
        items = json.loads(kube(c, 'get', resource, '-n', ns, '-o', 'json'))['items']
        for item in items:
            allowed = (item['kind'], item['metadata']['name']) in [('ConfigMap', 'kube-root-ca.crt'), ('ServiceAccount', 'default')]
            require(allowed or item['kind'] == 'Event', 'Unknown namespace resource preserved')


def smoke(c, args):
    review = authorize(c, args)
    live(c); api = admin(c)
    run_id = uuid.uuid4().hex[:12]; mount = 'pcloud-test-' + run_id; role = mount; policy = mount
    ns = 'pcloud-secrets-test-' + run_id; wrong_ns = ns + '-other'
    marker = OWNER + ':' + run_id
    created = []; tokens = []; mount_owned = role_owned = policy_owned = False
    checks = []; report = {'status': 'FAIL', 'revision': review['revision'], 'artifact_digest': review['artifact_digest'],
                           'run_id': run_id, 'checks': checks}
    failed = None
    try:
        for n in (ns, wrong_ns):
            obj = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': n,
                   'labels': {'pcloud.io/run': run_id, 'pcloud.io/secrets-access': 'true',
                              'pod-security.kubernetes.io/enforce': 'restricted',
                              'pod-security.kubernetes.io/enforce-version': 'v1.35'}}}
            created.append(json.loads(kube(c, 'create', '-f', '-', '-o', 'json', data=json.dumps(obj))))
            for sa in ('reader', 'wrong'):
                obj = {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': sa, 'namespace': n,
                       'labels': {'pcloud.io/run': run_id}}, 'automountServiceAccountToken': False}
                created.append(json.loads(kube(c, 'create', '-f', '-', '-o', 'json', data=json.dumps(obj))))
        require(mount + '/' not in api.request('GET', 'sys/mounts')['data'], 'Test mount already exists')
        require(policy not in api.request('GET', 'sys/policies/acl')['data']['policies'], 'Test policy already exists')
        roles = api.request('LIST', 'auth/' + c['auth_mount'] + '/role', allowed=(200, 404))
        require(role not in roles.get('data', {}).get('keys', []), 'Test role already exists')
        api.request('POST', 'sys/mounts/' + mount, {'type': 'kv', 'options': {'version': '2'}, 'description': marker})
        mount_owned = True
        api.request('POST', mount + '/config', {'cas_required': True, 'max_versions': 3})
        policy_text = f'path "{mount}/data/allowed" {{ capabilities = ["read"] }}'
        api.request('PUT', 'sys/policies/acl/' + policy, {'policy': policy_text}); policy_owned = True
        role_body = {'bound_service_account_names': ['reader'], 'bound_service_account_namespaces': [ns],
                     'audience': 'pcloud-secrets', 'token_policies': [policy], 'token_no_default_policy': True,
                     'token_ttl': 900, 'token_max_ttl': 900}
        api.request('POST', 'auth/' + c['auth_mount'] + '/role/' + role, role_body); role_owned = True
        def jwt(n, sa='reader', audience='pcloud-secrets'):
            return kube(c, 'create', 'token', sa, '-n', n, '--audience=' + audience, '--duration=10m').strip()
        def login(n, sa='reader', audience='pcloud-secrets', denied=False):
            r = api.request('POST', 'auth/' + c['auth_mount'] + '/login', {'role': role, 'jwt': jwt(n, sa, audience)},
                            token='', allowed=(400, 403) if denied else (200,))
            if denied: return None
            token = r['auth']['client_token']; tokens.append(token)
            require(r['auth']['lease_duration'] <= 900, 'Client lease exceeds reviewed TTL')
            return token
        for n, sa, aud in [(ns, 'wrong', 'pcloud-secrets'), (wrong_ns, 'reader', 'pcloud-secrets'), (ns, 'reader', 'wrong-audience')]:
            login(n, sa, aud, denied=True)
        checks.append('wrong-service-account-namespace-and-audience-denied')
        token = login(ns)
        values = [uuid.uuid4().hex, uuid.uuid4().hex]
        for version, value in enumerate(values, 1):
            api.request('POST', mount + '/data/allowed', {'options': {'cas': version - 1}, 'data': {'value': value}})
            result = api.request('GET', mount + '/data/allowed', token=token)['data']
            require(result['data']['value'] == value and result['metadata']['version'] == version, 'KV rotation/read failed')
        api.request('POST', mount + '/data/allowed', {'options': {'cas': 1}, 'data': {'value': 'stale'}}, allowed=(400,))
        api.request('POST', mount + '/data/denied', {'options': {'cas': 0}, 'data': {'value': values[0]}})
        api.request('GET', mount + '/data/denied', token=token, allowed=(403,))
        api.request('POST', mount + '/data/allowed', {'options': {'cas': 2}, 'data': {'value': 'denied'}}, token=token, allowed=(403,))
        checks.extend(['kv-v2-read-and-cas-rotation', 'stale-cas-write-and-cross-path-denied'])
        # Administrative revocation does not need to grant clients token-management permissions.
        api.request('POST', 'auth/token/revoke', {'token': token})
        tokens.remove(token)
        api.request('GET', mount + '/data/allowed', token=token, allowed=(403,))
        checks.append('revoked-token-denied')
        if c['profile'] == 'openbao-raft-lab':
            raw = kube(c, 'exec', '-n', c['namespace'], 'openbao-0', '-c', 'openbao', '--', 'tail', '-c', str(MAX_JSON), AUDIT_PATH)
            entries = []
            for line in raw.splitlines():
                try: entry = json.loads(line)
                except json.JSONDecodeError: continue
                if entry.get('request', {}).get('path', '').startswith(mount + '/'):
                    entries.append(entry)
            require(entries and all(v not in json.dumps(entries) for v in values), 'Test audit evidence missing or raw secret exposed')
            require(any(e.get('request', {}).get('client_token', '').startswith('hmac-sha256:') for e in entries),
                    'Audit token HMAC missing')
            checks.append('persistent-audit-hmac-no-test-secret-disclosure')
        report['status'] = 'INCOMPLETE'
        report['remaining'] = ['operator-observed-sealed-restart-and-reunseal', 'isolated-snapshot-restore-and-known-secret-read']
        if c['profile'] == 'supplied-vault-api': report['remaining'].append('provider-audit-and-recovery-evidence')
    except Exception as exc:
        failed = exc
    finally:
        cleanup_errors = 0
        for token in tokens:
            try: api.request('POST', 'auth/token/revoke', {'token': token}, allowed=(200, 204))
            except Exception: cleanup_errors += 1
        for owned, path, match in [
            (role_owned, 'auth/' + c['auth_mount'] + '/role/' + role,
             lambda d: d.get('bound_service_account_names') == ['reader'] and d.get('bound_service_account_namespaces') == [ns]
                       and d.get('token_policies') == [policy]),
            (policy_owned, 'sys/policies/acl/' + policy, lambda d: d.get('policy') == policy_text)]:
            if owned:
                try:
                    require(match(api.request('GET', path)['data']), 'Test object changed; preserve it')
                    api.request('DELETE', path)
                except Exception: cleanup_errors += 1
        if mount_owned:
            try:
                require(api.request('GET', 'sys/mounts')['data'][mount + '/']['description'] == marker,
                        'Test mount changed; preserve it')
                api.request('DELETE', 'sys/mounts/' + mount)
            except Exception: cleanup_errors += 1
        for obj in reversed(created):
            try:
                if obj['kind'] == 'Namespace': namespace_empty(c, obj['metadata']['name'])
                delete_uid(c, obj)
            except Exception: cleanup_errors += 1
        report['cleanup'] = 'PASS' if cleanup_errors == 0 else 'FAIL-preserved-or-uncertain-test-resources'
        if failed and cleanup_errors == 0:
            report['cleanup'] = 'INCOMPLETE-failed-operation-inspect-run-owned-resources'
        if failed or cleanup_errors: report['status'] = 'FAIL'
        emit(report, args.output)
    if failed: raise CheckRejected('Secrets conformance failed; report contains only safe evidence') from None
    require(cleanup_errors == 0, 'Secrets cleanup incomplete; investigate run-owned resources')
    return report


def secure_destination(path):
    require(os.name == 'posix', 'Snapshot export requires POSIX private-file permissions; use a Linux operator station')
    path = Path(path).absolute()
    require(not path.exists() and not path.is_symlink(), 'Snapshot destination must be new')
    for parent in [path.parent, *path.parent.parents]:
        require(not parent.is_symlink(), 'Symlink snapshot directory rejected')
        require(not (parent / '.git').exists(), 'Snapshot must be outside every product/worktree')
    info = path.parent.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            'Snapshot directory must be operator-owned mode 0700')
    return path


def backup(c, args):
    require(getattr(args, 'allow_sensitive_export', False) and c['profile'] == 'openbao-raft-lab', 'Reviewed installed snapshot export required')
    authorize(c, args, mutation=False)
    path = secure_destination(args.snapshot)
    api = admin(c); health(api)
    size = 0; h = hashlib.sha256()
    try:
        with api.open('GET', 'sys/storage/raft/snapshot') as response:
            require(response.status == 200, 'Snapshot download rejected')
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'wb') as out:
                while True:
                    chunk = response.read(65536)
                    if not chunk: break
                    size += len(chunk); require(size <= MAX_SNAPSHOT, 'Snapshot exceeds bounded export size')
                    h.update(chunk); out.write(chunk)
                out.flush(); os.fsync(out.fileno())
        require(size > 0, 'Empty snapshot rejected')
    except Exception:
        # Never delete an existing destination from another operator/racing process.
        if 'fd' in locals(): path.unlink(missing_ok=True)
        raise
    return {'status': 'PASS', 'sha256': h.hexdigest(), 'bytes': size,
            'recovery': 'not-proven-by-export; use-isolated-restore-runbook'}


def restore(c, args):
    review = authorize(c, args, extra=('snapshot_sha256',))
    require(getattr(args, 'allow_store_restore', False) and c['profile'] == 'openbao-raft-lab' and c['purpose'] == 'recovery'
            and c['namespace'].startswith('pcloud-secrets-test-'), 'Restore requires isolated recovery target and explicit consent')
    require(os.name == 'posix', 'Restore requires Linux operator station private-file permissions')
    source = Path(args.snapshot).absolute()
    for parent in [source.parent, *source.parent.parents]:
        require(not parent.is_symlink() and not (parent / '.git').exists(), 'Snapshot must be outside repositories and symlink directories')
    require(not source.is_symlink(), 'Symlink snapshot rejected')
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600
                and 0 < info.st_size <= MAX_SNAPSHOT, 'Private bounded regular snapshot required')
        data = file.read(MAX_SNAPSHOT + 1)
    require(hashlib.sha256(data).hexdigest() == review['snapshot_sha256'], 'Snapshot digest differs from reviewed bytes')
    preflight(c)
    ns = get(c, 'namespace', c['namespace'])
    require(ns['metadata'].get('labels', {}).get('pcloud.io/purpose') == 'recovery', 'Recovery namespace marker required')
    api = admin(c); health(api)
    # Refuse every non-core engine/auth/policy on the destination; never overwrite an application store.
    require(set(api.request('GET', 'sys/mounts')['data']) == {'sys/', 'identity/', 'cubbyhole/'}, 'Restore target has non-core mounts')
    require(set(api.request('GET', 'sys/auth')['data']) == {'token/'}, 'Restore target has non-core authentication')
    require(set(api.request('GET', 'sys/policies/acl')['data']['policies']) == {'root', 'default'}, 'Restore target has non-core policies')
    require(api.request('GET', 'sys/audit')['data'] == {}, 'Restore target is not a fresh unaudited initialization')
    for path in ('identity/entity/id', 'identity/group/id', 'auth/token/roles'):
        listing = api.request('LIST', path, allowed=(200, 404))
        require(not listing.get('data', {}).get('keys'), 'Restore target contains identity or token role state')
    accessor = api.request('GET', 'auth/token/lookup-self')['data']['accessor']
    require(api.request('LIST', 'auth/token/accessors')['data']['keys'] == [accessor], 'Fresh target initialization token required')
    servers = api.request('GET', 'sys/storage/raft/configuration')['data']['config']['servers']
    require(len(servers) == 1 and servers[0].get('node_id') == 'openbao-0', 'Fresh single-node restore target required')
    with api.open('POST', 'sys/storage/raft/snapshot-force', data) as response:
        require(response.status in (200, 204), 'Snapshot restore submission rejected')
    return {'status': 'INCOMPLETE', 'snapshot_sha256': review['snapshot_sha256'],
            'remaining': ['original-snapshot-keyholders-unseal-target', 'known-synthetic-secret-read',
                          'audit-device-path-and-access-revalidation', 'record-source-and-target-revision-evidence']}


def uninstall_check(c):
    require(c['profile'] == 'openbao-raft-lab', 'Supplied service is not owned for uninstall')
    claims = get(c, 'pvc', namespace=c['namespace'])['items']
    require(not claims, 'Persistent claims present; preserve store and audit data')
    for pv in get(c, 'pv')['items']:
        require(pv.get('spec', {}).get('claimRef', {}).get('namespace') != c['namespace'], 'Retained PV present; preserve it')
    # Without data deletion or key custody proof, this command never authorizes deleting an initialized store.
    return {'status': 'INCOMPLETE', 'read_only': True, 'remaining': ['operator-data-disposition-and-key-custody-review']}


def emit(report, output=None):
    raw = json.dumps(report, indent=2) + '\n'
    if output:
        p = Path(output).absolute()
        for parent in [p.parent, *p.parent.parents]:
            require(not (parent / '.git').exists(), 'Reports must be outside repositories')
        p.write_text(raw, encoding='utf-8')
    print(raw, end='')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['render', 'capability', 'digest', 'preflight', 'live', 'bootstrap', 'smoke', 'backup', 'restore', 'uninstall-check'])
    p.add_argument('--site', required=True); p.add_argument('--review'); p.add_argument('--output')
    p.add_argument('--allow-cluster-changes', action='store_true')
    p.add_argument('--allow-sensitive-export', action='store_true'); p.add_argument('--snapshot')
    p.add_argument('--allow-store-restore', action='store_true')
    args = p.parse_args()
    try:
        c = validate(read_json(args.site))
        if args.command == 'render': print(manifest(c), end=''); return 0
        if args.command == 'digest': print(digest(c)); return 0
        if args.command == 'capability': report = capability(c)
        elif args.command in ('bootstrap', 'smoke', 'backup', 'restore'):
            require(args.review is not None, 'Reviewed revision and artifact digest required')
            report = globals()[args.command](c, args)
        else: report = globals()[args.command.replace('-', '_')](c)
        if args.command != 'smoke': emit(report, args.output)
        return 3 if report.get('status') == 'INCOMPLETE' else 0
    except CheckRejected as exc:
        print('FAIL: ' + str(exc), file=sys.stderr); return 1
    except Exception:
        print('FAIL: operation rejected; diagnostic bodies and credentials suppressed', file=sys.stderr); return 1


if __name__ == '__main__': sys.exit(main())
