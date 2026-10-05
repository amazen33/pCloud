#!/usr/bin/env python3
"""Standalone lab storage rendering and conformance. No default cluster writes."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent
NAMESPACE = 'pcloud-storage-system'
CLASS = 'pcloud-local-retain'
PROVISIONER = 'pcloud.io/local-path'
LABEL = 'pcloud.io/package'


class CheckRejected(ValueError):
    """Only constant, non-secret validation messages may use this exception."""


def require(condition, message):
    if not condition:
        raise CheckRejected(message)


def run(argv, data=None, timeout=60):
    result = subprocess.run(argv, input=data, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, f'{Path(argv[0]).name} failed (exit {result.returncode}); output withheld to protect credentials')
    return result.stdout


def load(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    jsonschema.validate(config, json.loads((ROOT/'site.schema.json').read_text()))
    if config['profile'] == 'node-local-lab':
        require(not any(k in config for k in ('supplied_class','supplied_provisioner')),'Local profile cannot contain supplied-provider inputs')
        names = [n['name'] for n in config['nodes']]
        require(len(names) == len(set(names)), 'Duplicate worker names')
        root = PurePosixPath(config['storage_root'])
        require(str(root) == config['storage_root'] and '..' not in root.parts, 'Noncanonical storage path')
        require(root.is_relative_to(PurePosixPath('/var/lib/pcloud/storage')) and root != PurePosixPath('/var/lib/pcloud/storage'),
                'Storage must be a dedicated mount below /var/lib/pcloud/storage')
        require(config['capacity']['reserve_fraction'] >= .2, 'At least 20% capacity reserve required')
    else:
        require(set(config)=={'profile','context','resources','supplied_class','supplied_provisioner'},'Supplied profile cannot contain node/host installation inputs')
    def cpu(value):return int(value[:-1]) if value.endswith('m') else int(value)*1000
    def memory(value):return int(value[:-2])*(1024**2 if value.endswith('Mi') else 1024**3)
    for resources in config['resources'].values():
        require(cpu(resources['limits']['cpu'])>=cpu(resources['requests']['cpu']) and
            memory(resources['limits']['memory'])>=memory(resources['requests']['memory']),'Resource limit below request')
    return config


def images(records=None):
    if records is None: records = json.loads((ROOT/'images.lock.json').read_text())
    require(set(records)=={'docker.io/rancher/local-path-provisioner:v0.0.37','docker.io/library/busybox:1.37.0'},'Unreviewed image source set')
    for source, item in records.items():
        require(item['source'] == source and re.fullmatch(r'.+@sha256:[a-f0-9]{64}', item['image']), 'Invalid immutable image lock')
        repository,tag=source.rsplit(':',1)
        require(item['image']==repository+'@'+item['manifest_sha256'],'Image repository/digest differs from provenance')
        require(item['manifest_url']=='https://registry-1.docker.io/v2/'+repository.removeprefix('docker.io/')+'/manifests/'+tag,
                'Registry provenance URL differs from reviewed source')
    return {key: item['image'] for key, item in records.items()}


def helper_script(config, teardown=False):
    root = config['storage_root']
    identities = '|'.join(shlex.quote('pcloud-storage-v1:'+n['filesystem_uuid']) for n in config['nodes'])
    operation = 'rm -rf -- "$VOL_DIR"' if teardown else 'mkdir -m 0700 -- "$VOL_DIR"\nchown 1000:1000 -- "$VOL_DIR"'
    return f'''#!/bin/sh
set -eu
root={shlex.quote(root)}
test "$VOL_MODE" = Filesystem
test -d "$root" && test ! -L "$root"
test "$(readlink -f "$root")" = "$root"
test -f "$root/.pcloud-storage-ready" && test ! -L "$root/.pcloud-storage-ready"
test "$(wc -c < "$root/.pcloud-storage-ready")" -le 128
case "$(cat "$root/.pcloud-storage-ready")" in {identities}) ;; *) exit 42 ;; esac
test "$(dirname "$VOL_DIR")" = "$root"
case "$(basename "$VOL_DIR")" in pvc-*) ;; *) exit 43 ;; esac
case "$VOL_DIR" in *'..'*|*'//'*) exit 44 ;; esac
test ! -L "$VOL_DIR"
test "$(readlink -f "$VOL_DIR")" = "$VOL_DIR"
{operation}
'''


def render(config):
    if config['profile'] == 'supplied-storage':
        return []
    docs = list(yaml.safe_load_all((ROOT/'upstream/local-path-storage.yaml').read_text()))
    locked = images()
    for doc in docs:
        meta = doc['metadata']
        meta.setdefault('labels', {})[LABEL] = 'local-path'
        if doc['kind'] == 'Namespace':
            meta['name'] = NAMESPACE
            meta['labels'].update({'pod-security.kubernetes.io/enforce':'privileged',
                'pod-security.kubernetes.io/audit':'restricted', 'pod-security.kubernetes.io/warn':'restricted',
                'pod-security.kubernetes.io/enforce-version':'v1.35', 'pod-security.kubernetes.io/audit-version':'v1.35',
                'pod-security.kubernetes.io/warn-version':'v1.35'})
        if 'namespace' in meta:
            meta['namespace'] = NAMESPACE
        if doc['kind'] in ('Role','ClusterRole','RoleBinding','ClusterRoleBinding'):
            meta['name'] = 'pcloud-'+meta['name']
        if 'roleRef' in doc:
            doc['roleRef']['name'] = 'pcloud-'+doc['roleRef']['name']
        for subject in doc.get('subjects', []):
            if 'namespace' in subject:
                subject['namespace'] = NAMESPACE
        if doc['kind'] == 'Deployment':
            pod = doc['spec']['template']['spec']
            pod['securityContext'] = {'runAsNonRoot':True,'runAsUser':1000,'runAsGroup':1000,'fsGroup':1000,'seccompProfile':{'type':'RuntimeDefault'}}
            pod['nodeSelector'] = {'kubernetes.io/os':'linux'}
            container = pod['containers'][0]
            container['image'] = locked['docker.io/rancher/local-path-provisioner:v0.0.37']
            container['command'] = ['local-path-provisioner','start','--config','/etc/config/config.json',
                '--provisioner-name',PROVISIONER,'--helper-image',locked['docker.io/library/busybox:1.37.0']]
            container['securityContext'] = {'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}
            container['resources'] = config['resources']['controller']
            # Declare API defaults so strict Live comparison sees the same inputs
            # after Kubernetes stores the Deployment. Real drift stays rejected.
            for env in container.get('env', []):
                ref = env.get('valueFrom', {}).get('fieldRef')
                if ref is not None: ref.setdefault('apiVersion', 'v1')
            for volume in pod.get('volumes', []):
                if 'configMap' in volume: volume['configMap'].setdefault('defaultMode', 0o644)
        if doc['kind'] == 'StorageClass':
            meta['name'] = CLASS
            meta['annotations'] = {'storageclass.kubernetes.io/is-default-class':'false','defaultVolumeType':'local'}
            doc.update(provisioner=PROVISIONER, reclaimPolicy='Retain', volumeBindingMode='WaitForFirstConsumer', allowVolumeExpansion=False,
                allowedTopologies=[{'matchLabelExpressions':[{'key':'kubernetes.io/hostname','values':[n['name'] for n in config['nodes']]}]}])
        if doc['kind'] == 'ConfigMap':
            doc['data']['config.json'] = json.dumps({'nodePathMap':[{'node':'DEFAULT_PATH_FOR_NON_LISTED_NODES','paths':[]}]+[
                {'node':n['name'],'paths':[config['storage_root']]} for n in config['nodes']], 'cmdTimeoutSeconds':60})
            doc['data']['setup'] = helper_script(config)
            doc['data']['teardown'] = helper_script(config, True)
            doc['data']['helperPod.yaml'] = yaml.safe_dump({'apiVersion':'v1','kind':'Pod','metadata':{'name':'pcloud-storage-helper'},
                'spec':{'automountServiceAccountToken':False,'containers':[{'name':'helper-pod',
                    'image':locked['docker.io/library/busybox:1.37.0'], 'resources':config['resources']['helper']}]}})
    return docs


def manifest(config):
    return yaml.safe_dump_all(render(config), sort_keys=False)


def digest(config):
    # Include the supplied profile as well: a zero-resource render must not
    # authorize a different context/class/capability declaration.
    source = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ('storage.py','scripts/node-report.py','site.schema.json','capability.schema.json',
         'images.lock.json','UPSTREAM.json','upstream/local-path-storage.yaml')}
    return hashlib.sha256((json.dumps(config,sort_keys=True)+manifest(config)+json.dumps(source,sort_keys=True)).encode()).hexdigest()


def capability(config):
    supplied = config['profile'] == 'supplied-storage'
    result={'contract_version':'pcloud.storage/v1','profile':config['profile'],
        'storage_class':config['supplied_class'] if supplied else CLASS,
        'provisioner':config['supplied_provisioner'] if supplied else PROVISIONER,
        'volume_mode':'Filesystem','access_modes':['ReadWriteOnce'],
        'binding':'WaitForFirstConsumer','reclaim':'Retain','capacity_enforced':False,
        'replication':False,'snapshots':False,'backup':False,'expansion':False,
        'node_names':[n['name'] for n in config.get('nodes',[])],
        'component_version':None if supplied else 'v0.0.37',
        'permanent_node_loss':'data may be lost; restore requires an independently provided backup'}
    jsonschema.validate(result,json.loads((ROOT/'capability.schema.json').read_text()))
    return result


def kube(config, *args, data=None):
    long=args[0] in ('wait','rollout','delete')
    return run(['kubectl','--context',config['context'],'--request-timeout='+('200s' if long else '30s'),*args],data,210 if long else 60)


def get(config, kind, name=None, namespace=None, optional=False):
    args = ['get',kind]
    if name: args.append(name)
    if namespace: args += ['-n',namespace]
    if optional: args += ['--ignore-not-found']
    output = kube(config,*args,'-o','json')
    return json.loads(output) if output.strip() else None


def validate_node(config, declared, node, report):
    conditions = {c['type']:c['status'] for c in node['status']['conditions']}
    require(conditions.get('Ready') == 'True' and conditions.get('DiskPressure') == 'False', 'Worker is not healthy')
    labels = node['metadata'].get('labels',{})
    require(not any(k in labels for k in ('node-role.kubernetes.io/control-plane','node-role.kubernetes.io/master','node-role.kubernetes.io/etcd')), 'Control plane cannot provide lab storage')
    require(labels.get('kubernetes.io/hostname') == declared['name'], 'Worker hostname differs from reviewed topology')
    require(report['root'] == config['storage_root'] and not report['symlink'], 'Storage root escapes its canonical mount')
    require(report['target'] == config['storage_root'] and report['uuid'] == declared['filesystem_uuid'], 'Application-storage mount missing or wrong filesystem')
    require(report['device'] not in (report['os_device'],report['rke2_device']), 'Application storage shares OS/RKE2 filesystem')
    require(report['marker'] == 'pcloud-storage-v1:'+declared['filesystem_uuid'], 'Filesystem marker absent or wrong')
    require(report['root_uid']==0 and report['root_mode'] & 0o022==0, 'Storage root must be root-owned and not group/world writable')
    require(report['marker_uid']==0 and report['marker_mode'] & 0o022==0, 'Mount marker must be root-owned and not group/world writable')
    budget = config['capacity']
    require(report['free_bytes']-budget['projected_bytes'] >= max(budget['minimum_free_bytes'],report['total_bytes']*budget['reserve_fraction']), 'Insufficient projected byte headroom')
    require(report['total_inodes']>0 and report['free_inodes']/report['total_inodes'] >= budget['reserve_fraction'], 'Insufficient inode headroom')


def preflight(config, installed=False):
    version = json.loads(kube(config,'version','-o','json'))['serverVersion']['gitVersion']
    require(version.startswith('v1.35.'), 'Target must be the reviewed Kubernetes 1.35 minor')
    supplied = config['profile'] == 'supplied-storage'
    class_name = config['supplied_class'] if supplied else CLASS
    sc = get(config,'storageclass',class_name,optional=True)
    if supplied or installed:
        require(sc is not None, 'StorageClass is not installed')
    if sc:
        require(sc['reclaimPolicy'] == 'Retain' and sc['volumeBindingMode'] == 'WaitForFirstConsumer', 'StorageClass lacks required retain/binding capability')
        require(sc['provisioner'] == (config['supplied_provisioner'] if supplied else PROVISIONER),'Provisioner mismatch')
        if not supplied:
            require(sc['metadata'].get('labels',{}).get(LABEL) == 'local-path','Conflicting StorageClass ownership')
            annotations=sc['metadata'].get('annotations',{})
            require(all(annotations.get(key,'false')=='false' for key in
                ('storageclass.kubernetes.io/is-default-class','storageclass.beta.kubernetes.io/is-default-class')),'Consumer class must not be default')
            require(annotations.get('defaultVolumeType')=='local','Consumer class must use local volumes')
    if supplied:
        return {'status':'PASS','class':class_name,'version':version}
    host = config['host_capacity']
    require(time.time()-host['observed_unix_seconds'] <= 86400 and host['observed_unix_seconds'] <= time.time()+60,'Host capacity observation must be within the preceding day')
    require(host['free_bytes']-host['projected_growth_bytes'] >= host['minimum_free_bytes'],'Insufficient Hyper-V host capacity')
    nodes = {n['metadata']['name']:n for n in get(config,'nodes')['items']}
    for declared in config['nodes']:
        require(declared['name'] in nodes,'Reviewed worker is missing')
        script = (ROOT/'scripts/node-report.py').read_text()
        command = 'python3 - '+shlex.quote(config['storage_root'])
        output = run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',declared['ssh'],command],script)
        validate_node(config,declared,nodes[declared['name']],json.loads(output))
    for doc in render(config):
        obj = get(config,doc['kind'],doc['metadata']['name'],doc['metadata'].get('namespace'),optional=True)
        if obj:
            require(obj['metadata'].get('labels',{}).get(LABEL) == 'local-path','Conflicting existing resource ownership')
    if installed:
        deploy = get(config,'deployment','local-path-provisioner',NAMESPACE)
        require(deploy.get('status',{}).get('availableReplicas',0) == 1,'Provisioner is not available')
        expected = images()['docker.io/rancher/local-path-provisioner:v0.0.37']
        require(deploy['spec']['template']['spec']['containers'][0]['image'] == expected,'Installed controller image differs from lock')
        wanted=next(d for d in render(config) if d['kind']=='Deployment')['spec']['template']['spec']
        actual=deploy['spec']['template']['spec']
        require(actual['securityContext']==wanted['securityContext'],'Controller pod security drift')
        require(len(actual['containers'])==1 and not actual.get('initContainers'),'Unexpected controller containers')
        for key in ('command','resources','securityContext'):
            require(actual['containers'][0][key]==wanted['containers'][0][key],'Controller execution/security drift')
        for key in ('env','volumeMounts'):
            require(actual['containers'][0][key]==wanted['containers'][0][key],'Controller input/mount drift')
        for key in ('serviceAccountName','volumes','nodeSelector'):
            require(actual[key]==wanted[key],'Controller service-account/mount/placement drift')
        for doc in render(config):
            obj=get(config,doc['kind'],doc['metadata']['name'],doc['metadata'].get('namespace'))
            if doc['kind']=='Namespace':
                for key,value in doc['metadata']['labels'].items():
                    require(obj['metadata'].get('labels',{}).get(key)==value,'Namespace admission policy drift')
            if doc['kind']=='ConfigMap': require(obj.get('data')==doc['data'],'Provisioner configuration drift')
            if doc['kind'] in ('Role','ClusterRole'): require(obj['rules']==doc['rules'],'RBAC rule drift')
            if doc['kind'] in ('RoleBinding','ClusterRoleBinding'):
                require(obj['subjects']==doc['subjects'] and obj['roleRef']==doc['roleRef'],'RBAC subject drift')
            if doc['kind']=='StorageClass':
                require(not obj.get('parameters') and obj.get('allowedTopologies')==doc['allowedTopologies'],'Storage topology/path override drift')
                require(not obj.get('allowVolumeExpansion',False),'Expansion is outside the lab capability')
        for kind in ('statefulsets','daemonsets','jobs','cronjobs'):
            require(not get(config,kind,namespace=NAMESPACE)['items'],'Application workloads in storage exception namespace')
        require(all(d['metadata']['name']=='local-path-provisioner' for d in get(config,'deployments',namespace=NAMESPACE)['items']),
                'Application deployment in storage exception namespace')
    return {'status':'PASS','class':class_name,'version':version,'workers_checked':len(config['nodes'])}


def uninstall_check(config):
    require(config['profile']=='node-local-lab','Supplied provider must not be uninstalled by this package')
    preflight(config,installed=True)
    require(not [pv for pv in get(config,'pv')['items'] if pv['spec'].get('storageClassName')==CLASS],
            'Uninstall refused: class still has persistent volumes, including retained volumes')
    claims=json.loads(kube(config,'get','pvc','-A','-o','json'))['items']
    require(not [pvc for pvc in claims if pvc['spec'].get('storageClassName')==CLASS],
            'Uninstall refused: class still has consumer claims, including Pending claims')
    return {'status':'PASS','zero_consumers':True,'context':config['context'],'artifact_digest':digest(config),
        'objects':[{'kind':d['kind'],'name':d['metadata']['name'],'namespace':d['metadata'].get('namespace')} for d in render(config)],
        'action':'read-only check only; reviewed removal requires separate owner authorization'}


def security():
    return {'runAsNonRoot':True,'runAsUser':1000,'runAsGroup':1000,'fsGroup':1000,'seccompProfile':{'type':'RuntimeDefault'}}


def test_resources(config, namespace, claim='data', volume=None):
    cls = capability(config)['storage_class']
    pvc = {'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':claim,'namespace':namespace},
        'spec':{'accessModes':['ReadWriteOnce'],'storageClassName':cls,'volumeMode':'Filesystem','resources':{'requests':{'storage':'16Mi'}}}}
    if volume: pvc['spec']['volumeName'] = volume
    pod = {'apiVersion':'v1','kind':'Pod','metadata':{'name':'reader','namespace':namespace},'spec':{
        'automountServiceAccountToken':False,'restartPolicy':'Never','securityContext':security(),
        'containers':[{'name':'reader','image':images()['docker.io/library/busybox:1.37.0'],
            'command':['sh','-c','sleep 600'],'resources':{'requests':{'cpu':'10m','memory':'16Mi'},'limits':{'cpu':'100m','memory':'32Mi'}},
            'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
            'volumeMounts':[{'name':'data','mountPath':'/data'}]}],
        'volumes':[{'name':'data','persistentVolumeClaim':{'claimName':claim}}]}}
    if config['profile'] == 'node-local-lab':
        pod['spec']['affinity'] = {'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{
            'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':[n['name'] for n in config['nodes']]}]}]}}}
    return pvc,pod


def authorize(config, args):
    require(args.allow_cluster_changes,'Smoke requires explicit --allow-cluster-changes')
    require(args.approved_digest == digest(config),'Reviewed artifact digest mismatch')
    require(re.fullmatch('[a-f0-9]{40}',args.revision or ''),'Smoke requires the reviewed 40-character source revision')


def owned(obj, run_id, uid=None):
    require(obj['metadata'].get('labels',{}).get('pcloud.io/test-run')==run_id,'Test resource ownership mismatch')
    if uid: require(obj['metadata']['uid']==uid,'Test resource identity changed')


def pv_guard(pv, config, namespace, claim_uids, run_id):
    ref=pv['spec'].get('claimRef',{})
    require(pv['spec']['storageClassName']==capability(config)['storage_class'],'Foreign volume class')
    require(ref.get('namespace')==namespace and ref.get('uid') in claim_uids,'Foreign volume claim')
    label=pv['metadata'].get('labels',{}).get('pcloud.io/test-run')
    require(label in (None,run_id),'Foreign volume run label')
    if config['profile']=='node-local-lab':
        expected=config['storage_root']+'/'+pv['metadata']['name']+'_'+namespace+'_'+ref['name']
        require(pv['spec'].get('local',{}).get('path')==expected,'PV directory differs from exact synthetic target')
        require(pv['metadata'].get('annotations',{}).get('pv.kubernetes.io/provisioned-by')==PROVISIONER,'Foreign volume controller')


def helper_evidence(events, config, pv_names):
    matched=[]
    for event in events:
        pod=event.get('object',{})
        if pod.get('kind')!='Pod' or event.get('type') not in ('ADDED','MODIFIED'):continue
        spec=pod['spec']; env={e['name']:e.get('value') for e in spec['containers'][0].get('env',[])}
        if not any(env.get('VOL_DIR','').startswith(config['storage_root']+'/'+pv+'_') for pv in pv_names):continue
        require(spec.get('nodeName') in [n['name'] for n in config['nodes']],'Helper on non-reviewed node')
        require(spec.get('automountServiceAccountToken') is False,'Helper automounts credentials')
        require(len(spec['containers'])==1,'Unexpected helper container')
        c=spec['containers'][0]
        require(c['image']==images()['docker.io/library/busybox:1.37.0'] and c['resources']==config['resources']['helper'],'Generated helper differs from review')
        paths=[v['hostPath']['path'].rstrip('/') for v in spec['volumes'] if 'hostPath' in v]
        require(paths==[config['storage_root']],'Helper host path escapes reviewed mount')
        matched.append({'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],'node':spec['nodeName'],
            'host_paths':paths,'image':c['image'],'resources':c['resources'],
            'pod_security_context':spec.get('securityContext',{}),'container_security_context':c.get('securityContext',{}),
            'namespace_enforce':'privileged','restricted_compliant':False})
    require(matched,'No actual generated helper captured; admission evidence is incomplete')
    return list({p['uid']:p for p in matched}.values())


class HelperWatch:
    """Capture transient API objects from a resourceVersion, never helper logs."""
    def __init__(self,config):self.config=config;self.process=None;self.file=None
    def start(self):
        endpoint='/api/v1/namespaces/'+NAMESPACE+'/pods'
        # kubectl's assembled List can discard the API resourceVersion.
        rv=json.loads(kube(self.config,'get','--raw='+endpoint))['metadata']['resourceVersion']
        # API watch timeoutSeconds is independent of the client's request timeout.
        # Pin both so server-default watch expiry cannot truncate helper evidence.
        require(isinstance(rv,str) and 0<len(rv)<=512,'Invalid helper watch resource version')
        endpoint+='?'+urlencode({'watch':'true','resourceVersion':rv,'timeoutSeconds':900})
        self.file=tempfile.TemporaryFile()
        self.process=subprocess.Popen(['kubectl','--context',self.config['context'],'get',
            '--raw='+endpoint,'--request-timeout=910s'],
            stdout=self.file,stderr=subprocess.DEVNULL)
    def finish(self):
        if not self.process:return []
        was_running=self.process.poll() is None
        if was_running:self.process.terminate()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        self.file.seek(0);raw=self.file.read(5*1024*1024+1);self.file.close();self.process=None
        require(was_running,'Helper watch ended before acceptance completed')
        require(len(raw)<=5*1024*1024,'Helper watch evidence exceeds bounded size')
        text=raw.decode();decoder=json.JSONDecoder();events=[]
        while text.strip():
            item,end=decoder.raw_decode(text.lstrip());events.append(item);text=text.lstrip()[end:]
        return events


def wait_phase(config,kind,name,phase,namespace=None,timeout=60):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        obj=get(config,kind,name,namespace)
        if obj.get('status',{}).get('phase')==phase:return obj
        time.sleep(1)
    raise ValueError('Timed out waiting for resource lifecycle')


def controller_scale(config, replicas, original_uid):
    obj=get(config,'deployment','local-path-provisioner',NAMESPACE)
    require(obj['metadata']['uid']==original_uid and obj['metadata'].get('labels',{}).get(LABEL)=='local-path','Controller identity/ownership changed')
    current=obj['spec']['replicas'];require(current in (0,1),'Unexpected controller scale')
    if current!=replicas:
        kube(config,'scale','deployment/local-path-provisioner','-n',NAMESPACE,'--current-replicas='+str(current),'--replicas='+str(replicas))
    if replicas:kube(config,'rollout','status','deployment/local-path-provisioner','-n',NAMESPACE,'--timeout=180s')
    else:kube(config,'wait','--for=delete','pod','-l','app=local-path-provisioner','-n',NAMESPACE,'--timeout=60s')


def namespace_empty(config,namespace):
    # Inspect metadata only for every listable namespace resource, including
    # installed CRDs. Unknown objects are preserved, never swept by deletion.
    resources=kube(config,'api-resources','--namespaced=true','--verbs=list','-o','name').splitlines()
    require(resources,'API discovery did not enumerate namespace resources')
    require(len(resources)<=256,'Namespace inspection exceeds bounded resource types')
    for resource in sorted(set(resources)):
        require(re.fullmatch(r'[a-z0-9.-]+',resource),'Unexpected API resource name')
        identities=kube(config,'get',resource,'-n',namespace,
            '-o=jsonpath={range .items[*]}{.kind}{"|"}{.metadata.name}{"\\n"}{end}').splitlines()
        for identity in identities:
            require(identity in ('ServiceAccount|default','ConfigMap|kube-root-ca.crt') or
                resource in ('events','events.events.k8s.io'), 'Unexpected object in test namespace; removal withheld')


def volume_affinity_rejection(events,pod_uid):
    """Require a PV-affinity scheduling failure for this exact synthetic Pod."""
    messages=('volume node affinity conflict',"node(s) didn't match PersistentVolume's node affinity")
    return next((e for e in events if e.get('reason')=='FailedScheduling'
        and e.get('involvedObject',{}).get('uid')==pod_uid
        and any(message in e.get('message','') for message in messages)),None)


def smoke(config,args):
    authorize(config,args)
    local=config['profile']=='node-local-lab'
    require(not local or args.allow_controller_restart,'Local acceptance requires separate --allow-controller-restart consent')
    preflight(config,installed=True)
    cls=capability(config)['storage_class']
    if local:
        require(not [pv for pv in get(config,'pv')['items'] if pv['spec'].get('storageClassName')==cls],
                'Controller acceptance requires an isolated installation with no existing consumer volumes')
        require(not [pvc for pvc in json.loads(kube(config,'get','pvc','-A','-o','json'))['items'] if pvc['spec'].get('storageClassName')==cls],
                'Controller acceptance requires no existing consumer claims, including Pending claims')
    namespace='pcloud-storage-test-'+uuid.uuid4().hex[:12];run_id=namespace.removeprefix('pcloud-storage-test-')
    ns={'apiVersion':'v1','kind':'Namespace','metadata':{'name':namespace,'labels':{'pcloud.io/test-run':run_id,
        'pod-security.kubernetes.io/enforce':'restricted','pod-security.kubernetes.io/enforce-version':'v1.35'}}}
    pvc,pod=test_resources(config,namespace);claim_uids=set();pv_names=set();inventory={};ns_uid=None
    watch=HelperWatch(config);controller_uid=None;controller_stopped=False
    report={'status':'INCOMPLETE','context':config['context'],'revision':args.revision,'artifact_digest':digest(config),
        'run_id':run_id,'namespace':namespace,'checks':[],'retained_volumes':[],'cleanup':'INCOMPLETE'}
    def create(obj):
        obj['metadata'].setdefault('labels',{})['pcloud.io/test-run']=run_id
        kube(config,'create','-f','-',data=yaml.safe_dump(obj))
        actual=get(config,obj['kind'],obj['metadata']['name'],obj['metadata'].get('namespace'))
        owned(actual,run_id);inventory[(obj['kind'],obj['metadata']['name'])]=actual['metadata']['uid']
        if obj['kind']=='PersistentVolumeClaim':claim_uids.add(actual['metadata']['uid'])
        return actual
    def remove(kind,name):
        obj=get(config,kind,name,namespace,optional=True)
        if obj:
            owned(obj,run_id,inventory[(kind,name)])
            kube(config,'delete',kind,name,'-n',namespace,'--wait=true','--timeout=60s')
    def ready(name='reader'):
        kube(config,'wait','--for=condition=Ready','pod/'+name,'-n',namespace,'--timeout=180s')
    def read_marker():return kube(config,'exec','-n',namespace,'reader','--','cat','/data/marker').strip()
    try:
        if local:watch.start()
        ns_uid=create(ns)['metadata']['uid'];create(pvc)
        require(get(config,'pvc','data',namespace)['status']['phase']=='Pending','Binding must wait for a consumer')
        create(pod);ready();bound=get(config,'pvc','data',namespace);pv_name=bound['spec']['volumeName'];pv_names.add(pv_name)
        pv=get(config,'pv',pv_name);pv_guard(pv,config,namespace,claim_uids,run_id)
        require(pv['spec']['persistentVolumeReclaimPolicy']=='Retain','Unexpected reclaim policy')
        selected=get(config,'pod','reader',namespace)['spec']['nodeName']
        if local:
            require(selected in [n['name'] for n in config['nodes']],'Consumer scheduled outside reviewed workers')
            terms=pv['spec']['nodeAffinity']['required']['nodeSelectorTerms']
            require(len(terms)==1 and any(e['key']=='kubernetes.io/hostname' and e['operator']=='In' and e['values']==[selected]
                for e in terms[0]['matchExpressions']),'PV lacks selected-node affinity')
        marker=uuid.uuid4().hex
        kube(config,'exec','-n',namespace,'reader','--','sh','-c','printf "%s" "$1" > /data/marker','sh',marker)
        remove('Pod','reader');create(copy.deepcopy(pod));ready()
        require(read_marker()==marker,'Pod replacement lost data');report['checks']+=['delayed-binding','non-root-write','pod-recreation']
        if local:
            active=json.loads(kube(config,'get','pvc','-A','-o','json'))['items']
            require(all(p['metadata']['namespace']==namespace and p['metadata'].get('labels',{}).get('pcloud.io/test-run')==run_id
                for p in active if p['spec'].get('storageClassName')==cls),'A foreign consumer appeared; controller restart withheld')
            controller_uid=get(config,'deployment','local-path-provisioner',NAMESPACE)['metadata']['uid']
            controller_stopped=True;controller_scale(config,0,controller_uid)
            require(read_marker()==marker,'Stopping provisioner interrupted existing data')
            controller_scale(config,1,controller_uid);controller_stopped=False
            require(read_marker()==marker,'Controller recovery lost data')
            extra,extra_pod=test_resources(config,namespace,claim='after-restart');extra_pod['metadata']['name']='after-restart'
            create(extra);create(extra_pod);ready('after-restart')
            report['checks']+=['controller-stop-data-readable','controller-restore','new-provisioning-after-restart']
            # The PV must forbid scheduling on a different healthy worker.
            others=[n['metadata']['name'] for n in get(config,'nodes')['items'] if n['metadata']['name']!=selected
                and not any(k.startswith('node-role.kubernetes.io/') for k in n['metadata'].get('labels',{}))
                and not n['spec'].get('unschedulable') and not n['spec'].get('taints')
                and any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions'])]
            require(others,'Affinity rejection needs a second healthy schedulable worker')
            wrong=copy.deepcopy(pod);wrong['metadata']['name']='wrong-node';wrong['spec'].pop('affinity',None)
            wrong['spec']['nodeSelector']={'kubernetes.io/hostname':others[0]};actual=create(wrong)
            end=time.monotonic()+60;rejected=False
            while time.monotonic()<end:
                require(not get(config,'pod','wrong-node',namespace)['status'].get('containerStatuses'),'Wrong-node consumer ran')
                events=json.loads(kube(config,'get','events','-n',namespace,'--field-selector','involvedObject.uid='+actual['metadata']['uid'],'-o','json'))['items']
                evidence=volume_affinity_rejection(events,actual['metadata']['uid'])
                if evidence:
                    report['affinity_rejection']={'pod_uid':actual['metadata']['uid'],'worker':others[0],
                        'volume_worker':selected,'reason':evidence['reason'],'message':evidence['message']}
                    rejected=True;break
                time.sleep(1)
            require(rejected,'Scheduler did not prove volume node affinity rejection');remove('Pod','wrong-node')
            report['checks'].append('incompatible-worker-rejected')
        remove('Pod','reader');remove('PersistentVolumeClaim','data')
        pv=wait_phase(config,'pv',pv_name,'Released');pv_guard(pv,config,namespace,claim_uids,run_id)
        if local or args.allow_volume_admin:
            # Atomic UID and original claim tests protect the retained volume rebind.
            kube(config,'patch','pv',pv_name,'--type=json','-p',json.dumps([
                {'op':'test','path':'/metadata/uid','value':pv['metadata']['uid']},
                {'op':'test','path':'/spec/claimRef/uid','value':pv['spec']['claimRef']['uid']},
                {'op':'remove','path':'/spec/claimRef'}]))
            rebound,_=test_resources(config,namespace,volume=pv_name);create(rebound);create(copy.deepcopy(pod));ready()
            require(read_marker()==marker,'Retained-volume rebind lost data');report['checks'].append('retained-volume-rebind')
            report['status']='PASS'
        else:
            report['pending_checks']=['operator-authorized-retained-volume-rebind','operator-owned-retained-data-cleanup']
    except Exception as error:
        report['status']='FAIL'
        report['failure']={'type':type(error).__name__,'message':str(error) if isinstance(error,CheckRejected) else 'Details withheld to protect credentials'}
        raise
    finally:
        try:
            if controller_stopped:controller_scale(config,1,controller_uid)
            ns_actual=get(config,'namespace',namespace,optional=True)
            if ns_actual:
                require(ns_uid is not None,'Namespace creation did not record identity; cleanup withheld')
                owned(ns_actual,run_id,ns_uid)
                # Discover volumes even if a wait/bind failed before its name was recorded.
                volumes=[v for v in get(config,'pv')['items'] if v['spec'].get('claimRef',{}).get('namespace')==namespace or v['metadata']['name'] in pv_names]
                report['observed_volumes']=[{'name':v['metadata']['name'],'uid':v['metadata']['uid']} for v in volumes]
                for v in volumes:pv_guard(v,config,namespace,claim_uids,run_id);pv_names.add(v['metadata']['name'])
                for kind in ('Pod','PersistentVolumeClaim'):
                    for name in [name for k,name in inventory if k==kind]:remove(kind,name)
                for v in volumes:
                    name=v['metadata']['name'];v=wait_phase(config,'pv',name,'Released');pv_guard(v,config,namespace,claim_uids,run_id)
                    if not local:
                        report['retained_volumes'].append({'name':name,'uid':v['metadata']['uid'],'operator_cleanup_required':True});continue
                    # Only the accepted local provider may dispose of synthetic data.
                    kube(config,'patch','pv',name,'--type=json','-p',json.dumps([
                        {'op':'test','path':'/metadata/uid','value':v['metadata']['uid']},
                        {'op':'test','path':'/spec/claimRef/uid','value':v['spec']['claimRef']['uid']},
                        {'op':'replace','path':'/spec/persistentVolumeReclaimPolicy','value':'Delete'}]))
                    kube(config,'wait','--for=delete','pv/'+name,'--timeout=120s')
                if local and volumes:
                    basenames=[PurePosixPath(v['spec']['local']['path']).name for v in volumes]
                    for node in config['nodes']:
                        command='python3 - '+shlex.quote(config['storage_root'])+' '+shlex.quote(json.dumps(basenames))
                        observed=json.loads(run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',node['ssh'],command],
                            (ROOT/'scripts/node-report.py').read_text()))
                        require(not observed['paths_remaining'],'Synthetic data still exists on a reviewed worker')
                require(not get(config,'pods',namespace=namespace)['items'] and not get(config,'pvc',namespace=namespace)['items'],'Test scope is not empty')
                namespace_empty(config,namespace)
                owned(get(config,'namespace',namespace),run_id,ns_uid)
                kube(config,'delete','namespace',namespace,'--wait=true','--timeout=60s')
            if local:report['generated_helpers']=helper_evidence(watch.finish(),config,pv_names)
            report['cleanup']='INCOMPLETE' if report['retained_volumes'] else 'PASS'
            if report['retained_volumes']:report['status']='INCOMPLETE'
        except Exception as error:
            report['status']='FAIL';report['cleanup']='FAIL'
            report['cleanup_failure']={'type':type(error).__name__,'message':str(error) if isinstance(error,CheckRejected) else 'Details withheld to protect credentials'}
            raise
        finally:
            if watch.process:watch.finish()
            report['created_objects']=[{'kind':k,'name':name,'uid':uid} for (k,name),uid in inventory.items()]
            if args.output:args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(report,indent=2))
    return 0 if report['status']=='PASS' else 3


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['render','capability','preflight','live','smoke','digest','uninstall-check'])
    p.add_argument('--site',type=Path,required=True)
    p.add_argument('--output',type=Path)
    p.add_argument('--allow-cluster-changes',action='store_true')
    p.add_argument('--allow-controller-restart',action='store_true')
    p.add_argument('--allow-volume-admin',action='store_true')
    p.add_argument('--review',type=Path,help='Explicit review JSON for dispatcher runs; contains revision and approved_digest')
    p.add_argument('--approved-digest');p.add_argument('--revision')
    args=p.parse_args();config=load(args.site)
    if args.review:
        review=json.loads(args.review.read_text(encoding='utf-8'))
        require(set(review)=={'revision','approved_digest'},'Invalid review file')
        require(not args.revision and not args.approved_digest,'Ambiguous review sources')
        args.revision=review['revision'];args.approved_digest=review['approved_digest']
    if args.command=='smoke':return smoke(config,args)
    if args.command=='render':result=manifest(config)
    elif args.command=='digest':result=digest(config)+'\n'
    elif args.command=='capability':result=json.dumps(capability(config),indent=2)+'\n'
    elif args.command=='uninstall-check':result=json.dumps(uninstall_check(config),indent=2)+'\n'
    else:result=json.dumps(preflight(config,args.command=='live'),indent=2)+'\n'
    if args.output:args.output.write_text(result,encoding='utf-8')
    else:print(result,end='')
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except CheckRejected as error:
        print('FAIL: '+str(error),file=sys.stderr)
        sys.exit(1)
    except (ValueError,KeyError,TypeError,jsonschema.ValidationError,subprocess.SubprocessError,OSError):
        print('FAIL: configuration, prerequisite, ownership or command check rejected; no secret output logged',file=sys.stderr)
        sys.exit(1)
