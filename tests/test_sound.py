import array
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pygame
from re7_21 import GameState
from sound import EVENTS, SoundManager, SoundTracker, volume
import main


def state():
    gs = GameState()
    gs.p1_hand, gs.p2_hand = [7, 8], [2, 3]
    gs.p1_trumps = [('Return', 'RETURN', 0), ('Perfect', 'PERFECT', 0)]
    return gs


class TransitionSounds(unittest.TestCase):
    def setUp(self):
        self.tracker = SoundTracker()
        self.gs = state()

    def test_repeated_network_snapshots_and_round_deals_do_not_repeat(self):
        self.assertEqual(self.tracker.update(self.gs, 1), ['round_start'])
        for _ in range(100):
            self.assertEqual(self.tracker.update(copy.deepcopy(self.gs), 1), [])
        self.gs.reset_round()
        self.assertEqual(self.tracker.update(self.gs, 1), ['round_start'])

    def test_draw_stay_turn_and_bust(self):
        self.tracker.update(self.gs, 1)
        self.gs.p1_hand.append(9)
        events = self.tracker.update(self.gs, 1)
        self.assertIn('card_draw', events)
        self.assertIn('bust', events)
        self.gs.turn = 2
        self.gs.p1_stop = True
        self.assertIn('stay', self.tracker.update(self.gs, 1))
        self.gs.turn = 1
        self.assertIn('your_turn', self.tracker.update(self.gs, 1))

    def test_confirmed_use_discard_and_rejected_command(self):
        self.tracker.update(self.gs, 1)
        self.tracker.sent('DISCARD:0', self.gs, 1)
        self.assertEqual(self.tracker.update(self.gs, 1), [])
        self.gs.p1_trumps.pop(0)
        self.gs.last_action_time[1] = 1
        events = self.tracker.update(self.gs, 1)
        self.assertIn('card_discard', events)
        self.assertNotIn('trump_play', events)
        self.tracker.sent('TRUMP:0', self.gs, 1)
        self.gs.p1_trumps.pop(0)
        self.gs.last_action_time[1] = 2
        self.assertIn('trump_play', self.tracker.update(self.gs, 1))

    def test_results_damage_gameover_and_reset(self):
        self.tracker.update(self.gs, 1)
        self.gs.phase = 'RESULT'
        self.gs.round_winner = 2
        self.gs.p1_fingers -= 2
        self.assertEqual(self.tracker.update(self.gs, 1), ['damage', 'round_loss'])
        self.assertEqual(self.tracker.update(self.gs, 1), [])
        self.gs.phase = 'GAMEOVER'
        self.assertEqual(self.tracker.update(self.gs, 1), ['game_loss'])
        self.assertEqual(self.tracker.update(self.gs, 1), [])
        self.tracker.reset()
        self.gs.reset_round()
        self.assertEqual(self.tracker.update(self.gs, 1), ['round_start'])
        for winner, name in [(0, 'draw'), (1, 'win'), (2, 'loss')]:
            tracker = SoundTracker()
            self.gs.phase, self.gs.round_winner = 'RESULT', winner
            self.assertEqual(tracker.update(self.gs, 1), ['round_'+name])
            self.gs.phase = 'GAMEOVER'
            self.assertEqual(tracker.update(self.gs, 1), ['game_'+name])

    def test_opponent_hidden_card_cannot_trigger_a_sound(self):
        self.tracker.update(self.gs, 1)
        self.gs.p2_hand[0] = 99
        self.assertEqual(self.tracker.update(self.gs, 1), [])

    def test_app_receive_consumes_queued_transitions_once(self):
        app = main.App()
        try:
            initial = state()
            app.sound_tracker.update(initial, 1)
            changed = copy.deepcopy(initial)
            changed.p1_hand.append(4)
            for gs in (initial, changed, changed):
                app.connection.events.put((app.connection.generation, 'state', gs))
            with patch.object(app.sound, 'play_many') as play:
                app.receive()
                self.assertEqual(play.call_args.args[0], ['card_draw'])
                app.receive()
                self.assertEqual(play.call_args.args[0], [])
        finally:
            app.connection.close()
            pygame.quit()


class AudioAssetsAndConfiguration(unittest.TestCase):
    def setUp(self):
        pygame.init()

    def tearDown(self):
        pygame.quit()

    def test_all_default_assets_are_valid_distinct_nonclipping_pcm(self):
        manager = SoundManager(ROOT)
        self.assertEqual(set(manager.sounds), set(EVENTS))
        self.assertEqual(manager.issues, [])
        content = set()
        for event in EVENTS:
            path = ROOT/'sounds'/(event+'.wav')
            content.add(path.read_bytes())
            with wave.open(str(path)) as stream:
                self.assertEqual((stream.getnchannels(), stream.getsampwidth(), stream.getframerate()), (1, 2, 44100))
                self.assertLess(stream.getnframes()/stream.getframerate(), 2)
                samples = array.array('h', stream.readframes(stream.getnframes()))
                self.assertGreater(max(abs(n) for n in samples), 100)
                self.assertLess(max(abs(n) for n in samples), 32000)
        self.assertEqual(len(content), len(EVENTS))

    def test_custom_path_volumes_disable_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                'master_volume': .5, 'ui_volume': .4,
                'events': {event: False for event in EVENTS},
            }
            config['events']['ui_click'] = {'file': str(ROOT/'sounds/ui_click.wav'), 'volume': .5}
            config['events']['card_draw'] = {'file': 'missing.wav'}
            (root/'audio.json').write_text(json.dumps(config), encoding='utf-8')
            manager = SoundManager(root)
            self.assertEqual(set(manager.sounds), {'ui_click'})
            self.assertAlmostEqual(manager.sounds['ui_click'].get_volume(), .1, delta=.01)
            self.assertTrue(manager.play('ui_click'))
            self.assertFalse(manager.play('ui_click'))
            manager.toggle()
            self.assertFalse(manager.play('ui_click'))
            self.assertTrue(any('card_draw' in issue for issue in manager.issues))

    def test_malformed_config_and_no_audio_device_do_not_crash(self):
        self.assertEqual(volume(float('nan'), .65), .65)
        self.assertEqual(volume(4, .65), 1)
        self.assertEqual(volume(-2, .65), 0)
        self.assertEqual(volume('loud', .65), .65)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'audio.json').write_text('{broken', encoding='utf-8')
            manager = SoundManager(root)
            self.assertTrue(manager.issues)
            self.assertFalse(manager.play('ui_click'))
        with patch('pygame.mixer.get_init', return_value=None), patch('pygame.mixer.init', side_effect=pygame.error('No device')):
            manager = SoundManager(ROOT)
            self.assertFalse(manager.available)
            manager.play_many(['game_win', 'card_draw'])

    def test_event_priority_does_not_stack_an_entire_network_backlog(self):
        manager = SoundManager(ROOT)
        with patch.object(manager, 'play') as play:
            manager.play_many(['card_draw', 'card_draw', 'bust', 'round_loss', 'damage'])
            self.assertEqual([call.args[0] for call in play.call_args_list], ['round_loss', 'damage'])


if __name__ == '__main__':
    unittest.main()
