import copy
import json
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from dataclasses import replace
import main
from bot import Strategy, observe
from config_editor import ConfigEditor
from history import describe
from match import Match


class Feedback(unittest.TestCase):
    def test_presets_random_edit_save_and_quiet_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            shutil.copytree(main.ROOT/'presets',root/'presets')
            editor=ConfigEditor(root,main.engine.GAME_CONFIG)
            for i in range(len(editor.presets)):
                editor.apply_preset(i)
                editor.validate()
            for _ in range(50):
                editor.randomize(); editor.validate()
                self.assertEqual(editor.docs['config.json']['game_settings']['deck_range_end'],11)
            editor.action(('cfg','field',0));editor.buffer='17';editor.save()
            self.assertEqual(json.loads((root/'config.json').read_text(encoding='utf-8'))['game_settings']['max_hp'],17)
            editor.action(('cfg','tab','presets'));editor.action(('cfg','save_preset',None))
            self.assertEqual(len(editor.presets),6)
            self.assertEqual(json.loads((root/'presets'/ (editor.preset_name+'.json')).read_text(encoding='utf-8'))['game_settings']['max_hp'],17)
            editor.action(('cfg','tab','audio'));editor.action(('cfg','quiet_audio',None));editor.save()
            self.assertTrue(all((main.ROOT/e['file']).exists() for e in editor.docs['audio.json']['events'].values()))

    def test_selection_dismisses_notices_and_preview_unique(self):
        app=main.App()
        try:
            app.preview()
            all_cards=app.gs.p1_hand+app.gs.p2_hand+app.gs.deck
            self.assertEqual(sorted(all_cards),list(range(1,12)))
            app.notice=('Shield',time.monotonic()+4);app.notice_queue=['Perfect']
            app.action(('select',0));app.render()
            self.assertIsNone(app.notice);self.assertEqual(app.notice_queue,[])
            app.editor=ConfigEditor(main.ROOT,main.engine.GAME_CONFIG)
            app.editor.tab='presets';app.editor.render(app)
        finally: main.pg.quit()

    def test_public_draw_log(self):
        match=Match();gs=match.gs
        before=gs.p1_hand[:]
        self.assertTrue(match.command(1,f'HIT:{gs.round_id}'))
        hit=next(e for e in match.log if e['event']=='hit')
        self.assertEqual(hit['drawn'],gs.p1_hand[len(before):])
        self.assertIn(str(hit['drawn'][0]),describe(hit))
        self.assertNotIn('hand',hit)

    def test_hard_uses_damage_shield_and_target_and_gambler_takes_risks(self):
        v=replace(observe(Match().gs),hand=(10,11),opponent_visible=(4,),opponent_stopped=True,
                  trumps=(('Add 2','ADD',2),),opponent_hp=10,outgoing=1)
        self.assertEqual(Strategy('hard','conservative',1).choose(v),'TRUMP:0')
        v=replace(v,hand=(2,3),hp=1,incoming=3,trumps=(('Shield+','SHIELD',2),))
        self.assertEqual(Strategy('hard','conservative',1).choose(v),'TRUMP:0')
        v=replace(v,hand=(10,11,3),trumps=(('Go 24','TARGET',24),))
        self.assertEqual(Strategy('hard','conservative',1).choose(v),'TRUMP:0')
        # Risk appetite applies to an uncertain lead; a proven win now waits.
        v=replace(v,hand=(10,8),opponent_visible=(9,),opponent_stopped=False,trumps=())
        bold=sum(Strategy('hard','gambler',seed).choose(v)=='HIT' for seed in range(200))
        safe=sum(Strategy('hard','conservative',seed).choose(v)=='HIT' for seed in range(200))
        self.assertGreater(bold,safe+40)
