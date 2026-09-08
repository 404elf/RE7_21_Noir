import unittest
import random
import main
from match import Match
from cards import CARDS
from presentation_rules import enabled_cards
from sound import SoundTracker


class PresentationTests(unittest.TestCase):
    def test_enabled_defaults_alias_numbers_and_disabled_pool(self):
        result = enabled_cards(CARDS, {'Return+': 4, 'Shield': 0}, {'number_card_draw_probability': .166})
        self.assertTrue(result['ADD2+'])
        self.assertFalse(result['Shield'])
        self.assertTrue(result['Two'])
        self.assertTrue(result['Trump+'])
        self.assertFalse(result['Escape'])
        numbers_only = enabled_cards(CARDS, {}, {'number_card_draw_probability': 1})
        self.assertFalse(numbers_only['Trump+'])
        self.assertTrue(numbers_only['Two'])

    def test_blood_accumulates_once_and_rematch_clears(self):
        m = Match()
        m.gs.p1_fingers -= 2
        m.publish()
        self.assertEqual(m.gs.blood_loss, {1: 2, 2: 0})
        m.publish()
        self.assertEqual(m.gs.blood_loss[1], 2)
        m.gs.p1_fingers += 1
        m.publish()
        m.gs.p1_fingers -= 2
        m.gs.p2_fingers -= 1
        m.publish()
        self.assertEqual(m.gs.blood_loss, {1: 4, 2: 1})
        m.command(1, f'SURRENDER:{m.gs.round_id}')
        m.command(1, f'REMATCH:{m.gs.round_id}')
        m.command(2, f'REMATCH:{m.gs.round_id}')
        self.assertEqual(m.gs.blood_loss, {1: 0, 2: 0})

    def test_damage_sound_for_opponent_and_zero_delay_transition(self):
        m = Match()
        tracker = SoundTracker()
        tracker.update(m.gs, 1)
        m.gs.p2_fingers -= 2
        m.gs.round_id += 1
        self.assertIn('damage', tracker.update(m.gs, 1))
        self.assertEqual(tracker.update(m.gs, 1), [])
        m.gs.end_reason = 'timeout'
        m.gs.p1_fingers = 0
        m.gs.phase = 'GAMEOVER'
        self.assertNotIn('damage', tracker.update(m.gs, 1))

    def test_blood_render_cache_does_not_affect_game_randomness(self):
        app = main.App()
        try:
            rng = random.getstate()
            empty = main.pg.image.tobytes(app.theme.accumulated_blood({}, 1), 'RGBA')
            layer = app.theme.accumulated_blood({1: 3, 2: 2}, 1)
            self.assertNotEqual(main.pg.image.tobytes(layer, 'RGBA'), empty)
            for left,right in ((0,180),(800,978)):
                self.assertTrue(any(layer.get_at((x,y)).a for x in range(left,right,4) for y in range(0,479,4)))
            self.assertIs(layer, app.theme.accumulated_blood({1: 3, 2: 2}, 1))
            self.assertEqual(random.getstate(), rng)
            self.assertEqual(main.pg.image.tobytes(app.theme.accumulated_blood({}, 1), 'RGBA'), empty)
        finally:
            main.pg.quit()

    def test_opponent_notices_deduplicate_ignore_own_and_reset_match(self):
        app = main.App()
        try:
            m = Match()
            app.observe_notices(m.gs)
            m.record('trump', 2, card='Shield')
            m.record('trump', 1, card='Add 1')
            m.record('trump', 2, card='Perfect')
            m.publish()
            app.observe_notices(m.gs)
            app.observe_notices(m.gs)
            self.assertEqual(app.notice_queue, ['Shield', 'Perfect'])
            m.gs.match_id = 'new'
            app.observe_notices(m.gs)
            self.assertEqual(app.notice_queue, [])
        finally:
            main.pg.quit()

    def test_effect_pages_and_disabled_cards_remain_inspectable(self):
        app = main.App()
        try:
            app.preview()
            app.gs.active_trumps *= 4
            app.render()
            effects = [action for _, action in app.buttons if isinstance(action, tuple) and action[0] == 'effect']
            self.assertEqual(len(effects), 6)
            app.action('effect_next')
            app.render()
            self.assertEqual(app.effect_page, 1)
            self.assertEqual(len([a for _, a in app.buttons if isinstance(a, tuple) and a[0] == 'effect']), 2)
            app.action(('effect', 'Shield'))
            app.render()
            self.assertEqual(app.effect_selected, 'Shield')
            app.book = True
            app.book_page = 2
            app.render()
            self.assertEqual(len([a for _, a in app.buttons if isinstance(a, tuple) and a[0] == 'inspect']), 15)
        finally:
            main.pg.quit()
