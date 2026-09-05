"""Framed legacy transport with bounded reads; clocks run even with idle clients."""
import os
import io
import pickle
import select
import socket
import struct
import time
import re7_21 as engine
from match import Match

# Optional isolated port for local test sessions; normal players use 6666.
engine.DEFAULT_PORT = int(os.environ.get("RE7_PORT", engine.DEFAULT_PORT))


class CommandUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError('Commands cannot contain objects')


def server_worker(timer=None):
    clients, buffers = [], {}
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('0.0.0.0', engine.DEFAULT_PORT))
        listener.listen(2)
        try:
            while len(clients) < 2:
                client, _ = listener.accept()
                client.settimeout(2)
                clients.append(client)
                buffers[client] = bytearray()
                if not engine.send_msg(client, f'ID:{len(clients)}'):
                    return
            match = Match(timer)  # Clock starts only after both seats are occupied.
            while True:
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
                    while len(buffer) >= 4:
                        length = struct.unpack('>I', buffer[:4])[0]
                        if length > 2048:
                            return
                        if len(buffer) < length+4:
                            break
                        raw = bytes(buffer[4:length+4])
                        del buffer[:length+4]
                        try:
                            command = CommandUnpickler(io.BytesIO(raw)).load()
                        except Exception:
                            return
                        match.command(clients.index(client)+1, command)
        except (OSError, ValueError):
            return
        finally:
            for client in clients:
                client.close()
