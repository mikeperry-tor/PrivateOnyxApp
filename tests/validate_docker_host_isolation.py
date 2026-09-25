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

# A test-owned upstream sits outside the internal networks. Positive controls
# join its network; negative controls must not deliver queries across the boundary.
DNS_RECEIVER = r'''
import ipaddress,json,socket,struct,threading,time
def answer(packet):
 pos=12;labels=[]
 while packet[pos]:
  size=packet[pos];assert size<64
  labels.append(packet[pos+1:pos+1+size].decode('ascii'));pos+=size+1
 pos+=1
 qtype,qclass=struct.unpack('!HH',packet[pos:pos+4])
 name='.'.join(labels)
 assert name.endswith('.invalid') and qclass==1
 print(json.dumps({'dns_query':name,'qtype':qtype}),flush=True)
 raw=ipaddress.ip_address('198.51.100.10' if qtype==1 else '2001:db8::10').packed
 assert qtype in (1,28)
 return packet[:2]+struct.pack('!HHHHH',0x8180,1,1,0,0)+packet[12:pos+4]+struct.pack('!HHHIH',0xc00c,qtype,1,0,len(raw))+raw
def read_exact(conn,count):
 data=b''
 while len(data)<count:
  part=conn.recv(count-len(data));assert part
  data+=part
 return data
udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('0.0.0.0',53))
tcp=socket.socket(socket.AF_INET,socket.SOCK_STREAM);tcp.bind(('0.0.0.0',53));tcp.listen()
def serve_udp():
 while True:
  packet,peer=udp.recvfrom(4096);udp.sendto(answer(packet),peer)
def serve_tcp():
 while True:
  conn,peer=tcp.accept()
  with conn:
   conn.settimeout(5)
   packet=read_exact(conn,struct.unpack('!H',read_exact(conn,2))[0])
   response=answer(packet);conn.sendall(struct.pack('!H',len(response))+response)
threading.Thread(target=serve_udp,daemon=True).start()
threading.Thread(target=serve_tcp,daemon=True).start()
print(json.dumps({'dns_ready':True}),flush=True)
time.sleep(600)
'''
DNS_CLIENT = r'''
import json,socket,struct,sys
args=json.loads(sys.argv[1]);results=[]
# Positive internal-name control uses the actual container resolver.
assert socket.getaddrinfo(args['peer'],53,type=socket.SOCK_STREAM)
servers=[line.split()[1] for line in open('/etc/resolv.conf') if line.startswith('nameserver ')]
assert servers==['127.0.0.11'],servers
def read_exact(conn,count):
 data=b''
 while len(data)<count:
  part=conn.recv(count-len(data));assert part
  data+=part
 return data
for transport in ('udp','tcp'):
 for qtype in (1,28):
  name=f"{args['token']}-{transport}-{qtype}.invalid"
  qname=b''.join(bytes([len(label)])+label.encode() for label in name.split('.'))+b'\0'
  packet=struct.pack('!HHHHHH',1234,0x100,1,0,0,0)+qname+struct.pack('!HH',qtype,1)
  with socket.socket(socket.AF_INET,socket.SOCK_DGRAM if transport=='udp' else socket.SOCK_STREAM) as sock:
   sock.settimeout(5);sock.connect((servers[0],53))
   if transport=='udp': sock.send(packet);response=sock.recv(4096)
   else:
    sock.sendall(struct.pack('!H',len(packet))+packet)
    response=read_exact(sock,struct.unpack('!H',read_exact(sock,2))[0])
  ident,flags,questions,answers,authority,additional=struct.unpack('!HHHHHH',response[:12])
  assert ident==1234 and flags&0x8000
  rcode=flags&15
  if args['forward']: assert rcode==0 and answers==1,(name,rcode,answers)
  else: assert rcode in (2,3,5) and answers==0,(name,rcode,answers)
  results.append({'name':name,'transport':transport,'qtype':qtype,'rcode':rcode,'answers':answers})
print(json.dumps({'internal_dns':True,'queries':results}))
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
    def create(name,network,code,*,uid='65534:65534',payload=None,dns=None):
        command=['create','--pull','never','--name',name,'--label',f'private-onyx.probe={prefix}',
                 '--network',network,'--user',uid,'--cap-drop','NET_RAW','--cap-drop','NET_ADMIN',
                 '--security-opt','no-new-privileges:true','--entrypoint','python']
        if dns is not None: command+=['--dns',dns]
        command += [args.image,'-u','-c',code]
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
    def dns_checks(pair,isolated):
        uplink=prefix+f'-dns-control-{isolated}'
        call('network','create','--label',f'private-onyx.probe={prefix}',uplink)
        networks.append(uplink)
        peer=prefix+f'-dns-peer-{isolated}'
        receiver(peer,pair[0])
        server=create(prefix+f'-dns-{isolated}',uplink,DNS_RECEIVER)
        call('start',server)
        for _ in range(30):
            if any(json.loads(line).get('dns_ready') for line in call('logs',server).splitlines()):
                break
            time.sleep(.1)
        else:
            raise RuntimeError('test DNS receiver did not start')
        address=json.loads(call('inspect',server,'--format','{{json .NetworkSettings.Networks}}'))[uplink]['IPAddress']
        cases=[]
        for forward,uid in ((True,'65534:65534'),(False,'65534:65534'),(False,'0:0'),(True,'65534:65534')):
            token=f'{prefix}-dns-{len(cases)}-{isolated}'.lower()
            subject=create(token,pair[0],DNS_CLIENT,uid=uid,dns=address,
                           payload={'peer':peer,'token':token,'forward':forward})
            call('network','connect',uplink if forward else pair[1],subject)
            result=json.loads(call('start','-a',subject))
            deliveries=[json.loads(line) for line in call('logs',server).splitlines()
                        if json.loads(line).get('dns_query','').startswith(token+'-')]
            expected={query['name'] for query in result['queries']} if forward else set()
            if {item['dns_query'] for item in deliveries} != expected:
                raise RuntimeError('DNS forwarding boundary failed: '+json.dumps(deliveries))
            cases.append({'external_access':forward,'uid':uid,'result':result,'upstream_deliveries':deliveries})
        # Re-read after the trailing positive control to catch delayed deliveries.
        all_deliveries=[json.loads(line) for line in call('logs',server).splitlines() if 'dns_query' in json.loads(line)]
        forbidden={query['name'] for case in cases if not case['external_access'] for query in case['result']['queries']}
        if any(item['dns_query'] in forbidden for item in all_deliveries):
            raise RuntimeError('internal DNS query reached the test upstream')
        report.setdefault('dns_cases',[]).append({'isolated_gateway':isolated,'cases':cases})
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
            dns_checks(pair,isolated)
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
