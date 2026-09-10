import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
import queue
import socket
import time
import unittest
from unittest.mock import patch
import main
from match import Match
from tactics import simulator

class SoloRaiseFixes(unittest.TestCase):
    def test_add_only_raises_and_does_not_draw(self):
        for pid in (1,2):
            for card in (('Add 1','ADD',1),('Add 2','ADD',2),('Add 21','ADD_21',21)):
                m=Match();g=m.gs;g.turn=pid
                setattr(g,f'p{pid}_hand',[10,11])
                setattr(g,f'p{pid}_trumps',[card,('Shield','SHIELD',1)])
                before=g.calculate_potential_damage(3-pid)
                with patch.object(g,'give_trump',wraps=g.give_trump) as reward:
                    self.assertTrue(m.command(pid,'TRUMP:0:1'))
                    reward.assert_not_called()
                self.assertEqual(getattr(g,f'p{pid}_trumps'),[('Shield','SHIELD',1)])
                self.assertEqual(g.calculate_potential_damage(3-pid),before+card[2])
        g=simulator(5)();g.p1_trumps=[('Add 1','ADD',1)]
        g.use_trump(1,0);self.assertEqual(g.p1_trumps,[])

    def test_harvest_remains_an_independent_reward(self):
        m=Match();g=m.gs;g.p1_trumps=[('Add 1','ADD',1)]
        g.active_trumps=[dict(name='Harvest',type='HARVEST',val=0,owner=1)]
        with patch.object(g,'give_trump',wraps=g.give_trump) as reward:
            self.assertTrue(m.command(1,'TRUMP:0:1'))
            reward.assert_called_once_with(1,1)

    def test_solo_starts_with_occupied_default_port_and_two_instances(self):
        sessions=[main.Connection(),main.Connection()]
        def wait(c):
            deadline=time.monotonic()+18
            while time.monotonic()<deadline:
                try:_,event,value=c.events.get(timeout=.1)
                except queue.Empty:continue
                if event=='error':self.fail(str(value))
                if event=='state':return value
            self.fail('Solo startup timed out')
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',main.engine.DEFAULT_PORT));occupied.listen()
            try:
                for c in sessions:c.open('127.0.0.1',True,('easy','conservative'))
                for c in sessions:self.assertEqual(wait(c).phase,'ACTION')
                self.assertNotEqual(sessions[0].bot.port,sessions[1].bot.port)
                self.assertNotEqual(sessions[0].bot.port,main.engine.DEFAULT_PORT)
                for _ in range(2):
                    sessions[0].open('127.0.0.1',True,('hard','gambler'))
                    self.assertEqual(wait(sessions[0]).phase,'ACTION')
            finally:
                for c in sessions:c.close()

if __name__=='__main__':unittest.main()
