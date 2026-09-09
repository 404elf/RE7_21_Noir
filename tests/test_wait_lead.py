import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
import unittest
from dataclasses import replace
from unittest.mock import patch
import re7_21 as engine
from bot import Strategy,observe
from match import Match
from tactics import Planner


def position(ours=(10,7),theirs=(11,1),trumps=()):
    now=[100000.]
    m=Match(monotonic=lambda:now[0],wall=lambda:now[0]);g=m.gs
    g.p2_hand=list(ours);g.p1_hand=list(theirs)
    g.p2_trumps=list(trumps);g.p1_trumps=[]
    g.p1_stop=False;g.p2_stop=False;g.turn=2
    g.deck=[n for n in range(1,12) if n not in ours+theirs]
    return m,now


class WaitForInformation(unittest.TestCase):
    def test_example_waits_even_for_gamblers_and_holds_number_trumps(self):
        for cards in ((),(('Perfect','PERFECT',0),('Four','DRAW_SPEC',4))):
            m,_=position(trumps=cards)
            for difficulty in ('hard','nightmare'):
                for style in ('conservative','gambler','swing'):
                    for seed in range(12):
                        self.assertEqual(Strategy(difficulty,style,seed).choose(observe(m.gs)),'STAY')

    def test_real_opponent_draw_reopens_normal_decisions(self):
        for difficulty in ('hard','nightmare'):
            m,now=position((3,10));g=m.gs
            bot=Strategy(difficulty,'conservative',0)
            self.assertEqual(bot.choose(observe(g)),'STAY')
            self.assertTrue(m.command(2,'STAY:1'))
            g.deck.remove(4);g.deck.append(4)
            with patch.dict(engine.SETTINGS,{'hit_draw_trump_probability':0}):
                self.assertTrue(m.command(1,'HIT:1'))
            self.assertEqual(g.p1_hand,[11,1,4])
            self.assertEqual(g.turn,2)
            self.assertFalse(Planner.certainly_ahead(observe(g)))
            self.assertEqual(bot.choose(observe(g)),'HIT')
            now[0]+=1
            self.assertTrue(m.command(2,'HIT:1'))
            self.assertFalse(g.p2_stop)

    def test_ties_busts_and_custom_number_ranges_are_not_certain_wins(self):
        v=observe(position()[0].gs)
        self.assertTrue(Planner.certainly_ahead(v))
        self.assertFalse(Planner.certainly_ahead(replace(v,hand=(10,2))))
        self.assertFalse(Planner.certainly_ahead(replace(v,target=16)))
        self.assertFalse(Planner.certainly_ahead(replace(v,numbers=tuple(range(1,21)))))
        self.assertFalse(Planner.certainly_ahead(replace(v,opponent_visible=(1,6))))

    def test_uses_all_possible_hidden_cards_and_keeps_lethal_raises(self):
        for hidden in (2,11):
            v=observe(position(theirs=(hidden,1))[0].gs)
            self.assertTrue(Planner.certainly_ahead(v))
        m,_=position((10,11),(3,2),(('Add 2','ADD',2),))
        m.gs.p1_fingers=3;m.gs.p1_stop=True
        for difficulty in ('hard','nightmare'):
            self.assertEqual(Strategy(difficulty,'conservative',2).choose(observe(m.gs)),'TRUMP:0')


if __name__=='__main__':unittest.main()
