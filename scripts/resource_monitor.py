#!/usr/bin/env python3
"""Sample RK3588 host resources once a second and retain bounded history/logs."""
import argparse
from collections import deque
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import signal
import threading
import time
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
INTERVAL = 1.0
HISTORY_SIZE = 3600
LOG_LIMIT = 20 * 1024 * 1024
LOG_BACKUPS = 4
HISTORY_FIELDS = ('timestamp', 'cpu', 'npu', 'npu0', 'npu1', 'npu2', 'gpu', 'ram',
                  'swap', 'disk', 'disk_read_Bps', 'disk_write_Bps', 'net_rx_Bps',
                  'net_tx_Bps', 'cpu_temp_c', 'npu_temp_c', 'gpu_temp_c')


def read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return ''


def number(path, divisor=1):
    try:
        return round(float(read(path)) / divisor, 2)
    except ValueError:
        return None


def rate(current, previous, elapsed, scale=1):
    if previous is None or elapsed <= 0 or current < previous:
        return None
    return round((current - previous) * scale / elapsed, 2)


def cpu_usage(current, previous):
    # guest and guest_nice are already counted in user/nice; don't count twice.
    if previous is None:
        return None
    total = sum(current[:8]) - sum(previous[:8])
    idle = (current[3] + current[4]) - (previous[3] + previous[4])
    if total <= 0 or idle < 0:
        return None
    return round(max(0, min(100, 100 * (total - idle) / total)), 1)


def parse_npu_load(value):
    return [{'id': int(i), 'percent': float(load)}
            for i, load in re.findall(r'Core(\d+):\s*(\d+(?:\.\d+)?)%', value)]


def cpu_counters():
    return {p[0]: [int(x) for x in p[1:]] for line in read('/proc/stat').splitlines()
            if (p := line.split()) and re.fullmatch(r'cpu\d*', p[0])}


def io_counters():
    counters = {}
    for line in read('/proc/diskstats').splitlines():
        p = line.split()
        if len(p) >= 14 and re.fullmatch(r'(mmcblk\d+|nvme\d+n\d+|sd[a-z]+|vd[a-z]+)', p[2]):
            counters[p[2]] = (int(p[5]), int(p[9]), int(p[12]))
    return counters


def network_counters():
    counters = {}
    for line in read('/proc/net/dev').splitlines():
        if ':' not in line:
            continue
        name, values = line.split(':', 1)
        name, values = name.strip(), values.split()
        if name == 'lo' or name.startswith(('veth', 'docker', 'br-', 'virbr', 'can', 'dummy')):
            continue
        if len(values) >= 16:
            counters[name] = (int(values[0]), int(values[8]))
    return counters


def total_or_none(values):
    valid = [x for x in values if x is not None]
    return round(sum(valid), 2) if valid else None


def filesystems():
    result, devices = [], set()
    for line in read('/proc/mounts').splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].startswith('/dev/'):
            continue
        mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), parts[1])
        try:
            device = os.stat(mount).st_dev
            if device in devices:
                continue
            total, used, free = shutil.disk_usage(mount)
            devices.add(device)
            result.append({'mount': mount, 'device': parts[0], 'filesystem': parts[2],
                           'total_bytes': total, 'used_bytes': used, 'free_bytes': free,
                           'percent': round(100 * used / total, 1) if total else None})
        except OSError:
            continue
    return sorted(result, key=lambda v: (v['mount'] != '/', v['mount']))


def compact(sample):
    cpu, npu, gpu, memory = (sample[k] for k in ('cpu', 'npu', 'gpu', 'memory'))
    disk, net = sample['storage'], sample['network']
    cores = {c['id']: c['percent'] for c in npu['cores']}
    return dict(zip(HISTORY_FIELDS, (sample['timestamp'], cpu['percent'], npu['percent'],
        cores.get(0), cores.get(1), cores.get(2), gpu['percent'], memory['percent'],
        memory['swap_percent'], disk['filesystems'][0]['percent'] if disk['filesystems'] else None,
        disk['read_bytes_s'], disk['write_bytes_s'], net['rx_bytes_s'], net['tx_bytes_s'],
        cpu['temperature_c'], npu['temperature_c'], gpu['temperature_c'])))


class Monitor:
    def __init__(self, log_dir):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.latest = None
        self.history = deque(maxlen=HISTORY_SIZE)
        self.log_error = None
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / 'metrics.jsonl'
        self._restore_history()
        self.logger = logging.getLogger('resource_samples')
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = RotatingFileHandler(self.log_file, maxBytes=LOG_LIMIT,
                                      backupCount=LOG_BACKUPS, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        def log_failed(_record):
            self.log_error = '资源记录写入失败，请检查磁盘空间'
        handler.handleError = log_failed
        self.logger.addHandler(handler)
        self.previous_cpu, self.previous_io, self.previous_net = {}, {}, {}
        self.previous_processes = {}
        self.previous_mono = time.monotonic()
        self.storage_cache = []
        self.storage_checked = -float('inf')
        self.devfreq = {p.name: p for p in Path('/sys/class/devfreq').glob('*')}
        self.npu_path = next((p for name, p in self.devfreq.items() if name.endswith('.npu')), None)
        self.gpu_path = next((p for name, p in self.devfreq.items() if name.endswith('.gpu')), None)
        self.tick_hz = os.sysconf('SC_CLK_TCK')
        self.page_size = os.sysconf('SC_PAGE_SIZE')

    def _restore_history(self):
        lines = []
        for path in [self.log_file] + [Path(str(self.log_file) + f'.{i}') for i in range(1, LOG_BACKUPS + 1)]:
            if not path.exists():
                continue
            with path.open(encoding='utf-8', errors='replace') as f:
                lines = list(deque(f, maxlen=HISTORY_SIZE - len(lines))) + lines
            if len(lines) >= HISTORY_SIZE:
                break
        now = time.time()
        for line in lines:
            try:
                sample = json.loads(line)
                if now - HISTORY_SIZE <= sample['timestamp'] <= now + 2:
                    self.history.append(compact(sample))
            except (ValueError, KeyError, TypeError):
                continue

    def _processes(self, elapsed, cpu_count):
        current, top = {}, []
        for path in Path('/proc').glob('[0-9]*/stat'):
            content = read(path)
            try:
                end = content.rindex(')')
                name = content[content.index('(') + 1:end]
                values = content[end + 2:].split()
                ticks = int(values[11]) + int(values[12])
                start = int(values[19])
                rss = max(0, int(values[21])) * self.page_size
                key = (int(path.parent.name), start)
                used = rate(ticks, self.previous_processes.get(key), elapsed,
                            100 / self.tick_hz / cpu_count)
                current[key] = ticks
                top.append({'pid': key[0], 'name': name, 'cpu_percent': used, 'rss_bytes': rss})
            except (ValueError, IndexError):
                continue
        self.previous_processes = current
        return sorted(top, key=lambda p: (p['cpu_percent'] or 0, p['rss_bytes']), reverse=True)[:6], len(current)

    def sample(self):
        now = time.monotonic()
        elapsed = now - self.previous_mono
        cpus, disks, nets = cpu_counters(), io_counters(), network_counters()
        total_cpu_percent = cpu_usage(cpus['cpu'], self.previous_cpu.get('cpu'))
        cores = [{'id': int(k[3:]), 'percent': cpu_usage(v, self.previous_cpu.get(k))}
                 for k, v in cpus.items() if k != 'cpu']
        thermal = {read(p / 'type'): number(p / 'temp', 1000)
                   for p in Path('/sys/class/thermal').glob('thermal_zone*')}
        cpu_temp = [v for k, v in thermal.items() if 'core' in k and v is not None]
        npu_cores = parse_npu_load(read('/sys/kernel/debug/rknpu/load'))
        gpu_load = number('/sys/devices/platform/fb000000.gpu/utilisation')
        mem = {}
        for line in read('/proc/meminfo').splitlines():
            key, value = line.split(':', 1)
            mem[key] = int(value.split()[0]) * 1024
        available = mem.get('MemAvailable', mem.get('MemFree', 0))
        used = mem['MemTotal'] - available
        swap_used = mem.get('SwapTotal', 0) - mem.get('SwapFree', 0)
        disk_rates = []
        for name, values in disks.items():
            previous = self.previous_io.get(name)
            values_per_sec = [rate(v, previous[i] if previous else None, elapsed, 512 if i < 2 else .1)
                              for i, v in enumerate(values)]
            disk_rates.append({'device': name, 'read_bytes_s': values_per_sec[0],
                               'write_bytes_s': values_per_sec[1],
                               'busy_percent': min(100, values_per_sec[2]) if values_per_sec[2] is not None else None})
        net_rates = []
        for name, values in nets.items():
            previous = self.previous_net.get(name)
            net_rates.append({'interface': name,
                'rx_bytes_s': rate(values[0], previous[0] if previous else None, elapsed),
                'tx_bytes_s': rate(values[1], previous[1] if previous else None, elapsed)})
        if now - self.storage_checked >= 10:
            self.storage_cache, self.storage_checked = filesystems(), now
        top, process_count = self._processes(elapsed, max(1, len(cores)))
        self.previous_cpu, self.previous_io, self.previous_net = cpus, disks, nets
        self.previous_mono = now
        uptime = float(read('/proc/uptime').split()[0])
        sample = {'timestamp': time.time(), 'uptime_s': round(uptime),
            'sample_interval_s': round(elapsed, 3),
            'cpu': {'percent': total_cpu_percent,
                    'cores': cores, 'count': len(cores), 'load_average': list(os.getloadavg()),
                    'temperature_c': max(cpu_temp) if cpu_temp else thermal.get('soc-thermal')},
            'npu': {'available': bool(npu_cores), 'cores': npu_cores,
                    'percent': round(sum(c['percent'] for c in npu_cores) / len(npu_cores), 1) if npu_cores else None,
                    'frequency_mhz': number(self.npu_path / 'cur_freq', 1e6) if self.npu_path else None,
                    'temperature_c': thermal.get('npu-thermal')},
            'gpu': {'available': gpu_load is not None, 'percent': gpu_load,
                    'frequency_mhz': number(self.gpu_path / 'cur_freq', 1e6) if self.gpu_path else None,
                    'temperature_c': thermal.get('gpu-thermal')},
            'memory': {'total_bytes': mem['MemTotal'], 'used_bytes': used, 'available_bytes': available,
                       'percent': round(100 * used / mem['MemTotal'], 1),
                       'swap_total_bytes': mem.get('SwapTotal', 0), 'swap_used_bytes': swap_used,
                       'swap_percent': round(100 * swap_used / mem['SwapTotal'], 1) if mem.get('SwapTotal') else 0,
                       'ddr_frequency_mhz': number('/sys/class/devfreq/dmc/cur_freq', 1e6)},
            'storage': {'filesystems': self.storage_cache, 'devices': disk_rates,
                        'read_bytes_s': total_or_none([v['read_bytes_s'] for v in disk_rates]),
                        'write_bytes_s': total_or_none([v['write_bytes_s'] for v in disk_rates])},
            'network': {'interfaces': net_rates,
                        'rx_bytes_s': total_or_none([v['rx_bytes_s'] for v in net_rates]),
                        'tx_bytes_s': total_or_none([v['tx_bytes_s'] for v in net_rates])},
            'temperatures_c': thermal, 'processes': top, 'process_count': process_count,
            'recording': {'enabled': self.log_error is None, 'error': self.log_error,
                          'interval_s': INTERVAL, 'retained_history_s': HISTORY_SIZE,
                          'max_log_bytes': LOG_LIMIT * (LOG_BACKUPS + 1)}}
        return sample

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                sample = self.sample()
                self.logger.info(json.dumps(sample, ensure_ascii=False, separators=(',', ':')))
                with self.lock:
                    self.latest = sample
                    self.history.append(compact(sample))
            except Exception:
                logging.exception('Resource sampling failed')
            self.stop.wait(max(.05, INTERVAL - (time.monotonic() - started)))

    def status(self):
        with self.lock:
            sample = self.latest
        return {'service': 'robonix-host-resources', 'ok': sample is not None and time.time() - sample['timestamp'] < 5,
                'sample': sample}

    def recent(self, seconds):
        cutoff = time.time() - seconds
        with self.lock:
            return [row for row in self.history if row['timestamp'] >= cutoff]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            seconds = max(1, min(HISTORY_SIZE, int(query.get('seconds', ['600'])[0])))
        except ValueError:
            self.send_error(400, 'seconds must be an integer')
            return
        if parsed.path in ('/status', '/health'):
            body = json.dumps(self.server.monitor.status(), ensure_ascii=False).encode()
            content_type = 'application/json; charset=utf-8'
        elif parsed.path == '/history':
            body = json.dumps({'interval_s': INTERVAL, 'history': self.server.monitor.recent(seconds)}).encode()
            content_type = 'application/json; charset=utf-8'
        elif parsed.path == '/export.csv':
            output = io.StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=HISTORY_FIELDS)
            writer.writeheader()
            writer.writerows(self.server.monitor.recent(seconds))
            body, content_type = ('\ufeff' + output.getvalue()).encode(), 'text/csv; charset=utf-8'
        else:
            self.send_error(404)
            return
        try:
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            if parsed.path == '/export.csv':
                self.send_header('Content-Disposition', 'attachment; filename="rk3588-resources.csv"')
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8771)
    parser.add_argument('--log-dir', type=Path, default=ROOT / 'run-logs/resources')
    args = parser.parse_args()
    monitor = Monitor(args.log_dir)
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    thread = threading.Thread(target=monitor.run, daemon=True)
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.monitor = monitor
        thread.start()
        try:
            server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass
        finally:
            monitor.stop.set()
            thread.join(timeout=3)
            logging.shutdown()


if __name__ == '__main__':
    main()
