#!/usr/bin/env python3
"""Opt-in, disposable receiver-side host delivery probes; never a startup gate.

Uses an explicitly selected local Python image. Does not pull, mutate host
firewalls/services, or inspect private mounts. JSON output is qualification
evidence, not a blanket platform security verdict.
"""
from __future__ import annotations
import argparse
import ipaddress
import json
import subprocess
import time
import uuid

RECEIVER = r'''
import json,socket,threading,time
sockets=[]
ports={}
def receive(sock,kind):
 while True:
  if kind=='tcp':
   conn,addr=sock.accept()
   conn.settimeout(2)
   try: data=conn.recv(256);conn.sendall(data)
   except OSError: data=b''
   finally: conn.close()
  else:
   data,addr=sock.recvfrom(256)
   sock.sendto(data,addr)
  if data.startswith(b'private-onyx-probe-'):
   print(json.dumps({'received':data.decode(),'family':sock.family,'kind':kind}),flush=True)
for family,host in ((socket.AF_INET,'0.0.0.0'),(socket.AF_INET6,'::')):
 for kind,stype in (('tcp',socket.SOCK_STREAM),('udp',socket.SOCK_DGRAM)):
  sock=socket.socket(family,stype)
  if family==socket.AF_INET6: sock.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
  sock.bind((host,0))
  if kind=='tcp': sock.listen()
  ports[f'{family}:{kind}']=sock.getsockname()[1]
  sockets.append(sock)
  threading.Thread(target=receive,args=(sock,kind),daemon=True).start()
print(json.dumps({'ports':ports}),flush=True)
time.sleep(600)
'''
CLIENT = r'''
import json,os,socket,sys
args=json.loads(sys.argv[1]);results=[]
status={line.split(':')[0]:line.split(':')[1].strip() for line in open('/proc/self/status') if line.startswith(('Uid:','Gid:','CapEff:','CapBnd:','NoNewPrivs:'))}
for key in ('CapEff','CapBnd'):
 assert not int(status[key],16)&((1<<12)|(1<<13)),status
for family,kind,host,port in args['targets']:
 sock=socket.socket(family,socket.SOCK_STREAM if kind=='tcp' else socket.SOCK_DGRAM)
 sock.settimeout(.4)
 if family==socket.AF_INET: sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
 try:
  sock.connect((host,port))
  sock.send(args['token'].encode())
  reply=sock.recv(256)
  result='echo' if reply==args['token'].encode() else 'unexpected'
 except OSError as e: result=type(e).__name__
 finally: sock.close()
 results.append({'family':family,'kind':kind,'host':host,'result':result})
raw=[]
for family,kind,proto in ((socket.AF_PACKET,socket.SOCK_RAW,3),(socket.AF_INET,socket.SOCK_RAW,socket.IPPROTO_ICMP),(socket.AF_INET6,socket.SOCK_RAW,socket.IPPROTO_ICMPV6)):
 try: socket.socket(family,kind,proto).close();raw.append('allowed')
 except PermissionError: raw.append('denied')
assert raw==['denied']*3,raw
print(json.dumps({'credentials':status,'raw':raw,'probes':results}))
'''
HOST_FACTS = r'''
import json,socket,fcntl,struct,pathlib
addresses=[]
for index,name in socket.if_nameindex():
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
 try: addresses.append(socket.inet_ntoa(fcntl.ioctl(s.fileno(),0x8915,struct.pack('256s',name.encode()[:15]))[20:24]))
 except OSError: pass
 finally: s.close()
ipv6=[]
for line in pathlib.Path('/proc/net/if_inet6').read_text().splitlines():
 value,index,plen,scope,flags,name=line.split()
 ipv6.append({'address':socket.inet_ntop(socket.AF_INET6,bytes.fromhex(value)),'interface':name})
print(json.dumps({'ipv4':addresses,'ipv6':ipv6,'interfaces':[name for _,name in socket.if_nameindex()]}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container-bin',default='docker')
    parser.add_argument('--image',required=True)
    parser.add_argument('--service',action='append',default=[],help='Running Python container to probe via exec, with its own privileges and networks')
    args=parser.parse_args()
    if 'podman' in args.container_bin:
        parser.error('this harness tests Docker gateway semantics only')
    def call(*argv):
        result = subprocess.run([args.container_bin,*argv],capture_output=True,text=True,timeout=90)
        if result.returncode:
            raise RuntimeError('probe command failed: '+result.stderr.strip())
        return result.stdout.strip()
    identity=json.loads(call('version','--format','{{json .Server}}'))
    if int(identity['Version'].split('.')[0])<28:
        parser.error('isolated gateway probes require Docker Engine 28+')
    image_id=call('image','inspect',args.image,'--format','{{.Id}}')
    prefix='private-onyx-probe-'+uuid.uuid4().hex[:12]
    containers=[];networks=[]
    report={'server':{k:identity.get(k) for k in ('Version','Os','Arch','ApiVersion')},'image':image_id,'security_options':json.loads(call('info','--format','{{json .SecurityOptions}}')),'cases':[]}
    def create(name,network,code,*,uid='65534:65534',payload=None):
        command=['create','--pull','never','--name',name,'--label',f'private-onyx.probe={prefix}',
                 '--network',network,'--user',uid,'--cap-drop','NET_RAW','--cap-drop','NET_ADMIN',
                 '--security-opt','no-new-privileges:true','--entrypoint','python',args.image,'-u','-c',code]
        if payload is not None: command.append(json.dumps(payload))
        call(*command);containers.append(name)
        return name
    def receiver(name,network):
        create(name,network,RECEIVER)
        call('start',name)
        for _ in range(30):
            for line in call('logs',name).splitlines():
                data=json.loads(line)
                if 'ports' in data: return data['ports']
            time.sleep(.1)
        raise RuntimeError('test receiver did not start')
    def received(name,token):
        return [json.loads(line) for line in call('logs',name).splitlines() if json.loads(line).get('received')==token]
    try:
        host=prefix+'-host';host_ports=receiver(host,'host')
        facts_name=create(prefix+'-facts','host',HOST_FACTS)
        facts=json.loads(call('start','-a',facts_name));report['host_network']=facts
        for isolated in (False,True):
            pair=[]
            used=[ipaddress.ip_network(c['Subnet']) for n in json.loads(call('network','inspect',*call('network','ls','-q').split())) for c in (n.get('IPAM',{}).get('Config') or []) if c.get('Subnet')]
            for index in range(2):
                name=f'{prefix}-{isolated}-{index}'
                options=['network','create','--internal','--ipv6','--label',f'private-onyx.probe={prefix}']
                subnet=next(s for s in ipaddress.ip_network('198.18.0.0/15').subnets(new_prefix=28) if not any(s.version==u.version and s.overlaps(u) for u in used))
                used.append(subnet)
                v6=f'fd{uuid.uuid4().hex[:2]}:{uuid.uuid4().hex[:4]}:{uuid.uuid4().hex[:4]}::/64'
                options+=['--subnet',str(subnet),'--subnet',v6]
                if isolated:
                    for version in (4,6): options+=['--opt',f'com.docker.network.bridge.gateway_mode_ipv{version}=isolated']
                call(*options,name);networks.append(name);pair.append(name)
            peer=prefix+f'-peer-{isolated}';peer_ports=receiver(peer,pair[0])
            peer_network=json.loads(call('inspect',peer,'--format','{{json .NetworkSettings.Networks}}'))[pair[0]]
            inventory=[json.loads(call('network','inspect',n))[0] for n in pair]
            report.setdefault('networks',[]).extend({k:n.get(k) for k in ('Name','Internal','EnableIPv6','IPAM','Options')} for n in inventory)
            facts=json.loads(call('exec',host,'python','-c',HOST_FACTS))
            report.setdefault('host_network_snapshots',[]).append(facts)
            addresses=set(facts['ipv4'])-{'127.0.0.1'}
            addresses.add('255.255.255.255')
            for network in inventory:
                for config in network['IPAM']['Config']:
                    subnet=ipaddress.ip_network(config['Subnet'])
                    if subnet.version==4:
                        addresses.add(str(subnet.broadcast_address))
                        if config.get('Gateway'): addresses.add(config['Gateway'])
            targets=[(2,kind,address,host_ports[f'2:{kind}']) for address in sorted(addresses) for kind in ('tcp','udp')]
            for address in facts['ipv6']:
                ip=ipaddress.ip_address(address['address'])
                if not ip.is_loopback and not ip.is_link_local:
                    targets.extend((10,kind,str(ip),host_ports[f'10:{kind}']) for kind in ('tcp','udp'))
            # Scoped multicast/link-local probes are bounded to this disposable network.
            targets.extend((10,'udp',address,host_ports['10:udp']) for address in ('ff02::1%eth0','fe80::1%eth0'))
            for uid in ('65534:65534','0:0'):
                token=f'{prefix}-{isolated}-{uid}'
                subject=create(prefix+f'-client-{isolated}-{uid.split(":")[0]}',pair[0],CLIENT,uid=uid,payload={'token':token,'targets':targets})
                call('network','connect',pair[1],subject)
                result=json.loads(call('start','-a',subject))
                deliveries=received(host,token)
                if isolated and deliveries: raise RuntimeError('isolated fixture delivered to the host receiver: '+json.dumps(deliveries))
                if not isolated and not {'tcp','udp'} <= {item['kind'] for item in deliveries}:
                    raise RuntimeError('ordinary bridge positive control did not reach both host receivers')
                internal=[]
                for family,address in ((2,peer),(2,peer_network['IPAddress']),(10,peer_network['GlobalIPv6Address'])):
                    internal.extend((family,kind,address,peer_ports[f'{family}:{kind}']) for kind in ('tcp','udp'))
                positive=create(prefix+f'-internal-{isolated}-{uid.split(":")[0]}',pair[0],CLIENT,uid=uid,payload={'token':token,'targets':internal})
                connectivity=json.loads(call('start','-a',positive))
                if any(p['result']!='echo' for p in connectivity['probes']):
                    raise RuntimeError('internal DNS/TCP/UDP positive control failed: '+json.dumps(connectivity))
                report['cases'].append({'isolated':isolated,'uid':uid,'host_deliveries':deliveries,'host_probes':result,'internal_connectivity':connectivity})
        for service in args.service:
            facts=json.loads(call('exec',host,'python','-c',HOST_FACTS))
            targets=[(2,kind,address,host_ports[f'2:{kind}'])
                     for address in facts['ipv4'] if address!='127.0.0.1' for kind in ('tcp','udp')]
            for uid in (None,'0'):
                token=f'{prefix}-{service}-{uid}'
                command=['exec']
                if uid is not None: command+=['--user',uid]
                command += [service,'python3','-S','-c',CLIENT,json.dumps({'token':token,'targets':targets})]
                result=json.loads(call(*command))
                deliveries=received(host,token)
                if deliveries: raise RuntimeError('application delivered to host receiver: '+service)
                report.setdefault('actual_services',[]).append({'service':service,'exec_user':uid,'host_probes':result,'host_deliveries':deliveries})
        report['limitations']=['Disposable privilege fixtures; actual service and executor integration must be recorded separately.','Firewall backend/firewalld and complete host IPv6 link-local inventory require platform qualification.','No packet-capture or crafted-frame test with additional authority; raw sockets are denied under the tested bounding sets.']
        print(json.dumps(report,indent=2))
    finally:
        errors=[]
        for name in reversed(containers):
            try: call('rm','-f',name)
            except (subprocess.SubprocessError, RuntimeError) as exc: errors.append(str(exc))
        for name in reversed(networks):
            try: call('network','rm',name)
            except (subprocess.SubprocessError, RuntimeError) as exc: errors.append(str(exc))
        if errors: raise RuntimeError('test-owned resource cleanup failed: '+'; '.join(errors))

if __name__=='__main__': main()
