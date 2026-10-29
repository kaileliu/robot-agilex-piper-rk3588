"""Exercise the real NPU server and Piper's HTTP contract; no robot actions."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    # Use a free temporary port, leaving the robot's usual port available.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    raw = (ROOT/'samples/camera_calibration/calib_006.jpg').read_bytes()
    report = {'checks': [], 'sample': 'samples/camera_calibration/calib_006.jpg'}

    def request(path, body=None):
        req = urllib.request.Request(base+path, data=body)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def detect():
        code, result = request('/detect?conf=0.5&iou=0.45', raw)
        assert code == 200 and result['success'], result
        return result

    log_path = ROOT/'.cache/http_smoke.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('w') as log:
        process = subprocess.Popen(['bash', str(ROOT/'run_yolo_npu.sh'), '--port', str(port)],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+30
            while True:
                if process.poll() is not None:
                    raise RuntimeError(log_path.read_text())
                try:
                    code, health = request('/health')
                    break
                except (urllib.error.URLError, ConnectionError):
                    if time.monotonic() > deadline:
                        raise TimeoutError(log_path.read_text())
                    time.sleep(.1)
            assert code == 200 and health['success'] and health['device'] == 'rk3588-npu'
            assert health['classes'] == {'0': 'carrot', '1': 'potato', '2': 'tomato'}
            assert health['input_size'] == [736, 1280]
            report['health'] = health
            report['checks'].append('health, model size and original class mapping')
            maps = Path(f'/proc/{process.pid}/maps').read_text()
            assert str(ROOT/'runtime/librknnrt.so') in maps
            assert 'libtorch' not in maps
            report['checks'].append('local librknnrt loaded; server has no libtorch')

            result = detect()
            assert result['image_size'] == [720, 1280]
            assert result['detections'], 'The included camera sample must exercise nonempty detections'
            for detection in result['detections']:
                u, v, w, h, r = detection['obb']
                expected = [u-w/2, v-h/2, u+w/2, v+h/2, math.degrees(r)]
                assert all(abs(a-b) < 1e-5 for a, b in zip(expected, detection['bbox_2d']))
                assert detection['class_id'] == 2 and detection['object_name'] == 'tomato'
                assert .5 <= detection['confidence'] <= 1
            report['example_response'] = result
            report['checks'].append('real image, nonempty detection, OBB radians and bbox degrees')

            code, filtered = request('/detect?conf=0.5&object_name=ToMa', raw)
            assert code == 200 and filtered['detections'] == result['detections']
            code, missing = request('/detect?conf=0.5&object_name=carrot', raw)
            assert code == 200 and missing['detections'] == []
            report['checks'].append('case-insensitive substring filtering and empty result')

            for path, body, expected_code in [
                ('/detect', b'', 400), ('/detect', b'not-an-image', 400),
                ('/detect?conf=nan', raw, 400), ('/detect?conf=inf', raw, 400),
                ('/detect?conf=abc', raw, 400), ('/detect?iou=-1', raw, 400),
                ('/missing', None, 404), ('/missing', raw, 404),
            ]:
                code, error = request(path, body)
                assert code == expected_code and error['success'] is False, (path, code, error)
            report['checks'].append('invalid images, thresholds, empty body and unknown routes')

            loads = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(detect) for _ in range(4)]
                while not all(f.done() for f in futures):
                    load_path = Path('/sys/kernel/debug/rknpu/load')
                    if load_path.exists():
                        loads.append(load_path.read_text().strip())
                    time.sleep(.05)
                concurrent = [f.result() for f in futures]
            assert all(r['detections'] == result['detections'] for r in concurrent)
            report['npu_load_samples'] = sorted(set(loads))
            report['checks'].append('four concurrent requests share one NPU context correctly')
        finally:
            process.terminate()
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError('Server did not stop cleanly')
    assert code == 0, log_path.read_text()
    report['checks'].append('SIGTERM exits cleanly and releases runtime')
    report['status'] = 'passed'
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/http_smoke.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
