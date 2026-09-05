import dataclasses
import os
from pathlib import Path
import sys
import socket
import time
import unittest
from unittest.mock import patch

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import pygame
from bot import BotSession, DIFFICULTIES, STYLES, Strategy, observe
from re7_21 import GameState, send_msg, recv_msg

sys.modules['__main__'].GameState = GameState


def sample():
    gs = GameState()
    gs.p2_hand = [7, 8]
    gs.p1_hand = [3, 2]
    gs.p2_trumps = []
    return gs


class FairStrategy(unittest.TestCase):
    def test_hidden_information_cannot_change_decisions(self):
        gs = sample()
        before = observe(gs)
        gs.p1_hand[0] = 11
        gs.p1_trumps = [('Escape', 'ESCAPE', 0)]*12
        gs.deck = list(reversed(gs.deck))
        after = observe(gs)
        self.assertEqual(before, after)
        for difficulty in DIFFICULTIES:
            for style in STYLES:
                left, right = Strategy(difficulty, style, 42), Strategy(difficulty, style, 42)
                self.assertEqual([left.choose(before) for _ in range(15)], [right.choose(after) for _ in range(15)])

    def test_gambler_hits_more_often_than_cautious(self):
        view = observe(sample())
        for difficulty in ('easy', 'normal'):
            gambler = sum(Strategy(difficulty, 'gambler', seed).choose(view) == 'HIT' for seed in range(100))
            cautious = sum(Strategy(difficulty, 'conservative', seed).choose(view) == 'HIT' for seed in range(100))
            self.assertGreater(gambler, cautious+40)

    def test_swing_switches_every_two_to_four_decisions(self):
        strategy = Strategy('normal', 'swing', 19)
        moods = []
        for _ in range(40):
            strategy.choose(observe(sample()))
            moods.append(strategy.mood)
        runs = []
        size = 1
        for before, after in zip(moods, moods[1:]):
            if before == after:
                size += 1
            else:
                runs.append(size)
                size = 1
        self.assertGreater(len(runs), 8)
        self.assertTrue(all(2 <= run <= 4 for run in runs))

    def test_hard_uses_recovery_and_respects_locks(self):
        gs = sample()
        gs.p2_hand = [7, 8, 9]
        gs.p2_trumps = [('Return', 'RETURN', 0)]
        view = observe(gs)
        self.assertEqual(Strategy('hard', 'conservative', 0).choose(view), 'TRUMP:0')
        self.assertEqual(Strategy('hard', 'conservative', 0).choose(dataclasses.replace(view, trump_locked=True)), 'STAY')
        self.assertEqual(Strategy('hard', 'gambler', 0).choose(dataclasses.replace(view, table_full=True)), 'STAY')
        view = dataclasses.replace(observe(sample()), draw_locked=True)
        self.assertEqual(Strategy('hard', 'gambler', 0).choose(view), 'STAY')

    def test_hard_uses_trumps_more_reliably_than_easy(self):
        view = dataclasses.replace(observe(sample()), hand=(7, 8, 9), trumps=(('Return', 'RETURN', 0),))
        hard = sum(Strategy('hard', 'conservative', seed).choose(view).startswith('TRUMP') for seed in range(60))
        easy = sum(Strategy('easy', 'conservative', seed).choose(view).startswith('TRUMP') for seed in range(60))
        self.assertEqual(hard, 60)
        self.assertLess(easy, 40)

    def test_full_hand_discards_useless_card_and_action_budget_ends_turn(self):
        view = dataclasses.replace(observe(sample()), trumps=(('Shield', 'SHIELD', 1),)*20)
        strategy = Strategy('hard', 'conservative', 0)
        self.assertTrue(strategy.choose(view).startswith('DISCARD:'))
        self.assertIn(strategy.choose(view, extra_actions=4), ('HIT', 'STAY'))


class SoloInterface(unittest.TestCase):
    def test_bot_waits_for_human_rematch_request(self):
        client, server = socket.socketpair()
        server.settimeout(.15)
        bot = BotSession('normal', 'gambler')
        try:
            with patch('bot.socket.create_connection', return_value=client):
                bot.start()
                send_msg(server, 'ID:2')
                gs = sample()
                gs.phase = 'GAMEOVER'
                gs.p1_req_rematch = False
                send_msg(server, gs)
                self.assertIsNone(recv_msg(server))
                time.sleep(1.05)
                gs.p1_req_rematch = True
                send_msg(server, gs)
                server.settimeout(2)
                self.assertEqual(recv_msg(server), f'REMATCH:{gs.round_id}')
                send_msg(server, gs)
                server.settimeout(.15)
                self.assertIsNone(recv_msg(server))
        finally:
            bot.close()
            server.close()

    def test_all_settings_languages_and_leave_overlay(self):
        app = main.App()
        try:
            app.action('solo_setup')
            for zh in (True, False):
                app.zh = zh
                for difficulty in DIFFICULTIES:
                    for style in STYLES:
                        app.action(('difficulty', difficulty))
                        app.action(('style', style))
                        app.render()
                        self.assertEqual(app.difficulty, difficulty)
                        self.assertEqual(app.style, style)
                        self.assertEqual(sum(isinstance(action, tuple) for _, action in app.buttons), 6)
            app.preview()
            app.solo = True
            app.action('leave')
            app.render()
            self.assertEqual({action for _, action in app.buttons}, {'continue', 'menu'})
            app.action('continue')
            self.assertFalse(app.leave_prompt)
        finally:
            app.connection.close()
            pygame.quit()

    def test_real_solo_entry_bot_turn_settlement_and_cleanup(self):
        app = main.App()
        try:
            app.difficulty = 'hard'
            app.style = 'swing'
            app.action('solo_start')

            def until(predicate, timeout=15):
                deadline = time.monotonic()+timeout
                while time.monotonic() < deadline:
                    app.receive()
                    if app.error:
                        self.fail(app.error)
                    if predicate():
                        return
                    time.sleep(.025)
                self.fail('Solo game did not progress')

            until(lambda: app.gs is not None)
            self.assertTrue(app.solo)
            self.assertEqual(app.pid, 1)
            bot = app.connection.bot
            server = app.connection.server
            app.action('stay')
            until(lambda: app.gs.last_action_time[2] > 0)
            self.assertIn(app.bot_mood, ('gambler', 'conservative'))
            deadline = time.monotonic()+25
            while app.gs.phase == 'ACTION' and time.monotonic() < deadline:
                app.receive()
                if app.can_act():
                    app.action('stay')
                time.sleep(.05)
            self.assertEqual(app.gs.phase, 'RESULT')
            app.action('menu')
            self.assertFalse(bot.thread.is_alive())
            self.assertIsNotNone(server.poll())
            self.assertIsNone(app.connection.bot)
            self.assertIsNone(app.connection.server)
        finally:
            app.connection.close()
            pygame.quit()


if __name__ == '__main__':
    unittest.main()
