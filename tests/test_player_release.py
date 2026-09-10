import copy
import hashlib
import io
import json
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import main
from app_paths import FILES,config_path,migrate_legacy
from config_editor import ConfigEditor
from match import Match
from history import describe
from bot import Strategy,observe
from tactics import Planner
from wire import encode,decode
from updater import install

class PlayerRelease(unittest.TestCase):
    def match(self,**kw):
        now=[100000.]
        m=Match(dict(enabled=True,mode='fischer',initial_minutes=5,increment_seconds=3,**kw),lambda:now[0],lambda:now[0])
        return m,now

    def test_preparation_actions_and_increment(self):
        for command in ('HIT','STAY','TRUMP:0','DISCARD:0'):
            m,now=self.match();m.gs.p1_hand=[1,2];m.gs.p1_trumps=[('Shield','SHIELD',1)]
            now[0]+=20
            self.assertTrue(m.command(1,command+':1'))
            self.assertFalse(m.preparation_pending[1])
            self.assertTrue(m.preparation_pending[2])
            self.assertEqual(m.remaining[1],303 if command in ('HIT','STAY') else 300)
            if command in ('TRUMP:0','DISCARD:0'):
                now[0]+=2;m.tick();self.assertEqual(m.remaining[1],298)
            else:
                now[0]+=20;m.tick();self.assertEqual(m.remaining[2],300)
                self.assertEqual(m.preparation_remaining[2],10)

    def test_invalid_move_and_late_move_do_not_escape_preparation(self):
        m,now=self.match()
        now[0]+=29
        self.assertFalse(m.command(1,'TRUMP:999:1'))
        self.assertTrue(m.preparation_pending[1]);self.assertEqual(m.remaining[1],300)
        now[0]+=1
        self.assertFalse(m.command(1,'STAY:1'))
        self.assertEqual(m.gs.end_reason,'preparation_timeout')
        self.assertEqual(m.gs.round_winner,2)
        self.assertEqual(m.gs.blood_loss,{1:0,2:0})

    def test_each_round_resets_preparation_not_match_bank(self):
        m,now=self.match(settlement_seconds=0)
        m.gs.p1_hand=[10,7];m.gs.p2_hand=[11,1]
        self.assertTrue(m.command(1,'STAY:1'));self.assertTrue(m.command(2,'STAY:1'))
        m.tick()
        self.assertEqual(m.gs.round_id,2)
        self.assertEqual(m.preparation_pending,{1:True,2:True})
        self.assertEqual(m.remaining,{1:303,2:303})
        self.assertEqual(m.preparation_remaining,{1:30,2:30})

    def test_unlimited_values_roundtrip_without_fake_large_number(self):
        m,now=self.match(preparation_seconds=None)
        now[0]+=100000;m.tick()
        self.assertEqual(m.gs.phase,'ACTION');self.assertEqual(m.remaining[1],300)
        m.timer['initial_minutes']=None;m.reset_clock();m.publish()
        loaded=decode(encode(m.gs)[8:])
        self.assertIsNone(loaded.clock_remaining[1]);self.assertIsNone(loaded.preparation_remaining[1])
        with tempfile.TemporaryDirectory() as root:
            e=ConfigEditor(root,main.engine.GAME_CONFIG);e.tab='timer'
            e.docs['timer.json'].update(initial_minutes=None,preparation_seconds=None)
            e.save();self.assertIsNone(json.loads((Path(root)/'timer.json').read_text())['initial_minutes'])

    def test_manual_inspection_survives_opponent_notification_flood(self):
        app=main.App()
        try:
            app.preview();m=Match();app.observe_notices(m.gs)
            app.action(('select',0))
            for i in range(30):m.record('trump',2,card='Shield')
            m.publish();app.observe_notices(m.gs);app.render()
            self.assertIsNone(app.notice);self.assertEqual(app.notice_queue,[]);self.assertEqual(app.selected,0)
            app.action(('effect','Go 24'))
            m.record('trump',2,card='Perfect');m.publish();app.observe_notices(m.gs);app.render()
            self.assertEqual(app.effect_selected,'Go 24');self.assertEqual(app.notice_queue,[])
        finally:main.pg.quit()

    def test_relative_logs_and_no_hand_transition_formula(self):
        e=dict(event='trump',pid=2,card='Return',before=[[1,5],[3,9]],after=[[1,5],[3]],target=21,target_before=21)
        self.assertTrue(describe(e,viewer=2).startswith('你'))
        self.assertTrue(describe(e,viewer=1).startswith('对手'))
        self.assertTrue(describe(dict(e,viewer=2),viewer=1).startswith('你'))  # A later seat change cannot invert old logs.
        self.assertNotIn('→',describe(e));self.assertNotIn('1 + 5',describe(e))
        self.assertTrue(describe(dict(event='hit',pid=1,drawn=[6]),viewer=2).startswith('对手'))

    def test_number_then_blind_hit_blocked_until_opponent_acts_or_return(self):
        for difficulty in ('hard','nightmare'):
            m=Match();g=m.gs;g.turn=2;g.p2_hand=[3,9];g.p1_hand=[1,7]
            g.deck=[2,4,5,6,8,10,11];g.p2_trumps=[('Four+','DRAW_SPEC_PLUS',4)]
            self.assertTrue(m.command(2,'TRUMP:0:1'))
            self.assertTrue(Planner.number_draw_pending(observe(g)))
            for seed in range(6):self.assertNotEqual(Strategy(difficulty,'gambler',seed).choose(observe(g)),'HIT')
            m.record('stay',1);m.publish();self.assertFalse(Planner.number_draw_pending(observe(g)))
        m=Match();g=m.gs;g.turn=2;g.p2_hand=[3,9];g.p1_hand=[4,7];g.deck=[1,2,5,6,8,10,11];g.p2_trumps=[('Four','DRAW_SPEC',4)]
        self.assertTrue(m.command(2,'TRUMP:0:1'))
        self.assertFalse(Planner.number_draw_pending(observe(g)))

    def test_random_locks_and_manual_target(self):
        with tempfile.TemporaryDirectory() as root:
            e=ConfigEditor(root,main.engine.GAME_CONFIG)
            e.docs['config.json']['game_settings']['max_hp']=73
            e.action(('cfg','lock',0));e.tab='weights';e.values()[e.rows()[0][0]]=123
            e.action(('cfg','lock',0))
            for _ in range(10):e.randomize()
            self.assertEqual(e.docs['config.json']['game_settings']['target_score'],21)
            self.assertEqual(e.docs['config.json']['game_settings']['max_hp'],73)
            self.assertEqual(e.get(e.rows()[0]),123)
            e.docs['config.json']['game_settings']['target_score']=24;e.randomize()
            self.assertEqual(e.docs['config.json']['game_settings']['target_score'],24)
            e.action(('cfg','unlock_all',None));e.randomize()
            self.assertNotEqual(e.docs['config.json']['game_settings']['max_hp'],73)

    def test_new_layout_update_and_legacy_migration_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'custom/game').mkdir(parents=True);(root/'_internal').mkdir()
            (root/'custom/game/config.json').write_text('new default')
            (root/'config.json').write_text('my settings')
            (root/'version.json').write_text('legacy metadata')
            (root/'_internal/version.json').write_text('new metadata')
            migrate_legacy(root)
            self.assertEqual(config_path(root,'config.json').read_text(),'my settings')
            self.assertFalse((root/'config.json').exists());self.assertFalse((root/'version.json').exists())
            self.assertEqual(config_path(root,'version.json').read_text(),'new metadata')
            self.assertEqual(next((root/'userdata/migration-backups').glob('*/original/config.json')).read_text(),'my settings')
            migrate_legacy(root)
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:
                for name,value in {'_internal/version.json':json.dumps(dict(product='RE7_21_Noir',version='1.5.0')),'custom/game/config.json':'next default','RE7_21_Noir.exe':'fixture'}.items():z.writestr('RE7_21_Noir/'+name,value)
            blob=stream.getvalue()
            exe=install(root,dict(tag='v1.5.0',url='mock',size=len(blob),digest=hashlib.sha256(blob).hexdigest()),lambda url:io.BytesIO(blob))
            self.assertEqual(config_path(exe.parent,'config.json').read_text(),'my settings')
            self.assertEqual((exe.parent/'userdata/package-defaults/config.json').read_text(),'next default')

if __name__=='__main__':unittest.main()
