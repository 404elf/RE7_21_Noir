"""Real packaged-program verification for layout migration and authoritative timing."""
import json
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
os.environ['RE7_BIND']='127.0.0.1'
os.environ['RE7_PORT']='16451'
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import threading
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from re7_21 import recv_msg,send_msg
package=ROOT/'dist/v1.5.0/RE7_21_Noir'
work=ROOT/('build/player-package-migration-'+uuid.uuid4().hex[:8])
shutil.copytree(package,work)
# Simulate the root files copied by a v1.4.x updater into its compatibility asset.
metadata=(work/'_internal/version.json').read_bytes();(work/'version.json').write_bytes(metadata)
rules=json.loads((work/'custom/game/config.json').read_text(encoding='utf-8'))
rules['game_settings']['max_hp']=19
(work/'config.json').write_text(json.dumps(rules),encoding='utf-8')
(work/'timer.json').write_text(json.dumps(dict(enabled=True,mode='fischer',initial_minutes=5,increment_seconds=3,preparation_seconds=4)),encoding='utf-8')
exe=work/'RE7_21_Noir.exe'
subprocess.run([str(exe),'--preview','--screenshot',str(work.parent/'migrated-preview.png')],timeout=30,check=True)
assert not (work/'version.json').exists() and not (work/'config.json').exists()
assert json.loads((work/'custom/game/config.json').read_text())['game_settings']['max_hp']==19
backups=list((work/'userdata/migration-backups').glob('*/original/config.json'))
assert len(backups)==1 and json.loads(backups[0].read_text())['game_settings']['max_hp']==19
assert (work/'_internal/version.json').read_bytes()==metadata
assert all(p.is_dir() or p.name in ('RE7_21_Noir.exe','开始游戏.txt') for p in work.iterdir())
server=subprocess.Popen([str(exe),'--server'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
clients=[]
try:
    deadline=time.monotonic()+10
    while True:
        try:
            a=socket.create_connection(('127.0.0.1',16451),timeout=1);clients.append(a);break
        except OSError:
            if time.monotonic()>deadline:raise
            time.sleep(.1)
    a.settimeout(8);assert recv_msg(a)=='ID:1'
    b=socket.create_connection(('127.0.0.1',16451),timeout=3);clients.append(b);assert recv_msg(b)=='ID:2'
    def drain_guest():
        while recv_msg(b) is not None: pass
    threading.Thread(target=drain_guest,daemon=True).start()
    def until(predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            state=recv_msg(a)
            assert state is not None,'Server disconnected before the expected state'
            if predicate(state):return state
        raise AssertionError('State timeout')
    state=until(lambda s:True)
    assert state.max_hp_limit==19 and state.p1_fingers==19
    assert state.clock_remaining[1]==300 and state.preparation_active==1
    count=len(state.p1_trumps)
    send_msg(a,'DISCARD:0:1')
    state=until(lambda s:len(s.p1_trumps)==count-1)
    assert state.clock_remaining[1]<=300 and state.clock_remaining[1]>299
    assert state.clock_active==1 and state.preparation_active==0
    time.sleep(.65);send_msg(a,'STAY:1')
    state=until(lambda s:s.turn==2)
    assert 301<state.clock_remaining[1]<=303 and state.clock_remaining[2]==300
    assert state.preparation_active==2
    state=until(lambda s:s.phase=='GAMEOVER')
    assert state.end_reason=='preparation_timeout' and state.round_winner==1
    assert state.blood_loss[2]==0
    print('PASS: real EXE migration backups, clean root, custom rules, preparation, discard, STAY increment and preparation forfeit')
finally:
    for c in clients:c.close()
    server.terminate();server.wait(timeout=5)
