#!/usr/bin/env python3
"""Standalone M3c entry. --render validates real Kustomize and Kubernetes schemas."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backend', ROOT / 'backend.py')
backend = importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)
spec = importlib.util.spec_from_file_location('posix_probe', ROOT / 'scripts/posix-probe.py')
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_cluster import Cluster


def fixture(): return backend.read_json(ROOT / 'site.example.json')


def schemas(c):
    ns = {**c, 'namespace': 'pcloud-backend-test-' + 'a'*12}
    resources = [*backend.render(c), *[backend.probe_pod(ns, x, 'a'*12, action)
                  for x in backend.COMPONENTS for action in ('write', 'read', 'deny', 'cleanup')]]
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder); (p / 'resources.yaml').write_text(yaml.safe_dump_all(resources, sort_keys=False), encoding='utf-8')
        (p / 'kustomization.yaml').write_text('resources:\n- resources.yaml\n', encoding='utf-8')
        rendered = backend.run(['kubectl', 'kustomize', str(p)])
        backend.require(list(yaml.safe_load_all(rendered)), 'Empty render rejected')
        backend.run(['kubeconform', '-strict', '-summary', '-kubernetes-version', '1.35.0', '-'], rendered)


def versions():
    v = json.loads(backend.run(['kubectl', 'version', '--client', '-o', 'json']))
    backend.require(v['clientVersion']['gitVersion'] == 'v1.35.0', 'Pinned kubectl 1.35.0 required')
    backend.require(backend.run(['kubeconform', '-v']).strip() == 'v0.7.0', 'Pinned kubeconform 0.7.0 required')


class Checks(unittest.TestCase):
    def test_examples_and_capability_schemas(self):
        for name in ('site.example.json', 'supplied.example.json'):
            c = backend.validate(backend.read_json(ROOT / name))
            jsonschema.validate(backend.capability(c), backend.read_json(ROOT / 'capability.schema.json'))
        for name in ('site.schema.json', 'capability.schema.json'):
            jsonschema.Draft202012Validator.check_schema(backend.read_json(ROOT / name))

    def test_profiles_budget_and_unique_claims(self):
        c = fixture(); c['volumes']['tempo']['claim'] = c['volumes']['loki']['claim']
        for invalid in [c, {**fixture(), 'profile': 'production'}, {**fixture(), 'max_total_gib': 15},
                        {**fixture(), 'reserve_fraction': 0.1}, {**fixture(), 'root_token': 'secret'}]:
            with self.assertRaises(ValueError): backend.validate(invalid)
        c = fixture(); c['volumes']['loki']['uid'] = '11111111-1111-4111-8111-111111111111'
        with self.assertRaises(ValueError): backend.validate(c)
        c = backend.read_json(ROOT / 'supplied.example.json'); del c['volumes']['mimir']['uid']
        with self.assertRaises(ValueError): backend.validate(c)

    def test_no_install_for_supplied_profile(self):
        self.assertEqual(backend.render(backend.read_json(ROOT / 'supplied.example.json')), [])
        self.assertEqual([x['kind'] for x in backend.render(fixture())], ['Namespace'] + ['PersistentVolumeClaim']*3)

    def test_claims_restricted_namespace_and_labels(self):
        for obj in backend.render(fixture()):
            for value in obj['metadata']['labels'].values():
                self.assertRegex(value, r'^[a-zA-Z0-9]([a-zA-Z0-9_.-]{0,61}[a-zA-Z0-9])?$')
            if obj['kind'] == 'PersistentVolumeClaim':
                self.assertEqual(obj['spec']['accessModes'], ['ReadWriteOnce'])
                self.assertEqual(obj['spec']['volumeMode'], 'Filesystem')
                self.assertEqual(obj['spec']['storageClassName'], fixture()['storage_class'])
            else: self.assertEqual(obj['metadata']['labels']['pod-security.kubernetes.io/enforce'], 'restricted')

    def test_fragment_paths_and_separate_mimir_buckets(self):
        f = backend.fragments()
        def strings(x):
            if isinstance(x, dict): return [s for v in x.values() for s in strings(v)]
            if isinstance(x, list): return [s for v in x for s in strings(v)]
            return [x] if isinstance(x, str) else []
        for component, fragment in f.items():
            paths = [s for s in strings(fragment) if s.startswith('/')]
            self.assertTrue(paths)
            self.assertTrue(all(p.startswith(backend.MOUNT + '/' + component + '/') or p == backend.MOUNT + '/' + component for p in paths))
            self.assertNotIn('/tmp/', json.dumps(fragment)); self.assertNotIn('s3', json.dumps(fragment))
        mimir = f['mimir']
        self.assertEqual(len({mimir[k]['filesystem']['dir'] for k in ('blocks_storage', 'ruler_storage', 'alertmanager_storage')}), 3)
        self.assertEqual(f['loki']['schema_config']['configs'][0]['index']['period'], '24h')
        self.assertEqual(f['tempo']['storage']['trace']['backend'], 'local')

    def test_fragment_hash_and_no_false_capabilities(self):
        cap = backend.capability(fixture())
        self.assertEqual(cap['fragments_sha256'], hashlib.sha256(json.dumps(backend.fragments(), sort_keys=True).encode()).hexdigest())
        for key in ('replication', 'backup', 'quota_enforced'): self.assertFalse(cap[key])
        self.assertIsNone(cap['object_store_api'])

    def test_pinned_upstream_and_probe_image(self):
        upstream = backend.read_json(ROOT / 'UPSTREAM.json')
        self.assertEqual(upstream['components'], backend.COMPONENTS)
        for item in upstream['files']:
            self.assertEqual(hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest(), item['sha256'])
            self.assertTrue(any('/'+tag+'/' in item['source'] for tag in ('v3.7.8', 'v2.10.8', 'mimir-2.17.11')))
        lock = backend.read_json(ROOT / 'images.lock.json')[backend.IMAGE_SOURCE]
        self.assertEqual(lock['source'], backend.IMAGE_SOURCE)
        self.assertRegex(lock['manifest_sha256'], r'^sha256:[0-9a-f]{64}$')
        self.assertEqual(lock['image'], 'docker.io/library/python@' + lock['manifest_sha256'])

    def test_probe_is_restricted_and_uid_denial_has_no_fs_group(self):
        for action in ('write', 'read', 'deny', 'cleanup'):
            pod = backend.probe_pod(fixture(), 'loki', 'a'*12, action)['spec']; container = pod['containers'][0]
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
            self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
            self.assertNotIn('hostPath', json.dumps(pod)); self.assertNotIn('nodeName', pod)
            if action == 'deny':
                self.assertNotIn('fsGroup', pod['securityContext']); self.assertEqual(pod['securityContext']['runAsUser'], 10002)
                self.assertTrue(container['volumeMounts'][0]['readOnly'])
            else:
                self.assertEqual(pod['securityContext']['fsGroup'], 10001)
                self.assertEqual(pod['securityContext']['fsGroupChangePolicy'], 'OnRootMismatch')

    def test_digest_binds_context_claims_and_worker(self):
        c = fixture(); first = backend.digest(c)
        self.assertNotEqual(first, backend.digest({**c, 'context': 'different'}))
        c['volumes']['loki']['worker'] = 'different'; self.assertNotEqual(first, backend.digest(c))

    def test_mutation_refused_before_api_without_review(self):
        args = argparse.Namespace(allow_cluster_changes=False, review='missing')
        with mock.patch.object(backend, 'kube', side_effect=AssertionError('No API')):
            with self.assertRaises(ValueError): backend.smoke(fixture(), args)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'review'; p.write_text(json.dumps({'revision': 'a'*40, 'artifact_digest': 'b'*64}))
            with self.assertRaises(ValueError): backend.authorize(fixture(), argparse.Namespace(allow_cluster_changes=True, review=p))

    def test_preflight_rejects_delete_policy_before_nodes(self):
        with mock.patch.object(backend, 'kube', return_value=json.dumps({'serverVersion': {'gitVersion': 'v1.35.7'}})), \
             mock.patch.object(backend, 'get', return_value={'provisioner': fixture()['provisioner'], 'reclaimPolicy': 'Delete'}) as reads:
            with self.assertRaises(ValueError): backend.preflight(fixture())
            self.assertEqual(reads.call_count, 1)

    def test_preflight_checks_all_claims_even_if_first_missing(self):
        c = fixture(); objects = backend.render(c); ns = objects[0]
        bad = objects[2]; bad['metadata']['labels']['pcloud.io/package'] = 'foreign'
        node = {'metadata': {'labels': {'kubernetes.io/hostname': 'same-worker'}}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        for v in c['volumes'].values(): v['worker'] = 'same-worker'
        with mock.patch.object(backend, 'get', side_effect=[{'provisioner': c['provisioner'], 'reclaimPolicy': 'Retain', 'volumeBindingMode': 'WaitForFirstConsumer'}, node]), \
             mock.patch.object(backend, 'kube', side_effect=[json.dumps({'serverVersion': {'gitVersion': 'v1.35.7'}}), json.dumps(ns), '', json.dumps(bad)]):
            with self.assertRaisesRegex(ValueError, 'Foreign'): backend.preflight(c)

    def test_supplied_claim_uid_and_capacity_guard(self):
        c = backend.read_json(ROOT / 'supplied.example.json'); pvc = backend.claim(c, 'loki'); pvc['metadata']['uid'] = 'wrong'
        with self.assertRaises(ValueError): backend.claim_matches(c, 'loki', pvc)
        pvc['metadata']['uid'] = c['volumes']['loki']['uid']; backend.claim_matches(c, 'loki', pvc)
        pvc['spec']['resources']['requests']['storage'] = '4096Mi'; backend.claim_matches(c, 'loki', pvc)
        pvc['spec']['resources']['requests']['storage'] = '4000Mi'
        with self.assertRaises(ValueError): backend.claim_matches(c, 'loki', pvc)

    def test_bound_affinity_and_claim_uid_rejection(self):
        c = fixture(); fake = Cluster(backend, c); pvc = json.loads(fake.kube(c, 'create', data=json.dumps(backend.claim(c, 'loki'))))
        pv = fake.volumes[pvc['spec']['volumeName']]
        with mock.patch.object(backend, 'get', side_effect=fake.get):
            backend.bound_volume(c, pvc, c['volumes']['loki']['worker'])
            pv['spec']['nodeAffinity']['required']['nodeSelectorTerms'].append({'matchExpressions': []})
            with self.assertRaises(ValueError): backend.bound_volume(c, pvc, c['volumes']['loki']['worker'])
            pv['spec']['nodeAffinity']['required']['nodeSelectorTerms'].pop(); pv['spec']['claimRef']['uid'] = 'foreign'
            with self.assertRaises(ValueError): backend.bound_volume(c, pvc, c['volumes']['loki']['worker'])

    def simulated_smoke(self, fail=False, foreign=False):
        c = fixture(); cluster = Cluster(backend, c); cluster.fail_read = fail; cluster.foreign_object = foreign
        with mock.patch.object(backend, 'authorize', return_value={'revision': 'a'*40, 'artifact_digest': backend.digest(c)}), \
             mock.patch.object(backend, 'preflight'), mock.patch.object(backend, 'get', side_effect=cluster.get), \
             mock.patch.object(backend, 'kube', side_effect=cluster.kube), contextlib.redirect_stdout(io.StringIO()) as output:
            if fail or foreign:
                with self.assertRaises(ValueError): backend.smoke(c, argparse.Namespace(output=None))
                self.assertEqual(json.loads(output.getvalue())['status'], 'FAIL')
            else:
                report = backend.smoke(c, argparse.Namespace(output=None))
                self.assertEqual(report['status'], 'INCOMPLETE'); self.assertEqual(len(report['retained_test_volumes']), 3)
        return cluster

    def test_simulated_full_lifecycle_preserves_retained_pvs(self):
        cluster = self.simulated_smoke(); self.assertFalse(cluster.objects); self.assertEqual(len(cluster.volumes), 3)
        self.assertEqual(cluster.actions, [(x, a) for x in backend.COMPONENTS for a in ('write', 'read', 'deny')]
                         + [(x, 'cleanup') for x in reversed(backend.COMPONENTS)])

    def test_failed_remount_never_passes_and_cleans_only_synthetic_data(self):
        cluster = self.simulated_smoke(fail=True); self.assertFalse(cluster.objects); self.assertEqual(len(cluster.volumes), 1)

    def test_unknown_crd_prevents_namespace_removal(self):
        cluster = self.simulated_smoke(foreign=True)
        self.assertTrue(any(k[0] == 'Namespace' for k in cluster.objects))

    def test_pending_claims_are_incomplete_not_accepted(self):
        c = fixture()
        with mock.patch.object(backend, 'preflight'), mock.patch.object(backend, 'claim_matches'), \
             mock.patch.object(backend, 'get', return_value={'status': {'phase': 'Pending'}}):
            report = backend.live(c); self.assertEqual(report['status'], 'INCOMPLETE')
            self.assertEqual(len(report['awaiting_first_consumer']), 3)

    def test_safe_error_and_report_path_guards(self):
        with mock.patch('sys.argv', ['backend.py', 'live', '--site', str(ROOT / 'site.example.json')]), \
             mock.patch.object(backend, 'live', side_effect=RuntimeError('private-credential')), \
             contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(backend.main(), 1)
        self.assertNotIn('private-credential', output.getvalue())
        if any((p / '.git').exists() for p in ROOT.parents):
            with self.assertRaises(ValueError): backend.emit({'status': 'PASS'}, ROOT / 'out.json')

    def test_uninstall_preserves_claims(self):
        with mock.patch.object(backend, 'kube', return_value=json.dumps({'metadata': {'name': 'loki-data'}})):
            with self.assertRaises(ValueError): backend.uninstall_check(fixture())

    def test_real_render_failures_and_tool_version_guard(self):
        with mock.patch.object(backend, 'run', side_effect=ValueError('render')) as commands:
            with self.assertRaises(ValueError): schemas(fixture())
            self.assertEqual(commands.call_count, 1)
        with mock.patch.object(backend, 'run', side_effect=[yaml.safe_dump_all(backend.render(fixture())), ValueError('schema')]):
            with self.assertRaises(ValueError): schemas(fixture())
        with mock.patch.object(backend, 'run', return_value=json.dumps({'clientVersion': {'gitVersion': 'v1.34.0'}})):
            with self.assertRaises(ValueError): versions()

    def test_copied_package_renders_without_sibling_files(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'package'; shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns('__pycache__', 'site.json', 'review.json', 'out'))
            p = subprocess.run([sys.executable, str(target / 'backend.py'), 'render', '--site', str(target / 'site.example.json')],
                               cwd=folder, capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr); self.assertEqual(len(list(yaml.safe_load_all(p.stdout))), 4)

    @unittest.skipUnless(os.name == 'posix', 'Real POSIX fixture runs on Linux CI/operator station; Windows cannot prove flock/fsync semantics')
    def test_real_posix_probe_persistence_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); args = (root, 'a'*12, 'loki', os.getuid(), 0, 0, 0)
            self.assertEqual(probe.execute('write', *args)['status'], 'PASS')
            self.assertEqual(probe.execute('read', *args)['status'], 'PASS')
            self.assertEqual(probe.execute('cleanup', *args)['status'], 'PASS')
            self.assertTrue(root.exists()); self.assertFalse(list(root.iterdir()))

    @unittest.skipUnless(os.name == 'posix', 'Real POSIX fixture requires Linux')
    def test_posix_probe_unknown_file_and_symlink_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); args = (root, 'a'*12, 'loki', os.getuid(), 0, 0, 0)
            probe.execute('write', *args); d = root / ('pcloud-probe-' + 'a'*12 + '-loki')
            (d / 'foreign').write_text('preserve')
            with self.assertRaises(ValueError): probe.execute('cleanup', *args)
            self.assertTrue((d / 'foreign').exists()); (d / 'foreign').unlink()
            (d / 'marker').unlink(); (d / 'marker').symlink_to(root / 'unrelated')
            with self.assertRaises(ValueError): probe.execute('cleanup', *args)
            self.assertTrue((d / 'marker').is_symlink())

    @unittest.skipUnless(os.name == 'posix', 'Real POSIX fixture requires Linux')
    def test_posix_probe_capacity_guard_never_creates_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): probe.execute('write', Path(folder).resolve(), 'a'*12, 'loki', os.getuid(), 2**63, 0, 0)
            self.assertFalse(list(Path(folder).iterdir()))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--render', action='store_true'); args = p.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    if not result.wasSuccessful(): sys.exit(1)
    if args.render:
        versions(); schemas(fixture()); print('PASS: real Kustomize and strict Kubernetes 1.35.0 schemas, four permanent resources plus 12 restricted probe pods')
