"""Player-reported failures: refuse a free loss, rebuild, and order trumps sensibly."""
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot import Strategy,observe
from match import Match
from tactics import legal,play


def position(ours,theirs,trumps=()):
    m=Match();g=m.gs
    g.p2_hand=list(ours);g.p1_hand=list(theirs)
    g.p2_trumps=list(trumps);g.p1_trumps=[]
    g.deck=[n for n in range(1,12) if n not in ours+theirs]
    g.turn=2;g.p1_stop=True
    return m


class DecisionFeedback(unittest.TestCase):
    def test_known_unavailable_number_is_not_played_or_discarded_with_space(self):
        for ours,theirs,value in (([4,9,7],[8,2,1],4),([4,9,7],[8,6,1],6)):
            m=position(ours,theirs,[('Number','DRAW_SPEC',value)])
            for s in self.strategies():
                self.assertIn(s.choose(observe(m.gs)),('HIT','STAY'))

    def test_number_filter_uses_visible_information_and_preserves_secondary_effects(self):
        m=position([4,9,7],[8,6,1],[('Four','DRAW_SPEC',4)])
        g=m.gs
        for value in (4,9,7,6,1):
            self.assertFalse(legal(g,2,('Number','DRAW_SPEC',value)))
        # The actual hidden eight is unavailable, but the AI cannot know that.
        self.assertTrue(legal(g,2,('Eight','DRAW_SPEC',8)))
        self.assertTrue(legal(g,2,('Four+','DRAW_SPEC_PLUS',4)))
        self.assertIsNotNone(play(g,2,('DISCARD',g.p2_trumps[0])))
        for owner,kind in ((2,'HARVEST'),(1,'FORCE_CONSUME')):
            g.active_trumps=[dict(owner=owner,type=kind,name=kind,val=2)]
            self.assertTrue(legal(g,2,('Four','DRAW_SPEC',4)))

    def test_number_filter_rechecks_after_return_and_draw_lock(self):
        m=position([9,4],[8,6],[])
        card=('Four','DRAW_SPEC',4)
        self.assertFalse(legal(m.gs,2,card))
        m.gs.p2_hand.remove(4);m.gs.deck.append(4)
        self.assertTrue(legal(m.gs,2,card))
        m.gs.active_trumps=[dict(owner=1,type='SILENCE',name='Silence',val=0)]
        self.assertFalse(legal(m.gs,2,card))

    def strategies(self):
        for difficulty in ('hard','nightmare'):
            for style in ('conservative','gambler','swing'):
                for seed in range(12):
                    yield Strategy(difficulty,style,seed)

    def test_five_points_vs_visible_ten_never_stays(self):
        m=position([3,2],[6,10])
        for s in self.strategies():
            self.assertEqual(s.choose(observe(m.gs)),'HIT',(s.difficulty,s.style))

    def test_failed_two_probe_deduces_hidden_two_and_keeps_fighting(self):
        m=position([9,3],[2,8,4],[('Two','DRAW_SPEC',2)])
        self.assertTrue(m.command(2,'TRUMP:0:1'))
        self.assertEqual(m.gs.p2_hand,[9,3])
        for s in self.strategies():
            self.assertEqual(s.choose(observe(m.gs),1),'HIT',(s.difficulty,s.style))
            self.assertGreater(s.planner.infer(observe(m.gs))[2],.95)

    def test_return_recovery_continues_at_action_budget(self):
        m=position([3,2,11],[6,10],[('Return','RETURN',0)])
        self.assertTrue(m.command(2,'TRUMP:0:1'))
        for s in self.strategies():
            self.assertEqual(s.choose(observe(m.gs),12),'HIT')

    def test_low_total_draws_before_setup_or_random_raise(self):
        cards=[('Go 27','TARGET',27),('Six','DRAW_SPEC',6),('Add 2','ADD',2)]
        m=position([3,2],[6,10],cards)
        for s in self.strategies():
            self.assertEqual(s.choose(observe(m.gs)),'HIT')

    def test_temporary_low_lead_is_not_a_raise(self):
        m=position([8,6],[3,2],[('Add 2','ADD',2)])
        m.gs.p1_stop=False
        for s in self.strategies():
            self.assertNotEqual(s.choose(observe(m.gs)),'TRUMP:0')
        # ADD reopens the opponent's stopped hand; even a known low total is
        # not a secure final result when both can still improve.
        m=position([8,6],[3,2],[('Add 2','ADD',2)])
        for s in self.strategies():
            self.assertNotEqual(s.choose(observe(m.gs)),'TRUMP:0')

    def test_legitimate_exact_draw_and_target_rescue_remain_available(self):
        m=position([10,6],[2,8,7],[('Five','DRAW_SPEC',5)])
        for s in self.strategies():
            self.assertEqual(s.choose(observe(m.gs)),'TRUMP:0')
        m=position([11,9,4],[2,8],[('Go 24','TARGET',24)])
        for s in self.strategies():
            self.assertEqual(s.choose(observe(m.gs)),'TRUMP:0')

    def test_locked_draw_and_certain_win_do_not_trigger_desperation(self):
        m=position([3,2],[6,10])
        m.gs.active_trumps=[dict(owner=1,type='SILENCE',name='Silence',val=0)]
        for s in self.strategies():self.assertEqual(s.choose(observe(m.gs)),'STAY')
        m=position([10,11],[3,2])
        for s in self.strategies():self.assertEqual(s.choose(observe(m.gs)),'STAY')

if __name__=='__main__':unittest.main()
