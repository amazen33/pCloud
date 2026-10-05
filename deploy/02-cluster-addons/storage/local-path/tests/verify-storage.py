#!/usr/bin/env python3
"""Offline storage checks, with optional real Kustomize/schema validation."""
import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import jsonschema
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import storage
from fake_cluster import Cluster


def fixture():return storage.load(ROOT/'site.example.json')


def validate_render(docs):
    sc=next(d for d in docs if d['kind']=='StorageClass')
    storage.require(sc['reclaimPolicy']=='Retain' and sc['volumeBindingMode']=='WaitForFirstConsumer','Unsafe storage lifecycle')
    storage.require(sc['metadata']['annotations']['storageclass.kubernetes.io/is-default-class']=='false','Default class forbidden')
    storage.require(sc['metadata']['annotations'].get('storageclass.beta.kubernetes.io/is-default-class','false')=='false','Beta default class forbidden')
    storage.require(sc['metadata']['annotations']['defaultVolumeType']=='local','Local volume type required')
    container=next(d for d in docs if d['kind']=='Deployment')['spec']['template']['spec']['containers'][0]
    storage.require(container['image'] in storage.images().values(),'Image is not locked')
    storage.require('--helper-image' in container['command'] and not any('unsafe' in a for a in container['command']),'Unsafe helper configuration')
    cm=next(d for d in docs if d['kind']=='ConfigMap')['data']
    mapping=json.loads(cm['config.json'])['nodePathMap']
    storage.require(mapping[0]['paths']==[],'Non-listed nodes may not provision')
    helper=yaml.safe_load(cm['helperPod.yaml'])
    storage.require(helper['spec']['containers'][0]['image'] in storage.images().values(),'Unpinned helper')
    return helper


def verify_render(config):
    with tempfile.TemporaryDirectory(prefix='pcloud-storage-render-') as folder:
        path=Path(folder)
        (path/'resources.yaml').write_text(storage.manifest(config),encoding='utf-8')
        (path/'helper.yaml').write_text(yaml.safe_dump(validate_render(storage.render(config))),encoding='utf-8')
        (path/'smoke.yaml').write_text(yaml.safe_dump_all(storage.test_resources(config,'pcloud-storage-test-schema')),encoding='utf-8')
        (path/'kustomization.yaml').write_text('apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n- resources.yaml\n',encoding='utf-8')
        result=storage.run(['kubectl','kustomize',str(path)])
        (path/'rendered.yaml').write_text(result,encoding='utf-8')
        storage.run(['kubeconform','-strict','-summary','-kubernetes-version','1.35.0',str(path/'rendered.yaml'),str(path/'helper.yaml'),str(path/'smoke.yaml')])


def verify_tool_versions():
    kubectl=json.loads(storage.run(['kubectl','version','--client','-o','json']))
    storage.require(kubectl['clientVersion']['gitVersion']=='v1.35.0','kubectl must be the reviewed v1.35.0')
    storage.require(storage.run(['kubeconform','-v']).strip() in ('v0.7.0','0.7.0'),'kubeconform must be v0.7.0')


def validate_consumer(pod):
    storage.require(pod['spec']['automountServiceAccountToken'] is False,'Consumer must not mount credentials')
    storage.require(pod['spec']['securityContext']==storage.security(),'Consumer must run as the reviewed non-root identity')
    c=pod['spec']['containers'][0]
    storage.require(c['securityContext']=={'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},'Permissive consumer security')
    storage.require(c['image'] in storage.images().values(),'Unpinned consumer')


class StorageChecks(unittest.TestCase):
    def test_upstream_digest(self):
        for filename,entry in json.loads((ROOT/'UPSTREAM.json').read_text()).items():
            self.assertEqual(hashlib.sha256((ROOT/'upstream'/filename).read_bytes()).hexdigest(),entry['sha256'])

    def test_image_provenance_rejections(self):
        records=json.loads((ROOT/'images.lock.json').read_text());source=next(iter(records))
        storage.images(records)
        for key,value in [('source','docker.io/foreign/image:1.0.0'),('image','docker.io/foreign/image@sha256:'+'a'*64),
            ('manifest_sha256','sha256:'+'a'*64),('manifest_url','https://example.invalid/manifest')]:
            altered=copy.deepcopy(records);altered[source][key]=value
            with self.assertRaises(ValueError):storage.images(altered)

    def test_render_and_capability(self):
        config=fixture();docs=storage.render(config)
        self.assertEqual(len(docs),9)
        validate_render(docs)
        jsonschema.validate(storage.capability(config),json.loads((ROOT/'capability.schema.json').read_text()))
        validate_consumer(storage.test_resources(config,'example')[1])

    def test_consumer_security_negatives(self):
        for change in ('root','token','escalation','writable','capability','image'):
            with self.subTest(change=change):
                pod=storage.test_resources(fixture(),'example')[1];c=pod['spec']['containers'][0]
                if change=='root':pod['spec']['securityContext']['runAsUser']=0
                if change=='token':pod['spec']['automountServiceAccountToken']=True
                if change=='escalation':c['securityContext']['allowPrivilegeEscalation']=True
                if change=='writable':c['securityContext']['readOnlyRootFilesystem']=False
                if change=='capability':c['securityContext']['capabilities']['drop']=[]
                if change=='image':c['image']='busybox:latest'
                with self.assertRaises(ValueError):validate_consumer(pod)

    def test_security_negatives(self):
        for change in ('default','beta-default','delete','immediate','image','helper-fallback','nonlisted','helper-image','unsafe'):
            with self.subTest(change=change):
                docs=storage.render(fixture());sc=next(d for d in docs if d['kind']=='StorageClass')
                container=next(d for d in docs if d['kind']=='Deployment')['spec']['template']['spec']['containers'][0]
                cm=next(d for d in docs if d['kind']=='ConfigMap')['data']
                if change=='default':sc['metadata']['annotations']['storageclass.kubernetes.io/is-default-class']='true'
                if change=='beta-default':sc['metadata']['annotations']['storageclass.beta.kubernetes.io/is-default-class']='true'
                if change=='delete':sc['reclaimPolicy']='Delete'
                if change=='immediate':sc['volumeBindingMode']='Immediate'
                if change=='image':container['image']='rancher/local-path-provisioner:latest'
                if change=='helper-fallback':container['command'].remove('--helper-image')
                if change=='nonlisted':cm['config.json']=json.dumps({'nodePathMap':[{'node':'DEFAULT_PATH_FOR_NON_LISTED_NODES','paths':['/tmp']} ]})
                if change=='helper-image':cm['helperPod.yaml']=cm['helperPod.yaml'].replace('sha256:', 'sha257:')
                if change=='unsafe':container['command'].append('--allow-unsafe-helper-pod-template')
                with self.assertRaises(ValueError):validate_render(docs)

    def test_config_rejections(self):
        cases=[('profile','production'),('storage_root','/'),('storage_root','/var/lib/rancher'),('storage_root','/var/lib/pcloud/storage/app/../etc'),('capacity',None)]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'site.json'
            for key,value in cases:
                with self.subTest(key=key,value=value):
                    c=fixture();c[key]=value;path.write_text(json.dumps(c))
                    with self.assertRaises((ValueError,jsonschema.ValidationError)):storage.load(path)
            c=fixture();c['nodes']*=2;path.write_text(json.dumps(c))
            with self.assertRaises(ValueError):storage.load(path)

    def test_node_mount_and_capacity_guards(self):
        c=fixture();n=c['nodes'][0]
        node={'metadata':{'labels':{'kubernetes.io/hostname':n['name']}},'status':{'conditions':[{'type':'Ready','status':'True'},{'type':'DiskPressure','status':'False'}]}}
        report={'root':c['storage_root'],'symlink':False,'target':c['storage_root'],'uuid':n['filesystem_uuid'],'device':3,'os_device':1,'rke2_device':2,
            'marker':'pcloud-storage-v1:'+n['filesystem_uuid'],'free_bytes':10**11,'total_bytes':2*10**11,'free_inodes':9000,'total_inodes':10000,
            'root_uid':0,'root_mode':0o755,'marker_uid':0,'marker_mode':0o644}
        storage.validate_node(c,n,node,report)
        for key,value in [('symlink',True),('target','/'),('uuid','wrong'),('marker',''),('device',1),('device',2),('free_bytes',1),('free_inodes',1),
            ('total_inodes',0),('root_uid',1000),('root_mode',0o777),('marker_uid',1000),('marker_mode',0o666)]:
            with self.subTest(key=key):
                r={**report,key:value}
                with self.assertRaises(ValueError):storage.validate_node(c,n,node,r)
        node['metadata']['labels']['node-role.kubernetes.io/control-plane']=''
        with self.assertRaises(ValueError):storage.validate_node(c,n,node,report)

    def test_smoke_refuses_mutation_without_review(self):
        c=fixture()
        for consent,digest,revision in [(False,storage.digest(c),'a'*40),(True,'b'*64,'a'*40),(True,storage.digest(c),None)]:
            args=argparse.Namespace(allow_cluster_changes=consent,approved_digest=digest,revision=revision)
            with mock.patch.object(storage,'kube',side_effect=AssertionError('No cluster call allowed')):
                with self.assertRaises(ValueError):storage.smoke(c,args)

    def test_command_failure_is_not_pass(self):
        with mock.patch('subprocess.run',return_value=subprocess.CompletedProcess(['kubectl'],1,'sensitive-output','sensitive-credential-value')):
            with self.assertRaises(ValueError) as error:storage.run(['kubectl'])
        self.assertNotIn('sensitive-credential-value',str(error.exception));self.assertNotIn('sensitive-output',str(error.exception))

    def test_isolated_controller_consent_required_before_cluster_access(self):
        c=fixture();args=argparse.Namespace(allow_cluster_changes=True,approved_digest=storage.digest(c),revision='a'*40,allow_controller_restart=False)
        with mock.patch.object(storage,'kube',side_effect=AssertionError('No cluster call allowed')):
            with self.assertRaises(ValueError):storage.smoke(c,args)

    def test_cleanup_rejects_foreign_identity(self):
        c=fixture();pv={'metadata':{'name':'pvc-example','labels':{},'annotations':{'pv.kubernetes.io/provisioned-by':storage.PROVISIONER}},
            'spec':{'storageClassName':storage.CLASS,'local':{'path':c['storage_root']+'/pvc-example_test_data'},'claimRef':{'namespace':'test','uid':'own','name':'data'}}}
        storage.pv_guard(pv,c,'test',{'own'},'run')
        for field,value in [('namespace','application'),('uid','foreign')]:
            altered=copy.deepcopy(pv);altered['spec']['claimRef'][field]=value
            with self.assertRaises(ValueError):storage.pv_guard(altered,c,'test',{'own'},'run')
        pv['metadata']['labels']['pcloud.io/test-run']='other'
        with self.assertRaises(ValueError):storage.pv_guard(pv,c,'test',{'own'},'run')
        with self.assertRaises(ValueError):storage.owned({'metadata':{'uid':'new','labels':{'pcloud.io/test-run':'run'}}},'run','old')

    def test_actual_helper_evidence_bound_to_volume_and_mount(self):
        c=fixture();pod=yaml.safe_load(next(d for d in storage.render(c) if d['kind']=='ConfigMap')['data']['helperPod.yaml'])
        pod['metadata']['uid']='helper';pod['spec'].update(nodeName=c['nodes'][0]['name'],volumes=[{'name':'data','hostPath':{'path':c['storage_root']+'/'}}])
        pod['spec']['containers'][0]['env']=[{'name':'VOL_DIR','value':c['storage_root']+'/pvc-own_test_data'}]
        event={'type':'ADDED','object':pod}
        self.assertFalse(storage.helper_evidence([event],c,{'pvc-own'})[0]['restricted_compliant'])
        with self.assertRaises(ValueError):storage.helper_evidence([event],c,{'pvc-foreign'})
        pod['spec']['volumes'][0]['hostPath']['path']='/'
        with self.assertRaises(ValueError):storage.helper_evidence([event],c,{'pvc-own'})

    def test_standalone_copy_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'package';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('__pycache__','site.json','reports','rendered'))
            result=subprocess.run([sys.executable,str(target/'storage.py'),'render','--site',str(target/'site.example.json')],cwd=folder,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(len(list(yaml.safe_load_all(result.stdout))),9)

    def test_smoke_lifecycle_and_cleanup_with_fake_api(self):
        c=fixture();cluster=Cluster(storage,c)
        args=argparse.Namespace(allow_cluster_changes=True,approved_digest=storage.digest(c),revision='a'*40,
            allow_controller_restart=True,allow_volume_admin=False,output=None)
        with mock.patch.object(storage,'preflight'),mock.patch.object(storage,'kube',side_effect=cluster.kube),\
             mock.patch.object(storage,'get',side_effect=cluster.get),mock.patch.object(storage,'run',side_effect=cluster.run),\
             mock.patch.object(storage,'HelperWatch',cluster.watcher()),mock.patch('builtins.print'):
            self.assertEqual(storage.smoke(c,args),0)
        self.assertFalse([key for key in cluster.objects if key[0] in ('Namespace','Pod','PersistentVolumeClaim','PersistentVolume')])
        self.assertEqual(cluster.objects[('Deployment',storage.NAMESPACE,'local-path-provisioner')]['spec']['replicas'],1)
        self.assertEqual(len([a for a in cluster.calls if a[0]=='scale']),2)
        patches=[json.loads(a[a.index('-p')+1]) for a in cluster.calls if a[0]=='patch']
        self.assertTrue(all(p[0]['op']=='test' and p[0]['path']=='/metadata/uid' for p in patches))

    def test_foreign_pending_claim_prevents_controller_smoke(self):
        c=fixture();cluster=Cluster(storage,c)
        claim=storage.test_resources(c,'application')[0];cluster.put(claim)
        args=argparse.Namespace(allow_cluster_changes=True,approved_digest=storage.digest(c),revision='a'*40,
            allow_controller_restart=True,allow_volume_admin=False,output=None)
        with mock.patch.object(storage,'preflight'),mock.patch.object(storage,'kube',side_effect=cluster.kube),mock.patch.object(storage,'get',side_effect=cluster.get):
            with self.assertRaises(ValueError):storage.smoke(c,args)
        self.assertTrue(all(a[0]=='get' for a in cluster.calls))

    def test_uninstall_refuses_pending_claims_and_retained_pvs(self):
        c=fixture()
        for volumes,claims in [([{'spec':{'storageClassName':storage.CLASS}}],[]),([],[{'spec':{'storageClassName':storage.CLASS}}])]:
            with mock.patch.object(storage,'preflight'),mock.patch.object(storage,'get',return_value={'items':volumes}),\
                 mock.patch.object(storage,'kube',return_value=json.dumps({'items':claims})) as api:
                with self.assertRaises(ValueError):storage.uninstall_check(c)
                self.assertTrue(all(call.args[1]=='get' for call in api.call_args_list))
        with mock.patch.object(storage,'preflight'),mock.patch.object(storage,'get',return_value={'items':[]}),\
             mock.patch.object(storage,'kube',return_value=json.dumps({'items':[]})):
            self.assertTrue(storage.uninstall_check(c)['zero_consumers'])

    def test_namespace_cleanup_refuses_unknown_objects_including_crds(self):
        with mock.patch.object(storage,'kube',side_effect=['widgets.example.io\n','Widget|foreign\n']) as api:
            with self.assertRaises(ValueError):storage.namespace_empty(fixture(),'test')
        self.assertTrue(all(c.args[1] in ('get','api-resources') for c in api.call_args_list))
        with mock.patch.object(storage,'kube',side_effect=['configmaps\nserviceaccounts\n','ConfigMap|kube-root-ca.crt\n','ServiceAccount|default\n']):
            storage.namespace_empty(fixture(),'test')

    def test_failure_cleanup_never_hides_failure(self):
        c=fixture();cluster=Cluster(storage,c)
        args=argparse.Namespace(allow_cluster_changes=True,approved_digest=storage.digest(c),revision='a'*40,
            allow_controller_restart=True,allow_volume_admin=False,output=None)
        normal=cluster.kube
        def failed(config,*argv,data=None):
            if argv[0]=='exec' and 'cat' in argv:return 'wrong-marker'
            return normal(config,*argv,data=data)
        with mock.patch.object(storage,'preflight'),mock.patch.object(storage,'kube',side_effect=failed),\
             mock.patch.object(storage,'get',side_effect=cluster.get),mock.patch.object(storage,'run',side_effect=cluster.run),\
             mock.patch.object(storage,'HelperWatch',cluster.watcher()),mock.patch('builtins.print') as output:
            with self.assertRaises(ValueError):storage.smoke(c,args)
            self.assertEqual(json.loads(output.call_args.args[0])['status'],'FAIL')
        self.assertFalse([key for key in cluster.objects if key[0] in ('Namespace','Pod','PersistentVolumeClaim','PersistentVolume')])

    def test_failed_render_stops_schema_validation(self):
        with mock.patch.object(storage,'run',side_effect=ValueError('render failed')) as tool:
            with self.assertRaises(ValueError):verify_render(fixture())
        self.assertEqual(tool.call_count,1)

    def test_failed_schema_validation_fails(self):
        with mock.patch.object(storage,'run',side_effect=[storage.manifest(fixture()),ValueError('schema failed')]):
            with self.assertRaises(ValueError):verify_render(fixture())

    def test_missing_render_tool_and_wrong_version_fail(self):
        with mock.patch.object(storage,'run',side_effect=FileNotFoundError('kubectl')):
            with self.assertRaises(FileNotFoundError):verify_tool_versions()
        with mock.patch.object(storage,'run',return_value=json.dumps({'clientVersion':{'gitVersion':'v1.34.0'}})):
            with self.assertRaises(ValueError):verify_tool_versions()

    def test_preflight_rejects_conflicting_class_before_node_access(self):
        c=fixture();sc=next(d for d in storage.render(c) if d['kind']=='StorageClass')
        sc['metadata']['labels']['pcloud.io/package']='another-owner'
        with mock.patch.object(storage,'kube',return_value=json.dumps({'serverVersion':{'gitVersion':'v1.35.7'}})),\
             mock.patch.object(storage,'get',return_value=sc),mock.patch.object(storage,'run',side_effect=AssertionError('No SSH')):
            with self.assertRaises(ValueError):storage.preflight(c)

    def test_installed_preflight_accepts_api_defaults_and_rejects_real_drift(self):
        c=fixture();c['host_capacity']['observed_unix_seconds']=int(storage.time.time())
        docs=storage.render(c)
        objects={(d['kind'].lower(),d['metadata']['name']):copy.deepcopy(d) for d in docs}
        deploy=objects[('deployment','local-path-provisioner')]
        deploy['status']={'availableReplicas':1}
        pod=deploy['spec']['template']['spec']
        # Kubernetes adds these fields even when the submitted YAML omits them.
        ref=next(e['valueFrom']['fieldRef'] for e in pod['containers'][0]['env'] if e['name']=='POD_NAMESPACE')
        ref['apiVersion']='v1'
        pod['volumes'][0]['configMap']['defaultMode']=0o644
        node=c['nodes'][0]
        report={'root':c['storage_root'],'symlink':False,'target':c['storage_root'],'uuid':node['filesystem_uuid'],
            'device':3,'os_device':1,'rke2_device':2,'marker':'pcloud-storage-v1:'+node['filesystem_uuid'],
            'free_bytes':10**11,'total_bytes':2*10**11,'free_inodes':9000,'total_inodes':10000,
            'root_uid':0,'root_mode':0o755,'marker_uid':0,'marker_mode':0o644}
        def api(config,kind,name=None,namespace=None,optional=False):
            if kind=='nodes':return {'items':[{'metadata':{'name':node['name'],'labels':{'kubernetes.io/hostname':node['name']}},
                'status':{'conditions':[{'type':'Ready','status':'True'},{'type':'DiskPressure','status':'False'}]}}]}
            if kind=='deployments':return {'items':[deploy]}
            if kind in ('statefulsets','daemonsets','jobs','cronjobs'):return {'items':[]}
            return objects[(kind.lower(),name)]
        with mock.patch.object(storage,'kube',return_value=json.dumps({'serverVersion':{'gitVersion':'v1.35.7'}})),\
             mock.patch.object(storage,'get',side_effect=api),mock.patch.object(storage,'run',return_value=json.dumps(report)):
            self.assertEqual(storage.preflight(c,installed=True)['status'],'PASS')
            for change in ('api-version','namespace-reference','file-mode','mount-write','extra-env'):
                with self.subTest(change=change):
                    altered=copy.deepcopy(deploy);objects[('deployment','local-path-provisioner')]=altered
                    spec=altered['spec']['template']['spec']
                    env=spec['containers'][0]['env']
                    changed_ref=next(e['valueFrom']['fieldRef'] for e in env if e['name']=='POD_NAMESPACE')
                    if change=='api-version':changed_ref['apiVersion']='v2'
                    if change=='namespace-reference':changed_ref['fieldPath']='metadata.name'
                    if change=='file-mode':spec['volumes'][0]['configMap']['defaultMode']=0o666
                    if change=='mount-write':spec['containers'][0]['volumeMounts'][0]['readOnly']=False
                    if change=='extra-env':env.append({'name':'UNREVIEWED','value':'true'})
                    with self.assertRaises(ValueError):storage.preflight(c,installed=True)
            objects[('deployment','local-path-provisioner')]=deploy

    def test_supplied_profile_has_no_install_resources(self):
        c={'profile':'supplied-storage','context':'example','supplied_class':'existing','supplied_provisioner':'example.csi','resources':fixture()['resources']}
        self.assertEqual(storage.render(c),[])
        self.assertNotEqual(storage.digest(c),storage.digest({**c,'context':'different'}))

    def test_helper_shell_guards(self):
        bash='C:/Program Files/Git/bin/bash.exe' if os.name=='nt' and Path('C:/Program Files/Git/bin/bash.exe').exists() else shutil.which('bash')
        if not bash:self.skipTest('POSIX shell unavailable for real helper-script execution')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'disk';root.mkdir();outside=Path(folder)/'outside';outside.mkdir()
            def posix(p):
                s=p.as_posix()
                return '/'+s[0].lower()+s[2:] if os.name=='nt' else s
            config=fixture();script=storage.helper_script(config,True).replace('root='+config['storage_root'],'root='+posix(root))
            scriptpath=Path(folder)/'guard.sh';scriptpath.write_text(script,encoding='utf-8')
            volume=root/'pvc-example';volume.mkdir();(volume/'marker').write_text('preserve')
            env={**os.environ,'VOL_DIR':posix(volume),'VOL_MODE':'Filesystem'}
            def call():return subprocess.run([bash,posix(scriptpath)],env=env,capture_output=True).returncode
            self.assertNotEqual(call(),0);self.assertTrue(volume.exists())
            (root/'.pcloud-storage-ready').write_text('wrong')
            self.assertNotEqual(call(),0);self.assertTrue(volume.exists())
            (root/'.pcloud-storage-ready').write_text('pcloud-storage-v1:'+config['nodes'][0]['filesystem_uuid'])
            env['VOL_DIR']=posix(root)+'/../outside'
            self.assertNotEqual(call(),0);self.assertTrue(outside.exists())
            env['VOL_DIR']=posix(volume)
            result=subprocess.run([bash,'-x',posix(scriptpath)],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(volume.exists())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--render',action='store_true');args=parser.parse_args()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StorageChecks))
    if not result.wasSuccessful():sys.exit(1)
    if args.render:verify_tool_versions();verify_render(fixture());print('PASS: real Kustomize render and Kubernetes 1.35.0 strict schemas')
