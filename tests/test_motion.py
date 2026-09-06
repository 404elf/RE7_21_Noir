import copy
import unittest
from unittest.mock import patch
import main
from match import Match
from motion import CardMotion


class MotionTests(unittest.TestCase):
    def test_expiry_and_hidden_card_privacy(self):
        app = main.App()
        try:
            m = Match()
            m.gs.p2_hand = [99, 4]
            with patch.object(app, 'card_sprite', wraps=app.card_sprite) as sprite:
                app.animate_state(None, m.gs)
                self.assertFalse(any(call.args and call.args[0] == 99 for call in sprite.call_args_list))
            count = len(app.motion.items)
            app.animate_state(m.gs, copy.deepcopy(m.gs))
            self.assertEqual(len(app.motion.items), count)
            now = max(item['began']+item['duration'] for item in app.motion.items)+1
            app.motion.clock = lambda: now
            app.motion.prune()
            self.assertEqual(app.motion.items, [])
        finally:
            main.pg.quit()

    def test_confirmed_play_and_reveal(self):
        app = main.App()
        try:
            m = Match()
            before = copy.deepcopy(m.gs)
            m.record('trump', 2, card='Shield')
            m.publish()
            app.animate_state(before, m.gs)
            self.assertEqual([item['style'] for item in app.motion.items], ['play'])
            app.motion.items.clear()
            before = copy.deepcopy(m.gs)
            m.gs.phase = 'RESULT'
            app.animate_state(before, m.gs)
            self.assertEqual([item['style'] for item in app.motion.items], ['flip'])
            self.assertIsNotNone(app.motion.items[0]['back'])
        finally:
            main.pg.quit()

    def test_motion_never_consumes_buttons_or_blocks_actions(self):
        app = main.App()
        try:
            app.preview()
            app.demo = False
            app.animate_state(None, app.gs)
            app.render()
            self.assertTrue(app.can_act())
            self.assertTrue(any(action == 'hit' for _, action in app.buttons))
            app.action('menu')
            self.assertEqual(app.motion.items, [])
        finally:
            main.pg.quit()
