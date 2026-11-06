import argparse
import os
import base64
import configparser
import json
import secrets
import io
import tarfile
import subprocess
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import time
from pathlib import Path
import boto3

PROJECT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('DFSHA_LAB_STATE_DIR', str(PROJECT / '.aws-lab'))).resolve()
STATE = ROOT / 'state.json'
def session():
    config = configparser.ConfigParser()
    config.read(ROOT / 'credentials.txt')
    credentials = dict(config['default']) if config.has_section('default') else {}
    return boto3.Session(**credentials, region_name='us-east-1')
def load():
    return json.loads(STATE.read_text())
def save(data):
    ROOT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2))
    STATE.chmod(0o600)
def command(instance_id, script, timeout=900):
    response = session().client('ssm').send_command(InstanceIds=[instance_id], DocumentName='AWS-RunShellScript', Parameters={'commands':[script], 'executionTimeout':[str(timeout)]}, TimeoutSeconds=timeout)
    return response['Command']['CommandId']
def result(instance_id, command_id):
    return session().client('ssm').get_command_invocation(InstanceId=instance_id, CommandId=command_id)
def provision():
    ec2 = session().client('ec2')
    if STATE.exists():
        raise RuntimeError('State exists; inspect existing project instances before provisioning again')
    vpc = ec2.describe_vpcs(Filters=[{'Name':'is-default','Values':['true']}])['Vpcs'][0]
    group = ec2.create_security_group(GroupName='DFSha-2026-2-private', Description='DFSha lab; private service traffic only', VpcId=vpc['VpcId'], TagSpecifications=[{'ResourceType':'security-group','Tags':[{'Key':'Project','Value':'DFSha-2026-2'}]}])['GroupId']
    ec2.authorize_security_group_ingress(GroupId=group, IpPermissions=[{'IpProtocol':'tcp','FromPort':port,'ToPort':port,'UserIdGroupPairs':[{'GroupId':group}]} for port in [8443,9443,9444]])
    ami=session().client('ssm').get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64')['Parameter']['Value']
    subnets=sorted(ec2.describe_subnets(Filters=[{'Name':'vpc-id','Values':[vpc['VpcId']]}])['Subnets'], key=lambda item:item['AvailabilityZone'])[:3]
    state={'group':group,'region':'us-east-1','created_at':time.time(),'instances':[], 'commands':{}, 'secrets':{name:secrets.token_hex(32) for name in ['jwt','block','internal','password','dn-1','dn-2','dn-3','dn-4']}}
    save(state)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip()
    state['commit']=commit
    save(state)
    user_data=f'''#!/bin/bash
set -eu
dnf install -y docker git
systemctl enable --now docker
mkdir -p /opt/dfsha-runtime
chmod 700 /opt/dfsha-runtime
git clone https://github.com/Lifenzito/DFSha-2026-2.git /opt/dfsha
cd /opt/dfsha
git checkout {commit}
docker build -t dfsha:lab .
touch /opt/dfsha-runtime/build-ready
'''
    for index,subnet in enumerate(subnets,1):
        instance=ec2.run_instances(ImageId=ami,InstanceType='t3.micro',MinCount=1,MaxCount=1,IamInstanceProfile={'Name':'LabInstanceProfile'},NetworkInterfaces=[{'DeviceIndex':0,'SubnetId':subnet['SubnetId'],'Groups':[group],'AssociatePublicIpAddress':True}],BlockDeviceMappings=[{'DeviceName':'/dev/xvda','Ebs':{'VolumeSize':8,'VolumeType':'gp3','DeleteOnTermination':True,'Encrypted':True}}],MetadataOptions={'HttpTokens':'required','HttpPutResponseHopLimit':1},CreditSpecification={'CpuCredits':'standard'},UserData=user_data,TagSpecifications=[{'ResourceType':'instance','Tags':[{'Key':'Name','Value':f'DFSha-VM-{index}'},{'Key':'Project','Value':'DFSha-2026-2'}]},{'ResourceType':'volume','Tags':[{'Key':'Project','Value':'DFSha-2026-2'}]}],ClientToken=f'dfsha-2026-2-vm-{index}')['Instances'][0]
        state['instances'].append({'id':instance['InstanceId'],'ip':instance['PrivateIpAddress'],'az':subnet['AvailabilityZone'],'name':f'VM-{index}'})
        save(state)
        print(json.dumps(state['instances'][-1]),flush=True)
def status():
    state=load()
    ec2=session().client('ec2')
    ids=[i['id'] for i in state['instances']]
    data=ec2.describe_instances(InstanceIds=ids)
    for reservation in data['Reservations']:
        for item in reservation['Instances']:
            print(item['InstanceId'],item['State']['Name'],item.get('PrivateIpAddress'))
    info=session().client('ssm').describe_instance_information(Filters=[{'Key':'InstanceIds','Values':ids}])['InstanceInformationList']
    print('SSM',[(item['InstanceId'],item['PingStatus']) for item in info])
def probe():
    state=load()
    for instance in state['instances']:
        cmd=command(instance['id'],'test -f /opt/dfsha-runtime/build-ready && docker image inspect dfsha:lab --format "{{.Id}}" || tail -n 15 /var/log/cloud-init-output.log')
        state['commands'][instance['id']]=cmd
        print(instance['name'],cmd)
    save(state)
def results():
    state=load()
    for instance in state['instances']:
        cmd=state['commands'].get(instance['id'])
        if cmd:
            r=result(instance['id'],cmd)
            print(instance['name'],r['Status'],r.get('StandardOutputContent','')[-1200:],r.get('StandardErrorContent','')[-1200:])
def stop():
    state=load()
    ids=[i['id'] for i in state['instances']]
    print(session().client('ec2').stop_instances(InstanceIds=ids)['StoppingInstances'])
def configure(target=None):
    state=load()
    project=PROJECT
    ips=[i['ip'] for i in state['instances']]
    if not (ROOT/'certs/client/ca.crt').exists():
        subprocess.run([sys.executable,str(project/'scripts/generate_certs.py'),'--output',str(ROOT/'certs'),*[value for ip in ips for value in ['--ip',ip]]],check=True,stdout=subprocess.DEVNULL)
    mds=[f'https://{ip}:8443' for ip in ips]
    dns=[f'https://{ip}:9443' for ip in ips]+[f'https://{ips[2]}:9444']
    state['mds']=mds
    state['dns']=dns
    common={'DFSHA_TLS_ENABLED':'true','DFSHA_TLS_CA':'/certs/ca.crt','DFSHA_JWT_SECRET':state['secrets']['jwt'],'DFSHA_BLOCK_TOKEN_SECRET':state['secrets']['block'],'DFSHA_INTERNAL_SECRET':state['secrets']['internal'],'DFSHA_REQUEST_TIMEOUT_S':'2.0','DFSHA_DATA_DIR':'/data'}
    for index,instance in enumerate(state['instances']):
        if target and instance['name']!=target:
            continue
        files={}
        commands=['set -eu','shutdown -h +100 || true','mkdir -p /opt/dfsha-runtime','chmod 700 /opt/dfsha-runtime']
        nodes=[(f'mds-{index+1}',8443,'mds_app'),(f'dn-{index+1}',9443,'datanode_app')]
        if index==2:
            nodes.append(('dn-4',9444,'datanode_app'))
        for name,port,module in nodes:
            for cert in ['server.crt','server.key','ca.crt']:
                files[f'{name}/{cert}']=(ROOT/'certs'/name/cert).read_bytes()
            env={**common,'DFSHA_NODE_ID':name,'DFSHA_PUBLIC_URL':f'https://{instance["ip"]}:{port}'}
            if module=='mds_app':
                env.update(DFSHA_MDS_PEERS=','.join(url for url in mds if url!=env['DFSHA_PUBLIC_URL']),DFSHA_DATANODES=','.join(dns),DFSHA_BOOTSTRAP_USER='demo',DFSHA_BOOTSTRAP_PASSWORD=state['secrets']['password'],DFSHA_BLOCK_SIZE='1048576',DFSHA_LEASE_TTL_S='300',DFSHA_REPAIR_INTERVAL_S='3',DFSHA_ELECTION_MIN_S='2',DFSHA_ELECTION_MAX_S='3.5')
            else:
                env.update(DFSHA_ENCRYPTION_KEY_HEX=state['secrets'][name],DFSHA_ZONE=instance['az'])
            files[name+'.env']=''.join(f'{k}={v}\n' for k,v in env.items()).encode()
            commands.append(f'mkdir -p /opt/dfsha-runtime/{name}/data')
            commands.append(f'docker run -d --name {name} --restart unless-stopped --network host --env-file /opt/dfsha-runtime/{name}.env -v /opt/dfsha-runtime/{name}:/certs:ro -v /opt/dfsha-runtime/{name}/data:/data dfsha:lab uvicorn dfsha.{module}:app --host 0.0.0.0 --port {port} --ssl-certfile /certs/server.crt --ssl-keyfile /certs/server.key --log-level info')
        files['client/ca.crt']=(ROOT/'certs/client/ca.crt').read_bytes()
        files['client.env']=''.join(f'{k}={v}\n' for k,v in {**common,'DFSHA_MDS_URLS':','.join(mds),'DFSHA_USER':'demo','DFSHA_PASSWORD':state['secrets']['password']}.items()).encode()
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w:gz') as tar:
            for path,body in files.items():
                item=tarfile.TarInfo(path)
                item.size=len(body)
                item.mode=0o600
                tar.addfile(item,io.BytesIO(body))
        encoded=base64.b64encode(archive.getvalue()).decode()
        commands.insert(4, f"printf %s '{encoded}' | base64 -d | tar xz -C /opt/dfsha-runtime")
        commands.insert(5,"timeout 600 bash -c 'until test -f /opt/dfsha-runtime/build-ready; do sleep 3; done'")
        cmd=command(instance['id'],'\n'.join(commands))
        state['commands'][instance['id']]=cmd
        print(instance['name'],'configure',cmd,flush=True)
    save(state)
def wait_command(instance_id, command_id):
    for _ in range(300):
        try:
            item=result(instance_id,command_id)
        except session().client('ssm').exceptions.InvocationDoesNotExist:
            time.sleep(2)
            continue
        if item['Status'] in ['Success','Failed','Cancelled','TimedOut']:
            if item['Status']!='Success':
                raise RuntimeError(item.get('StandardOutputContent','')+'\n'+item.get('StandardErrorContent',''))
            return item['StandardOutputContent']
        time.sleep(2)
    raise TimeoutError(command_id)
def fault(node, operation):
    state=load()
    index=int(node.split('-')[1])-1
    if node=='dn-4': index=2
    instance=state['instances'][index]
    print(node,operation,wait_command(instance['id'],command(instance['id'],f'docker {operation} {node}')),flush=True)
def phase(phase_name):
    state=load()
    project=PROJECT
    source=base64.b64encode((project/'scripts/verify_deployment.py').read_bytes()).decode()
    instance=state['instances'][2]
    script=f'''set -eu
mkdir -p /opt/dfsha-runtime/evidence
printf %s '{source}' | base64 -d > /opt/dfsha-runtime/verify_deployment.py
docker run --rm --network host --env-file /opt/dfsha-runtime/client.env -v /opt/dfsha-runtime/client:/certs:ro -v /opt/dfsha-runtime/evidence:/evidence -v /opt/dfsha-runtime/verify_deployment.py:/verify.py:ro dfsha:lab python /verify.py {phase_name} --output /evidence/{phase_name}.json
'''
    cmd=command(instance['id'],script)
    print('phase',phase_name,'command',cmd,flush=True)
    output=wait_command(instance['id'],cmd)
    report=json.loads(output.strip().splitlines()[-1])
    destination=project/'evidence/aws'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/(phase_name+'.json')).write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)
def diagnostic():
    jobs=[(i,command(i['id'],'tail -n 8 /var/log/cloud-init-output.log; docker ps --format "{{.Names}} {{.Status}}"')) for i in load()['instances']]
    for instance,cmd in jobs:
        print(instance['name'],wait_command(instance['id'],cmd),flush=True)
def start():
    state=load()
    ids=[i['id'] for i in state['instances']]
    print(session().client('ec2').start_instances(InstanceIds=ids)['StartingInstances'])
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['provision','status','probe','results','stop','start','configure','phase','fault','diagnostic'])
    parser.add_argument('--phase')
    parser.add_argument('--node')
    parser.add_argument('--operation',choices=['start','stop'],default='stop')
    args=parser.parse_args()
    if args.action=='phase': phase(args.phase)
    elif args.action=='fault': fault(args.node,args.operation)
    elif args.action=='configure': configure(args.node)
    else: globals()[args.action]()
