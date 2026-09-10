import unittest
from unittest.mock import Mock
import time
import main
from match import Match, timer_config
from test_match import Clock


class InteractionTests(unittest.TestCase):
    def test_surrender_out_of_turn_and_rematch(self):
        m = Match()
        rid = m.gs.round_id
        self.assertTrue(m.command(2, f'SURRENDER:{rid}'))
        self.assertEqual((m.gs.phase, m.gs.round_winner, m.gs.end_reason), ('GAMEOVER', 1, 'surrender'))
        self.assertFalse(m.command(1, f'DRAW_OFFER:{rid}'))
        m.command(1, f'REMATCH:{rid}')
        m.command(2, f'REMATCH:{rid}')
        self.assertEqual((m.gs.phase, m.gs.draw_offer, m.gs.end_reason), ('ACTION', 0, ''))

    def test_draw_requires_opponent_and_limits_repeated_offers(self):
        m = Match()
        r = m.gs.round_id
        self.assertFalse(m.command(1, f'DRAW_ACCEPT:{r}'))
        self.assertTrue(m.command(1, f'DRAW_OFFER:{r}'))
        self.assertFalse(m.command(1, f'DRAW_ACCEPT:{r}'))
        self.assertFalse(m.command(2, f'DRAW_ACCEPT:{r+1}'))
        self.assertTrue(m.command(2, f'DRAW_DECLINE:{r}'))
        self.assertFalse(m.command(1, f'DRAW_OFFER:{r}'))
        self.assertTrue(m.command(2, f'DRAW_OFFER:{r}'))
        self.assertTrue(m.command(1, f'DRAW_ACCEPT:{r}'))
        self.assertEqual((m.gs.phase, m.gs.round_winner), ('GAMEOVER', 0))

    def test_offers_do_not_pause_time(self):
        c = Clock()
        m = Match(dict(enabled=True, mode='fischer', initial_minutes=1), c, c)
        m.command(1, f'DRAW_OFFER:{m.gs.round_id}')
        c.advance(61)
        self.assertFalse(m.command(2, f'DRAW_ACCEPT:{m.gs.round_id}'))
        self.assertEqual(m.gs.end_reason, 'preparation_timeout')

    def test_settlement_zero_default_and_custom(self):
        for delay in (0, 1, 3.5):
            c = Clock()
            m = Match(dict(settlement_seconds=delay), c, c)
            m.gs.p1_hand, m.gs.p2_hand = [5], [5]
            r = m.gs.round_id
            m.finish_round()
            saved = m.gs.last_result
            self.assertEqual(saved['hands'], [[5], [5]])
            self.assertEqual(m.gs.result_timer, c.now+delay)
            if delay:
                c.advance(delay/2)
                m.tick()
                self.assertEqual(m.gs.phase, 'RESULT')
                c.advance(delay/2)
            m.tick()
            self.assertEqual(m.gs.round_id, r+1)
            self.assertEqual(m.gs.last_result, saved)
            self.assertEqual(saved['hands'], [[5], [5]])
        self.assertEqual(timer_config({})['settlement_seconds'], 3)
        self.assertEqual(timer_config(dict(settlement_seconds=-5))['settlement_seconds'], 0)

    def test_drag_targets_and_stale_hand(self):
        app = main.App()
        try:
            app.preview()
            app.demo = False
            app.gs.turn = app.pid
            app.command = Mock()
            def start():
                app.drag = dict(index=0, start=(100, 750), token=(app.gs.round_id, tuple(app.gs.p1_trumps)))
            start()
            app.release_drag((120, 500))
            app.command.assert_called_once_with('TRUMP:0')
            app.command.reset_mock()
            start()
            app.release_drag((230, 765))
            app.command.assert_called_once_with('DISCARD:0')
            app.command.reset_mock()
            for point in ((120, 735), (-100, 750), (230, 650)):
                start()
                app.release_drag(point)
            app.command.assert_not_called()
            start()
            app.gs.p1_trumps.pop(0)
            app.release_drag((120, 500))
            app.command.assert_not_called()
            start()
            app.gs.turn = 2
            app.release_drag((230, 750))
            app.command.assert_not_called()
            app.overlay = 'match_options'
            app.action('menu')
            self.assertIsNone(app.overlay)
            self.assertIsNone(app.drag)
        finally:
            app.connection.close()
            main.pg.quit()
