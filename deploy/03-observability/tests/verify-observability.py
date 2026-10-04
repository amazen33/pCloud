#!/usr/bin/env python3
"""Offline package checks; --render also uses real Kustomize and strict schemas."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observability', ROOT / 'observability.py')
obs = importlib.util.module_from_spec(spec); spec.loader.exec_module(obs)


def fixture():
    return tuple(obs.read_json(ROOT / name) for name in ('site.example.json', 'backend-capability.example.json', 'backend-fragments.example.json'))


def synthetic_fixture():
    c, b, f = fixture(); c['purpose'] = 'conformance'; c['namespace'] = b['namespace'] = 'pcloud-observe-test-example'
    return c, b, f


def cluster_fixture(c, b, f):
    objects = {(x['kind'].lower(), x['metadata']['name']): copy.deepcopy(x) for x in obs.render(c, b, f)}
    objects['namespace', c['namespace']] = {'metadata': {'labels': {
        'pcloud.io/backend-owner': 'observability-filesystem', **{k: v for mode in ('enforce', 'audit', 'warn')
        for k, v in [('pod-security.kubernetes.io/' + mode, 'restricted'), ('pod-security.kubernetes.io/' + mode + '-version', 'v1.35')]}}}}
    objects['storageclass', c['storage_class']] = {'reclaimPolicy': 'Retain', 'volumeBindingMode': 'WaitForFirstConsumer'}
    for x, v in b['volumes'].items():
        objects['pvc', v['claim']] = {'metadata': {'uid': v.get('uid', x + '-uid'), 'labels': {'pcloud.io/package': 'observability-filesystem', 'pcloud.io/backend-component': x}},
            'spec': {'storageClassName': c['storage_class'], 'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem'}}
    for worker in {v['worker'] for v in b['volumes'].values()} | {c['grafana_worker'], c['gateway_worker']}:
        objects['node', worker] = {'metadata': {'labels': {}}, 'spec': {}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
    for (kind, name), obj in objects.items():
        if kind in ('statefulset', 'deployment'): obj['status'] = {'readyReplicas': 1}
    return objects


class API:
    def __init__(self, c):
        self.run = None; self.trace = None; self.start = None; self.writes = []; self.denied = []

    def request(self, path, role=None, payload=None):
        if path.startswith('/ingest/'):
            if role != 'ingest': self.denied.append(role); return 401, b'{}'
            self.writes.append(path)
            if path.endswith('/traces'):
                span = payload['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
                self.trace = span['traceId']; self.start = int(span['startTimeUnixNano'])
                self.run = span['attributes'][0]['value']['stringValue']
            return 200, b'{}'
        if role != 'query': return 401, b'{}'
        if '/api/traces/' in path: return 200, json.dumps({'resourceSpans': [{'traceId': self.trace}]}).encode()
        if 'query_range?' in path: body = {'data': {'result': [{'values': [[str(self.start), 'pcloud probe ' + self.run, {'trace_id': self.trace}]]}]}}
        elif '/alerts' in path: body = {'data': {'alerts': [{'labels': {'alertname': 'PCloudLabSyntheticSignal'}, 'state': 'firing'}]}}
        elif 'traces_service_graph' in path: body = {'data': {'result': [] if self.start is None else [{'value': [self.start / 1e9, '1']}]}}
        elif 'timestamp' in path: body = {'data': {'result': [{'value': [self.start / 1e9, str(self.start / 1e9)]}]}}
        else: body = {'data': {'result': [{'metric': {'__name__': 'pcloud_lab_probe', 'source': 'pcloud-lab', 'job': 'pcloud-lab-client'}, 'value': [self.start / 1e9, '7']}]}}
        return 200, json.dumps(body).encode()


class Checks(unittest.TestCase):
    def test_example_schema(self): obs.validate(*fixture())

    def test_reject_unimplemented_profile(self):
        c, b, f = fixture(); c['profile'] = 'production'
        with self.assertRaises(ValueError): obs.validate(c, b, f)

    def test_reject_http_credentials_and_hostname_mismatch(self):
        for endpoint in ('http://observe.example.test', 'https://user:secret@observe.example.test', 'https://other.example.test', 'https://observe.example.test/?token=secret'):
            c, b, f = fixture(); c['endpoint'] = endpoint
            with self.assertRaises(ValueError): obs.validate(c, b, f)

    def test_reject_receiver_credentials(self):
        c, b, f = fixture(); c['alert_receiver'] = 'https://alerts.example.test/?token=secret'
        with self.assertRaises(ValueError): obs.validate(c, b, f)

    def test_reject_unknown_secret_data(self):
        c, b, f = fixture(); c['admin_password'] = 'secret'
        with self.assertRaises(ValueError): obs.validate(c, b, f)

    def test_reject_missing_retention_and_wide_cidrs(self):
        for action in ('retention', 'cidr', 'resources'):
            c, b, f = fixture()
            if action == 'retention': del c['retention_hours']['logs']
            if action == 'cidr': c['client_cidrs'] = ['0.0.0.0/0']
            if action == 'resources': c['resources']['loki']['limits']['memory'] = '1Mi'
            with self.assertRaises(ValueError): obs.validate(c, b, f)

    def test_backend_handover_hash_namespace_and_component(self):
        for action in ('hash', 'namespace', 'version', 'fragment', 'claim'):
            c, b, f = fixture()
            if action == 'hash': b['fragments_sha256'] = '0' * 64
            if action == 'namespace': b['namespace'] = 'foreign'
            if action == 'version': b['components']['tempo'] = '3.0.0'
            if action == 'fragment': f['tempo']['storage']['trace']['backend'] = 's3'; b['fragments_sha256'] = obs.sha(f)
            if action == 'claim': c['grafana_claim'] = b['volumes']['loki']['claim']
            with self.assertRaises(ValueError): obs.validate(c, b, f)

    def test_claim_names_cannot_inject_config(self):
        c, b, f = fixture(); b['volumes']['tempo']['claim'] = '../foreign'
        with self.assertRaises(ValueError): obs.validate(c, b, f)

    def test_conformance_scope(self):
        obs.validate(*synthetic_fixture())
        c, b, f = fixture(); c['purpose'] = 'conformance'
        with self.assertRaises(ValueError): obs.validate(c, b, f)

    def test_all_images_immutable_and_commands_from_recorded_images(self):
        lock = obs.read_json(ROOT / 'images.lock.json')
        for x, source in obs.SOURCES.items():
            record = lock[source]
            self.assertRegex(record['image'], r'@sha256:[0-9a-f]{64}$')
            self.assertTrue(record['image'].endswith(record['manifest_sha256']))
            self.assertRegex(record['linux_amd64']['config_sha256'], r'^sha256:[0-9a-f]{64}$')
        objects = obs.render(*fixture())
        for obj in objects:
            if obj['kind'] == 'StatefulSet' and obj['metadata']['name'] in ('loki', 'tempo', 'mimir', 'collector'):
                c = obj['spec']['template']['spec']['containers'][0]
                self.assertEqual(c['command'][0], lock[obs.SOURCES[c['name']]]['linux_amd64']['entrypoint'][0])

    def test_vendor_sources_untouched(self):
        for record in obs.read_json(ROOT / 'UPSTREAM.json')['files']:
            self.assertTrue(record['source'].startswith('https://raw.githubusercontent.com/'))
            self.assertEqual(hashlib.sha256((ROOT / record['path']).read_bytes()).hexdigest(), record['sha256'])

    def test_render_no_namespace_secret_or_backend_claim_creation(self):
        c, b, f = fixture(); objects = obs.render(c, b, f)
        self.assertEqual(len(objects), 28)
        self.assertFalse(any(o['kind'] in ('Namespace', 'Secret', 'ClusterRole', 'DaemonSet') for o in objects))
        self.assertEqual([o['metadata']['name'] for o in objects if o['kind'] == 'PersistentVolumeClaim'], [c['grafana_claim']])

    def test_workloads_restricted_one_replica_bounded(self):
        for o in obs.render(*fixture()):
            if o['kind'] not in ('Deployment', 'StatefulSet'): continue
            self.assertEqual(o['spec']['replicas'], 1)
            spec = o['spec']['template']['spec']; self.assertFalse(spec['automountServiceAccountToken'])
            self.assertEqual(spec['securityContext']['runAsUser'], 10001)
            self.assertEqual(spec['securityContext']['seccompProfile']['type'], 'RuntimeDefault')
            self.assertNotIn('nodeName', spec)
            self.assertIn('nodeAffinity', spec['affinity'])
            container = spec['containers'][0]
            self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
            self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
            self.assertIn('resources', container); self.assertIn('startupProbe', container)

    def test_persistent_storage_and_ondelete_updates(self):
        c, b, f = fixture()
        for o in obs.render(c, b, f):
            if o['kind'] != 'StatefulSet': continue
            self.assertEqual(o['spec']['updateStrategy']['type'], 'OnDelete')
            if o['metadata']['name'] == 'collector': continue
            spec = o['spec']['template']['spec']
            self.assertEqual(len([v for v in spec['volumes'] if 'persistentVolumeClaim' in v]), 1)

    def test_storage_fragments_preserved_and_retention_enabled(self):
        c, b, f = fixture(); cfg = obs.configs(c, b, f)
        for x in ('loki', 'tempo', 'mimir'):
            expected = copy.deepcopy(f[x])
            if x == 'mimir': del expected['ruler_storage']
            self.assertTrue(obs.matches(expected, cfg[x]))
        self.assertEqual(cfg['mimir']['ruler_storage'], {'backend': 'local', 'local': {'directory': '/etc/mimir-rules'}})
        self.assertTrue(cfg['loki']['compactor']['retention_enabled'])
        self.assertEqual(cfg['loki']['limits_config']['retention_period'], '24h')
        self.assertEqual(cfg['tempo']['compactor']['compaction']['block_retention'], '24h')
        self.assertEqual(cfg['mimir']['limits']['compactor_blocks_retention_period'], '24h')

    def test_collector_bounded_queues_no_disk_wal_no_debug(self):
        cfg = obs.configs(*fixture())['collector']
        self.assertEqual(cfg['service']['pipelines']['traces']['processors'], ['memory_limiter', 'batch'])
        self.assertNotIn('debug', cfg['exporters'])
        for value in cfg['exporters'].values():
            self.assertEqual(value['retry_on_failure']['max_elapsed_time'], '30s')
            self.assertEqual(value.get('sending_queue', value.get('remote_write_queue'))['queue_size'], 256)
            self.assertNotIn('wal', value)
        self.assertFalse(cfg['exporters']['prometheusremotewrite']['target_info']['enabled'])
        self.assertTrue(cfg['exporters']['prometheusremotewrite']['disable_scope_info'])
        self.assertEqual(cfg['exporters']['prometheusremotewrite']['resource_constant_labels']['included'], ['service.name', 'service.namespace'])

    def test_tls_role_routes_and_backend_header_stripping(self):
        text = obs.gateway_config(fixture()[0])
        self.assertIn('ssl_protocols TLSv1.2 TLSv1.3', text)
        for role in ('ingest', 'query', 'admin'): self.assertIn('/etc/access/' + role + '.htpasswd', text)
        self.assertIn('proxy_set_header Authorization ""', text)
        self.assertIn('proxy_set_header X-Scope-OrgID ""', text)
        self.assertNotIn('/api/v1/push', text); self.assertNotIn('/config', text)
        self.assertIn('access_log off', text)
        self.assertIn('location / { return 404; }', text)
        self.assertIn('location ~ "^/query/tempo/api/traces/([0-9a-f]{32})$"', text)
        self.assertIn('upstream tempo_query { server tempo:3200; }', text)

    def test_network_policies_private_services(self):
        objects = obs.render(*fixture())
        self.assertEqual(len([o for o in objects if o['kind'] == 'NetworkPolicy']), 6)
        for o in objects:
            if o['kind'] == 'NetworkPolicy': self.assertEqual(o['spec']['policyTypes'], ['Ingress', 'Egress'])
            if o['kind'] == 'Service': self.assertEqual(o['spec']['type'], 'ClusterIP')

    def test_tempo_service_graph_and_grafana_provisioning(self):
        cfg = obs.configs(*fixture())
        self.assertEqual(cfg['tempo']['overrides']['defaults']['metrics_generator']['processors'], ['service-graphs', 'span-metrics'])
        self.assertEqual(cfg['datasources']['datasources'][2]['jsonData']['serviceMap']['datasourceUid'], 'mimir')
        self.assertEqual(len(obs.dashboard()['panels']), 3)
        grafana = next(o for o in obs.render(*fixture()) if o['kind'] == 'StatefulSet' and o['metadata']['name'] == 'grafana')
        self.assertEqual(grafana['spec']['template']['spec']['containers'][0]['command'], ['/run.sh'])
        self.assertIn('VAR_NAME_FILE', (ROOT/'upstream/grafana-run.sh').read_text())
        self.assertEqual(cfg['rules']['groups'][0]['rules'][1]['alert'], 'PCloudLabSyntheticSignal')
        self.assertIn('credentials_file', cfg['alertmanager']['receivers'][0]['webhook_configs'][0]['http_config']['authorization'])
        mimir = next(o for o in obs.render(*fixture()) if o['kind'] == 'StatefulSet' and o['metadata']['name'] == 'mimir')
        self.assertIn('-target=all,alertmanager', mimir['spec']['template']['spec']['containers'][0]['command'])

    def test_capability_limits_and_endpoints(self):
        cap = obs.capability(*fixture())
        jsonschema.Draft202012Validator(obs.read_json(ROOT/'capability.schema.json')).validate(cap)
        self.assertEqual(cap['api'], 'pcloud.observability/v1')
        self.assertFalse(cap['replication']); self.assertFalse(cap['backup'])
        self.assertFalse(cap['auth']['tenant_isolation']); self.assertTrue(cap['collector']['loss_on_restart_or_overflow'])

    def test_digest_changes_with_reviewed_inputs(self):
        c, b, f = fixture(); original = obs.digest(c, b, f)
        c['retention_hours']['logs'] = 48; self.assertNotEqual(original, obs.digest(c, b, f))
        c, b, f = fixture(); b['volumes']['loki']['worker'] = 'another-worker'
        self.assertNotEqual(original, obs.digest(c, b, f))

    def test_telemetry_fixed_cardinality_and_correlated_ids(self):
        data = obs.telemetry('a'*12, 'b'*32, 1_000_000_000)
        self.assertNotIn('a'*12, json.dumps(data['metrics'])); self.assertNotIn('b'*32, json.dumps(data['metrics']))
        spans = data['traces']['resourceSpans']; self.assertEqual(spans[1]['scopeSpans'][0]['spans'][0]['parentSpanId'], '1111111111111111')
        self.assertEqual(data['logs']['resourceLogs'][0]['scopeLogs'][0]['logRecords'][0]['traceId'], 'b'*32)

    def test_smoke_consent_and_scope_before_any_access(self):
        with mock.patch.object(obs, 'live') as live, mock.patch.object(obs, 'reviewed') as review:
            for consent in (False, True):
                with self.assertRaises(ValueError): obs.smoke(*fixture(), {}, consent)
            live.assert_not_called(); review.assert_not_called()

    def test_review_hash_and_revision(self):
        c, b, f = synthetic_fixture()
        review = {'revision': 'a'*40, 'artifact_sha256': obs.digest(c, b, f), 'namespace': c['namespace'], 'purpose': 'conformance'}
        with mock.patch.object(obs.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='a'*40+'\n')): obs.reviewed(c, b, f, review)
        review['artifact_sha256'] = '0'*64
        with self.assertRaises(ValueError): obs.reviewed(c, b, f, review)

    def test_simulated_signal_graph_alert_complete_still_incomplete(self):
        c, b, f = synthetic_fixture(); api = API(c)
        with mock.patch.object(obs, 'live'), mock.patch.object(obs, 'reviewed'), mock.patch.object(obs, 'Client', return_value=api):
            report = obs.smoke(c, b, f, {}, True)
        self.assertEqual(report['status'], 'INCOMPLETE'); self.assertTrue(all(report['checks'].values()))
        self.assertEqual(len(api.writes), 3); self.assertEqual(len(api.denied), 9)

    def test_partial_success_is_failure(self):
        c, b, f = synthetic_fixture(); api = API(c); original = api.request
        def partial(path, role=None, payload=None):
            code, raw = original(path, role, payload)
            return (code, b'{"partialSuccess":{"rejectedSpans":1}}') if role == 'ingest' else (code, raw)
        api.request = partial
        with mock.patch.object(obs, 'live'), mock.patch.object(obs, 'reviewed'), mock.patch.object(obs, 'Client', return_value=api):
            with self.assertRaises(ValueError): obs.smoke(c, b, f, {}, True)

    def test_denial_fails_open(self):
        with self.assertRaises(ValueError): obs.denial(mock.Mock(request=mock.Mock(return_value=(200, b'secret'))), '/query/loki/ready')

    def test_report_path_and_safe_error(self):
        with self.assertRaises(ValueError): obs.report_path(ROOT / 'out/report.json')
        proc = subprocess.run([sys.executable, str(ROOT/'observability.py'), 'render', '--site', 'secret-file-not-found',
            '--backend', 'secret-backend-not-found', '--fragments', 'secret-fragments-not-found'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1); self.assertNotIn('secret-file-not-found', proc.stdout)

    def test_no_redirect_and_no_proxy(self):
        self.assertIsNone(obs.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))
        with mock.patch.object(obs.ssl, 'create_default_context', return_value=mock.Mock()), mock.patch.object(obs.urllib.request, 'build_opener') as opener:
            obs.Client(fixture()[0])
            self.assertEqual(opener.call_args.args[0].proxies, {})

    def test_resource_comparison_rejects_drift(self):
        self.assertTrue(obs.matches({'spec': {'replicas': 1}}, {'spec': {'replicas': 1, 'default': True}}))
        self.assertFalse(obs.matches({'data': {'retention': '24h'}}, {'data': {'retention': '0h'}}))

    def test_preflight_metadata_reads_only_and_incomplete(self):
        c, b, f = fixture(); objects = cluster_fixture(c, b, f)
        with mock.patch.object(obs, 'get', side_effect=lambda c, k, n: objects[k, n]), \
             mock.patch.object(obs, 'kube', return_value={'serverVersion': {'gitVersion': 'v1.35.0'}}), \
             mock.patch.object(obs.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='secret-uid')) as calls:
            self.assertEqual(obs.preflight(c, b)['status'], 'INCOMPLETE')
        self.assertEqual(len(calls.call_args_list), 4)
        for call in calls.call_args_list:
            self.assertIn('get', call.args[0]); self.assertIn('jsonpath={.metadata.uid}', call.args[0])
            self.assertNotIn('json', call.args[0])

    def test_preflight_rejects_foreign_claim_and_control_plane(self):
        for kind in ('claim', 'worker'):
            c, b, f = fixture(); objects = cluster_fixture(c, b, f)
            if kind == 'claim': objects['pvc', b['volumes']['loki']['claim']]['metadata']['labels']['pcloud.io/package'] = 'foreign'
            else: objects['node', c['grafana_worker']]['metadata']['labels']['node-role.kubernetes.io/control-plane'] = ''
            with mock.patch.object(obs, 'get', side_effect=lambda c, k, n: objects[k, n]), \
                 mock.patch.object(obs, 'kube', return_value={'serverVersion': {'gitVersion': 'v1.35.0'}}), \
                 mock.patch.object(obs.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='secret-uid')):
                with self.assertRaises(ValueError): obs.preflight(c, b)

    def test_live_drift_and_shared_credentials_rejected(self):
        c, b, f = fixture(); objects = cluster_fixture(c, b, f)
        env = {'PCLOUD_OBSERVE_' + x.upper(): x + ':fixture-password' for x in ('ingest', 'query', 'admin')}
        objects['configmap', 'loki-config']['data']['loki.yaml'] = 'retention disabled'
        with mock.patch.object(obs, 'preflight'), mock.patch.dict(os.environ, env), \
             mock.patch.object(obs, 'get', side_effect=lambda c, k, n: objects[k, n]):
            with self.assertRaises(ValueError): obs.live(c, b, f)

    def test_live_successful_read_subchecks_stay_incomplete(self):
        c, b, f = fixture(); objects = cluster_fixture(c, b, f); requests = []
        env = {'PCLOUD_OBSERVE_' + x.upper(): x + ':fixture-password' for x in ('ingest', 'query', 'admin')}
        def response(path, role=None, payload=None):
            requests.append((path, role, payload))
            allowed = role == ('admin' if path.startswith('/grafana') else 'query')
            return (200, b'{"status":"success"}') if allowed else (401, b'{}')
        with mock.patch.object(obs, 'preflight'), mock.patch.dict(os.environ, env), \
             mock.patch.object(obs, 'get', side_effect=lambda c, k, n: objects[k, n]), \
             mock.patch.object(obs, 'Client', return_value=mock.Mock(request=mock.Mock(side_effect=response))):
            self.assertEqual(obs.live(c, b, f)['status'], 'INCOMPLETE')
        self.assertTrue(requests); self.assertTrue(all(payload is None for _, _, payload in requests))
        with mock.patch.object(obs, 'preflight'), mock.patch.dict(os.environ, {k: 'shared:password' for k in env}):
            with self.assertRaises(ValueError): obs.live(c, b, f)

    def test_old_metric_and_graph_cannot_satisfy_new_run(self):
        c, b, f = synthetic_fixture(); api = API(c); original = api.request
        def stale(path, role=None, payload=None):
            result = original(path, role, payload)
            if 'timestamp' in path: return 200, b'{"data":{"result":[{"value":[0,"0"]}]}}'
            return result
        api.request = stale
        with mock.patch.object(obs, 'live'), mock.patch.object(obs, 'reviewed'), mock.patch.object(obs, 'Client', return_value=api), \
             mock.patch.object(obs.time, 'monotonic', side_effect=[0, 0, 1000]), mock.patch.object(obs.time, 'sleep'):
            with self.assertRaises(ValueError): obs.smoke(c, b, f, {}, True)

    def test_copied_package_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'package'; shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns('__pycache__', 'site.json', 'review.json', 'out'))
            proc = subprocess.run([sys.executable, str(target/'observability.py'), 'render', '--site', str(target/'site.example.json'),
                '--backend', str(target/'backend-capability.example.json'), '--fragments', str(target/'backend-fragments.example.json')],
                cwd=folder, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(len(list(yaml.safe_load_all(proc.stdout))), 28)


def render_check():
    def command(argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        if result.returncode: raise RuntimeError('Validation tool failed: ' + result.stdout[:500] + result.stderr[:500])
        return result.stdout
    version = json.loads(command(['kubectl', 'version', '--client', '-o', 'json']))['clientVersion']['gitVersion']
    if version != 'v1.35.0': raise RuntimeError('kubectl 1.35.0 required')
    if command(['kubeconform', '-v']).strip().lstrip('v') != '0.7.0': raise RuntimeError('kubeconform 0.7.0 required')
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); (root/'resources.yaml').write_text(yaml.safe_dump_all(obs.render(*fixture()), sort_keys=False), encoding='utf-8')
        (root/'kustomization.yaml').write_text('resources:\n  - resources.yaml\n', encoding='utf-8')
        rendered = command(['kubectl', 'kustomize', str(root)])
        (root/'rendered.yaml').write_text(rendered, encoding='utf-8')
        result = command(['kubeconform', '-strict', '-summary', '-kubernetes-version', '1.35.0', str(root/'rendered.yaml')])
        if len(list(yaml.safe_load_all(rendered))) != 28: raise RuntimeError('Render count changed')
        print(result.strip())
    print('PASS: real Kustomize and strict Kubernetes 1.35.0 schemas for all 28 runtime resources')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--render', action='store_true'); args = p.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    if not result.wasSuccessful(): sys.exit(1)
    if args.render: render_check()
