"""Bounded JSON direct transport; clocks run even with idle clients."""
import os
import select
import socket
import time
import threading
import re7_21 as engine
from wire import extract
from networking import advertise
from match import Match

# Optional isolated port for local test sessions; normal players use 6666.
engine.DEFAULT_PORT = int(os.environ.get("RE7_PORT", engine.DEFAULT_PORT))


def server_worker(timer=None):
    clients, buffers = [], {}
    rates={};partial_since={}
    stop=threading.Event()
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((os.environ.get('RE7_BIND', '0.0.0.0'), engine.DEFAULT_PORT))
        listener.listen(2)
        if os.environ.get('RE7_BIND')!='127.0.0.1': advertise(engine.DEFAULT_PORT,stop)
        try:
            while len(clients) < 2:
                listener.settimeout(300)
                client, _ = listener.accept()
                client.settimeout(2)
                clients.append(client)
                buffers[client] = bytearray()
                rates[client]=[time.monotonic(),0]
                if not engine.send_msg(client, f'ID:{len(clients)}'):
                    return
            match = Match(timer)  # Clock starts only after both seats are occupied.
            while True:
                if any(time.monotonic()-start>5 for start in partial_since.values()): return
                match.tick()
                for client in clients:
                    if not engine.send_msg(client, match.gs):
                        return
                ready, _, _ = select.select(clients, [], [], .02)
                for client in ready:
                    chunk = client.recv(4096)
                    if not chunk:
                        return
                    buffer = buffers[client]
                    buffer.extend(chunk)
                    if buffer: partial_since.setdefault(client,time.monotonic())
                    processed = 0
                    while len(buffer) >= 8:
                        command = extract(buffer, 2048)
                        if command is None: break
                        processed += 1
                        if time.monotonic()-rates[client][0]>=1: rates[client]=[time.monotonic(),0]
                        rates[client][1]+=1
                        if rates[client][1]>30: return
                        if processed > 20 or not isinstance(command, str) or len(command)>80: return
                        match.command(clients.index(client)+1, command)
                    if not buffer: partial_since.pop(client,None)
        except (OSError, ValueError):
            return
        finally:
            stop.set()
            for client in clients:
                client.close()
