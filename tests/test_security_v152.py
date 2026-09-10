import copy
import json
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from match import Match
from wire import encode,decode,recv_msg
from history import History,MAX_SEEN

class SecurityAudit(unittest.TestCase):
    def test_host_cannot_send_invalid_winner_into_renderer(self):
        base=json.loads(encode(Match().gs)[8:])
        for field,value in [('round_winner',3),('round_winner',-1),('round_starter',0),('round_id',-1)]:
            bad=copy.deepcopy(base);bad['data'][field]=value
            with self.assertRaises(ValueError):decode(json.dumps(bad).encode())
        bad=copy.deepcopy(base);bad['data'].update(phase='RESULT',round_winner=3,round_damage=1)
        raw=json.dumps(bad).encode();a,b=socket.socketpair()
        try:
            import struct
            a.sendall(b'R21J'+struct.pack('>I',len(raw))+raw)
            self.assertIsNone(recv_msg(b))
        finally:a.close();b.close()

    def test_log_extensions_invalid_winners_and_table_counter_rejected(self):
        base=json.loads(encode(Match().gs)[8:])
        bad_logs=[dict(id=1,round=1,event='gameover',pid=0,winner=[]),dict(id=1,round=1,event='hit',pid=1,padding='x'*100),dict(id=1,round=1,event='trump',pid=0,card='Shield')]
        for event in bad_logs:
            bad=copy.deepcopy(base);bad['data']['action_log']=[event]
            with self.assertRaises(ValueError):decode(json.dumps(bad).encode())
        bad=copy.deepcopy(base);bad['data']['active_trumps']=[dict(name='Waste',type='FORCE_CONSUME',owner=1,val=2,counter='bad')]
        with self.assertRaises(ValueError):decode(json.dumps(bad).encode())

    def test_forged_log_flood_has_bounded_memory_and_disk(self):
        with tempfile.TemporaryDirectory() as root,patch('history.MAX_LOG_FILE',2048):
            h=History(root);g=Match().gs
            for i in range(MAX_SEEN+100):
                g.action_log=[dict(id=i+1,round=1,event='stay',pid=1)]
                h.ingest(g)
            self.assertTrue(h.limited)
            self.assertLessEqual(h.path.stat().st_size,2048)
            self.assertLessEqual(len(h.seen),MAX_SEEN)
            self.assertLessEqual(len(h.entries),2000)
            before=h.path.read_bytes();h.ingest(g)
            self.assertEqual(h.path.read_bytes(),before)

    def test_total_log_quota_never_deletes_existing_logs(self):
        with tempfile.TemporaryDirectory() as root,patch('history.MAX_LOG_FOLDER',512):
            h=History(root);h.path.parent.mkdir()
            old=h.path.parent/'old.jsonl';old.write_bytes(b'x'*512)
            h.ingest(Match().gs)
            self.assertTrue(h.limited);self.assertEqual(old.read_bytes(),b'x'*512)
            self.assertEqual(h.path.stat().st_size,0)

if __name__=='__main__':unittest.main()
