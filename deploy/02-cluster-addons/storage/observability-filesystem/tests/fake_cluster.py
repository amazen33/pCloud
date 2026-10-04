"""Lifecycle simulation only; not filesystem or provider acceptance evidence."""
import copy
import hashlib
import json
import uuid


class Cluster:
    def __init__(self, module, config):
        self.backend = module; self.config = config; self.objects = {}; self.volumes = {}; self.actions = []
        self.fail_read = False; self.foreign_object = False

    def kube(self, c, *args, data=None):
        if args[0] == 'create':
            o = json.loads(data); m = o['metadata']; m['uid'] = str(uuid.uuid4())
            key = (o['kind'], m.get('namespace'), m['name'])
            self.backend.require(key not in self.objects, 'Simulated duplicate create'); self.objects[key] = o
            if o['kind'] == 'PersistentVolumeClaim':
                o['spec']['volumeName'] = 'pv-' + m['name']; o['status'] = {'phase': 'Bound'}
                worker = next(v['worker'] for v in c['volumes'].values() if v['claim'] == m['name'])
                pv = {'kind': 'PersistentVolume', 'metadata': {'name': o['spec']['volumeName'], 'uid': str(uuid.uuid4()),
                      'annotations': {'pv.kubernetes.io/provisioned-by': c['provisioner']}},
                      'spec': {'storageClassName': c['storage_class'], 'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem',
                               'persistentVolumeReclaimPolicy': 'Retain', 'claimRef': {'uid': m['uid'], 'namespace': m['namespace']},
                               'nodeAffinity': {'required': {'nodeSelectorTerms': [{'matchExpressions': [{
                                 'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [worker]}]}]}}}}
                self.volumes[pv['metadata']['name']] = pv
            if o['kind'] == 'Pod':
                worker = o['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchExpressions'][0]['values'][0]
                o['spec']['nodeName'] = worker; o['status'] = {'phase': 'Succeeded'}
            return json.dumps(copy.deepcopy(o))
        if args[0] == 'delete':
            uid = json.loads(data)['preconditions']['uid']
            for key, o in list(self.objects.items()):
                if o['metadata']['uid'] == uid: del self.objects[key]
            return '{}'
        if args[0] == 'get' and '--ignore-not-found' in args:
            kind = args[1]; ns = args[args.index('-n') + 1] if '-n' in args else None
            obj = self.objects.get((kind, ns, args[2])); return json.dumps(obj) if obj else ''
        if args[0] == 'get':
            if self.foreign_object: return json.dumps({'items': [{'kind': 'Widget', 'metadata': {'name': 'foreign'}}]})
            return json.dumps({'items': []})
        if args[0] == 'api-resources': return 'widgets.example.io\n'
        if args[0] == 'logs':
            ns = args[args.index('-n') + 1]; obj = self.objects[('Pod', ns, args[args.index('-n') + 2])]
            argv = obj['spec']['containers'][0]['args']; action = argv[0]
            component = argv[argv.index('--component') + 1]; run = argv[argv.index('--run') + 1]
            self.actions.append((component, action))
            marker = hashlib.sha256(('pcloud-backend:' + run + ':' + component).encode()).hexdigest()
            return json.dumps({'status': 'PASS', 'checks': [action], 'marker_sha256': 'wrong' if self.fail_read and action == 'read' else marker})
        raise AssertionError('Unexpected simulated kubectl command')

    def get(self, c, kind, name=None, ns=None):
        if kind == 'pv': return copy.deepcopy(self.volumes[name])
        actual = {'pod': 'Pod', 'pvc': 'PersistentVolumeClaim', 'namespace': 'Namespace'}.get(kind, kind)
        return copy.deepcopy(self.objects[(actual, ns, name)])
