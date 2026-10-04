#!/usr/bin/env python3
"""Standalone lab runtime; cluster access is read-only, writes require a review."""
import argparse
import base64
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent
OWNER = 'observability-lab'
VERSIONS = {'loki': '3.7.8', 'tempo': '2.10.8', 'mimir': '2.17.11',
            'grafana': '13.2.3', 'collector': '0.161.0', 'gateway': '1.30.5-alpine'}
SOURCES = {x: 'docker.io/grafana/' + x + ':' + VERSIONS[x] for x in ('loki', 'tempo', 'mimir', 'grafana')}
SOURCES.update(collector='docker.io/otel/opentelemetry-collector-contrib:0.161.0',
               gateway='docker.io/nginxinc/nginx-unprivileged:1.30.5-alpine')
PORTS = {'loki': 3100, 'tempo': 3200, 'mimir': 9009, 'grafana': 3000, 'collector': 4318, 'gateway': 8443}
MOUNT = '/var/lib/pcloud/observability'
MAX_BODY = 2 * 1024**2


class CheckRejected(ValueError): pass


def require(condition, message):
    if not condition: raise CheckRejected(message)


def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(value): return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def origin(url):
    p = urllib.parse.urlsplit(url)
    require(p.scheme == 'https' and p.hostname and not p.username and not p.password
            and not p.query and not p.fragment and p.path in ('', '/'), 'Trusted HTTPS origin required')
    require(p.port in (None, 443, 8443), 'Unsupported TLS port')
    return p


def validate(c, b, f):
    try:
        jsonschema.Draft202012Validator(read_json(ROOT / 'site.schema.json')).validate(c)
        jsonschema.Draft202012Validator(read_json(ROOT / 'contracts/backend.schema.json')).validate(b)
    except jsonschema.ValidationError: raise CheckRejected('Input schema rejected') from None
    require(origin(c['endpoint']).hostname == c['gateway_host'], 'Endpoint hostname must match gateway certificate name')
    receiver = urllib.parse.urlsplit(c['alert_receiver'])
    require(receiver.scheme == 'https' and receiver.hostname and not receiver.username and not receiver.password
            and not receiver.query and not receiver.fragment and receiver.port in (None, 443), 'HTTPS alert receiver required')
    require(b['namespace'] == c['namespace'], 'Backend and runtime namespaces must match')
    require(b['components'] == {x: VERSIONS[x] for x in ('loki', 'tempo', 'mimir')}, 'Unsupported backend component versions')
    expected = read_json(ROOT / 'backend-fragments.example.json')
    require(f == expected and sha(f) == b['fragments_sha256'], 'Storage fragment contract mismatch')
    require(len({v['claim'] for v in b['volumes'].values()} | {c['grafana_claim']}) == 4, 'Claims must be distinct')
    for v in b['volumes'].values():
        require(re.fullmatch(r'[a-z][a-z0-9-]{0,51}[a-z0-9]', v['claim'])
                and re.fullmatch(r'[a-z][a-z0-9-]{0,51}[a-z0-9]', v['worker']), 'Unsafe backend object identifier')
    if c['purpose'] == 'conformance':
        require(c['namespace'].startswith('pcloud-observe-test-'), 'Conformance requires an isolated test namespace')
    else:
        require(not c['namespace'].startswith('pcloud-observe-test-'), 'Platform namespace cannot impersonate conformance')
    require(len({c[k] for k in ('tls_secret', 'access_secret', 'grafana_secret', 'alert_secret')}) == 4, 'Separate secrets required')
    for key in ('client_cidrs', 'alert_cidrs'):
        for cidr in c[key]:
            n = ipaddress.ip_network(cidr, strict=True)
            require(n.prefixlen > 0 and not n.is_multicast, 'Unrestricted network range rejected')
    for res in c['resources'].values():
        for key, suffix in (('cpu', 'm'), ('memory', 'Mi')):
            require(int(res['limits'][key].removesuffix(suffix)) >= int(res['requests'][key].removesuffix(suffix)),
                    'Resource limit below request')
    require(int(c['resources']['collector']['limits']['memory'].removesuffix('Mi')) >= 256,
            'Collector needs at least 256Mi memory limit')
    return c


def merged(a, b):
    result = copy.deepcopy(a)
    for key, value in b.items():
        result[key] = merged(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def configs(c, b, f):
    loki = merged(f['loki'], {
        'auth_enabled': False, 'server': {'http_listen_port': 3100, 'grpc_listen_port': 9096, 'log_level': 'warn'},
        'common': {'instance_addr': '127.0.0.1', 'replication_factor': 1, 'ring': {'kvstore': {'store': 'inmemory'}}},
        'compactor': {'retention_enabled': True, 'delete_request_store': 'filesystem'},
        'limits_config': {'retention_period': str(c['retention_hours']['logs']) + 'h', 'allow_structured_metadata': True,
                          'ingestion_rate_mb': 1, 'ingestion_burst_size_mb': 2, 'max_global_streams_per_user': 1000,
                          'max_query_series': 500, 'query_timeout': '30s'},
        'distributor': {'otlp_config': {'default_resource_attributes_as_index_labels': ['service.name', 'service.namespace']}},
        'analytics': {'reporting_enabled': False}})
    tempo = merged(f['tempo'], {
        'server': {'http_listen_port': 3200, 'log_level': 'warn'}, 'multitenancy_enabled': False,
        'distributor': {'receivers': {'otlp': {'protocols': {'http': {'endpoint': '0.0.0.0:4318'}}}}},
        'ingester': {'max_block_duration': '5m'},
        'compactor': {'compaction': {'block_retention': str(c['retention_hours']['traces']) + 'h'}},
        'metrics_generator': {'ring': {'kvstore': {'store': 'inmemory'}},
            'registry': {'external_labels': {'source': 'tempo', 'cluster': 'pcloud-lab'}},
            'storage': {'remote_write': [{'url': 'http://mimir:9009/api/v1/push', 'send_exemplars': True}]},
            'processor': {'service_graphs': {'max_items': 1000}, 'span_metrics': {'dimensions': []}}},
        'overrides': {'defaults': {'metrics_generator': {'processors': ['service-graphs', 'span-metrics']}}},
        'usage_report': {'reporting_enabled': False}})
    mimir = merged(f['mimir'], {
        'multitenancy_enabled': False, 'server': {'http_listen_port': 9009, 'log_level': 'warn'},
        'common': {'storage': {'backend': 'filesystem'}},
        'distributor': {'ring': {'instance_addr': '127.0.0.1', 'kvstore': {'store': 'inmemory'}}},
        'ingester': {'ring': {'instance_addr': '127.0.0.1', 'kvstore': {'store': 'inmemory'}, 'replication_factor': 1}},
        'store_gateway': {'sharding_ring': {'replication_factor': 1, 'kvstore': {'store': 'inmemory'}}},
        'compactor': {'sharding_ring': {'kvstore': {'store': 'inmemory'}}},
        'ruler': {'enable_api': False, 'poll_interval': '15s', 'evaluation_interval': '15s',
                  'alertmanager_url': 'http://mimir:9009/alertmanager',
                  'ring': {'kvstore': {'store': 'inmemory'}}},
        'alertmanager': {'enable_api': False, 'fallback_config_file': '/etc/pcloud/alertmanager.yaml',
                         'sharding_ring': {'replication_factor': 1, 'kvstore': {'store': 'inmemory'}}},
        'limits': {'compactor_blocks_retention_period': str(c['retention_hours']['metrics']) + 'h',
                   'ingestion_rate': 10000, 'ingestion_burst_size': 20000, 'max_global_series_per_user': 10000},
        'usage_stats': {'enabled': False}})
    # Static YAML provisioning uses Mimir's read-only local rule loader, not
    # the filesystem object bucket's protobuf rule format. Telemetry storage
    # and the alertmanager bucket remain the original persistent handover.
    mimir['ruler_storage'] = {'backend': 'local', 'local': {'directory': '/etc/mimir-rules'}}
    retry = {'enabled': True, 'initial_interval': '1s', 'max_interval': '5s', 'max_elapsed_time': '30s'}
    queue = {'enabled': True, 'num_consumers': 1, 'queue_size': 256}
    memory = int(c['resources']['collector']['limits']['memory'].removesuffix('Mi'))
    collector = {
        'receivers': {'otlp': {'protocols': {'http': {'endpoint': '0.0.0.0:4318', 'max_request_body_size': MAX_BODY}}},
                      'prometheus': {'config': {'scrape_configs': [{
                          'job_name': 'pcloud-' + x, 'scrape_interval': '15s', 'sample_limit': 2000,
                          'static_configs': [{'targets': [x + ':' + str(8888 if x == 'collector' else PORTS[x])]}]}
                          for x in ('loki', 'tempo', 'mimir', 'collector')]}}},
        'processors': {'memory_limiter': {'check_interval': '1s', 'limit_mib': memory * 3 // 4, 'spike_limit_mib': memory // 8},
                       'batch': {'timeout': '1s', 'send_batch_size': 256, 'send_batch_max_size': 256}},
        'exporters': {'otlp_http/loki': {'endpoint': 'http://loki:3100/otlp', 'timeout': '5s', 'retry_on_failure': retry, 'sending_queue': queue},
                      'otlp_http/tempo': {'endpoint': 'http://tempo:4318', 'timeout': '5s', 'retry_on_failure': retry, 'sending_queue': queue},
                      'prometheusremotewrite': {'http': {'endpoint': 'http://mimir:9009/api/v1/push', 'timeout': '5s'},
                          'retry_on_failure': retry, 'remote_write_queue': {'enabled': True, 'queue_size': 256, 'num_consumers': 1},
                          'target_info': {'enabled': False}, 'disable_scope_info': True,
                          'resource_constant_labels': {'included': ['service.name', 'service.namespace']}}},
        'extensions': {'health_check': {'endpoint': '0.0.0.0:13133'}},
        'service': {'extensions': ['health_check'], 'telemetry': {'logs': {'level': 'warn'}, 'metrics': {
            'readers': [{'pull': {'exporter': {'prometheus': {'host': '0.0.0.0', 'port': 8888}}}}]}},
            'pipelines': {x: {'receivers': ['otlp', 'prometheus'] if x == 'metrics' else ['otlp'],
                'processors': ['memory_limiter', 'batch'], 'exporters': [y]} for x, y in
                (('logs', 'otlp_http/loki'), ('traces', 'otlp_http/tempo'), ('metrics', 'prometheusremotewrite'))}}}
    datasources = {'apiVersion': 1, 'datasources': [
        {'name': 'Mimir', 'uid': 'mimir', 'type': 'prometheus', 'url': 'http://mimir:9009/prometheus', 'access': 'proxy', 'isDefault': True, 'editable': False},
        {'name': 'Loki', 'uid': 'loki', 'type': 'loki', 'url': 'http://loki:3100', 'access': 'proxy', 'editable': False,
         'jsonData': {'derivedFields': [{'name': 'trace_id', 'matcherType': 'label', 'matcherRegex': 'trace_id', 'datasourceUid': 'tempo', 'url': '$${__value.raw}'}]}},
        {'name': 'Tempo', 'uid': 'tempo', 'type': 'tempo', 'url': 'http://tempo:3200', 'access': 'proxy', 'editable': False,
         'jsonData': {'serviceMap': {'datasourceUid': 'mimir'}, 'nodeGraph': {'enabled': True}}}]}
    alert = {'route': {'receiver': 'platform', 'group_by': ['alertname'], 'group_wait': '10s', 'group_interval': '30s', 'repeat_interval': '1h'},
             'receivers': [{'name': 'platform', 'webhook_configs': [{'url': c['alert_receiver'], 'send_resolved': True,
                 'http_config': {'authorization': {'type': 'Bearer', 'credentials_file': '/etc/alert-secret/token'},
                                 'tls_config': {'ca_file': '/etc/alert-secret/ca.crt'}}}]}]}
    rules = {'groups': [{'name': 'pcloud-platform', 'rules': [
        {'alert': 'PCloudBackendDown', 'expr': 'up{job=~"pcloud-(loki|tempo|mimir)"} == 0', 'for': '1m',
         'labels': {'severity': 'warning'}, 'annotations': {'summary': 'pCloud observability backend is unavailable'}},
        {'alert': 'PCloudLabSyntheticSignal', 'expr': 'pcloud_lab_probe{source="pcloud-lab"} == 7', 'for': '0s',
         'labels': {'severity': 'test'}, 'annotations': {'summary': 'Isolated pCloud synthetic acceptance alert'}}]}]}
    return {'loki': loki, 'tempo': tempo, 'mimir': mimir, 'collector': collector,
            'datasources': datasources, 'alertmanager': alert, 'rules': rules}


def gateway_config(c):
    routes = [('/ingest/v1/' + x, 'ingest', 'collector:4318/v1/' + x, 'POST') for x in ('logs', 'traces', 'metrics')]
    routes += [(p, 'query', u, 'GET') for p, u in (
        ('/query/loki/loki/api/v1/query_range', 'loki:3100/loki/api/v1/query_range'),
        ('/query/loki/loki/api/v1/labels', 'loki:3100/loki/api/v1/labels'),
        ('/query/mimir/prometheus/api/v1/query', 'mimir:9009/prometheus/api/v1/query'),
        ('/query/mimir/prometheus/api/v1/query_range', 'mimir:9009/prometheus/api/v1/query_range'),
        ('/query/mimir/prometheus/api/v1/series', 'mimir:9009/prometheus/api/v1/series'),
        ('/query/mimir/prometheus/api/v1/alerts', 'mimir:9009/prometheus/api/v1/alerts'),
        ('/query/loki/ready', 'loki:3100/ready'), ('/query/tempo/ready', 'tempo:3200/ready'),
        ('/query/mimir/ready', 'mimir:9009/ready'), ('/query/collector/health', 'collector:13133/'))]
    locations = []
    for path, role, target, method in routes:
        locations.append(f'''location = {path} {{
      if ($request_method != {method}) {{ return 405; }}
      auth_basic "pCloud {role}"; auth_basic_user_file /etc/access/{role}.htpasswd;
      proxy_pass http://{target};
    }}''')
    locations.append('''location ~ "^/query/tempo/api/traces/([0-9a-f]{32})$" {
      if ($request_method != GET) { return 405; }
      auth_basic "pCloud query"; auth_basic_user_file /etc/access/query.htpasswd;
      proxy_pass http://tempo_query/api/traces/$1;
    }''')
    locations.append('''location /grafana/ {
      auth_basic "pCloud administration"; auth_basic_user_file /etc/access/admin.htpasswd;
      proxy_pass http://grafana:3000;
      proxy_set_header Host $host;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
    }''')
    return '''pid /tmp/nginx.pid;
error_log stderr crit;
worker_processes 1;
events { worker_connections 256; }
http {
  upstream tempo_query { server tempo:3200; }
  access_log off;
  server_tokens off;
  client_body_temp_path /tmp/client;
  proxy_temp_path /tmp/proxy;
  fastcgi_temp_path /tmp/fastcgi;
  uwsgi_temp_path /tmp/uwsgi;
  scgi_temp_path /tmp/scgi;
  client_max_body_size 2m;
  client_body_timeout 5s;
  proxy_connect_timeout 5s;
  proxy_read_timeout 30s;
  proxy_send_timeout 5s;
  proxy_set_header Authorization "";
  proxy_set_header X-Scope-OrgID "";
  proxy_set_header X-Forwarded-Proto https;
  map $http_upgrade $connection_upgrade { default upgrade; '' close; }
  server {
    listen 8443 ssl;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_certificate /etc/tls/tls.crt;
    ssl_certificate_key /etc/tls/tls.key;
    server_name ''' + c['gateway_host'] + ''';
    add_header Strict-Transport-Security "max-age=31536000" always;
    location / { return 404; }
    ''' + '\n    '.join(locations) + '''
  }
}
'''


def dashboard():
    expressions = [('Backend reachability', 'up{job=~"pcloud-(loki|tempo|mimir|collector)"}'),
                   ('Collector export failures', 'sum by (exporter) (rate(otelcol_exporter_send_failed_spans_total[5m]))'),
                   ('Trace service graph', 'sum by (client, server) (rate(traces_service_graph_request_total[5m]))')]
    return {'uid': 'pcloud-platform', 'title': 'pCloud platform', 'schemaVersion': 39, 'version': 1,
            'refresh': '15s', 'time': {'from': 'now-15m', 'to': 'now'}, 'panels': [
                {'id': i + 1, 'title': title, 'type': 'timeseries', 'datasource': {'type': 'prometheus', 'uid': 'mimir'},
                 'gridPos': {'h': 8, 'w': 24, 'x': 0, 'y': i * 8}, 'targets': [{'refId': 'A', 'expr': expr}]}
                for i, (title, expr) in enumerate(expressions)]}


def meta(c, name):
    return {'name': name, 'namespace': c['namespace'], 'labels': {
        'pcloud.io/package': OWNER, 'pcloud.io/purpose': c['purpose'], 'app.kubernetes.io/name': name}}


def config_map(c, name, data):
    return {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': meta(c, name), 'data': data}


def security():
    return {'runAsNonRoot': True, 'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
            'capabilities': {'drop': ['ALL']}}


def workload(c, b, component, configuration):
    worker = b['volumes'][component]['worker'] if component in b['volumes'] else c['grafana_worker' if component == 'grafana' else 'gateway_worker']
    image = read_json(ROOT / 'images.lock.json')[SOURCES[component]]['image']
    cmd = {'loki': ['/usr/bin/loki', '-target=all', '-config.file=/etc/pcloud/loki.yaml'],
           'tempo': ['/tempo', '-target=all', '-config.file=/etc/pcloud/tempo.yaml'],
           'mimir': ['/bin/mimir', '-target=all,alertmanager', '-config.file=/etc/pcloud/mimir.yaml'],
           'collector': ['/otelcol-contrib', '--config=/etc/pcloud/collector.yaml'],
           'grafana': ['/run.sh'],
           'gateway': ['/usr/sbin/nginx', '-e', 'stderr', '-c', '/etc/pcloud/nginx.conf', '-g', 'daemon off;']}[component]
    container = {'name': component, 'image': image, 'command': cmd, 'securityContext': security(), 'resources': c['resources'][component],
                 'ports': [{'name': 'http', 'containerPort': PORTS[component]}],
                 'volumeMounts': [{'name': 'config', 'mountPath': '/etc/pcloud', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}]}
    volumes = [{'name': 'config', 'configMap': {'name': component + '-config'}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '128Mi'}}]
    if component in b['volumes'] or component == 'grafana':
        claim = b['volumes'][component]['claim'] if component in b['volumes'] else c['grafana_claim']
        volumes.append({'name': 'data', 'persistentVolumeClaim': {'claimName': claim}})
        container['volumeMounts'].append({'name': 'data', 'mountPath': MOUNT + '/' + component})
    if component == 'mimir':
        volumes.extend([{'name': 'rules', 'configMap': {'name': 'mimir-rules'}}, {'name': 'alert-secret', 'secret': {'secretName': c['alert_secret'], 'defaultMode': 0o440}}])
        container['volumeMounts'].extend([{'name': 'rules', 'mountPath': '/etc/mimir-rules/anonymous', 'readOnly': True},
                                          {'name': 'alert-secret', 'mountPath': '/etc/alert-secret', 'readOnly': True}])
    if component == 'gateway':
        for name, secret in (('tls', c['tls_secret']), ('access', c['access_secret'])):
            volumes.append({'name': name, 'secret': {'secretName': secret, 'defaultMode': 0o440}})
            container['volumeMounts'].append({'name': name, 'mountPath': '/etc/' + name, 'readOnly': True})
        container['readinessProbe'] = {'tcpSocket': {'port': 'http'}, 'periodSeconds': 10}
    elif component == 'grafana':
        volumes.extend([{'name': 'grafana-secret', 'secret': {'secretName': c['grafana_secret'], 'defaultMode': 0o440}},
                        {'name': 'datasources', 'configMap': {'name': 'grafana-datasources'}},
                        {'name': 'dashboards', 'configMap': {'name': 'grafana-dashboards'}}])
        container['volumeMounts'].extend([{'name': 'grafana-secret', 'mountPath': '/etc/grafana-secret', 'readOnly': True},
            {'name': 'datasources', 'mountPath': '/etc/grafana/provisioning/datasources', 'readOnly': True},
            {'name': 'config', 'mountPath': '/etc/grafana/provisioning/dashboards', 'readOnly': True},
            {'name': 'dashboards', 'mountPath': '/var/lib/pcloud/dashboards', 'readOnly': True}])
        container['env'] = [{'name': k, 'value': v} for k, v in {
            'GF_PATHS_DATA': MOUNT + '/grafana', 'GF_PATHS_LOGS': '/tmp', 'GF_PATHS_PLUGINS': MOUNT + '/grafana/plugins',
            'GF_PATHS_PROVISIONING': '/etc/grafana/provisioning', 'GF_SECURITY_ADMIN_USER': 'pcloud-admin',
            'GF_SECURITY_ADMIN_PASSWORD__FILE': '/etc/grafana-secret/admin-password',
            'GF_SECURITY_SECRET_KEY__FILE': '/etc/grafana-secret/secret-key',
            'GF_AUTH_ANONYMOUS_ENABLED': 'false', 'GF_USERS_ALLOW_SIGN_UP': 'false', 'GF_AUTH_BASIC_ENABLED': 'false',
            'GF_SECURITY_COOKIE_SECURE': 'true', 'GF_SERVER_ROOT_URL': c['endpoint'].rstrip('/') + '/grafana/',
            'GF_SERVER_SERVE_FROM_SUB_PATH': 'true', 'GF_ANALYTICS_REPORTING_ENABLED': 'false',
            'GF_ANALYTICS_CHECK_FOR_UPDATES': 'false', 'GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES': 'false',
            'GF_PLUGINS_PREINSTALL_DISABLED': 'true', 'GF_PLUGINS_PREINSTALL_AUTO_UPDATE': 'false',
            'GF_PLUGINS_PUBLIC_KEY_RETRIEVAL_DISABLED': 'true', 'GF_PLUGINS_PLUGIN_ADMIN_ENABLED': 'false',
            'GF_UNIFIED_ALERTING_ENABLED': 'false', 'GF_LOG_LEVEL': 'warn'}.items()]
    if component != 'gateway':
        container['readinessProbe'] = {'httpGet': {'path': '/api/health' if component == 'grafana' else ('/' if component == 'collector' else '/ready'),
                                                   'port': 13133 if component == 'collector' else 'http'}, 'periodSeconds': 10}
    container['startupProbe'] = {**copy.deepcopy(container['readinessProbe']), 'failureThreshold': 60, 'periodSeconds': 5}
    spec = {'automountServiceAccountToken': False, 'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'fsGroup': 10001,
                 'fsGroupChangePolicy': 'OnRootMismatch', 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [container], 'volumes': volumes, 'terminationGracePeriodSeconds': 60,
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                {'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [worker]}]}]}}}}
    if component == 'tempo': container['ports'].append({'name': 'otlp-http', 'containerPort': 4318})
    if component == 'collector': container['ports'].extend([{'name': 'metrics', 'containerPort': 8888}, {'name': 'health', 'containerPort': 13133}])
    if component == 'gateway': return {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': meta(c, component),
        'spec': {'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': {'app.kubernetes.io/name': component}},
                 'template': {'metadata': {'labels': meta(c, component)['labels'], 'annotations': {'pcloud.io/config-sha256': sha(configuration)}}, 'spec': spec}}}
    return {'apiVersion': 'apps/v1', 'kind': 'StatefulSet', 'metadata': meta(c, component),
        'spec': {'serviceName': component, 'replicas': 1, 'updateStrategy': {'type': 'OnDelete'},
            'selector': {'matchLabels': {'app.kubernetes.io/name': component}},
            'template': {'metadata': {'labels': meta(c, component)['labels'], 'annotations': {'pcloud.io/config-sha256': sha(configuration)}}, 'spec': spec}}}


def peer(component): return {'podSelector': {'matchLabels': {'pcloud.io/package': OWNER, 'app.kubernetes.io/name': component}}}


def ports(*numbers): return [{'protocol': 'TCP', 'port': n} for n in numbers]


def policy(c, component):
    edges = {'loki': [('collector', 3100), ('gateway', 3100), ('grafana', 3100)],
             'tempo': [('collector', 4318), ('collector', 3200), ('gateway', 3200), ('grafana', 3200)],
             'mimir': [('collector', 9009), ('tempo', 9009), ('gateway', 9009), ('grafana', 9009)],
             'collector': [('gateway', 4318), ('gateway', 13133), ('collector', 8888)], 'grafana': [('gateway', 3000)], 'gateway': []}
    ingress = [{'from': [peer(source)], 'ports': ports(port)} for source, port in edges[component]]
    if component == 'gateway': ingress = [{'from': [{'ipBlock': {'cidr': x}} for x in c['client_cidrs']], 'ports': ports(8443)}]
    # Self-traffic is needed for monolithic rings, rule queries and self-scraping.
    ingress.append({'from': [peer(component)], 'ports': ports(PORTS[component], 9095, 9096)})
    egress = [{'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                      'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
               'ports': [{'protocol': x, 'port': 53} for x in ('TCP', 'UDP')]}]
    for target, incoming in edges.items():
        allowed = sorted({port for source, port in incoming if source == component})
        if allowed: egress.append({'to': [peer(target)], 'ports': ports(*allowed)})
    egress.append({'to': [peer(component)], 'ports': ports(PORTS[component], 8888, 9095, 9096)})
    if component == 'mimir': egress.append({'to': [{'ipBlock': {'cidr': x}} for x in c['alert_cidrs']], 'ports': ports(443)})
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': meta(c, component),
            'spec': {'podSelector': peer(component)['podSelector'], 'policyTypes': ['Ingress', 'Egress'], 'ingress': ingress, 'egress': egress}}


def render(c, b, f):
    cfg = configs(c, b, f); result = []
    for component in VERSIONS:
        data = {component + '.yaml': yaml.safe_dump(cfg[component], sort_keys=False)} if component in cfg else {}
        if component == 'mimir': data['alertmanager.yaml'] = yaml.safe_dump(cfg['alertmanager'], sort_keys=False)
        if component == 'gateway': data['nginx.conf'] = gateway_config(c)
        if component == 'grafana': data['provider.yaml'] = yaml.safe_dump({'apiVersion': 1, 'providers': [{
            'name': 'pcloud-platform', 'disableDeletion': True, 'allowUiUpdates': False,
            'options': {'path': '/var/lib/pcloud/dashboards'}}]})
        result.extend([config_map(c, component + '-config', data), workload(c, b, component, data), policy(c, component)])
        service_ports = ports(PORTS[component])
        for item in service_ports: item.update(name='http', targetPort=PORTS[component]); item['port'] = 443 if component == 'gateway' else PORTS[component]
        if component == 'tempo': service_ports.append({'name': 'otlp-http', 'port': 4318, 'targetPort': 4318})
        if component == 'collector': service_ports.extend([{'name': 'metrics', 'port': 8888, 'targetPort': 8888}, {'name': 'health', 'port': 13133, 'targetPort': 13133}])
        result.append({'apiVersion': 'v1', 'kind': 'Service', 'metadata': meta(c, component), 'spec': {
            'type': 'ClusterIP', 'selector': peer(component)['podSelector']['matchLabels'], 'ports': service_ports}})
    result.extend([config_map(c, 'mimir-rules', {'platform.yaml': yaml.safe_dump(cfg['rules'], sort_keys=False)}),
                   config_map(c, 'grafana-datasources', {'sources.yaml': yaml.safe_dump(cfg['datasources'], sort_keys=False)}),
                   config_map(c, 'grafana-dashboards', {'platform.json': json.dumps(dashboard())}),
                   {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': meta(c, c['grafana_claim']), 'spec': {
                       'storageClassName': c['storage_class'], 'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem',
                       'resources': {'requests': {'storage': str(c['grafana_size_gib']) + 'Gi'}}}}])
    return result


def digest(c, b, f):
    h = hashlib.sha256()
    for path in sorted([ROOT / 'observability.py', ROOT / 'images.lock.json', ROOT / 'site.schema.json',
                        ROOT / 'UPSTREAM.json', ROOT / 'backend-fragments.example.json', * (ROOT / 'contracts').glob('*.json')]):
        h.update(path.name.encode()); h.update(path.read_bytes().replace(b'\r\n', b'\n'))
    h.update(json.dumps([c, b, f, render(c, b, f)], sort_keys=True).encode())
    return h.hexdigest()


def capability(c, b, f):
    endpoint = c['endpoint'].rstrip('/')
    return {'api': 'pcloud.observability/v1', 'profile': 'lab', 'purpose': c['purpose'], 'namespace': c['namespace'],
            'versions': VERSIONS, 'artifact_sha256': digest(c, b, f), 'protocol': 'OTLP/HTTP',
            'endpoints': {'ingest': endpoint + '/ingest', 'logs': endpoint + '/query/loki', 'traces': endpoint + '/query/tempo',
                          'metrics': endpoint + '/query/mimir/prometheus', 'grafana': endpoint + '/grafana/'},
            'auth': {'transport': 'TLS1.2+', 'roles': ['ingest', 'query', 'admin'], 'type': 'separate-basic-credential-files',
                     'grafana': 'gateway-admin-plus-Grafana-login', 'tenant_isolation': False},
            'retention_hours': c['retention_hours'], 'collector': {'persistent_queue': False, 'queue_batches_per_signal': 256,
                'retry_seconds': 30, 'loss_on_restart_or_overflow': True}, 'replication': False, 'backup': False,
            'acceptance': 'live-signal-access-retention-alert-recovery-soak-and-rollback-required'}


def kube(c, *args):
    result = subprocess.run(['kubectl', '--context', c['context'], '--request-timeout=30s', *args], capture_output=True, text=True, timeout=60)
    require(result.returncode == 0 and len(result.stdout) <= 8 * 1024**2, 'Kubernetes read failed; raw diagnostics suppressed')
    return json.loads(result.stdout)


def get(c, kind, name): return kube(c, 'get', kind, name, '-n', c['namespace'], '-o', 'json')


def preflight(c, b):
    version = kube(c, 'version', '-o', 'json')['serverVersion']['gitVersion']
    require(re.match(r'^v1\.35\.', version), 'Kubernetes 1.35 required')
    ns = get(c, 'namespace', c['namespace'])
    labels = ns['metadata'].get('labels', {})
    for mode in ('enforce', 'audit', 'warn'):
        require(labels.get('pod-security.kubernetes.io/' + mode) == 'restricted'
                and labels.get('pod-security.kubernetes.io/' + mode + '-version') == 'v1.35', 'Restricted namespace required')
    require(labels.get('pcloud.io/backend-owner') == 'observability-filesystem', 'Explicit backend namespace owner required')
    sc = get(c, 'storageclass', c['storage_class'])
    require(sc.get('reclaimPolicy') == 'Retain' and sc.get('volumeBindingMode') == 'WaitForFirstConsumer', 'Retain/WFFC class required')
    for component, v in b['volumes'].items():
        pvc = get(c, 'pvc', v['claim']); spec = pvc['spec']
        require(spec.get('storageClassName') == c['storage_class'] and spec.get('accessModes') == ['ReadWriteOnce']
                and spec.get('volumeMode', 'Filesystem') == 'Filesystem', 'Backend claim contract changed')
        if 'uid' in v: require(pvc['metadata']['uid'] == v['uid'], 'Backend claim UID changed')
        else: require(pvc['metadata'].get('labels', {}).get('pcloud.io/package') == 'observability-filesystem'
                      and pvc['metadata']['labels'].get('pcloud.io/backend-component') == component, 'Backend claim owner changed')
    # Metadata-only secret reads avoid retrieving htpasswd/password/TLS-key contents.
    for secret in (c['tls_secret'], c['access_secret'], c['grafana_secret'], c['alert_secret']):
        result = subprocess.run(['kubectl', '--context', c['context'], '--request-timeout=30s', 'get', 'secret', secret,
            '-n', c['namespace'], '-o', 'jsonpath={.metadata.uid}'], capture_output=True, text=True, timeout=60)
        require(result.returncode == 0 and re.fullmatch(r'[a-zA-Z0-9-]{1,80}', result.stdout), 'Required Secret metadata unavailable')
    for worker in sorted({v['worker'] for v in b['volumes'].values()} | {c['grafana_worker'], c['gateway_worker']}):
        node = get(c, 'node', worker)
        require(not node['spec'].get('unschedulable') and not node['spec'].get('taints')
                and not any(x in node['metadata'].get('labels', {}) for x in ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master'))
                and any(x['type'] == 'Ready' and x['status'] == 'True' for x in node['status']['conditions']), 'Ready untainted worker required')
    return {'status': 'INCOMPLETE', 'reason': 'Read-only prerequisites checked; mounted capacity, secret content, policy enforcement and review still required'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


class Client:
    def __init__(self, c):
        self.endpoint = c['endpoint'].rstrip('/')
        ctx = ssl.create_default_context(cafile=c['ca_file']); ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ctx))

    def request(self, path, role=None, payload=None):
        require(path.startswith('/') and not path.startswith('//') and '\r' not in path and '\n' not in path, 'Unsafe request path')
        headers = {'Accept': 'application/json'}
        if role:
            value = os.environ.get('PCLOUD_OBSERVE_' + role.upper(), '')
            require(':' in value and '\r' not in value and '\n' not in value, 'Role credential missing or invalid')
            headers['Authorization'] = 'Basic ' + base64.b64encode(value.encode()).decode()
        if payload is not None: headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.endpoint + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = self.opener.open(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        except (OSError, urllib.error.URLError): raise CheckRejected('Trusted TLS request failed; raw diagnostics suppressed') from None
        with response:
            data = response.read(MAX_BODY + 1); require(len(data) <= MAX_BODY, 'Response exceeded bound')
            return response.code, data


def denial(client, path, role=None, payload=None):
    code, _ = client.request(path, role, payload)
    require(code in (401, 403), 'Endpoint failed authorization denial')


def query(client, path):
    code, raw = client.request(path, 'query'); require(code == 200, 'Query failed')
    try: return json.loads(raw)
    except (ValueError, UnicodeError): raise CheckRejected('Malformed query response') from None


def matches(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and matches(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(matches(x, y) for x, y in zip(expected, actual))
    return expected == actual


def live(c, b, f):
    preflight(c, b)
    credentials = [os.environ.get('PCLOUD_OBSERVE_' + x.upper(), '') for x in ('ingest', 'query', 'admin')]
    require(all(':' in x for x in credentials) and len(set(credentials)) == 3, 'Distinct role credentials required')
    for expected in render(c, b, f):
        actual = get(c, expected['kind'].lower(), expected['metadata']['name'])
        require(matches(expected, actual), 'Installed resource differs from reviewed runtime')
    for component in VERSIONS:
        kind = 'deployment' if component == 'gateway' else 'statefulset'
        obj = get(c, kind, component)
        require(obj['metadata'].get('labels', {}).get('pcloud.io/package') == OWNER, 'Foreign runtime resource')
        require(obj['spec']['replicas'] == 1 and obj['status'].get('readyReplicas') == 1, 'Runtime component not ready')
        require(obj['spec']['template']['spec']['containers'][0]['image'] == read_json(ROOT / 'images.lock.json')[SOURCES[component]]['image'], 'Runtime image differs')
    client = Client(c)
    for path in ('/query/loki/ready', '/query/tempo/ready', '/query/mimir/ready', '/query/collector/health'):
        require(client.request(path, 'query')[0] == 200, 'Backend health failed')
        denial(client, path); denial(client, path, 'ingest')
    require(client.request('/grafana/api/health', 'admin')[0] == 200, 'Grafana administration gateway health failed')
    for role in (None, 'ingest', 'query'): denial(client, '/grafana/api/health', role)
    require(query(client, '/query/mimir/prometheus/api/v1/query?query=up').get('status') == 'success', 'Metric query unavailable')
    return {'status': 'INCOMPLETE', 'reason': 'Read-only health, image pins and query denials passed; signal, effective configuration, retention, alert, recovery and soak remain required'}


def telemetry(run, trace, start):
    # Metric labels are fixed; run IDs occur only in logs/traces.
    attr = lambda k, v: {'key': k, 'value': {'stringValue': v}}
    spans = []
    for service, name, span, parent, kind in (('pcloud-lab-client', 'call', '1111111111111111', None, 3),
                                            ('pcloud-lab-server', 'receive', '2222222222222222', '1111111111111111', 2)):
        item = {'traceId': trace, 'spanId': span, 'name': name, 'kind': kind, 'startTimeUnixNano': str(start),
                'endTimeUnixNano': str(start + 100_000_000), 'attributes': [attr('pcloud.run', run)], 'status': {'code': 1}}
        if parent: item['parentSpanId'] = parent
        spans.append({'resource': {'attributes': [attr('service.name', service), attr('service.namespace', 'pcloud-lab')]}, 'scopeSpans': [{'spans': [item]}]})
    return {'traces': {'resourceSpans': spans}, 'logs': {'resourceLogs': [{
        'resource': {'attributes': [attr('service.name', 'pcloud-lab-client'), attr('service.namespace', 'pcloud-lab')]},
        'scopeLogs': [{'logRecords': [{'timeUnixNano': str(start), 'severityNumber': 9, 'body': {'stringValue': 'pcloud probe ' + run},
                                     'traceId': trace, 'spanId': '1111111111111111', 'attributes': [attr('pcloud.run', run)]}]}]}]},
        'metrics': {'resourceMetrics': [{'resource': {'attributes': [attr('service.name', 'pcloud-lab-client')]},
            'scopeMetrics': [{'metrics': [{'name': 'pcloud_lab_probe', 'gauge': {'dataPoints': [{
                'timeUnixNano': str(start), 'asDouble': 7, 'attributes': [attr('source', 'pcloud-lab')]}]}}]}]}]}}


def reviewed(c, b, f, review):
    require(set(review) == {'revision', 'artifact_sha256', 'namespace', 'purpose'}, 'Review fields rejected')
    require(re.fullmatch('[0-9a-f]{40}', review['revision']) and review['artifact_sha256'] == digest(c, b, f)
            and review['namespace'] == c['namespace'] and review['purpose'] == 'conformance', 'Review does not match isolated artifacts')
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=10)
    require(result.returncode == 0 and result.stdout.strip() == review['revision'], 'Reviewed revision differs from checkout')


def smoke(c, b, f, review, consent):
    require(consent, 'Explicit synthetic-write consent required')
    require(c['purpose'] == 'conformance' and c['namespace'].startswith('pcloud-observe-test-'), 'Synthetic writes require an isolated installed test stack')
    reviewed(c, b, f, review)
    live(c, b, f)
    client = Client(c); run = uuid.uuid4().hex[:12]; trace = uuid.uuid4().hex; start = time.time_ns()
    graph_path = '/query/mimir/prometheus/api/v1/query?' + urllib.parse.urlencode({
        'query': 'traces_service_graph_request_total{client="pcloud-lab-client",server="pcloud-lab-server"}'})
    before = query(client, graph_path).get('data', {}).get('result', [])
    before_count = sum(float(x['value'][1]) for x in before)
    payloads = telemetry(run, trace, start)
    for signal, payload in payloads.items():
        path = '/ingest/v1/' + signal
        for role in (None, 'query', 'admin'): denial(client, path, role, payload)
        code, raw = client.request(path, 'ingest', payload)
        require(code == 200, 'Synthetic OTLP ingest failed')
        response = json.loads(raw or b'{}')
        require(not response.get('partialSuccess'), 'OTLP partially rejected synthetic signal')
    checks = {'logs': False, 'traces': False, 'metrics': False, 'service_graph': False, 'alert_firing': False}
    deadline = time.monotonic() + c['query_timeout_seconds']
    while time.monotonic() < deadline and not all(checks.values()):
        logs = query(client, '/query/loki/loki/api/v1/query_range?' + urllib.parse.urlencode({
            'query': '{service_name="pcloud-lab-client"} |= "' + run + '"', 'start': str(start - 1_000_000_000), 'limit': 10}))
        checks['logs'] = any(run in value[1] and (
            stream.get('stream', {}).get('trace_id') == trace
            or (len(value) > 2 and value[2].get('trace_id') == trace))
            for stream in logs.get('data', {}).get('result', []) for value in stream.get('values', []))
        code, raw = client.request('/query/tempo/api/traces/' + trace, 'query')
        if code == 200:
            result = json.loads(raw); checks['traces'] = trace in json.dumps(result) or trace.lower() in raw.decode(errors='ignore')
            # Tempo JSON may encode trace IDs in base64; exact expected bytes also accepted.
            checks['traces'] |= base64.b64encode(bytes.fromhex(trace)).decode() in json.dumps(result)
        metric = query(client, '/query/mimir/prometheus/api/v1/query?' + urllib.parse.urlencode({'query': 'pcloud_lab_probe{source="pcloud-lab"}'}))
        series = metric.get('data', {}).get('result', [])
        require(len(series) <= 2 and all(set(x['metric']) <= {'__name__', 'source', 'job', 'service_name'}
            and x['metric'].get('source') == 'pcloud-lab'
            and x['metric'].get('job', 'pcloud-lab-client') == 'pcloud-lab-client'
            and x['metric'].get('service_name', 'pcloud-lab-client') == 'pcloud-lab-client' for x in series),
            'Synthetic metric cardinality/labels violated')
        ages = query(client, '/query/mimir/prometheus/api/v1/query?' + urllib.parse.urlencode({
            'query': 'timestamp(pcloud_lab_probe{source="pcloud-lab"})'})).get('data', {}).get('result', [])
        checks['metrics'] = bool(series) and len(ages) == len(series) and all(float(x['value'][1]) == 7 for x in series) \
            and all(start / 1e9 - 1 <= float(x['value'][1]) <= start / 1e9 + c['query_timeout_seconds'] for x in ages)
        graph = query(client, graph_path)
        checks['service_graph'] = sum(float(x['value'][1]) for x in graph.get('data', {}).get('result', [])) > before_count
        alerts = query(client, '/query/mimir/prometheus/api/v1/alerts').get('data', {}).get('alerts', [])
        checks['alert_firing'] = any(x.get('labels', {}).get('alertname') == 'PCloudLabSyntheticSignal' and x.get('state') == 'firing' for x in alerts)
        if not all(checks.values()): time.sleep(2)
    require(all(checks.values()), 'Synthetic acceptance deadline exceeded: ' + ','.join(k for k, v in checks.items() if not v))
    return {'status': 'INCOMPLETE', 'synthetic_run': run, 'trace_id': trace, 'signal_time_unix_ns': start, 'checks': checks,
            'reason': 'API checks passed; receiver receipt, retention expiry, mounted-data restart, failure isolation, soak and rollback evidence required',
            'cleanup': 'No resource deletions; test data expires under the isolated stack retention policy'}


def report_path(path):
    target = Path(path).resolve(); require(target.suffix == '.json', 'JSON report path required')
    for product in (ROOT.parent.parent, Path('D:/project/pCloud'), Path('D:/project/IOT-EE')):
        require(not target.is_relative_to(product.resolve()), 'Reports must be outside product repositories')
    require(not target.exists(), 'Existing report will not be overwritten')
    return target


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['render', 'configs', 'capability', 'digest', 'preflight', 'live', 'smoke'])
    p.add_argument('--site', required=True); p.add_argument('--backend', required=True); p.add_argument('--fragments', required=True)
    p.add_argument('--review'); p.add_argument('--allow-cluster-changes', action='store_true'); p.add_argument('--output')
    a = p.parse_args()
    try:
        output = report_path(a.output) if a.output else None
        c, b, f = [read_json(x) for x in (a.site, a.backend, a.fragments)]; validate(c, b, f)
        if a.action == 'render': print(yaml.safe_dump_all(render(c, b, f), sort_keys=False)); return 0
        if a.action == 'configs': print(json.dumps(configs(c, b, f), indent=2)); return 0
        if a.action == 'capability': value = capability(c, b, f)
        elif a.action == 'digest': print(digest(c, b, f)); return 0
        elif a.action == 'preflight': value = preflight(c, b)
        elif a.action == 'live': value = live(c, b, f)
        else: value = smoke(c, b, f, read_json(a.review) if a.review else {}, a.allow_cluster_changes)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8') as handle: json.dump(value, handle, indent=2)
        print(json.dumps(value, indent=2)); return 3 if value.get('status') == 'INCOMPLETE' else 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, CheckRejected) else 'Operation rejected; raw diagnostics suppressed'
        print(json.dumps({'status': 'FAIL', 'reason': message})); return 1


if __name__ == '__main__': sys.exit(main())
