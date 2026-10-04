"""In-memory API lifecycle for safety regression tests; never live evidence."""
import copy
import json
import yaml


class Cluster:
    aliases={'pvc':'PersistentVolumeClaim','pv':'PersistentVolume','pod':'Pod','namespace':'Namespace',
             'deployment':'Deployment','nodes':'Node','pods':'Pod'}

    def __init__(self,storage,config):
        self.storage=storage;self.config=config;self.objects={};self.calls=[];self.counter=0;self.markers={};self.helpers=[]
        deploy=next(d for d in storage.render(config) if d['kind']=='Deployment')
        self.put(deploy)
        for name in (config['nodes'][0]['name'],'other-worker'):
            self.put({'apiVersion':'v1','kind':'Node','metadata':{'name':name,'labels':{'kubernetes.io/hostname':name}},
                'spec':{},'status':{'conditions':[{'type':'Ready','status':'True'}]}})

    def put(self,obj):
        obj=copy.deepcopy(obj);self.counter+=1;obj['metadata']['uid']='uid-'+str(self.counter)
        key=(obj['kind'],obj['metadata'].get('namespace'),obj['metadata']['name']);self.objects[key]=obj
        return obj

    def get(self,config,kind,name=None,namespace=None,optional=False):
        kind=self.aliases.get(kind,kind)
        if name:
            obj=self.objects.get((kind,namespace,name))
            if obj is None and not optional:raise ValueError('Fixture missing object')
            return copy.deepcopy(obj)
        return {'metadata':{'resourceVersion':'1'},'items':[copy.deepcopy(v) for (k,ns,_),v in self.objects.items()
            if k==kind and (namespace is None or namespace==ns)]}

    def bind(self,pod):
        if pod['metadata']['name']=='wrong-node':pod['status']={'phase':'Pending'};return
        ref=pod['spec']['volumes'][0]['persistentVolumeClaim']['claimName'];ns=pod['metadata']['namespace']
        pvc=self.objects[('PersistentVolumeClaim',ns,ref)]
        name=pvc['spec'].get('volumeName') or 'pvc-'+pvc['metadata']['uid']
        if ('PersistentVolume',None,name) not in self.objects:
            self.put({'apiVersion':'v1','kind':'PersistentVolume','metadata':{'name':name,'annotations':{'pv.kubernetes.io/provisioned-by':self.storage.PROVISIONER}},
                'spec':{'storageClassName':self.storage.CLASS,'persistentVolumeReclaimPolicy':'Retain',
                    'local':{'path':self.config['storage_root']+'/'+name+'_'+ns+'_'+ref},
                    'nodeAffinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':[self.config['nodes'][0]['name']]}]}]}}}})
            helper=yaml.safe_load(next(d for d in self.storage.render(self.config) if d['kind']=='ConfigMap')['data']['helperPod.yaml'])
            helper['metadata']['uid']='helper-'+name
            helper['spec'].update(nodeName=self.config['nodes'][0]['name'],volumes=[{'name':'data','hostPath':{'path':self.config['storage_root']}}])
            helper['spec']['containers'][0]['env']=[{'name':'VOL_DIR','value':self.config['storage_root']+'/'+name+'_'+ns+'_'+ref}]
            self.helpers.append({'type':'ADDED','object':helper})
        pv=self.objects[('PersistentVolume',None,name)]
        pv['spec']['claimRef']={'namespace':ns,'name':ref,'uid':pvc['metadata']['uid']};pv['status']={'phase':'Bound'}
        pvc['spec']['volumeName']=name;pvc['status']={'phase':'Bound'}
        pod['spec']['nodeName']=self.config['nodes'][0]['name'];pod['status']={'phase':'Running'}

    def kube(self,config,*args,data=None):
        self.calls.append(args)
        if args[0]=='create':
            obj=self.put(yaml.safe_load(data))
            if obj['kind']=='PersistentVolumeClaim':obj['status']={'phase':'Pending'}
            if obj['kind']=='Pod':self.bind(obj)
        elif args[0]=='get':
            if any(a.startswith('-o=jsonpath=') for a in args):return ''
            if args[1]=='events':return json.dumps({'items':[{'reason':'FailedScheduling','message':'volume node affinity conflict'}]})
            if args[1]=='pvc':return json.dumps(self.get(config,'PersistentVolumeClaim'))
            raise AssertionError('Unexpected fixture GET')
        elif args[0]=='exec':
            ns=args[args.index('-n')+1];pod=self.objects[('Pod',ns,'reader')]
            claim=pod['spec']['volumes'][0]['persistentVolumeClaim']['claimName']
            volume=self.objects[('PersistentVolumeClaim',ns,claim)]['spec']['volumeName']
            if 'printf' in ' '.join(args):self.markers[volume]=args[-1]
            else:return self.markers[volume]
        elif args[0]=='delete':
            kind=self.aliases.get(args[1],args[1]);name=args[2];ns=args[args.index('-n')+1] if '-n' in args else None
            obj=self.objects.pop((kind,ns,name))
            if kind=='PersistentVolumeClaim' and 'volumeName' in obj['spec']:
                self.objects[('PersistentVolume',None,obj['spec']['volumeName'])]['status']={'phase':'Released'}
        elif args[0]=='patch':
            obj=self.objects[('PersistentVolume',None,args[2])]
            for op in json.loads(args[args.index('-p')+1]):
                if op['op']=='test':
                    current=obj
                    for part in op['path'].strip('/').split('/'):current=current[part]
                    assert current==op['value']
                elif op['op']=='remove':obj['spec'].pop('claimRef');obj['status']={'phase':'Available'}
                elif op['op']=='replace':
                    assert obj['status']['phase']=='Released'
                    self.objects.pop(('PersistentVolume',None,args[2]))
        elif args[0]=='scale':
            value=int(next(a for a in args if a.startswith('--replicas=')).split('=')[1])
            obj=self.objects[('Deployment',self.storage.NAMESPACE,'local-path-provisioner')]
            obj['spec']['replicas']=value
        elif args[0]=='api-resources':return 'pods\npersistentvolumeclaims\nconfigmaps\nsecrets\n'
        elif args[0] not in ('wait','rollout'):raise AssertionError('Unexpected fixture mutation')
        return ''

    def run(self,argv,data=None,timeout=60):
        assert argv[0]=='ssh'
        return json.dumps({'paths_remaining':[]})

    def watcher(self):
        cluster=self
        class Watch:
            process=None
            def __init__(self,config):pass
            def start(self):pass
            def finish(self):return copy.deepcopy(cluster.helpers)
        return Watch
