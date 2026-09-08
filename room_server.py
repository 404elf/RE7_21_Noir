"""Bounded multi-room service. TLS terminates here; no shell, files or relay targets from clients."""
import argparse
import asyncio
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import secrets
import ssl
import struct
import time
import re7_21 as engine
from config_editor import GAME
from match import Match, timer_config
from wire import MAGIC, encode, decode

MAX_ROOMS=32
MAX_CONNECTIONS=96
GRACE=30

def rules_checked(data):
    if not isinstance(data,dict) or set(data)-{'game_settings','trump_weights'}: raise ValueError('invalid_rules')
    result=copy.deepcopy(engine.GAME_CONFIG)
    settings=data.get('game_settings',{})
    weights=data.get('trump_weights',{})
    if not isinstance(settings,dict) or not isinstance(weights,dict) or len(weights)>100: raise ValueError('invalid_rules')
    known={row[0]:row for row in GAME}
    for key,value in settings.items():
        if key not in known: raise ValueError('invalid_rule')
        row=known[key]
        if type(value) not in (int,float) or not row[3]<=value<=row[4] or (row[6] and int(value)!=value): raise ValueError('invalid_rule')
        result['game_settings'][key]=int(value) if row[6] else float(value)
    for key,value in weights.items():
        if not isinstance(key,str) or len(key)>48 or type(value) not in (int,float) or not 0<=value<=10000: raise ValueError('invalid_weight')
        result['trump_weights'][key]=value
    conf=result['game_settings']
    if conf['deck_range_end']-conf['deck_range_start']<3: raise ValueError('invalid_deck')
    return result

@contextmanager
def rules_active(rules):
    # Game engine operations are synchronous on this event loop; never await here.
    old=(engine.SETTINGS,engine.WEIGHTS,engine.MAX_HP,engine.MAX_TRUMPS,engine.MAX_TABLE_SLOTS)
    engine.SETTINGS,engine.WEIGHTS=rules['game_settings'],rules['trump_weights']
    engine.MAX_HP=engine.SETTINGS['max_hp'];engine.MAX_TRUMPS=engine.SETTINGS['max_trumps_hand_size'];engine.MAX_TABLE_SLOTS=engine.SETTINGS['max_active_trumps_on_table']
    try: yield
    finally: engine.SETTINGS,engine.WEIGHTS,engine.MAX_HP,engine.MAX_TRUMPS,engine.MAX_TABLE_SLOTS=old

async def read(reader,timeout=15):
    header=await asyncio.wait_for(reader.readexactly(8),timeout)
    if header[:4]!=MAGIC: raise ValueError('version_mismatch')
    size=struct.unpack('>I',header[4:])[0]
    if not 0<size<=16384: raise ValueError('frame_limit')
    return decode(await asyncio.wait_for(reader.readexactly(size),5))

async def write(writer,value):
    writer.write(encode(value))
    await asyncio.wait_for(writer.drain(),2)

class Room:
    def __init__(self,code,request):
        self.code=code
        password=request.get('password','')
        if not isinstance(password,str) or len(password)>64: raise ValueError('invalid_password')
        self.password=password
        self.rules=rules_checked(request.get('rules',{}))
        self.timer=timer_config(request.get('timer',{}))
        self.tokens={1:secrets.token_urlsafe(32),2:secrets.token_urlsafe(32)}
        self.writers={}
        self.ready=set()
        self.match=None
        self.missing_since=None
        self.created=time.monotonic()
        self.updated=self.created
        self.closed=False
        self.tasks=set()

    def lobby(self):
        public_rules=dict(game_settings={row[0]:self.rules['game_settings'][row[0]] for row in GAME},trump_weights={k:v for k,v in self.rules['trump_weights'].items() if type(v) in (int,float)})
        return dict(op='lobby',room=self.code,players=len(self.writers),ready=sorted(self.ready),
                    rules=public_rules,timer=self.timer)

class RoomServer:
    def __init__(self):
        self.rooms={};self.connections=0;self.attempts={}

    async def client(self,reader,writer):
        room=None;pid=None
        peer=writer.get_extra_info('peername')[0]
        now=time.monotonic()
        self.attempts={k:v for k,v in self.attempts.items() if now-v[0]<60}
        count=self.attempts.get(peer,(now,0))
        if self.connections>=MAX_CONNECTIONS or count[1]>=30 or len(self.attempts)>=1024:
            writer.close();return
        self.attempts[peer]=(count[0],count[1]+1);self.connections+=1
        try:
            request=await read(reader,5)
            if not isinstance(request,dict): raise ValueError('version_mismatch')
            op=request.get('op')
            if op=='create':
                if len(self.rooms)>=MAX_ROOMS: raise ValueError('server_full')
                code=secrets.token_hex(4).upper()
                while code in self.rooms: code=secrets.token_hex(4).upper()
                room=Room(code,request);self.rooms[code]=room;pid=1
                task=asyncio.create_task(self.run(room));room.tasks.add(task)
            elif op in ('join','resume'):
                code=request.get('room')
                if not isinstance(code,str) or len(code)!=8: raise ValueError('room_unavailable')
                room=self.rooms.get(code.upper())
                if room is None or room.closed: raise ValueError('room_unavailable')
                if op=='join':
                    password=request.get('password','')
                    if not isinstance(password,str) or len(password)>64 or not secrets.compare_digest(password.encode(),room.password.encode()) or 2 in room.writers or room.match:
                        raise ValueError('room_unavailable')
                    pid=2
                else:
                    token=request.get('token','')
                    if not isinstance(token,str) or len(token)>100: raise ValueError('room_unavailable')
                    pid=next((p for p,t in room.tokens.items() if secrets.compare_digest(t,token)),None)
                    if pid is None or pid in room.writers: raise ValueError('room_unavailable')
            else: raise ValueError('invalid_operation')
            # Seat is installed before yielding so simultaneous joins cannot race.
            room.writers[pid]=writer
            await write(writer,dict(op='seat',room=room.code,pid=pid,token=room.tokens[pid]))
            start=time.monotonic();messages=0
            while not room.closed:
                value=await read(reader,15)
                now=time.monotonic()
                if now-start>=1: start=now;messages=0
                messages+=1
                if messages>30: raise ValueError('rate_limit')
                room.updated=now
                if isinstance(value,dict) and value.get('op')=='ping':
                    await write(writer,dict(op='pong',sent=value.get('sent',0)))
                elif isinstance(value,dict) and value.get('op')=='ready' and room.match is None:
                    room.ready.add(pid)
                elif isinstance(value,str) and len(value)<=80 and room.match and len(room.writers)==2:
                    with rules_active(room.rules): room.match.command(pid,value)
                else: raise ValueError('invalid_operation')
        except (ValueError,TypeError,KeyError,ConnectionError,OSError,asyncio.TimeoutError,asyncio.IncompleteReadError):
            pass
        finally:
            if room and pid and room.writers.get(pid) is writer:
                room.writers.pop(pid,None)
                if room.match is None: room.ready.discard(pid)
            self.connections-=1
            writer.close()

    async def run(self,room):
        try:
            while not room.closed:
                now=time.monotonic()
                if now-room.created>7200 or (room.match is None and now-room.created>300): break
                if room.match is None:
                    if room.ready=={1,2} and len(room.writers)==2:
                        with rules_active(room.rules): room.match=Match(room.timer)
                    payload=room.lobby()
                else:
                    paused=len(room.writers)<2
                    if paused:
                        if room.missing_since is None: room.missing_since=now
                        if now-room.missing_since>GRACE: break
                        # Freeze both action clocks and the settlement deadline.
                        elapsed=max(0,now-room.match.last)
                        room.match.last=now
                        if room.match.gs.phase=='RESULT': room.match.gs.result_timer+=elapsed
                    else:
                        room.missing_since=None
                        with rules_active(room.rules): room.match.tick()
                    room.match.gs.network_paused=paused
                    payload=room.match.gs
                for pid,writer in list(room.writers.items()):
                    try: await write(writer,payload)
                    except (OSError,ConnectionError,asyncio.TimeoutError,ValueError):
                        if room.writers.get(pid) is writer: room.writers.pop(pid,None)
                        writer.close()
                await asyncio.sleep(.1)
        finally:
            room.closed=True;self.rooms.pop(room.code,None)
            for writer in room.writers.values(): writer.close()

async def serve(config):
    host=config.get('bind','127.0.0.1');port=int(config.get('port',7443))
    context=None
    if config.get('certificate') and config.get('private_key'):
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(config['certificate'],config['private_key'])
    elif host not in ('127.0.0.1','::1'):
        raise ValueError('Public listeners require a TLS certificate and private key')
    service=RoomServer()
    server=await asyncio.start_server(service.client,host,port,ssl=context,limit=32768,backlog=64,**({'ssl_handshake_timeout':5} if context else {}))
    async with server: await server.serve_forever()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='room-server.json');args=parser.parse_args()
    asyncio.run(serve(json.loads(Path(args.config).read_text(encoding='utf-8-sig'))))
