"""Latest resource sample per transport source, without storing periodic logs."""
import json
import math
import time
from ipaddress import ip_address


def store(conn, host, message):
    try:
        host = str(ip_address(host))
        if len(message) > 16000:
            return False
        data = json.loads(message[len('METRICS '):])
        def percent(value):
            if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError('Invalid percentage')
            return round(value, 1)
        disks = data['disks']
        if not isinstance(disks, dict) or not 1 <= len(disks) <= 32:
            return False
        sample = dict(cpu=percent(data['cpu']), ram=percent(data['ram']), disks={})
        for path,value in disks.items():
            if not isinstance(path,str) or not path.startswith('/') or len(path)>512:
                return False
            sample['disks'][path] = percent(value)
        conn.execute('INSERT INTO resource_samples(host,sampled_at,payload) VALUES(?,?,?) ON CONFLICT(host) DO UPDATE SET sampled_at=excluded.sampled_at,payload=excluded.payload',
                     (host,time.time(),json.dumps(sample)))
        return True
    except (ValueError,TypeError,KeyError):
        return False


def latest(conn):
    now = time.time()
    result = {}
    for row in conn.execute('SELECT host,sampled_at,payload FROM resource_samples'):
        sample = json.loads(row['payload'])
        sample.update(sampled_at=row['sampled_at'], stale=now-row['sampled_at']>180)
        result[row['host']] = sample
    return result
