"""Costly information probes must be conditional, recoverable and consequential."""
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
import random
import unittest
from dataclasses import replace
from bot import Strategy,observe
from match import Match
from tactics import Planner


def position(hidden=11):
    now=[100000.]
    m=Match(monotonic=lambda:now[0],wall=lambda:now[0]);g=m.gs
    m.test_time=now
    g.p2_hand=[9,10];g.p1_hand=[hidden,6,8]
    g.p1_stop=True;g.turn=2;g.p2_fingers=3
    g.p2_trumps=[('Return','RETURN',0),('Go 17','TARGET',17),('Go 24','TARGET',24),
                 ('Add 2','ADD',2),('Shield','SHIELD',1),('Seven','DRAW_SPEC',7)]
    g.active_trumps=[dict(owner=1,name='Stake',type='ADD',val=5)]
    g.deck=[n for n in range(1,12) if n not in g.p1_hand+g.p2_hand]
    return m


class ProtectedProbe(unittest.TestCase):
    def test_successful_probe_returns_the_drawn_card_without_ending_turn(self):
        m=position();bot=Strategy('nightmare','swing',1)
        self.assertEqual(bot.choose(observe(m.gs)),'TRUMP:5')
        self.assertTrue(m.command(2,'TRUMP:5:1'))
        self.assertEqual(m.gs.p2_hand,[9,10,7])
        self.assertEqual(bot.choose(observe(m.gs),1),'TRUMP:0')
        # Advance the command cooldown without changing the game state.
        m.test_time[0]+=1
        self.assertTrue(m.command(2,'TRUMP:0:1'))
        self.assertEqual(m.gs.p2_hand,[9,10])
        self.assertEqual(m.gs.turn,2)
        self.assertIn(7,m.gs.deck)

    def test_failed_probe_deduces_hidden_card_without_returning_previous_card(self):
        m=position(7);p=Planner(random.Random(1),True);v=observe(m.gs)
        self.assertEqual(p.protected_probe(v,p.infer(v),0),'TRUMP:5')
        self.assertTrue(m.command(2,'TRUMP:5:1'))
        v=observe(m.gs);belief=p.infer(v)
        self.assertGreater(belief[7],.95)
        self.assertIsNone(p.protected_probe(v,belief,1))
        self.assertEqual(m.gs.p2_hand,[9,10])

    def test_declines_low_value_poor_stock_and_missing_escape_route(self):
        v=observe(position().gs)
        cases=[replace(v,trumps=v.trumps[:1]+v.trumps[-1:]),
               replace(v,trumps=v.trumps[1:]),
               replace(v,incoming=1,outgoing=1,hp=10,opponent_hp=10),
               replace(v,draw_locked=True),replace(v,trump_locked=True),
               replace(v,opponent_stopped=False),replace(v,table_full=True)]
        for case in cases:
            p=Planner(random.Random(1),True)
            self.assertIsNone(p.protected_probe(case,p.infer(case),0))
        p=Planner(random.Random(1),True)
        self.assertIsNone(p.protected_probe(v,p.infer(v),11))
        p=Planner(random.Random(1),False)
        self.assertIsNone(p.protected_probe(v,p.infer(v),0))

    def test_probe_does_not_consult_the_actual_hidden_card(self):
        commands=[]
        for hidden in (7,11):
            v=observe(position(hidden).gs);p=Planner(random.Random(1),True)
            commands.append(p.protected_probe(v,p.infer(v),0))
        self.assertEqual(commands,['TRUMP:5','TRUMP:5'])


if __name__=='__main__':unittest.main()
