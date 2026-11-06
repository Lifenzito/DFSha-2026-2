"""Run repeatable checks against an already deployed TLS cluster.

Credentials come from the environment. Output contains hashes and timings, never
passwords, access tokens or private keys. Run inside the private cluster network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from pathlib import Path
from datetime import datetime, timezone

import httpx
from dfsha.client import DFShaClient, DFShaClientError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['baseline', 'degraded', 'recovered', 'failover', 'no-quorum'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    urls = os.environ['DFSHA_MDS_URLS'].split(',')
    ca = os.environ['DFSHA_TLS_CA']
    client = DFShaClient(urls, verify=ca, timeout_s=3)
    checks = {}
    report = {'phase': args.phase, 'timestamp': datetime.now(timezone.utc).isoformat(), 'mds': urls, 'checks': checks}
    if args.phase == 'no-quorum':
        try:
            client.login(os.environ['DFSHA_USER'], os.environ['DFSHA_PASSWORD'])
            client.mkdir('/must-not-commit')
        except DFShaClientError as error:
            assert error.code in ['NO_LEADER', 'NO_QUORUM', 'MDS_UNAVAILABLE', 'NOT_LEADER'], error.code
            checks['writes_rejected_without_majority'] = error.code
        else:
            raise AssertionError('A write succeeded without a metadata majority')
    else:
        client.login(os.environ['DFSHA_USER'], os.environ['DFSHA_PASSWORD'])
        report['leader'] = client.discover_leader()
        if args.phase == 'baseline':
            try:
                httpx.get(urls[0]+'/health', verify=True, timeout=3)
            except httpx.ConnectError:
                checks['unknown_ca_rejected'] = True
            else:
                raise AssertionError('An unknown CA was accepted')
            assert httpx.get(urls[0]+'/health', verify=ca, timeout=3).status_code == 200
            checks['trusted_tls_accepted'] = True
            try:
                DFShaClient(urls, verify=ca).login(os.environ['DFSHA_USER'], 'incorrect-password')
            except DFShaClientError as error:
                assert error.status_code == 401
                checks['incorrect_password_rejected'] = True
            else:
                raise AssertionError('Invalid password accepted')
            client.mkdir('/aws-demo')
            client.mkdir('/aws-demo/empty')
            client.cd('/aws-demo/empty')
            client.cd('/')
            client.rmdir('/aws-demo/empty')
            checks['rf1'] = True
            content = b'DFSha-AWS-INTEGRITY-' + os.urandom(16*1024*1024)
            started = time.perf_counter()
            upload = client.put_bytes(content, '/aws-demo/large.bin')
            report['large_put_s'] = time.perf_counter()-started
            restored = client.get_bytes('/aws-demo/large.bin')
            assert restored == content
            assert len(upload['blocks']) > 1
            assert all(len(block['replicas']) == 3 for block in upload['blocks'])
            hosts = {replica for block in upload['blocks'] for replica in block['replicas']}
            assert len(hosts) >= 3
            report['large_file'] = {'size':len(content),'sha256':hashlib.sha256(content).hexdigest(),'blocks':len(upload['blocks']),'replica_endpoints':sorted(hosts)}
            checks['large_file_partition_replication_integrity'] = True
            client.put_bytes(b'abcdefghij', '/aws-demo/rf3')
            snapshot = client.open_read('/aws-demo/rf3')
            client.write('/aws-demo/rf3', 3, b'XYZ')
            client.append('/aws-demo/rf3', b'-tail')
            assert client.read('/aws-demo/rf3', 2, 5) == b'cXYZg'
            assert client.get_bytes('/aws-demo/rf3') == b'abcXYZghij-tail'
            assert b''.join(client._read_block(b) for b in snapshot['snapshot']['blocks']) == b'abcdefghij'
            client.close(snapshot)
            checks['rf3_and_snapshot'] = True
            lock = client.lock('/aws-demo/rf3')
            second = DFShaClient(urls, verify=ca)
            second.login(os.environ['DFSHA_USER'], os.environ['DFSHA_PASSWORD'])
            try:
                second.open_write('/aws-demo/rf3')
            except DFShaClientError as error:
                assert error.code == 'LOCKED'
                checks['second_writer_rejected'] = True
            else:
                raise AssertionError('Concurrent writer acquired existing lease')
            finally:
                client.close(lock)
            # Scoped token and internal API authorization.
            block = client.open_read('/aws-demo/large.bin')['snapshot']['blocks'][0]
            endpoint = block['replicas'][0]
            assert httpx.get(endpoint+'/v1/blocks/'+block['block_id'], verify=ca).status_code == 401
            assert httpx.get(endpoint+'/internal/blocks/'+block['block_id']+'/status', verify=ca).status_code == 403
            checks['data_and_internal_unauthorized_rejected'] = True
            intruder_uid = 'validation-intruder'
            response = httpx.post(report['leader']+'/v1/admin/users', json={'uid':intruder_uid,'password':os.environ['DFSHA_PASSWORD']}, headers={'X-DFSha-Internal':os.environ['DFSHA_INTERNAL_SECRET']}, verify=ca, timeout=10)
            response.raise_for_status()
            intruder = DFShaClient(urls, verify=ca)
            intruder.login(intruder_uid, os.environ['DFSHA_PASSWORD'])
            try:
                intruder.stat('/users/demo/aws-demo/large.bin')
            except DFShaClientError as error:
                assert error.code == 'PERMISSION_DENIED'
                checks['cross_user_access_denied'] = True
            else:
                raise AssertionError('Cross-user access accepted')
            samples=[]
            for size in [1048576,8388608]:
                payload=os.urandom(size)
                puts,gets=[],[]
                for repetition in range(3):
                    path=f'/aws-demo/bench-{size}-{repetition}'
                    start=time.perf_counter(); client.put_bytes(payload,path); puts.append(time.perf_counter()-start)
                    start=time.perf_counter(); downloaded=client.get_bytes(path); gets.append(time.perf_counter()-start)
                    assert hashlib.sha256(downloaded).digest()==hashlib.sha256(payload).digest()
                put,get=statistics.median(puts),statistics.median(gets)
                samples.append({'bytes':size,'repetitions':3,'put_median_s':put,'get_median_s':get,'put_mib_s':size/1048576/put,'get_mib_s':size/1048576/get})
            report['benchmark']=samples
            checks['two_condition_benchmark']=True
        else:
            baseline = json.loads(args.output.with_name('baseline.json').read_text())
            restored=client.get_bytes('/aws-demo/large.bin')
            assert hashlib.sha256(restored).hexdigest()==baseline['large_file']['sha256']
            checks['large_file_integrity']=True
            if args.phase == 'degraded':
                client.put_bytes(b'write with a disconnected replica', '/aws-demo/degraded')
                assert client.get_bytes('/aws-demo/degraded')==b'write with a disconnected replica'
                checks['write_and_read_with_disconnected_replica']=True
            elif args.phase == 'recovered':
                deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    manifest=client.open_read('/aws-demo/degraded')['snapshot']
                    if all(len(block['replicas'])==3 for block in manifest['blocks']):
                        break
                    time.sleep(2)
                else:
                    raise AssertionError('Replica count did not recover')
                checks['replication_restored']=True
            elif args.phase == 'failover':
                assert report['leader'] != baseline['leader']
                client.mkdir('/aws-demo/after-failover')
                assert client.stat('/aws-demo/after-failover')['kind']=='dir'
                checks['new_leader_and_new_commit']=True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
