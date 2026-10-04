"""Read-only node probe, passed over SSH stdin; creates no files."""
import json
import os
import subprocess
import sys
from pathlib import Path

root=Path(sys.argv[1])
def mount(path):
    data=json.loads(subprocess.check_output(['findmnt','--json','--output','TARGET,UUID','--target',str(path)]))['filesystems'][0]
    return data
info=mount(root)
stats=os.statvfs(root)
marker=root/'.pcloud-storage-ready'
names=json.loads(sys.argv[2]) if len(sys.argv)>2 else []
if any(not name.startswith('pvc-') or '/' in name or '..' in name for name in names):
    raise ValueError('Unsafe inspection target')
print(json.dumps({'root':str(root.resolve()),'symlink':str(root.resolve())!=str(root),
    'target':info['target'],'uuid':info.get('uuid'),'device':root.stat().st_dev,
    'os_device':Path('/').stat().st_dev,'rke2_device':Path('/var/lib/rancher').stat().st_dev,
    'marker':marker.read_text().strip() if marker.is_file() and not marker.is_symlink() and marker.stat().st_size<=128 else '',
    'root_uid':root.stat().st_uid,'root_mode':root.stat().st_mode & 0o777,
    'marker_uid':marker.stat().st_uid if marker.exists() else None,
    'marker_mode':marker.stat().st_mode & 0o777 if marker.exists() else None,
    'total_bytes':stats.f_blocks*stats.f_frsize,'free_bytes':stats.f_bavail*stats.f_frsize,
    'total_inodes':stats.f_files,'free_inodes':stats.f_favail,
    'paths_remaining':[name for name in names if (root/name).exists() or (root/name).is_symlink()]}))
