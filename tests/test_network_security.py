import asyncio
import copy
import json
import os
from pathlib import Path
import pickle
import socket
import ssl
import struct
import tempfile
import subprocess
import time
import unittest
from unittest.mock import patch
import main
from match import Match
from networking import endpoint, connect, EventQueue
from room_server import RoomServer, read, write, rules_checked
from wire import encode,decode,recv_msg,MAX_FRAME

class ProtocolTests(unittest.TestCase):
    def test_pickle_cannot_execute_and_oversize_header_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            marker=Path(temp)/'executed'
            class Exploit:
                def __reduce__(self): return eval,(f"__import__('pathlib').Path({str(marker)!r}).write_text('unsafe')",)
            payload=pickle.dumps(Exploit())
            for packet in (struct.pack('>I',len(payload))+payload,b'R21J'+struct.pack('>I',MAX_FRAME+1)):
                a,b=socket.socketpair()
                try:
                    b.settimeout(.3);a.sendall(packet)
                    self.assertIsNone(recv_msg(b));self.assertFalse(marker.exists())
                finally:a.close();b.close()

    def test_state_roundtrip_and_method_injection_rejected(self):
        state=Match().gs
        new=decode(encode(state)[8:])
        self.assertEqual(new.p1_trumps,state.p1_trumps)
        self.assertEqual(new.last_action_time,state.last_action_time)
        self.assertEqual(new.calculate_potential_damage(1),state.calculate_potential_damage(1))
        payload=json.loads(encode(state)[8:])
        for key in ('__class__','__dict__','check_bust'):
            bad=copy.deepcopy(payload);bad['data'][key]='injected'
            with self.assertRaises(ValueError):decode(json.dumps(bad).encode())
        for key,value in (('active_trumps',[None]),('last_result',[]),('clock_remaining',{'1':'bad','2':1}),('result_timer','bad')):
            bad=copy.deepcopy(payload);bad['data'][key]=value
            with self.assertRaises(ValueError):decode(json.dumps(bad).encode())

    def test_bad_json_depth_version_nonfinite_and_duplicate_keys(self):
        for raw in (b'{"v":2,"v":2,"kind":"message","data":"a"}',b'{"v":1,"kind":"message","data":"a"}',b'{"v":2,"kind":"message","data":NaN}',b'['*3000+b'0'+b']'*3000):
            with self.assertRaises(ValueError):decode(raw)

    def test_endpoint_tls_and_no_automatic_unsafe_fallback(self):
        self.assertEqual(endpoint('[::1]:7443'),('::1',7443,False))
        self.assertEqual(endpoint('tls://example.com:7443'),('example.com',7443,True))
        for value in ('https://example.com','tcp://user:secret@host','host:99999','host/path','host?command=x'):
            with self.assertRaises(ValueError):endpoint(value)
        with patch('networking.socket.create_connection') as dial:
            with self.assertRaises(ValueError):connect('tcp://example.com:7443',require_tls=True)
            dial.assert_not_called()
        with patch('networking.socket.create_connection') as dial,patch('networking.ssl.create_default_context') as context:
            context.return_value.wrap_socket.side_effect=ssl.SSLCertVerificationError('bad certificate')
            with self.assertRaises(ssl.SSLCertVerificationError):connect('tls://example.com:7443',require_tls=True)
            dial.assert_called_once();dial.return_value.close.assert_called_once()
            self.assertEqual(context.return_value.wrap_socket.call_args.kwargs['server_hostname'],'example.com')

    def test_snapshot_queue_bounded_and_rules_reject_external_paths(self):
        q=EventQueue()
        q.put((1,'id',2))
        for i in range(10000):q.put((1,'state',i))
        self.assertEqual(q.qsize(),2);self.assertEqual(q.get()[1],'id');self.assertEqual(q.get()[2],9999)
        for data in ({'file':'../../config.json'},{'game_settings':{'max_hp':999999}},{'trump_weights':{'Perfect':'shell'}}):
            with self.assertRaises(ValueError):rules_checked(data)

    def test_network_panels_and_read_only_rules_render(self):
        app=main.App()
        try:
            app.action('network')
            for language in (True,False):
                app.zh=language
                for mode in ('lan','rooms','direct','server'):
                    app.network.mode=mode;app.render()
                    self.assertTrue(app.buttons)
            app.net_lobby=dict(rules=dict(game_settings=main.engine.SETTINGS,trump_weights={k:v for k,v in main.engine.WEIGHTS.items() if type(v) in (int,float)}),timer={})
            app.overlay='network_rules';app.render()
            app.action(('rules_page',1));app.render()
        finally:main.pg.quit()

    def test_real_client_room_handshake_and_automatic_reconnect(self):
        from test_app import wait
        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            config=Path(folder)/'room.json';config.write_text(json.dumps(dict(bind='127.0.0.1',port=port)))
            executable=os.environ.get('RE7_TEST_ROOM_EXE')
            argv=[executable,'--room-server',str(config)] if executable else [main.sys.executable,str(main.ROOT/'room_server.py'),'--config',str(config)]
            proc=subprocess.Popen(argv,cwd=main.ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            host,guest=main.Connection(),main.Connection()
            try:
                deadline=time.monotonic()+5
                while True:
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                    except OSError:
                        if time.monotonic()>deadline:raise
                        time.sleep(.1)
                host.open_room(f'127.0.0.1:{port}',create=True)
                self.assertEqual(wait(host,lambda e,v:e=='id'),1)
                code=wait(host,lambda e,v:e=='room')
                guest.open_room(f'127.0.0.1:{port}',code=code)
                self.assertEqual(wait(guest,lambda e,v:e=='id'),2)
                wait(host,lambda e,v:e=='lobby' and v['players']==2)
                host.ready();guest.ready()
                state=wait(host,lambda e,v:e=='state')
                old=guest.sock;old.shutdown(socket.SHUT_RDWR);old.close()
                wait(host,lambda e,v:e=='state' and v.network_paused)
                self.assertEqual(wait(guest,lambda e,v:e=='id',seconds=10),2)
                resumed=wait(guest,lambda e,v:e=='state' and not v.network_paused)
                self.assertEqual(state.p1_hand,resumed.p1_hand)
            finally:
                host.close();guest.close();proc.terminate();proc.wait(timeout=5)

class RoomTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service=RoomServer()
        self.server=await asyncio.start_server(self.service.client,'127.0.0.1',0)
        self.port=self.server.sockets[0].getsockname()[1];self.clients=[]

    async def asyncTearDown(self):
        for writer in self.clients:writer.close()
        for room in list(self.service.rooms.values()):room.closed=True
        self.server.close();await self.server.wait_closed();await asyncio.sleep(.15)

    async def client(self,request):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        self.clients.append(writer);await write(writer,request)
        return reader,writer,await read(reader,2)

    async def until(self,reader,predicate):
        for _ in range(60):
            value=await read(reader,3)
            if predicate(value):return value
        self.fail('Expected room event not received')

    async def test_ready_password_reconnect_and_paused_clock(self):
        r1,w1,seat1=await self.client(dict(op='create',password='test',timer=dict(enabled=True,turn_seconds=60)))
        with self.assertRaises(asyncio.IncompleteReadError):await self.client(dict(op='join',room=seat1['room'],password='wrong'))
        r2,w2,seat2=await self.client(dict(op='join',room=seat1['room'],password='test'))
        lobby=await self.until(r1,lambda v:isinstance(v,dict) and v.get('players')==2)
        self.assertEqual(lobby['ready'],[])
        await write(w1,dict(op='ready'));await write(w2,dict(op='ready'))
        first=await self.until(r1,lambda v:isinstance(v,main.GameState))
        await write(w1,f'HIT:{first.round_id}')
        hit=await self.until(r1,lambda v:isinstance(v,main.GameState) and any(e['event']=='hit' for e in v.action_log))
        self.assertEqual(len(hit.p1_hand),len(first.p1_hand)+1)
        w2.close();await w2.wait_closed()
        paused=await self.until(r1,lambda v:isinstance(v,main.GameState) and v.network_paused)
        later=await self.until(r1,lambda v:isinstance(v,main.GameState) and v.network_paused)
        self.assertEqual(paused.clock_remaining,later.clock_remaining)
        r3,w3,seat3=await self.client(dict(op='resume',room=seat2['room'],token=seat2['token']))
        self.assertEqual(seat3['pid'],2)
        resumed=await self.until(r3,lambda v:isinstance(v,main.GameState) and not v.network_paused)
        self.assertEqual(resumed.p1_hand,hit.p1_hand)

    async def test_two_rooms_do_not_share_rules_and_invalid_peer_isolated(self):
        r1,w1,s1=await self.client(dict(op='create',rules=dict(game_settings=dict(max_hp=5))))
        r2,w2,s2=await self.client(dict(op='create',rules=dict(game_settings=dict(max_hp=20))))
        a=await self.until(r1,lambda v:isinstance(v,dict) and v.get('op')=='lobby')
        b=await self.until(r2,lambda v:isinstance(v,dict) and v.get('op')=='lobby')
        self.assertEqual(a['rules']['game_settings']['max_hp'],5)
        self.assertEqual(b['rules']['game_settings']['max_hp'],20)
        badr,badw=await asyncio.open_connection('127.0.0.1',self.port);self.clients.append(badw)
        badw.write(b'R21J'+struct.pack('>I',2**31));await badw.drain()
        self.assertEqual(await asyncio.wait_for(badr.read(),2),b'')
        await write(w1,dict(op='ping',sent=1))
        self.assertEqual((await self.until(r1,lambda v:isinstance(v,dict) and v.get('op')=='pong'))['sent'],1)
