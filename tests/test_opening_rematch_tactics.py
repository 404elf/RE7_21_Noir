"""Opening-only preparation, consistent rematches, and crowded-board tactics."""
import copy
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch
from bot import Strategy, observe
from cards import info, english_name
from match import Match
from room_server import rules_active, rules_checked
from config_editor import GAME
import re7_21 as engine


class MatchConsistency(unittest.TestCase):
    def test_all_presets_and_custom_caps_rematch_like_fresh_launch(self):
        root=Path(__file__).resolve().parents[1]
        configs=[(p.name,json.loads(p.read_text(encoding='utf-8'))) for p in (root/'presets').glob('*.json')]
        custom=copy.deepcopy(engine.GAME_CONFIG)
        custom['game_settings'].update(initial_trumps_count=19,round_reward_trumps_count=4,max_trumps_hand_size=5,target_score=24,max_hp=37)
        configs.append(('custom cap',custom))
        for name,config in configs:
            public=dict(game_settings={row[0]:config['game_settings'][row[0]] for row in GAME},trump_weights={k:v for k,v in config['trump_weights'].items() if type(v) in (int,float)})
            with self.subTest(preset=name), rules_active(rules_checked(public)):
                now=[10000.];random.seed(74)
                m=Match(dict(enabled=True,mode='fischer',initial_minutes=5),lambda:now[0],lambda:now[0]);g=m.gs
                expected={k:copy.deepcopy(getattr(g,k)) for k in ('p1_hand','p2_hand','p1_trumps','p2_trumps','deck','p1_fingers','p2_fingers','target_score','round_id','turn','max_hp_limit')}
                count=min(engine.MAX_TRUMPS,engine.SETTINGS['initial_trumps_count']+engine.SETTINGS['round_reward_trumps_count'])
                self.assertEqual(len(g.p1_trumps),count)
                for repeat in range(3):
                    g.p1_fingers=1;g.p1_trumps=[];g.target_score=17;g.round_id=29
                    m.preparation_pending={1:False,2:False};m.remaining={1:1,2:2}
                    self.assertTrue(m.command(1,'SURRENDER:29'))
                    self.assertTrue(m.command(1,'REMATCH:29'))
                    random.seed(74)
                    self.assertTrue(m.command(2,'REMATCH:29'))
                    self.assertEqual({k:getattr(g,k) for k in expected},expected)
                    self.assertEqual(m.preparation_pending,{1:True,2:True})
                    self.assertEqual(m.remaining,{1:300,2:300})
                    self.assertEqual(g.last_action_time,{1:0.,2:0.})
                    self.assertIsNone(g.last_result)
                    self.assertEqual(len(g.action_log),1)

    def test_next_round_charges_main_clock_and_oblivion_does_not_restart_prep(self):
        now=[10000.]
        m=Match(dict(enabled=True,mode='fischer',initial_minutes=5,settlement_seconds=0),lambda:now[0],lambda:now[0])
        m.gs.p1_hand=[10,7];m.gs.p2_hand=[11,1]
        m.command(1,'STAY:1');m.command(2,'STAY:1');m.tick()
        self.assertEqual(m.gs.preparation_active,0)
        active=m.gs.turn;before=m.remaining[active];now[0]+=4;m.tick()
        self.assertEqual(m.remaining[active],before-4)
        m.gs.p1_trumps=[('Oblivion','OBLIVION',0)];m.gs.turn=1
        now[0]+=1
        self.assertTrue(m.command(1,'TRUMP:0:2'))
        self.assertEqual(m.gs.round_id,3)
        self.assertEqual(m.preparation_pending,{1:False,2:False})
        self.assertEqual(m.gs.preparation_active,0)

    def test_card_display_name_keeps_saved_weight_key(self):
        self.assertEqual(info('ADD2+')[0],'加注2+')
        self.assertEqual(english_name('ADD2+'),'ADD2+')
        self.assertIn('Return+',engine.WEIGHTS)


class CrowdedBoardTactics(unittest.TestCase):
    def position(self):
        m=Match();g=m.gs;g.turn=2;g.p1_stop=True;g.p1_trumps=[];g.p2_fingers=3
        return m,g

    def choose(self,g):
        g.deck=[n for n in range(1,12) if n not in g.p1_hand+g.p2_hand]
        return Strategy('nightmare','swing',3).choose(observe(g))

    def test_full_table_target_replacement_rescues_bust(self):
        with patch.object(engine,'MAX_TABLE_SLOTS',2):
            m,g=self.position();g.p2_hand=[10,8,6];g.p1_hand=[1,11];g.target_score=17
            g.active_trumps=[dict(owner=2,name='Go 17',type='TARGET',val=17),dict(owner=2,name='Add 2',type='ADD',val=2)]
            g.p2_trumps=[('Go 24','TARGET',24)];g.p1_fingers=3
            action=self.choose(g)
            self.assertEqual(action,'TRUMP:0')
            self.assertTrue(m.command(2,action+':1'))
            self.assertFalse(g.check_bust(2));self.assertEqual(g.target_score,24)

    def test_full_table_shield_attack_can_finish_match(self):
        with patch.object(engine,'MAX_TABLE_SLOTS',2):
            m,g=self.position();g.p2_hand=[10,11];g.p1_hand=[1,7];g.p1_fingers=6
            g.active_trumps=[dict(owner=2,name='Shield',type='SHIELD',val=1) for _ in range(2)]
            g.p2_trumps=[('S-Attack+','SHIELD_ATTACK_PLUS',5)]
            action=self.choose(g)
            self.assertEqual(action,'TRUMP:0');self.assertTrue(m.command(2,action+':1'))
            self.assertGreaterEqual(g.calculate_potential_damage(1),g.p1_fingers)

    def test_blockade_and_desire_discard_under_capacity_avoids_lethal(self):
        m,g=self.position();g.p2_hand=[1,2];g.p1_hand=[10,11]
        g.active_trumps=[dict(owner=1,name='Desire+',type='DESIRE_PLUS',val=0),dict(owner=1,name='Destroy++',type='DESTROY_BLOCK',val=0),dict(owner=1,name='Silence',type='SILENCE',val=0)]
        g.p2_trumps=[('Add 2','ADD',2),('Go 21','TARGET',21),('Shield','SHIELD',1)]
        self.assertGreaterEqual(g.calculate_potential_damage(2),g.p2_fingers)
        for _ in range(3):
            action=self.choose(g);self.assertTrue(action.startswith('DISCARD:'))
            g.last_action_time[2]=0
            self.assertTrue(m.command(2,action+':1'))
        self.assertLess(g.calculate_potential_damage(2),g.p2_fingers)


if __name__=='__main__': unittest.main()
