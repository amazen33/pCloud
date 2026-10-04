"""Deterministic API/cluster harness; never a substitute for real OpenBao acceptance."""
import copy
import json
import uuid


class Service:
    def __init__(self, module, config):
        self.bao = module; self.config = config; self.mounts = {}; self.policies = {}; self.roles = {}
        self.values = {}; self.tokens = {}; self.objects = {}; self.audit = []; self.calls = []
        self.bad_read = False; self.bad_cleanup = False

    def request(self, method, path, body=None, token=None, allowed=(200, 204)):
        self.calls.append((method, path)); b = body or {}; status = 200; result = {}
        auth = 'auth/' + self.config['auth_mount']
        if path == 'sys/mounts': result = {'data': copy.deepcopy(self.mounts)}
        elif path == 'sys/policies/acl': result = {'data': {'policies': list(self.policies)}}
        elif path == auth + '/role': result = {'data': {'keys': list(self.roles)}}
        elif path.startswith('sys/mounts/'):
            mount = path.split('/')[-1] + '/'
            if method == 'POST': self.mounts[mount] = copy.deepcopy(b)
            elif method == 'DELETE': self.mounts.pop(mount, None)
        elif path.startswith('sys/policies/acl/'):
            name = path.split('/')[-1]
            if method == 'PUT': self.policies[name] = b['policy']
            elif method == 'GET': result = {'data': {'policy': self.policies[name]}}
            elif method == 'DELETE': self.policies.pop(name, None)
        elif path.startswith(auth + '/role/'):
            name = path.split('/')[-1]
            if method == 'POST': self.roles[name] = copy.deepcopy(b)
            elif method == 'GET':
                d = copy.deepcopy(self.roles[name])
                if self.bad_cleanup: d['bound_service_account_names'] = ['foreign']
                result = {'data': d}
            elif method == 'DELETE': self.roles.pop(name, None)
        elif path == auth + '/login':
            n, sa, audience = b['jwt'].split('|'); role = self.roles[b['role']]
            if n not in role['bound_service_account_namespaces'] or sa != 'reader' or audience != 'pcloud-secrets': status = 403
            else:
                t = uuid.uuid4().hex; self.tokens[t] = True
                result = {'auth': {'client_token': t, 'lease_duration': 900}}
        elif path == 'auth/token/revoke': self.tokens[b['token']] = False
        elif '/data/' in path:
            permitted = token is None or (self.tokens.get(token) and path.endswith('/allowed') and method == 'GET')
            if not permitted: status = 403
            elif method == 'POST':
                current = self.values.get(path, {'metadata': {'version': 0}})['metadata']['version']
                if b['options']['cas'] != current: status = 400
                else: self.values[path] = {'data': copy.deepcopy(b['data']), 'metadata': {'version': current + 1}}
            elif method == 'GET':
                result = {'data': copy.deepcopy(self.values[path])}
                if self.bad_read: result['data']['data']['value'] = 'wrong'
            self.audit.append({'request': {'path': path, 'client_token': 'hmac-sha256:fixture'}})
        self.bao.require(status in allowed, 'Simulated API status rejected')
        return result

    def kube(self, c, *args, data=None):
        if args[:2] == ('create', 'token'):
            return '|'.join([args[args.index('-n') + 1], args[2], next(a.split('=', 1)[1] for a in args if a.startswith('--audience='))])
        if args[0] == 'create':
            obj = json.loads(data); obj['metadata']['uid'] = uuid.uuid4().hex
            m = obj['metadata']; self.objects[(obj['kind'], m.get('namespace'), m['name'])] = obj
            return json.dumps(obj)
        if args[0] == 'delete':
            uid = json.loads(data)['preconditions']['uid']
            for key, obj in list(self.objects.items()):
                if obj['metadata']['uid'] == uid: del self.objects[key]
            return '{}'
        if args[0] == 'exec': return '\n'.join(json.dumps(a) for a in self.audit)
        if args[0] == 'api-resources': return 'serviceaccounts\nconfigmaps\n'
        if args[0] == 'get':
            ns = args[args.index('-n') + 1] if '-n' in args else None
            if '--ignore-not-found' in args:
                obj = self.objects.get((args[1], ns, args[2])); return json.dumps(obj) if obj else ''
            return json.dumps({'items': [o for (k, n, _), o in self.objects.items() if n == ns and k == 'ServiceAccount']})
        raise AssertionError('Unexpected simulated command')
