import dataclasses
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
from pathlib import Path
import random
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot import Strategy,observe
from match import Match
from history import History,describe
from tactics import Planner,simulator,clone,play
from wire import encode,decode


class NightmareTests(unittest.TestCase):
    def match(self):
        now=[10000.]
        m=Match({'settlement_seconds':0},monotonic=lambda:now[0],wall=lambda:now[0])
        return m,now

    def test_rematch_round_and_cooldowns_reset(self):
        m,now=self.match();m.gs.round_id=21
        m.command(1,'SURRENDER:21')
        m.command(1,'REMATCH:21');m.command(2,'REMATCH:21')
        self.assertEqual(m.gs.round_id,1)
        self.assertEqual(m.gs.last_action_time,{1:0.,2:0.})
        self.assertTrue(m.command(1,'STAY:1'))

    def test_openings_are_asymmetric_and_reveal_is_recorded(self):
        m,now=self.match()
        with tempfile.TemporaryDirectory() as root:
            a,b=History(root),History(root)
            a.ingest(m.gs,1);b.ingest(m.gs,2)
            self.assertEqual(a.entries[0]['hands'][0],m.gs.p1_hand)
            self.assertIsNone(a.entries[0]['hands'][1][0])
            self.assertIsNone(b.entries[0]['hands'][0][0])
            self.assertEqual(b.entries[0]['hands'][1],m.gs.p2_hand)
            self.assertIn('?',describe(a.entries[0]))
            m.command(1,'STAY:1');m.command(2,'STAY:1')
            a.ingest(m.gs,1)
            result=next(e for e in a.entries if e['event']=='result')
            self.assertEqual(result['hands'],[m.gs.p1_hand,m.gs.p2_hand])
            self.assertIn('亮牌',describe(result))
            self.assertIsNotNone(decode(encode(m.gs)[8:]))

    def test_perfect_and_probe_inference_without_secrets(self):
        m,now=self.match();gs=m.gs
        gs.p2_hand=[11,2];gs.p1_hand=[7,3];gs.deck=[1,4,5,6,8,9,10]
        gs.p1_trumps=[('Perfect','PERFECT',0)]
        m.command(1,'TRUMP:0:1')
        v=observe(gs)
        p=Planner(random.Random(0),True)
        belief=p.infer(v)
        # Perfect draws 10 at a hidden 7; hypotheses making that draw bust
        # while another safe card exists are strongly disfavoured.
        self.assertGreater(belief[7],belief[9]*10)
        gs.p1_hand[0]=9
        self.assertEqual(v,observe(gs))
        gs.p1_hand=[7,3];gs.p2_hand=[11,2]
        gs.p2_trumps=[('Seven','DRAW_SPEC',7)];gs.deck=[1,4,5,6,8,9,10]
        gs.turn=2;now[0]+=1
        m.command(2,'TRUMP:0:1')
        belief=Planner(random.Random(0),True).infer(observe(gs))
        self.assertGreater(belief[7],.95)

    def test_simulation_does_not_consume_global_rng_or_live_state(self):
        m,now=self.match();before=encode(m.gs);state=random.getstate()
        Strategy('nightmare','swing',5).choose(observe(m.gs))
        self.assertEqual(random.getstate(),state)
        self.assertEqual(before,encode(m.gs))

    def test_lethal_raise_and_counter_for_every_hard_style(self):
        m,now=self.match();gs=m.gs
        gs.p2_hand=[10,11];gs.p1_hand=[2,3];gs.p1_stop=True
        gs.p1_trumps=[];gs.p2_trumps=[('Add 2','ADD',2)]
        gs.p1_fingers=3;gs.deck=[1,4,5,6,7,8,9]
        for difficulty in ('hard','nightmare'):
            for style in ('gambler','conservative','swing'):
                self.assertEqual(Strategy(difficulty,style,2).choose(observe(gs)),'TRUMP:0')

    def test_real_engine_combo_and_discard_under_desire(self):
        m,now=self.match();gs=m.gs
        gs.p2_hand=[10,8,6];gs.p1_hand=[3,11];gs.p1_stop=True
        gs.p1_trumps=[];gs.p2_trumps=[('Return','RETURN',0),('Go 17','TARGET',17)]
        gs.deck=[1,2,4,5,7,9]
        strategy=Strategy('nightmare','swing',7)
        command=strategy.choose(observe(gs))
        self.assertTrue(command.startswith('TRUMP:'))
        self.assertGreaterEqual(len(strategy.planner.last_plan),2)
        # A discarded card reduces Desire damage through the real engine.
        cls=simulator(5);world=cls();world.p1_trumps=[('Two','DRAW_SPEC',2)]*3
        world.active_trumps=[dict(owner=2,type='DESIRE_PLUS',val=0,name='Desire+')]
        after=play(world,1,('DISCARD',world.p1_trumps[0]))
        self.assertLess(after.calculate_potential_damage(1),world.calculate_potential_damage(1))

    def test_recovery_into_lethal_is_a_two_card_plan(self):
        m,now=self.match();gs=m.gs
        gs.p2_hand=[11,10,7];gs.p1_hand=[3,8];gs.p1_stop=True
        gs.p1_trumps=[];gs.p2_trumps=[('Return','RETURN',0),('Add 2','ADD',2)]
        gs.p1_fingers=3;gs.deck=[1,2,4,5,6,9]
        strategy=Strategy('nightmare','swing',8)
        action=strategy.choose(observe(gs))
        plan=[c for a,c in strategy.planner.last_plan if a=='TRUMP']
        self.assertEqual(set(plan),set(gs.p2_trumps))
        self.assertEqual(action,f'TRUMP:{gs.p2_trumps.index(plan[0])}')
        # Raising before recovery is also legal: trumps do not hand over the turn.
        for card in plan: gs.use_trump(2,gs.p2_trumps.index(card))
        self.assertEqual(sum(gs.p2_hand),21)
        self.assertGreaterEqual(gs.calculate_potential_damage(1),gs.p1_fingers)

if __name__=='__main__':unittest.main()
