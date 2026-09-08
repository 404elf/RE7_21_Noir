"""User-selected endpoints, authenticated TLS, and bounded LAN discovery."""
import ipaddress
import json
import secrets
import socket
import ssl
import threading
import time
import queue
from urllib.parse import urlsplit

DISCOVERY_PORT=16667

class EventQueue(queue.Queue):
    """Coalesce snapshots so a slow UI cannot accumulate unbounded network data."""
    def put(self,item,block=True,timeout=None):
        with self.mutex:
            if len(item)>=3 and item[1] in ('state','lobby','latency'):
                self.queue=type(self.queue)(v for v in self.queue if v[:2]!=item[:2])
            if len(self.queue)>=64: self.queue.popleft()
            self.queue.append(item);self.not_empty.notify()

def room_loop(connection,address,create,code,password,rules,timer):
    from wire import send_msg,recv_msg
    from re7_21 import GameState
    generation=connection.generation
    token=None;deadline=time.monotonic()+30
    def emit(event,value): connection.events.put((generation,event,value))
    def heartbeat():
        while generation==connection.generation:
            with connection.send_lock:
                if connection.sock: send_msg(connection.sock,dict(op='ping',sent=time.monotonic()))
            time.sleep(5)
    threading.Thread(target=heartbeat,daemon=True).start()
    while generation==connection.generation:
        sock=None
        try:
            sock=connect(address,7443,require_tls=True);sock.settimeout(15)
            request=dict(op='resume',room=code,token=token) if token else dict(op='create' if create else 'join',room=code,password=password,rules=rules,timer=timer)
            if not send_msg(sock,request): raise ConnectionError()
            seat=recv_msg(sock,16384)
            if not isinstance(seat,dict) or seat.get('op')!='seat' or seat.get('pid') not in (1,2) or not isinstance(seat.get('room'),str) or len(seat['room'])!=8 or not isinstance(seat.get('token'),str) or len(seat['token'])>100:
                raise ConnectionError('Room unavailable or incompatible version')
            token,code=seat['token'],seat['room']
            with connection.lock:
                if generation!=connection.generation: return
                connection.sock=sock
            emit('id',seat['pid']);emit('room',code)
            while generation==connection.generation:
                value=recv_msg(sock)
                if value is None: raise ConnectionError()
                deadline=time.monotonic()+30
                if isinstance(value,dict):
                    if value.get('op')=='lobby':
                        from config_editor import GAME
                        from match import timer_config
                        if not isinstance(value.get('rules'),dict) or not isinstance(value.get('ready'),list) or any(type(p) is not int or p not in (1,2) for p in value['ready']) or type(value.get('players')) is not int or not 0<=value['players']<=2: raise ConnectionError()
                        settings=value['rules'].get('game_settings')
                        if not isinstance(settings,dict): raise ConnectionError()
                        weights=value['rules'].get('trump_weights')
                        if not isinstance(weights,dict) or any(not isinstance(k,str) or type(v) not in (int,float) or not 0<=v<=10000 for k,v in weights.items()): raise ConnectionError()
                        for row in GAME:
                            v=settings.get(row[0])
                            if type(v) not in (int,float) or not row[3]<=v<=row[4] or row[6] and int(v)!=v: raise ConnectionError()
                        value['timer']=timer_config(value.get('timer',{}))
                        emit('lobby',value)
                    elif value.get('op')=='pong':
                        sent=value.get('sent')
                        if type(sent) in (int,float): emit('latency',max(0,min(9999,int((time.monotonic()-sent)*1000))))
                    else: raise ConnectionError()
                elif isinstance(value,GameState): emit('state',value)
                else: raise ConnectionError()
        except ssl.SSLCertVerificationError:
            if generation==connection.generation: emit('error','certificate')
            return
        except (OSError,ValueError,TypeError,KeyError) as exc:
            if generation!=connection.generation: return
            if token is None or time.monotonic()>=deadline:
                emit('error','tls_required' if str(exc)=='tls_required' else 'room_connection');return
            emit('reconnecting',max(0,int(deadline-time.monotonic())))
            time.sleep(1)
        finally:
            with connection.lock:
                if connection.sock is sock: connection.sock=None
            if sock: sock.close()

def endpoint(address, default_port=6666):
    if not isinstance(address,str) or len(address)>253 or any(c.isspace() for c in address): raise ValueError('invalid_address')
    parsed=urlsplit(address if '://' in address else 'tcp://'+address)
    if parsed.scheme not in ('tcp','tls') or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment: raise ValueError('invalid_address')
    host=parsed.hostname
    port=parsed.port or default_port
    if not host or not 1<=port<=65535: raise ValueError('invalid_address')
    return host,port,parsed.scheme=='tls'

def connect(address, default_port=6666, require_tls=False):
    host,port,tls=endpoint(address,default_port)
    if require_tls and not tls:
        # Only loopback is allowed for local room-server testing without TLS.
        try: local=ipaddress.ip_address(host).is_loopback
        except ValueError: local=False
        if not local: raise ValueError('tls_required')
    sock=socket.create_connection((host,port),timeout=5)
    try:
        if tls:
            context=ssl.create_default_context()
            context.minimum_version=ssl.TLSVersion.TLSv1_2
            sock=context.wrap_socket(sock,server_hostname=host)
        return sock
    except Exception:
        sock.close();raise

def discover(timeout=1.2):
    nonce=secrets.token_hex(12);found={}
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
        sock.settimeout(.2)
        packet=json.dumps(dict(service='RE7_DISCOVER_V2',nonce=nonce)).encode()
        sock.sendto(packet,('255.255.255.255',DISCOVERY_PORT))
        sock.sendto(packet,('127.0.0.1',DISCOVERY_PORT))
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline and len(found)<32:
            try:
                raw,peer=sock.recvfrom(512)
                data=json.loads(raw)
                if data.get('nonce')!=nonce or data.get('service')!='RE7_ROOM_V2': continue
                port=data.get('port')
                if type(port) is int and 1<=port<=65535:
                    address=f'{peer[0]}:{port}'
                    found[address]=address
            except (OSError,ValueError,AttributeError): continue
    return list(found.values())

def advertise(port,stop):
    def worker():
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            try: sock.bind(('',DISCOVERY_PORT))
            except OSError: return
            sock.settimeout(.3)
            last={}
            while not stop.is_set():
                try:
                    raw,peer=sock.recvfrom(256)
                    # Discovery has no files/URLs and never trusts a supplied host address.
                    if not ipaddress.ip_address(peer[0]).is_private: continue
                    now=time.monotonic()
                    if now-last.get(peer[0],0)<.5: continue
                    last={k:v for k,v in last.items() if now-v<3}
                    if len(last)>64: continue
                    last[peer[0]]=now
                    data=json.loads(raw)
                    nonce=data.get('nonce','')
                    if data.get('service')!='RE7_DISCOVER_V2' or not isinstance(nonce,str) or len(nonce)!=24: continue
                    sock.sendto(json.dumps(dict(service='RE7_ROOM_V2',nonce=nonce,port=port)).encode(),peer)
                except (OSError,ValueError,AttributeError): continue
    thread=threading.Thread(target=worker,daemon=True);thread.start()
    return thread
