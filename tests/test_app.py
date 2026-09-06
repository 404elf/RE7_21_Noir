import ast
import hashlib
import os
from pathlib import Path
import queue
import socket
import sys
import time
import unittest

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import pygame
from cards import CARDS
from re7_21 import GameState

# unittest is the process entry point; emulate the old executable class lookup.
sys.modules['__main__'].GameState = GameState


def wait(connection, predicate, seconds=6):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        try:
            _, event, value = connection.events.get(timeout=.1)
        except queue.Empty:
            continue
        if event == 'error':
            raise AssertionError(value)
        if predicate(event, value):
            return value
    raise AssertionError('Timed out waiting for the original server')


class Preservation(unittest.TestCase):
    def test_engine_is_unchanged(self):
        digest = hashlib.sha256((main.ROOT/'re7_21.py').read_bytes()).hexdigest()
        self.assertEqual(digest, 'd96649c329fb5c80c21246acbe779cc4b56bedbcc96f648468b98696e11fb1e7')

    def test_catalog_covers_every_original_card(self):
        tree = ast.parse((main.ROOT/'re7_21.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'get_trump_card')
        names = set()
        for node in ast.walk(method):
            if isinstance(node, ast.Tuple) and len(node.elts) == 3 and all(isinstance(e, ast.Constant) for e in node.elts):
                name, kind, value = [e.value for e in node.elts]
                if isinstance(name, str) and isinstance(kind, str) and isinstance(value, int):
                    names.add(name)
        self.assertEqual(set(CARDS), names)


class Interface(unittest.TestCase):
    def setUp(self):
        self.app = main.App()

    def tearDown(self):
        self.app.connection.close()
        pygame.quit()

    def test_all_scenes_languages_catalog_and_long_hands(self):
        for zh in (True, False):
            self.app.zh = zh
            for scene in ('menu', 'connecting', 'waiting'):
                self.app.scene = scene
                self.app.render()
            self.app.preview()
            self.app.gs.p1_trumps *= 4
            for page in range(4):
                self.app.page = page
                self.app.render()
                self.assertEqual(len([action for _, action in self.app.buttons if isinstance(action, tuple) and action[0] == 'select']), 6)
            self.app.book = True
            for page in range(4):
                self.app.book_page = page
                self.app.book_selected = list(CARDS)[page*15]
                self.app.render()
            self.app.book = False
            for phase in ('RESULT', 'GAMEOVER'):
                self.app.gs.phase = phase
                self.app.render()
                self.assertFalse(any(action == 'hit' for _, action in self.app.buttons))

    def test_turn_gating_and_stale_selection(self):
        self.app.preview()
        self.assertFalse(self.app.can_act())
        self.app.demo = False
        self.assertTrue(self.app.can_act())
        self.app.gs.turn = 2
        self.assertFalse(self.app.can_act())
        state = GameState()
        self.app.connection.events.put((self.app.connection.generation, 'state', state))
        self.app.receive()
        self.assertIsNone(self.app.selected)


class LegacyNetwork(unittest.TestCase):
    def test_two_clients_commands_round_and_rematch(self):
        host, guest = main.Connection(), main.Connection()
        try:
            host.open('127.0.0.1', host=True)
            self.assertEqual(wait(host, lambda e, v: e == 'id'), 1)
            guest.open('127.0.0.1')
            self.assertEqual(wait(guest, lambda e, v: e == 'id'), 2)
            gs = wait(host, lambda e, v: e == 'state')
            self.assertIsInstance(gs, GameState)
            rid = gs.round_id
            count = len(gs.p1_trumps)
            self.assertTrue(host.send('DISCARD:0', rid))
            gs = wait(host, lambda e, v: e == 'state' and len(v.p1_trumps) == count-1)
            self.assertEqual(gs.turn, 1)
            time.sleep(.55)
            self.assertTrue(host.send('TRUMP:0', rid))
            gs = wait(host, lambda e, v: e == 'state' and v.last_action_time[1] > gs.last_action_time[1])
            time.sleep(.55)
            self.assertTrue(host.send('HIT', rid))
            # A trump can bust the hand; STAY is always a valid remaining action.
            if not gs.check_bust(1):
                gs = wait(host, lambda e, v: e == 'state' and v.turn == 2)
                time.sleep(.55)
                guest.send('STAY', rid)
                gs = wait(host, lambda e, v: e == 'state' and v.turn == 1)
            time.sleep(.55)
            host.send('STAY', rid)
            if not gs.p2_stop:
                wait(guest, lambda e, v: e == 'state' and v.turn == 2)
                time.sleep(.55)
                guest.send('STAY', rid)
            gs = wait(host, lambda e, v: e == 'state' and v.phase == 'RESULT')
            self.assertEqual(gs.round_id, rid)
            # Complete successive rounds with original commands until game over.
            deadline = time.monotonic()+100
            while gs.phase != 'GAMEOVER' and time.monotonic() < deadline:
                gs = wait(host, lambda e, v: e == 'state' and v.phase != 'RESULT')
                if gs.phase == 'GAMEOVER':
                    break
                rid = gs.round_id
                # One player deliberately draws beyond target; both then stay.
                for _ in range(30):
                    client = host if gs.turn == 1 else guest
                    pid = gs.turn
                    cmd = 'HIT' if pid == 1 and not gs.check_bust(1) and gs.deck else 'STAY'
                    time.sleep(.55)
                    client.send(cmd, rid)
                    old = gs.last_action_time[pid]
                    gs = wait(host, lambda e, v: e == 'state' and (v.last_action_time[pid] > old or v.phase != 'ACTION'))
                    if gs.phase != 'ACTION':
                        break
            self.assertEqual(gs.phase, 'GAMEOVER')
            host.send('REMATCH', gs.round_id)
            guest.send('REMATCH', gs.round_id)
            gs = wait(host, lambda e, v: e == 'state' and v.phase == 'ACTION')
            self.assertEqual(gs.p1_fingers, main.engine.MAX_HP)
            self.assertEqual(gs.p2_fingers, main.engine.MAX_HP)
        finally:
            guest.close()
            host.close()


if __name__ == '__main__':
    unittest.main()
