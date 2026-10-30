#!/usr/bin/env python3
"""Linux resource checks; emit state changes through the local syslog service."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def cpu_sample():
    # guest and guest_nice are already included in user/nice.
    values = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    return sum(values), values[3] + values[4]


def memory_usage():
    values = {line.split(':')[0]: int(line.split()[1])
              for line in Path('/proc/meminfo').read_text().splitlines()}
    return 100 * (1 - values['MemAvailable'] / values['MemTotal'])



def cgroup_metrics(root=Path('/sys/fs/cgroup'), membership=Path('/proc/self/cgroup')):
    """Read the visible cgroup v2 limits, including stricter parent limits."""
    relative = next((line.split(':', 2)[2] for line in membership.read_text().splitlines()
                     if line.startswith('0::')), None)
    if relative is None:
        raise RuntimeError('cgroup v1: Container-Limits werden nicht unterstützt; cgroup v2 verwenden')
    candidate = root / relative.lstrip('/')
    current = candidate if candidate.is_dir() and '..' not in Path(relative).parts else root
    capacity = os.cpu_count() or 1
    memory_limit = None
    node = current
    while True:
        cpu_max = node / 'cpu.max'
        if cpu_max.exists():
            quota, period = cpu_max.read_text().split()
            if quota != 'max':
                capacity = min(capacity, int(quota) / int(period))
        cpuset = node / 'cpuset.cpus.effective'
        if cpuset.exists() and cpuset.read_text().strip():
            count = 0
            for part in cpuset.read_text().strip().split(','):
                bounds = list(map(int, part.split('-')))
                count += bounds[-1] - bounds[0] + 1
            capacity = min(capacity, count)
        limit_file = node / 'memory.max'
        if limit_file.exists() and limit_file.read_text().strip() != 'max':
            limit = int(limit_file.read_text())
            memory_limit = min(memory_limit, limit) if memory_limit else limit
        if node == root:
            break
        node = node.parent
    usage = dict(line.split() for line in (current / 'cpu.stat').read_text().splitlines())
    memory = None
    if memory_limit:
        used = int((current / 'memory.current').read_text())
        stats = dict(line.split() for line in (current / 'memory.stat').read_text().splitlines())
        memory = 100 * max(0, used - int(stats.get('inactive_file', 0))) / memory_limit
    return int(usage['usage_usec']), capacity, memory

def transition(previous, value, threshold, now, duration, repeat):
    """Require sustained pressure; repeat alarms and emit one recovery."""
    state = dict(previous)
    event = None
    if value >= threshold:
        state.setdefault('since', now)
        if now - state['since'] >= duration:
            if not state.get('active') or now - state.get('sent', 0) >= repeat:
                event = 'warning'
                state.update(active=True, sent=now)
    else:
        if state.get('active'):
            event = 'recovery'
        state = {}
    return state, event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='JSON configuration file')
    parser.add_argument('--scope', choices=['auto', 'host', 'cgroup'], default='auto')
    parser.add_argument('--cpu', type=float, default=90)
    parser.add_argument('--ram', type=float, default=90)
    parser.add_argument('--disk', type=float, default=90)
    parser.add_argument('--duration', type=int, default=600, help='CPU/RAM pressure duration in seconds')
    parser.add_argument('--repeat', type=int, default=1800, help='Repeat warning after seconds')
    parser.add_argument('--mount', action='append', help='Filesystem path; repeat for more volumes (default: /)')
    parser.add_argument('--state', default='/var/lib/rsyslog-resource-monitor/state.json')
    parser.add_argument('--dry-run', action='store_true', help='Print measurements without logging or saving state')
    preliminary, _ = parser.parse_known_args()
    if preliminary.config:
        defaults = json.loads(Path(preliminary.config).read_text())
        unknown = set(defaults) - {'cpu', 'ram', 'disk', 'duration', 'repeat', 'mount', 'scope'}
        if unknown:
            parser.error(f'Unknown configuration keys: {unknown}')
        parser.set_defaults(**defaults)
    args = parser.parse_args()
    if any(not 0 < value <= 100 for value in (args.cpu, args.ram, args.disk)) or args.duration < 0 or args.repeat <= 0:
        parser.error('Thresholds must be > 0 and <= 100; duration >= 0; repeat > 0')

    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if state.pop('_boot_id', None) != boot_id:
        state = {}
    container = False
    if args.scope == 'auto':
        detected = subprocess.run(['systemd-detect-virt', '--container', '--quiet'], check=False)
        container = detected.returncode == 0
    use_cgroup = args.scope == 'cgroup' or container
    if use_cgroup:
        first, _, _ = cgroup_metrics()
        started = time.monotonic()
        time.sleep(1)
        current, capacity, ram = cgroup_metrics()
        cpu = 100 * (current - first) / ((time.monotonic() - started) * 1_000_000 * capacity)
        if ram is None:
            ram = memory_usage()
    else:
        first_total, first_idle = cpu_sample()
        time.sleep(1)
        total, idle = cpu_sample()
        delta = total - first_total
        if delta <= 0:
            raise RuntimeError('No CPU sample available')
        cpu = 100 * (1 - (idle - first_idle) / delta)
        ram = memory_usage()
    checks = [('CPU', cpu, args.cpu, args.duration),
              ('RAM', ram, args.ram, args.duration)]
    for mount in args.mount or ['/']:
        usage = shutil.disk_usage(mount)
        checks.append((f'Disk {mount}', 100 * usage.used / usage.total, args.disk, 0))
    now = time.time()
    for name, value, threshold, duration in checks:
        if args.dry_run:
            print(f'{name}: {value:.1f}% (threshold {threshold:g}%)')
            continue
        updated, event = transition(state.get(name, {}), value, threshold, now, duration, args.repeat)
        if event:
            severity = 'warning' if event == 'warning' else 'notice'
            message = (f'{"ALARM" if event == "warning" else "ENTWARNUNG"}: '
                       f'{name} {value:.1f}% (Schwelle {threshold:g}%)')
            subprocess.run(['logger', '-p', f'user.{severity}', '-t', 'resource-monitor', '--', message], check=True)
        state[name] = updated
    if not args.dry_run:
        subprocess.run(['logger', '-p', 'user.info', '-t', 'resource-monitor', '--', 'HEARTBEAT'], check=True)
        state['_boot_id'] = boot_id
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state))
        os.replace(temporary, state_path)


if __name__ == '__main__':
    main()
