#!/usr/bin/env python3
"""Package entry: offline safety/TLS tests; --render additionally checks real Kubernetes schemas."""
import argparse
import contextlib
import copy
import hashlib
import http.server
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bao', ROOT / 'bao.py')
bao = importlib.util.module_from_spec(spec); spec.loader.exec_module(bao)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_service import Service


def fixture(): return bao.read_json(ROOT / 'site.example.json')


def render_check(c):
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder); (p / 'resources.yaml').write_text(bao.manifest(c), encoding='utf-8')
        (p / 'kustomization.yaml').write_text('resources:\n- resources.yaml\n', encoding='utf-8')
        rendered = bao.run(['kubectl', 'kustomize', str(p)])
        bao.require(list(yaml.safe_load_all(rendered)), 'Empty render rejected')
        bao.run(['kubeconform', '-strict', '-summary', '-kubernetes-version', '1.35.0', '-'], rendered)


def versions():
    kubectl = json.loads(bao.run(['kubectl', 'version', '--client', '-o', 'json']))
    bao.require(kubectl['clientVersion']['gitVersion'] == 'v1.35.0', 'Pinned kubectl required')
    bao.require(bao.run(['kubeconform', '-v']).strip() == 'v0.7.0', 'Pinned kubeconform required')


class Checks(unittest.TestCase):
    def test_examples_and_schemas(self):
        for file in ('site.example.json', 'supplied.example.json'):
            c = bao.validate(bao.read_json(ROOT / file))
            jsonschema.validate(bao.capability(c), bao.read_json(ROOT / 'capability.schema.json'))
        for file in ('site.schema.json', 'capability.schema.json'):
            jsonschema.Draft202012Validator.check_schema(bao.read_json(ROOT / file))

    def test_no_extra_credentials_or_unsafe_origins(self):
        for change in [{'root_token': 'secret'}, {'endpoint': 'http://example.invalid'},
                       {'endpoint': 'https://user:pass@example.invalid'}, {'endpoint': 'https://example.invalid/path'},
                       {'endpoint': 'https://example.invalid/?token=secret'}, {'endpoint': 'https://example.invalid/#frag'}]:
            with self.subTest(change=change), self.assertRaises(ValueError): bao.validate({**fixture(), **change})

    def test_profiles_do_not_mix(self):
        c = bao.read_json(ROOT / 'supplied.example.json')
        self.assertEqual(bao.render(c), [])
        for key in ('workers', 'tls_secret', 'resources', 'storage_class', 'data_size'):
            with self.assertRaises(ValueError): bao.validate({**c, key: fixture()[key]})

    def test_resources_and_cidr_guards(self):
        for field, value in [('operator_cidrs', ['0.0.0.0/0']), ('api_cidrs', ['::/0']),
                             ('operator_cidrs', ['192.0.2.1/24']), ('workers', []), ('purpose', 'production')]:
            with self.assertRaises(ValueError): bao.validate({**fixture(), field: value})
        c = fixture(); c['resources']['limits']['memory'] = '128Mi'
        with self.assertRaises(ValueError): bao.validate(c)

    def test_recovery_namespace_guard(self):
        with self.assertRaises(ValueError): bao.validate({**fixture(), 'purpose': 'recovery'})
        bao.validate({**fixture(), 'purpose': 'recovery', 'namespace': 'pcloud-secrets-test-restore'})

    def test_source_and_image_locks(self):
        for item in bao.read_json(ROOT / 'UPSTREAM.json')['files']:
            self.assertEqual(hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest(), item['sha256'])
            self.assertIn('/v2.7.1/', item['source'])
        lock = bao.read_json(ROOT / 'images.lock.json')['docker.io/openbao/openbao:2.7.1']
        self.assertRegex(lock['image'], r'^docker.io/openbao/openbao@sha256:[0-9a-f]{64}$')
        self.assertTrue(lock['image'].endswith(lock['manifest_sha256']))

    def test_restricted_pod_and_explicit_non_dev_command(self):
        objects = bao.render(fixture()); pod = next(o for o in objects if o['kind'] == 'StatefulSet')['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertEqual(pod['securityContext']['runAsUser'], 1000)
        self.assertTrue(pod['securityContext']['runAsNonRoot'])
        container = pod['containers'][0]
        self.assertEqual(container['command'], ['/usr/bin/bao'])
        self.assertNotIn('-dev', container['args'])
        self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        self.assertNotIn('initContainers', pod)
        self.assertNotIn('hostPath', json.dumps(pod))
        reviewer = next(v for v in pod['volumes'] if v['name'] == 'reviewer')
        self.assertEqual(reviewer['projected']['sources'][0]['serviceAccountToken']['expirationSeconds'], 3600)
        self.assertEqual(container['livenessProbe']['tcpSocket']['port'], 'https')

    def test_storage_retention_network_and_tokenreview_only(self):
        objects = bao.render(fixture()); sts = next(o for o in objects if o['kind'] == 'StatefulSet')['spec']
        for obj in objects:
            for value in obj['metadata']['labels'].values():
                self.assertRegex(value, r'^[a-zA-Z0-9]([a-zA-Z0-9_.-]{0,61}[a-zA-Z0-9])?$')
        self.assertEqual(sts['replicas'], 1)
        self.assertEqual(set(sts['persistentVolumeClaimRetentionPolicy'].values()), {'Retain'})
        self.assertEqual([v['metadata']['name'] for v in sts['volumeClaimTemplates']], ['data', 'audit'])
        role = next(o for o in objects if o['kind'] == 'ClusterRole')
        self.assertEqual(role['rules'], [{'apiGroups': ['authentication.k8s.io'], 'resources': ['tokenreviews'], 'verbs': ['create']}])
        policy = next(o for o in objects if o['kind'] == 'NetworkPolicy')['spec']
        self.assertEqual(policy['policyTypes'], ['Ingress', 'Egress'])
        self.assertNotIn('0.0.0.0/0', json.dumps(policy))
        conf = json.loads(next(o for o in objects if o['kind'] == 'ConfigMap')['data']['server.json'])
        self.assertTrue(conf['disable_mlock']); self.assertEqual(conf['listener']['tcp']['tls_min_version'], 'tls12')

    def test_digest_binds_context_endpoint_and_ca(self):
        c = fixture(); first = bao.digest(c)
        self.assertNotEqual(first, bao.digest({**c, 'context': 'different'}))
        self.assertNotEqual(first, bao.digest({**c, 'endpoint': 'https://different.invalid'}))
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'ca'; p.write_text('one'); c['ca_file'] = str(p); d = bao.digest(c)
            p.write_text('two'); self.assertNotEqual(d, bao.digest(c))

    def test_mutation_requires_consent_and_digest_before_access(self):
        args = argparse.Namespace(allow_cluster_changes=False, review='missing', output=None)
        for fn in (bao.bootstrap, bao.smoke, bao.backup, bao.restore):
            with mock.patch.object(bao, 'admin', side_effect=AssertionError('No service access')):
                with self.assertRaises(ValueError): fn(fixture(), args)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'review.json'; p.write_text(json.dumps({'revision': 'a'*40, 'artifact_digest': 'b'*64}))
            args.allow_cluster_changes = True; args.review = str(p)
            with self.assertRaises(ValueError): bao.authorize(fixture(), args)

    def test_safe_errors_do_not_expose_credentials(self):
        with mock.patch('sys.argv', ['bao.py', 'live', '--site', str(ROOT / 'site.example.json')]), \
             mock.patch.object(bao, 'live', side_effect=RuntimeError('sensitive-private-token')), \
             contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(bao.main(), 1)
        self.assertNotIn('sensitive-private-token', output.getvalue())
        with mock.patch('subprocess.run', return_value=argparse.Namespace(returncode=1, stdout='secret', stderr='private-key')):
            with self.assertRaisesRegex(ValueError, 'suppressed'): bao.run(['example'])

    def test_readonly_preflight_rejects_storage_before_secret_read(self):
        c = fixture()
        with mock.patch.object(bao, 'kube', return_value=json.dumps({'serverVersion': {'gitVersion': 'v1.35.7'}})) as command, \
             mock.patch.object(bao, 'get', return_value={'provisioner': c['storage_provisioner'], 'reclaimPolicy': 'Delete'}):
            with self.assertRaises(ValueError): bao.preflight(c)
            self.assertEqual(command.call_count, 1)

    def test_namespace_audit_preserves_unknown_crd(self):
        with mock.patch.object(bao, 'kube', side_effect=['widgets.example.io\n', json.dumps({'items': [
             {'kind': 'Widget', 'metadata': {'name': 'foreign'}}]})]):
            with self.assertRaises(ValueError): bao.namespace_empty(fixture(), 'test')

    def test_uid_precondition_and_replacement_guard(self):
        obj = {'kind': 'ServiceAccount', 'metadata': {'name': 'reader', 'namespace': 'test', 'uid': 'owned'}}
        with mock.patch.object(bao, 'kube', side_effect=['{}', json.dumps({'metadata': {'uid': 'replacement'}})]) as command:
            with self.assertRaises(ValueError): bao.delete_uid(fixture(), obj)
            self.assertEqual(json.loads(command.call_args_list[0].kwargs['data'])['preconditions']['uid'], 'owned')

    def test_subset_allows_api_defaults_but_rejects_changed_security(self):
        desired = {'spec': {'replicas': 1, 'template': {'securityContext': {'runAsNonRoot': True}}}}
        actual = copy.deepcopy(desired); actual['spec']['revisionHistoryLimit'] = 10
        self.assertTrue(bao.contains(actual, desired))
        actual['spec']['template']['securityContext']['runAsNonRoot'] = False
        self.assertFalse(bao.contains(actual, desired))

    def test_bootstrap_does_not_overwrite_foreign_mount(self):
        c = fixture(); api = mock.Mock()
        api.request.side_effect = [{'data': {'pcloud-file/': {'type': 'file', 'options': {
            'file_path': bao.AUDIT_PATH, 'log_raw': 'false', 'format': 'json'}}}},
            {'data': {c['kv_mount'] + '/': {'type': 'kv', 'description': 'foreign', 'options': {'version': '2'}}}}]
        with mock.patch.object(bao, 'authorize'), mock.patch.object(bao, 'preflight'), mock.patch.object(bao, 'health'), mock.patch.object(bao, 'admin', return_value=api):
            with self.assertRaises(ValueError): bao.bootstrap(c, argparse.Namespace())
        self.assertTrue(all(call.args[0] == 'GET' for call in api.request.call_args_list))

    def simulated_smoke(self, failure=None):
        c = fixture(); fake = Service(bao, c)
        if failure == 'read': fake.bad_read = True
        if failure == 'cleanup': fake.bad_cleanup = True
        args = argparse.Namespace(output=None)
        with mock.patch.object(bao, 'authorize', return_value={'revision': 'a'*40, 'artifact_digest': bao.digest(c)}), \
             mock.patch.object(bao, 'live'), mock.patch.object(bao, 'admin', return_value=fake), \
             mock.patch.object(bao, 'kube', side_effect=fake.kube), contextlib.redirect_stdout(io.StringIO()) as output:
            if failure:
                with self.assertRaises(ValueError): bao.smoke(c, args)
                self.assertEqual(json.loads(output.getvalue())['status'], 'FAIL')
            else:
                report = bao.smoke(c, args)
                self.assertEqual(report['status'], 'INCOMPLETE'); self.assertEqual(report['cleanup'], 'PASS')
                self.assertIn('revoked-token-denied', report['checks'])
        return fake

    def test_simulated_smoke_conformance_and_cleanup(self):
        fake = self.simulated_smoke()
        self.assertFalse(fake.objects or fake.mounts or fake.roles or fake.policies)
        self.assertFalse(any(fake.tokens.values()))

    def test_failed_read_still_cleans_only_test_resources(self):
        fake = self.simulated_smoke('read')
        self.assertFalse(fake.objects or fake.mounts or fake.roles or fake.policies)

    def test_changed_cleanup_role_is_preserved_and_fails(self):
        fake = self.simulated_smoke('cleanup')
        self.assertTrue(fake.roles); self.assertFalse(fake.objects)

    def test_render_and_schema_failures_propagate(self):
        with mock.patch.object(bao, 'run', side_effect=ValueError('render failed')) as tool:
            with self.assertRaises(ValueError): render_check(fixture())
            self.assertEqual(tool.call_count, 1)
        with mock.patch.object(bao, 'run', side_effect=[bao.manifest(fixture()), ValueError('schema failed')]):
            with self.assertRaises(ValueError): render_check(fixture())

    def test_version_mismatch_fails(self):
        with mock.patch.object(bao, 'run', return_value=json.dumps({'clientVersion': {'gitVersion': 'v1.34.0'}})):
            with self.assertRaises(ValueError): versions()

    def test_copied_package_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'package'; shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns('__pycache__', 'site.json', 'review.json', 'out'))
            r = subprocess.run([sys.executable, str(target / 'bao.py'), 'render', '--site', str(target / 'site.example.json')],
                               cwd=folder, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr); self.assertEqual(len(list(yaml.safe_load_all(r.stdout))), 9)

    def test_snapshot_permissions_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            if os.name == 'nt':
                with self.assertRaisesRegex(ValueError, 'POSIX'): bao.secure_destination(Path(folder) / 'new.snap')
            else:
                os.chmod(folder, 0o700); bao.secure_destination(Path(folder) / 'new.snap')
                os.chmod(folder, 0o755)
                with self.assertRaises(ValueError): bao.secure_destination(Path(folder) / 'new.snap')

    def test_uninstall_never_removes_persistent_data(self):
        with mock.patch.object(bao, 'get', return_value={'items': [{'metadata': {'name': 'data-openbao-0'}}]}) as api:
            with self.assertRaises(ValueError): bao.uninstall_check(fixture())
            self.assertEqual(api.call_count, 1)

    def test_bootstrap_projected_reviewer_and_audit_first(self):
        c = fixture(); api = mock.Mock()
        def request(method, path, body=None, **kwargs):
            if method == 'GET': return {'data': {}}
            return {}
        api.request.side_effect = request
        with mock.patch.object(bao, 'authorize'), mock.patch.object(bao, 'preflight'), mock.patch.object(bao, 'health'), mock.patch.object(bao, 'admin', return_value=api):
            self.assertEqual(bao.bootstrap(c, argparse.Namespace())['status'], 'PASS')
        writes = [call for call in api.request.call_args_list if call.args[0] != 'GET']
        self.assertEqual(writes[0].args[1], 'sys/audit/pcloud-file')
        self.assertEqual(writes[0].args[2]['options']['log_raw'], 'false')
        config = writes[-1].args[2]
        self.assertNotIn('token_reviewer_jwt', config); self.assertNotIn('kubernetes_ca_cert', config)
        self.assertFalse(config['disable_local_ca_jwt'])

    def test_conflicting_audit_is_preserved(self):
        api = mock.Mock(); api.request.return_value = {'data': {'pcloud-file/': {'type': 'file', 'options': {'log_raw': 'true'}}}}
        with mock.patch.object(bao, 'authorize'), mock.patch.object(bao, 'preflight'), mock.patch.object(bao, 'health'), mock.patch.object(bao, 'admin', return_value=api):
            with self.assertRaises(ValueError): bao.bootstrap(fixture(), argparse.Namespace())
        self.assertEqual(api.request.call_count, 1)

    def test_simulated_snapshot_stream_hash_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot'; api = mock.MagicMock()
            response = mock.MagicMock(); response.status = 200; response.read.side_effect = [b'fixture-snapshot', b'']
            api.open.return_value.__enter__.return_value = response
            args = argparse.Namespace(snapshot=str(path), allow_sensitive_export=True)
            with mock.patch.object(bao, 'authorize'), mock.patch.object(bao, 'secure_destination', return_value=path), \
                 mock.patch.object(bao, 'admin', return_value=api), mock.patch.object(bao, 'health'):
                report = bao.backup(fixture(), args)
                self.assertEqual(report['sha256'], hashlib.sha256(b'fixture-snapshot').hexdigest())
                self.assertEqual(path.read_bytes(), b'fixture-snapshot')
                with self.assertRaises(FileExistsError): bao.backup(fixture(), args)
                self.assertEqual(path.read_bytes(), b'fixture-snapshot')

    def test_simulated_failed_export_removes_only_its_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot'; api = mock.MagicMock()
            response = mock.MagicMock(); response.status = 200; response.read.side_effect = [b'part', RuntimeError('network')]
            api.open.return_value.__enter__.return_value = response
            args = argparse.Namespace(snapshot=str(path), allow_sensitive_export=True)
            with mock.patch.object(bao, 'authorize'), mock.patch.object(bao, 'secure_destination', return_value=path), \
                 mock.patch.object(bao, 'admin', return_value=api), mock.patch.object(bao, 'health'):
                with self.assertRaises(RuntimeError): bao.backup(fixture(), args)
            self.assertFalse(path.exists())

    def test_restore_never_accepts_platform_target(self):
        args = argparse.Namespace(allow_store_restore=True)
        with mock.patch.object(bao, 'authorize'), mock.patch.object(bao, 'admin', side_effect=AssertionError('No API')):
            with self.assertRaises(ValueError): bao.restore(fixture(), args)

    def test_simulated_isolated_restore_and_foreign_state_guard(self):
        c = {**fixture(), 'purpose': 'recovery', 'namespace': 'pcloud-secrets-test-restore'}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot'; path.write_bytes(b'fixture-snapshot')
            args = argparse.Namespace(allow_store_restore=True, snapshot=str(path))
            review = {'snapshot_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            # Simulated POSIX permission contract; real TLS and Windows export refusal are tested separately.
            simulated_os = types.SimpleNamespace(**{**vars(os), 'name': 'posix', 'getuid': lambda: 0,
                'fstat': lambda fd: types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_size=16)})
            api = mock.MagicMock(); mounts = {'sys/': {}, 'identity/': {}, 'cubbyhole/': {}}
            def request(method, endpoint, **kwargs):
                d = {'sys/mounts': mounts, 'sys/auth': {'token/': {}}, 'sys/policies/acl': {'policies': ['root', 'default']},
                     'sys/audit': {}, 'auth/token/lookup-self': {'accessor': 'root-fixture'},
                     'auth/token/accessors': {'keys': ['root-fixture']},
                     'sys/storage/raft/configuration': {'config': {'servers': [{'node_id': 'openbao-0'}]}}}.get(endpoint, {})
                return {'data': d}
            api.request.side_effect = request; api.open.return_value.__enter__.return_value.status = 204
            with mock.patch.object(bao, 'authorize', return_value=review), mock.patch.object(bao, 'os', simulated_os), \
                 mock.patch.object(bao, 'preflight'), mock.patch.object(bao, 'health'), mock.patch.object(bao, 'admin', return_value=api), \
                 mock.patch.object(bao, 'get', return_value={'metadata': {'labels': {'pcloud.io/purpose': 'recovery'}}}):
                self.assertEqual(bao.restore(c, args)['status'], 'INCOMPLETE')
                self.assertEqual(api.open.call_args.args, ('POST', 'sys/storage/raft/snapshot-force', b'fixture-snapshot'))
                api.open.reset_mock(); mounts['business/'] = {'type': 'kv'}
                with self.assertRaises(ValueError): bao.restore(c, args)
                api.open.assert_not_called()
            self.assertEqual(path.read_bytes(), b'fixture-snapshot')

    def test_real_tls_ca_hostname_and_redirect_protection(self):
        openssl = 'C:/Program Files/Git/usr/bin/openssl.exe' if os.name == 'nt' else shutil.which('openssl')
        self.assertTrue(openssl and Path(openssl).is_file(), 'OpenSSL required for ephemeral local TLS fixture')
        with tempfile.TemporaryDirectory() as folder:
            cert = Path(folder) / 'cert.pem'; key = Path(folder) / 'key.pem'
            r = subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost', '-keyout', str(key), '-out', str(cert)],
                capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0)
            hits = []
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_GET(self):
                    hits.append((self.path, self.headers.get('X-Vault-Token')))
                    if self.path.endswith('redirect'):
                        self.send_response(302); self.send_header('Location', '/stolen'); self.end_headers()
                    else:
                        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(cert, key)
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                c = {**fixture(), 'endpoint': f'https://localhost:{server.server_port}', 'ca_file': str(cert)}
                client = bao.Client(c, 'synthetic-test-token')
                self.assertTrue(client.request('GET', 'ok')['ok'])
                with self.assertRaises(ValueError): client.request('GET', 'redirect')
                self.assertEqual([p for p, _ in hits], ['/v1/ok', '/v1/redirect'])
                wrong = bao.Client({**c, 'endpoint': f'https://127.0.0.1:{server.server_port}'})
                with self.assertRaises(Exception): wrong.request('GET', 'ok')
                with mock.patch.object(ssl, 'create_default_context', wraps=lambda **kw: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)):
                    untrusted = bao.Client(c)
                    with self.assertRaises(Exception): untrusted.request('GET', 'ok')
                self.assertEqual(len(hits), 2)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--render', action='store_true'); args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    if not result.wasSuccessful(): sys.exit(1)
    if args.render:
        versions(); render_check(fixture()); render_check({**fixture(), 'purpose': 'recovery', 'namespace': 'pcloud-secrets-test-restore'})
        print('PASS: real Kustomize render and Kubernetes 1.35.0 strict schemas for platform/recovery profiles')
