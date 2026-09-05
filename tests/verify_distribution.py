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

exe = ROOT/'dist/RE7_21_Noir/RE7_21_Noir.exe'
screenshot = ROOT/'dist/packaged-preview.png'
subprocess.run([str(exe), '--preview', '--screenshot', str(screenshot)], check=True, timeout=30)
assert pygame.image.load(str(screenshot)).get_size() == (1440, 900)

server = subprocess.Popen([str(exe), '--server'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
clients = []
try:
    deadline = time.monotonic()+10
    while True:
        try:
            first = socket.create_connection(('127.0.0.1', 6666), timeout=1)
            clients.append(first)
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(.1)
    first.settimeout(6)
    assert recv_msg(first) == 'ID:1'
    second = socket.create_connection(('127.0.0.1', 6666), timeout=5)
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
    print('PASS: packaged UI, two-client handshake, legacy pickle and discard command')
finally:
    for client in clients:
        client.close()
    server.terminate()
    server.wait(timeout=5)
