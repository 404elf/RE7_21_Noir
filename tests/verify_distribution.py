"""Explicit release smoke test: real EXE rendering, server and wire commands."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from re7_21 import GameState, recv_msg, send_msg
import pygame

output = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'dist/v1.4.5'
exe = output/'RE7_21_Noir/RE7_21_Noir.exe'
screenshot = output/'packaged-preview.png'
subprocess.run([str(exe), '--preview', '--screenshot', str(screenshot)], check=True, timeout=30)
assert pygame.image.load(str(screenshot)).get_size() == (1440, 900)

server = subprocess.Popen([str(exe), '--server'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
clients = []
try:
    deadline = time.monotonic()+10
    while True:
        try:
            first = socket.create_connection(('127.0.0.1', int(os.environ.get('RE7_PORT', 6666))), timeout=1)
            clients.append(first)
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(.1)
    first.settimeout(6)
    assert recv_msg(first) == 'ID:1'
    second = socket.create_connection(('127.0.0.1', int(os.environ.get('RE7_PORT', 6666))), timeout=5)
    clients.append(second)
    assert recv_msg(second) == 'ID:2'
    state = recv_msg(first)
    assert isinstance(state, GameState)
    assert isinstance(recv_msg(second), GameState)
    count = len(state.p1_trumps)
    send_msg(first, f'DISCARD:0:{state.round_id}')
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        state = recv_msg(first)
        assert isinstance(state, GameState)
        if len(state.p1_trumps) == count-1:
            break
    assert len(state.p1_trumps) == count-1
    assert state.turn == 1
    def until(predicate):
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            value = recv_msg(first)
            assert isinstance(value, GameState)
            if predicate(value):
                return value
        raise AssertionError('Packaged command timed out')
    send_msg(first, f'DRAW_OFFER:{state.round_id}')
    state = until(lambda value: value.draw_offer == 1)
    send_msg(second, f'DRAW_ACCEPT:{state.round_id}')
    state = until(lambda value: value.phase == 'GAMEOVER')
    assert state.round_winner == 0 and state.end_reason == 'agreement'
    send_msg(first, f'REMATCH:{state.round_id}')
    send_msg(second, f'REMATCH:{state.round_id}')
    state = until(lambda value: value.phase == 'ACTION')
    send_msg(second, f'SURRENDER:{state.round_id}')
    state = until(lambda value: value.phase == 'GAMEOVER')
    assert state.round_winner == 1 and state.end_reason == 'surrender'
    print('PASS: packaged UI, two clients, discard, agreed draw, rematch and surrender')
finally:
    for client in clients:
        client.close()
    server.terminate()
    server.wait(timeout=5)
