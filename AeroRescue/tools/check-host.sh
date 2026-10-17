#!/bin/sh
# Read-only host inspection. Does not install Docker or alter host security.
set -eu
out=runs/acceptance/host-report.json
if [ "${1:-}" = --out ]; then out=$2; shift 2; fi
if [ "$#" -ne 0 ]; then echo 'usage: check-host.sh [--out file]' >&2; exit 2; fi
python3 - "$out" <<'PY'
import datetime, json, os, pathlib, platform, shutil, subprocess, sys

def command(argv):
    if not shutil.which(argv[0]):
        return {'available': False, 'reason': f'{argv[0]} not installed'}
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return {'available': result.returncode == 0, 'exit_code': result.returncode,
                'output': (result.stdout + result.stderr).strip()[-6000:]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'available': False, 'reason': str(error)}

def entries(path):
    try:
        return pathlib.Path(path).read_text()
    except OSError:
        return None

memory = {}
for line in (entries('/proc/meminfo') or '').splitlines():
    key, value = line.split(':', 1)
    if key in ('MemTotal', 'MemAvailable'):
        memory[key + '_bytes'] = int(value.split()[0]) * 1024
security = {}
for line in (entries('/proc/self/status') or '').splitlines():
    if line.split(':')[0] in ('CapEff', 'NoNewPrivs', 'Seccomp'):
        key, value = line.split(':', 1)
        security[key] = value.strip()
disk = shutil.disk_usage('.')
docker = command(['docker', 'version', '--format', '{{json .}}'])
compose = command(['docker', 'compose', 'version'])
report = {
    'recorded_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'system': platform.platform(), 'architecture': platform.machine(),
    'os_release': entries('/etc/os-release'), 'python': platform.python_version(),
    'cpu_count': os.cpu_count(), 'memory': memory,
    'disk_bytes': {'total': disk.total, 'free': disk.free},
    'uid': os.getuid(), 'security': security,
    'docker': docker, 'compose': compose,
    'docker_sockets': [p for p in ('/var/run/docker.sock', '/run/docker.sock',
        f'/run/user/{os.getuid()}/docker.sock') if pathlib.Path(p).is_socket()],
    'graphics_devices': [str(p) for p in pathlib.Path('/dev').glob('nvidia*')]
                        + [str(p) for p in pathlib.Path('/dev/dri').glob('*')],
    'recommended_build_jobs': max(1, min(os.cpu_count() or 1,
        memory.get('MemAvailable_bytes', 2 * 1024**3) // (2 * 1024**3))),
    'native_container_status': 'READY_FOR_SMOKE' if docker['available'] and compose['available'] else 'BLOCKED',
    'native_camera_status': 'NOT_RUN',
    'note': 'A working daemon and real container smoke are required. No nested privileged Docker or host security changes are attempted.'
}
path = pathlib.Path(sys.argv[1]); path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
PY
