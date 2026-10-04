#!/usr/bin/env python3
"""Optional Linux loopback runtime exercise; never a Kubernetes/lab acceptance."""
import argparse
import copy
import hashlib
import http.cookiejar
import http.server
import importlib.util
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import urllib.parse

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observability', ROOT/'observability.py')
obs = importlib.util.module_from_spec(spec); spec.loader.exec_module(obs)


def loopback(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r: return r.code
    except Exception: return 0


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--directory', type=Path, required=True); args = p.parse_args()
    if os.name != 'posix': raise ValueError('Linux required')
    bins = args.directory.resolve()
    if json.loads((bins/'image-lock.json').read_text()) != obs.read_json(ROOT/'images.lock.json'): raise ValueError('Runtime image provenance differs')
    c, b, f = [obs.read_json(ROOT/name) for name in ('site.example.json', 'backend-capability.example.json', 'backend-fragments.example.json')]
    c.update(purpose='conformance', namespace='pcloud-observe-test-local', gateway_host='localhost', endpoint='https://localhost:8443', query_timeout_seconds=120)
    b['namespace'] = c['namespace']
    processes = {}; logs = {}; receiver = None; received = []
    with tempfile.TemporaryDirectory(prefix='pcloud-m4-loopback-') as folder:
        root = Path(folder); data = root/'data'; config = root/'config'; secret = root/'secrets'
        for path in (data, config, secret): path.mkdir()
        if os.getuid() == 0: os.chown(root,10001,10001)
        cert = secret/'tls.crt'; key = secret/'tls.key'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),
            '-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'], check=True, capture_output=True)
        c['ca_file'] = str(cert); (secret/'ca.crt').write_bytes(cert.read_bytes()); (secret/'token').write_text('synthetic-receiver-token')
        (secret/'admin-password').write_text('synthetic-admin-password')
        (secret/'secret-key').write_text('synthetic-signing-key')
        grafana_start = root/'grafana-start.sh'; grafana_start.write_bytes((ROOT/'upstream/grafana-run.sh').read_bytes())
        (config/'grafana.ini').write_text('')
        for role in ('ingest','query','admin'):
            password = 'synthetic-'+role+'-password'
            result = subprocess.run(['openssl','passwd','-6','-stdin'],input=password+'\n', text=True,capture_output=True,check=True)
            (secret/(role+'.htpasswd')).write_text(role+':'+result.stdout.strip()+'\n')
            os.environ['PCLOUD_OBSERVE_'+role.upper()] = role+':'+password
        class Receiver(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length=int(self.headers.get('Content-Length','0'))
                if length>obs.MAX_BODY or self.headers.get('Authorization')!='Bearer synthetic-receiver-token': self.send_error(403);return
                received.append(json.loads(self.rfile.read(length)));self.send_response(200);self.end_headers()
            def log_message(self,*a): pass
        receiver = http.server.ThreadingHTTPServer(('127.0.0.1',17300),Receiver)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key);receiver.socket=tls.wrap_socket(receiver.socket,server_side=True)
        thread=threading.Thread(target=receiver.serve_forever,daemon=True);thread.start()
        cfg=obs.configs(c,b,f)
        def rewrite(value):
            if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
            if isinstance(value,list):return [rewrite(v) for v in value]
            if isinstance(value,str):
                value=value.replace(obs.MOUNT,str(data)).replace('/etc/pcloud',str(config)).replace('/etc/alert-secret',str(secret)).replace('/etc/mimir-rules', str(config/'mimir-rules'))
                value=value.replace('0.0.0.0:', '127.0.0.1:')
                if value == '0.0.0.0': value = '127.0.0.1'
                for component in ('loki','tempo','mimir','collector'):value=value.replace('http://'+component+':','http://127.0.0.1:').replace(component+':','127.0.0.1:')
                return value
            return value
        cfg=rewrite(cfg)
        for component in ('loki', 'tempo', 'mimir'):
            cfg[component]['server'].update(http_listen_address='127.0.0.1', grpc_listen_address='127.0.0.1')
        cfg['tempo']['server']['grpc_listen_port']=9097
        cfg['tempo']['ingester']['lifecycler'] = {'address': '127.0.0.1', 'ring': {'kvstore': {'store': 'inmemory'}, 'replication_factor': 1}}
        cfg['tempo']['metrics_generator']['ring']['instance_addr'] = '127.0.0.1'
        for name in ('compactor', 'store_gateway', 'alertmanager'):
            cfg['mimir'][name]['sharding_ring']['instance_addr'] = '127.0.0.1'
        cfg['mimir']['ruler']['ring']['instance_addr'] = '127.0.0.1'
        cfg['tempo']['distributor']['receivers']['otlp']['protocols']['http']['endpoint']='127.0.0.1:14318'
        cfg['collector']['exporters']['otlp_http/tempo']['endpoint']='http://127.0.0.1:14318'
        cfg['alertmanager']['receivers'][0]['webhook_configs'][0]['url']='https://localhost:17300/pcloud-test'
        for component in ('loki','tempo','mimir','grafana'): (data/component).mkdir()
        rules=config/'mimir-rules/anonymous';rules.mkdir(parents=True)
        (rules/'platform.yaml').write_text(yaml.safe_dump(cfg['rules']))
        for component in ('loki','tempo','mimir','collector','alertmanager'):(config/(component+'.yaml')).write_text(yaml.safe_dump(cfg[component],sort_keys=False))
        nginx=obs.gateway_config(c).replace('/etc/pcloud',str(config)).replace('/etc/tls',str(secret)).replace('/etc/access',str(secret))
        nginx=nginx.replace('listen 8443 ssl;', 'listen 127.0.0.1:8443 ssl;')
        nginx=nginx.replace('/tmp/nginx.pid', str(root/'nginx.pid')).replace('/tmp/client', str(root/'nginx-client')).replace('/tmp/proxy', str(root/'nginx-proxy'))
        for name in ('fastcgi', 'uwsgi', 'scgi'): nginx = nginx.replace('/tmp/' + name, str(root/('nginx-' + name)))
        for component in ('loki','tempo','mimir','collector','grafana'): nginx=nginx.replace(component+':','127.0.0.1:')
        (config/'nginx.conf').write_text(nginx)
        # Provisioning files from the rendered package; localhost URLs are the only change.
        provision=root/'provision';(provision/'datasources').mkdir(parents=True);(provision/'dashboards').mkdir()
        (provision/'datasources/sources.yaml').write_text(yaml.safe_dump(rewrite(cfg['datasources'])))
        dashboards=root/'dashboards';dashboards.mkdir();(dashboards/'platform.json').write_text(json.dumps(obs.dashboard()))
        (provision/'dashboards/provider.yaml').write_text(yaml.safe_dump({'apiVersion':1,'providers':[{
            'name':'pcloud-platform','options':{'path':str(dashboards)},'allowUiUpdates':False,'disableDeletion':True}]}))
        # Drop to the same numeric UID as the restricted Kubernetes workload.
        for path in root.rglob('*'):
            if os.getuid()==0:os.chown(path,10001,10001)
        def restrict():
            if os.getuid()==0:os.setgroups([]);os.setgid(10001);os.setuid(10001)
        commands={x:[str(bins/x/path),'-target=all','-config.file='+str(config/(x+'.yaml'))] for x,path in
                  [('loki','usr/bin/loki'),('tempo','tempo'),('mimir','bin/mimir')]}
        commands['mimir'][1] = '-target=all,alertmanager'
        commands['collector']=[str(bins/'collector/otelcol-contrib'),'--config='+str(config/'collector.yaml')]
        # SONAME aliases for early fetches that extracted regular library files only.
        compat = root/'library-aliases'; compat.mkdir()
        for name in ('libpcre2-8.so.0','libz.so.1'):
            choices=list((bins/'gateway').glob('**/'+name+'.*'))
            if len(choices)==1: (compat/name).symlink_to(choices[0])
        if os.getuid()==0:os.chown(compat,10001,10001)
        commands['gateway']=[str(bins/'gateway/lib/ld-musl-x86_64.so.1'),'--library-path',str(compat)+':'+str(bins/'gateway/lib')+':'+str(bins/'gateway/usr/lib'),
                             str(bins/'gateway/usr/sbin/nginx'),'-e','stderr','-c',str(config/'nginx.conf'),'-g','daemon off;']
        commands['grafana']=['bash', str(grafana_start)]
        env={'PATH': str(bins/'grafana/usr/share/grafana/bin')+':'+os.environ['PATH'], 'LANG': 'C.UTF-8',
             'GF_PATHS_HOME': str(bins/'grafana/usr/share/grafana'), 'GF_PATHS_CONFIG': str(config/'grafana.ini'),
             'GF_PATHS_DATA':str(data/'grafana'),'GF_PATHS_LOGS':str(root),'GF_PATHS_PLUGINS':str(data/'grafana/plugins'),
             'GF_SERVER_HTTP_ADDR': '127.0.0.1',
             'GF_PATHS_PROVISIONING':str(provision),'GF_SECURITY_ADMIN_USER':'pcloud-admin',
             'GF_SECURITY_ADMIN_PASSWORD__FILE': str(secret/'admin-password'),
             'GF_SECURITY_SECRET_KEY__FILE': str(secret/'secret-key'),'GF_AUTH_ANONYMOUS_ENABLED':'false','GF_USERS_ALLOW_SIGN_UP':'false',
             'GF_AUTH_BASIC_ENABLED':'false','GF_SERVER_ROOT_URL':c['endpoint']+'/grafana/','GF_SERVER_SERVE_FROM_SUB_PATH':'true',
             'GF_ANALYTICS_REPORTING_ENABLED':'false','GF_ANALYTICS_CHECK_FOR_UPDATES':'false','GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES':'false',
             'GF_PLUGINS_PREINSTALL_DISABLED':'true','GF_PLUGINS_PREINSTALL_AUTO_UPDATE':'false','GF_PLUGINS_PUBLIC_KEY_RETRIEVAL_DISABLED':'true',
             'GF_PLUGINS_PLUGIN_ADMIN_ENABLED':'false','GF_UNIFIED_ALERTING_ENABLED':'false','GF_LOG_LEVEL':'warn'}
        def start(component):
            logs[component]=(root/(component+'.log')).open('ab')
            processes[component]=subprocess.Popen(commands[component],cwd=root,env=env,stdout=logs[component],stderr=subprocess.STDOUT,preexec_fn=restrict)
            status = Path('/proc/' + str(processes[component].pid) + '/status').read_text()
            uid = next(line for line in status.splitlines() if line.startswith('Uid:')).split()[1:]
            if any(value != '10001' for value in uid): raise RuntimeError('Runtime UID differs from restricted workload')
        def stop(component):
            proc=processes[component]
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:proc.wait(timeout=20)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            logs[component].close()
        try:
            validation=subprocess.run([str(bins/'collector/otelcol-contrib'),'validate','--config='+str(config/'collector.yaml')],capture_output=True,text=True,timeout=30)
            if validation.returncode:raise RuntimeError('Collector validation: '+validation.stdout+validation.stderr)
            print('PASS: actual Collector configuration validation',flush=True)
            for component in ('mimir','loki','tempo','collector','grafana','gateway'):start(component)
            deadline=time.monotonic()+100
            health={'loki':'http://127.0.0.1:3100/ready','tempo':'http://127.0.0.1:3200/ready','mimir':'http://127.0.0.1:9009/ready',
                    'collector':'http://127.0.0.1:13133/','grafana':'http://127.0.0.1:3000/grafana/api/health'}
            while time.monotonic()<deadline:
                for component,proc in processes.items():
                    if proc.poll() is not None:raise RuntimeError(component+' exited: '+(root/(component+'.log')).read_text()[-5000:])
                if all(loopback(url)==200 for url in health.values()):break
                time.sleep(1)
            else:raise RuntimeError('Runtime readiness deadline: '+str({x:loopback(y) for x,y in health.items()}))
            print('PASS: six pinned processes started, five HTTP health endpoints ready',flush=True)
            client = obs.Client(c)
            for path in ('/query/loki/ready', '/query/tempo/ready', '/query/mimir/ready', '/query/collector/health'):
                if client.request(path, 'query')[0] != 200: raise RuntimeError('Authorized gateway health failed')
                for role in (None, 'ingest', 'admin'): obs.denial(client, path, role)
            if client.request('/grafana/api/health', 'admin')[0] != 200: raise RuntimeError('Grafana gateway health failed')
            for role in (None, 'query', 'ingest'): obs.denial(client, '/grafana/api/health', role)
            invalid = {**c, 'endpoint': 'https://127.0.0.1:8443'}
            try: obs.Client(invalid).request('/query/loki/ready', 'query')
            except obs.CheckRejected: pass
            else: raise RuntimeError('TLS hostname mismatch was accepted')
            print('PASS: actual trusted TLS, hostname rejection and separate query/admin role endpoints', flush=True)
            cookies = http.cookiejar.CookieJar(); client.opener.add_handler(urllib.request.HTTPCookieProcessor(cookies))
            if client.request('/grafana/login', 'admin', {'user':'pcloud-admin','password':'synthetic-admin-password'})[0] != 200:
                raise RuntimeError('Grafana file-secret login failed')
            code, raw = client.request('/grafana/api/datasources', 'admin')
            if code != 200 or {x['uid'] for x in json.loads(raw)} != {'loki','tempo','mimir'}: raise RuntimeError('Grafana data-source provisioning failed')
            code, raw = client.request('/grafana/api/dashboards/uid/pcloud-platform', 'admin')
            if code != 200 or json.loads(raw).get('dashboard',{}).get('uid') != 'pcloud-platform': raise RuntimeError('Platform dashboard provisioning failed')
            print('PASS: pinned Grafana entrypoint file-secrets, authenticated login, three data sources and platform dashboard', flush=True)
            obs.live=lambda *a:None;obs.reviewed=lambda *a:None
            original_query = obs.query
            metric_labels_reported = False
            def diagnostic_query(client, path):
                nonlocal metric_labels_reported
                body = original_query(client, path)
                if not metric_labels_reported and 'pcloud_lab_probe' in path and 'timestamp' not in path and body.get('data', {}).get('result'):
                    print('Synthetic metric label keys:', [sorted(x.get('metric', {})) for x in body['data']['result']], flush=True)
                    metric_labels_reported = True
                return body
            obs.query = diagnostic_query
            result=obs.smoke(c,b,f,{},True)
            print('PASS: real loopback OTLP logs/metrics/traces, correlation, query, service graph, role denials and alert firing',flush=True)
            deadline=time.monotonic()+30
            while not any(a.get('labels',{}).get('alertname')=='PCloudLabSyntheticSignal' for body in received for a in body.get('alerts',[])) and time.monotonic()<deadline:time.sleep(1)
            if not any(a.get('labels',{}).get('alertname')=='PCloudLabSyntheticSignal' for body in received for a in body.get('alerts',[])):
                raise RuntimeError('Synthetic alert receiver did not receive expected webhook')
            print('PASS: authenticated TLS synthetic alert receiver receipt',flush=True)
            # Restart only these temporary local processes, retaining their synthetic directories.
            client=obs.Client(c)
            for component in ('loki','tempo','mimir'):
                stop(component);start(component);deadline=time.monotonic()+90
                while loopback(health[component])!=200 and time.monotonic()<deadline:
                    if processes[component].poll() is not None:raise RuntimeError(component+' restart exited')
                    time.sleep(1)
                if loopback(health[component])!=200:raise RuntimeError(component+' restart deadline')
            if client.request('/query/tempo/api/traces/'+result['trace_id'],'query')[0]!=200:raise RuntimeError('Trace not available after restart')
            retained_logs=original_query(client, '/query/loki/loki/api/v1/query_range?'+urllib.parse.urlencode({
                'query':'{service_name="pcloud-lab-client"} |= "'+result['synthetic_run']+'"', 'start':str(result['signal_time_unix_ns']-1_000_000_000),'limit':10}))
            if not any(result['synthetic_run'] in value[1] for stream in retained_logs.get('data',{}).get('result',[]) for value in stream.get('values',[])):
                raise RuntimeError('Synthetic log not available after restart')
            retained_metrics=original_query(client, '/query/mimir/prometheus/api/v1/query_range?'+urllib.parse.urlencode({
                'query':'pcloud_lab_probe{source="pcloud-lab"}', 'start':result['signal_time_unix_ns']/1e9,
                'end':result['signal_time_unix_ns']/1e9+30, 'step':1}))
            if not any(float(value[1])==7 for series in retained_metrics.get('data',{}).get('result',[]) for value in series.get('values',[])):
                raise RuntimeError('Synthetic metric not available after restart')
            print('PASS: local backend process restarts; synthetic log/metric/trace remain queryable',flush=True)
            print('LOCAL RUNTIME PASS; Kubernetes deployment/PVC/TLS-policy enforcement/retention expiry/soak/rollback remain unverified',flush=True)
        except Exception as exc:
            if (root/'mimir.log').exists(): print('Synthetic Mimir diagnostics:', (root/'mimir.log').read_text()[-3500:], flush=True)
            tb = exc.__traceback__
            while tb:
                if tb.tb_frame.f_code.co_name == 'smoke' and 'checks' in tb.tb_frame.f_locals:
                    print('Synthetic acceptance subchecks:', tb.tb_frame.f_locals['checks'], flush=True)
                tb = tb.tb_next
            raise
        finally:
            for component in reversed(list(processes)):stop(component)
            receiver.shutdown();receiver.server_close()


if __name__=='__main__':main()
