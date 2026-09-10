"""RE7 · 21 NOIR — a bilingual presentation layer over the unchanged engine."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import json

os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import pygame as pg
from app_paths import migrate_legacy, config_path as player_config
if getattr(sys,'frozen',False): migrate_legacy(Path(sys.executable).parent)
import re7_21 as engine
from re7_21 import GameState
from cards import CARDS, CATEGORIES, info, english_name
from bot import BotSession, DIFFICULTIES, STYLES
from sound import SoundManager, SoundTracker
from match import load_timer, timer_config
from session_server import server_worker
from updater import Updater
from history import History, describe
from config_editor import ConfigEditor
from motion import CardMotion
from horror_theme import HorrorTheme
from presentation_rules import enabled_cards
from networking import EventQueue, connect, endpoint, discover, room_loop
from network_panel import NetworkPanel

ROOT = Path(__file__).resolve().parent
W, H = 1440, 900
BG = (12, 10, 9)
PANEL = (26, 23, 20)
LINE = (78, 62, 48)
INK = (229, 220, 201)
MUTED = (165, 152, 133)
GOLD = (205, 172, 120)
GREEN = (184, 146, 107)
RED = (232, 104, 83)


class Connection:
    """Owns the safe UI connection and optional session server subprocess."""
    def __init__(self):
        self.events = EventQueue()
        self.send_lock = threading.Lock()
        self.sock = None
        self.server = None
        self.generation = 0
        self.lock = threading.Lock()
        self.bot = None

    def close(self):
        with self.lock:
            self.generation += 1
            sock, self.sock = self.sock, None
            server, self.server = self.server, None
            bot, self.bot = self.bot, None
        if bot:
            bot.close()
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if server:
            server.terminate()
            server.wait(timeout=5)

    def open(self, address, host=False, bot_options=None, timer=None):
        self.close()
        generation = self.generation

        def worker():
            connected = None
            try:
                if host:
                    # Avoid silently joining an unrelated listener on the legacy port.
                    with socket.socket() as probe:
                        probe.bind(('0.0.0.0', engine.DEFAULT_PORT))
                    with self.lock:
                        if generation != self.generation:
                            return
                        argv = [sys.executable, '--server'] if getattr(sys, 'frozen', False) else [sys.executable, str(ROOT / 'server.py')]
                        if timer is not None:
                            argv += ['--timer', json.dumps(timer)]
                        environment=os.environ.copy()
                        if bot_options: environment['RE7_BIND']='127.0.0.1'
                        self.server = subprocess.Popen(
                            argv, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=environment,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                deadline = time.monotonic() + 5
                while generation == self.generation:
                    try:
                        connected = connect(address, engine.DEFAULT_PORT)
                        break
                    except OSError:
                        if not host or time.monotonic() >= deadline:
                            raise
                        time.sleep(.1)
                with self.lock:
                    if generation != self.generation:
                        return
                    self.sock = connected
                connected.settimeout(5)
                message = engine.recv_msg(connected)
                if message not in ('ID:1', 'ID:2'):
                    raise ConnectionError('handshake')
                self.events.put((generation, 'id', int(message[-1])))
                if bot_options:
                    with self.lock:
                        if generation != self.generation:
                            return
                        self.bot = BotSession(
                            *bot_options,
                            on_error=lambda: self.events.put((generation, 'error', 'bot')),
                            on_mood=lambda mood: self.events.put((generation, 'mood', mood)))
                        self.bot.start()
                connected.settimeout(310)  # Waiting rooms are bounded; match snapshots arrive frequently.
                while generation == self.generation:
                    state = engine.recv_msg(connected)
                    if not isinstance(state, GameState):
                        raise ConnectionError('disconnected')
                    self.events.put((generation, 'state', state))
            except OSError as exc:
                if generation == self.generation:
                    self.events.put((generation, 'error', 'port' if host and getattr(exc, 'winerror', None) == 10048 else 'connection'))
            except Exception:
                if generation == self.generation:
                    self.events.put((generation, 'error', 'connection'))
            finally:
                if connected:
                    connected.close()

        threading.Thread(target=worker, daemon=True).start()

    def send(self, command, round_id):
        with self.send_lock:
            return engine.send_msg(self.sock, f'{command}:{round_id}')

    def open_room(self,address,create=False,code='',password='',rules=None,timer=None):
        self.close()
        threading.Thread(target=room_loop,args=(self,address,create,code,password,rules or {},timer or {}),daemon=True).start()

    def ready(self):
        with self.send_lock: return engine.send_msg(self.sock,dict(op='ready'))


class App:
    def __init__(self):
        pg.init()
        audio_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT
        self.sound = SoundManager(audio_root)
        self.sound_tracker = SoundTracker()
        self.data_root = audio_root
        self.network = NetworkPanel(audio_root)
        self.net_room='';self.net_lobby=None;self.net_status='';self.net_latency=None;self.net_rules_page=0
        self.timer = load_timer(audio_root)
        self.updater = Updater(audio_root)
        self.history = History(audio_root)
        self.editor = None
        self.overlay = None
        self.log_page = 0
        self.state_received_at = time.monotonic()
        self.window = pg.display.set_mode((1280, 800), pg.RESIZABLE)
        pg.display.set_caption('RE7 · 21 | THE BASEMENT')
        self.canvas = pg.Surface((W, H))
        self.theme = HorrorTheme((W, H))
        self.clock = pg.time.Clock()
        self.fonts = {}
        self.zh = True
        self.scene = 'menu'
        self.ip = '127.0.0.1'
        self.focus = False
        self.replace_input = False
        self.connection = Connection()
        self.gs = None
        self.pid = 1
        self.host = False
        self.demo = False
        self.selected = None
        self.motion = CardMotion()
        self.pending_motion = None
        self.animation_origin = None
        self.drag = None
        self.selection_token = None
        self.page = 0
        self.book = False
        self.effect_page = 0
        self.effect_selected = None
        self.review_result = False
        self.notice = None
        self.notice_queue = []
        self.notice_cursor = None
        self.detail_manual = False
        self.book_page = 0
        self.book_selected = None
        self.error = ''
        self.cooldown = 0
        self.rematch = False
        self.buttons = []
        self.mouse = (-1, -1)
        self.running = True
        self.viewport = pg.Rect(0, 0, 1280, 800)
        self.scale = 1280 / W
        self.solo = False
        self.difficulty = 'normal'
        self.style = 'swing'
        self.bot_mood = None
        self.leave_prompt = False

    def t(self, zh, en):
        return zh if self.zh else en

    def clock_label(self):
        if not self.timer['enabled']:
            return self.t('不限时', 'No clock')
        mode = self.timer['mode']
        if mode == 'fischer':
            return '∞' if self.timer['initial_minutes'] is None else f'{self.timer["initial_minutes"]:g}+{self.timer["increment_seconds"]:g}'
        seconds = self.timer[mode+'_seconds']
        if seconds is None: return self.t('不限时','Unlimited')
        return self.t(f'{seconds:g} 秒 / '+('行动' if mode == 'turn' else '每人每局'), f'{seconds:g}s / '+('turn' if mode == 'turn' else 'player per round'))

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            path = pg.font.match_font('microsoftyahei,notosanscjk,simhei,arial', bold=bold)
            self.fonts[key] = pg.font.Font(path, size)
        return self.fonts[key]

    def text(self, text, x, y, size=18, color=INK, bold=False, width=None):
        text = str(text)
        font = self.font(size, bold)
        if width:
            original = text
            while text and font.size(text)[0] > width:
                text = text[:-1]
            if text != original:
                text = text[:-1] + '…'
        self.canvas.blit(font.render(text, True, color), (x, y))

    def wrap(self, text, rect, size=18, color=MUTED):
        font = self.font(size)
        line = ''
        y = rect.y
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9’.,;:+/()-]*|[^\S\n]+|\n|.", text)
        for char in tokens:
            if char == '\n' or font.size(line + char)[0] > rect.w:
                self.text(line, rect.x, y, size, color)
                y += size + 9
                line = '' if char == '\n' else char.lstrip()
                if y + size > rect.bottom:
                    return
            else:
                line += char
        if line and y + size <= rect.bottom:
            self.text(line, rect.x, y, size, color)

    def panel(self, rect, fill=PANEL, border=LINE, radius=16):
        rect = pg.Rect(rect)
        if rect.w > 0 and rect.h > 0:
            self.canvas.blit(self.theme.panel(rect.size, fill, border), rect)

    def button(self, rect, label, action, primary=False, enabled=True, danger=False):
        rect = pg.Rect(rect)
        hovered = rect.collidepoint(self.mouse)
        fill = (107, 29, 23) if primary else (34, 29, 25)
        color = INK if primary else (RED if danger else INK)
        if hovered and enabled:
            fill = (145, 39, 28) if primary else (53, 43, 33)
        if not enabled:
            fill, color = (26, 23, 21), (111, 100, 86)
        self.panel(rect, fill, GOLD if primary and enabled else LINE, 9)
        surf = self.font(18, True).render(label, True, color)
        self.canvas.blit(surf, surf.get_rect(center=rect.center))
        if enabled:
            self.buttons.append((rect, action))

    def header(self):
        self.text('21', 32, 20, 40, GOLD, True)
        self.text('RE7 / 21', 98, 29, 20, INK, True)
        self.text(self.t('禁止公开的录像', 'BANNED FOOTAGE'), 99, 55, 12, RED)
        if self.scene == 'menu':
            self.button((300, 27, 120, 42), self.t('手动更新', 'Updates'), 'updates')
        if self.scene in ('menu', 'solo_setup'):
            self.button((900, 27, 164, 42), self.t('游戏配置', 'Settings'), 'config')
            self.button((434, 27, 154, 42), self.t('计时设置', 'Time control'), 'timers')
        elif self.scene == 'game':
            self.button((434, 27, 154, 42), self.t('战斗日志', 'Action log'), 'history')
        self.button((1080, 27, 142, 42), self.t('卡牌图鉴', 'Card guide'), 'book')
        self.button((1234, 27, 174, 42), '中文  /  EN', 'language')
        sound_label = self.t('音效：开', 'Sound: on') if self.sound.enabled else self.t('音效：关', 'Sound: off')
        if not self.sound.available:
            sound_label = self.t('音效不可用', 'No audio device')
        self.button((722, 27, 162, 42), sound_label, 'audio_toggle', enabled=self.sound.available)
        if self.scene == 'game' and not self.demo:
            self.button((900, 27, 164, 42), self.t('对局选项', 'Match options'), 'match_options')
        pg.draw.line(self.canvas, LINE, (32, 88), (1408, 88))

    def number_card(self, value, x, y, width=83, height=113, hidden=False, secret=False):
        rect = pg.Rect(x, y, width, height)
        self.panel(rect.move(3, 6), (5, 4, 3), None, 9)
        self.panel(rect, (67, 24, 19) if hidden else (199, 182, 146), (120, 65, 42) if hidden else (142, 117, 81), 9)
        if hidden:
            pg.draw.rect(self.canvas, (129, 66, 44), rect.inflate(-12, -12), 1)
            for dy in range(12, height-12, 10):
                pg.draw.line(self.canvas, (79, 32, 24), (x+9, y+dy), (x+width-9, y+dy+6))
        else:
            self.text(value, x+10, y+6, 17, (70, 39, 25), True)
            surf = self.font(min(42, height//3), True).render(str(value), True, (49, 24, 17))
            self.canvas.blit(surf, surf.get_rect(center=rect.center))
            self.text(self.t('暗牌', 'HIDDEN') if secret else '· 21 ·', x+10, y+height-24, 11, (93, 65, 43))

    def menu(self):
        self.text(self.t('禁止公开的录像', 'BANNED FOOTAGE'), 70, 151, 17, RED)
        self.text(self.t('二 十 一 点', 'TWENTY ONE'), 64, 206, 64, INK, True)
        self.text(self.t('赌上你的命。', 'Your life is on the line.'), 70, 310, 29, RED)
        self.wrap(self.t('尽量接近 21 点，但别超过。\n输掉这一局，就付出代价。', 'Get as close to 21 as you can without going over.\nLose the hand. Pay the price.'), pg.Rect(72, 379, 590, 105), 20)
        self.number_card(7, 105, 518, 142, 193)
        self.number_card(3, 272, 492, 142, 193, hidden=True)
        self.number_card(11, 439, 518, 142, 193)
        self.text('01 / SURVIVE', 75, 760, 14, GOLD)
        self.text('02 / ADAPT', 282, 760, 14, MUTED)
        self.text('03 / OUTPLAY', 476, 760, 14, MUTED)
        self.panel((780, 144, 590, 654))
        self.text(self.t('开始游戏', 'PLAY'), 820, 178, 28, INK, True)
        self.text(self.t('坐下，发牌。', 'Take a seat. Deal the cards.'), 822, 231, 17, MUTED)
        self.button((822, 285, 244, 60), self.t('人机对战   →', 'Play against AI   →'), 'solo_setup', True)
        self.button((1082, 285, 246, 60), self.t('选择联机方式', 'Multiplayer options'), 'network')
        self.text(self.t('房主计时：', 'Host clock: ')+self.clock_label(), 822, 360, 15, MUTED)
        pg.draw.line(self.canvas, LINE, (822, 402), (1328, 402))
        self.text(self.t('加入已有房间', 'JOIN AN EXISTING ROOM'), 822, 426, 16, GOLD)
        self.text(self.t('房主 IP 地址', 'Host IP address'), 822, 471, 16, MUTED)
        self.panel((822, 505, 506, 56), BG, GOLD if self.focus else LINE, 9)
        self.text(self.ip + ('|' if self.focus and int(time.time()*2)%2 else ''), 840, 518, 21)
        self.buttons.append((pg.Rect(822, 505, 506, 56), 'focus'))
        self.button((822, 578, 506, 56), self.t('加入房间', 'Join room'), 'join', enabled=bool(self.ip.strip()))
        self.wrap(self.t('双方需处于同一局域网或虚拟组网。默认端口 6666。', 'Use the same LAN or virtual network. Default port: 6666.'), pg.Rect(822, 649, 506, 66), 16)
        if self.error:
            self.wrap(self.error, pg.Rect(822, 716, 506, 64), 16, RED)

    def solo_setup(self):
        self.text(self.t('人机对战', 'PLAY AGAINST AI'), 180, 129, 38, INK, True)
        self.text(self.t('选择难度和对手。', 'Choose a difficulty and an opponent.'), 182, 186, 20, MUTED)
        self.text(self.t('01 / 难度', '01 / DIFFICULTY'), 183, 246, 17, GOLD)
        for i, (key, values) in enumerate(DIFFICULTIES.items()):
            rect = pg.Rect(180+i*278, 284, 258, 151)
            self.panel(rect, PANEL, GOLD if self.difficulty == key else LINE)
            self.text(self.t(values[0], values[1]), rect.x+24, rect.y+21, 25, INK, True)
            self.wrap(self.t(values[2], values[3]), pg.Rect(rect.x+24, rect.y+71, 212, 73), 16)
            self.buttons.append((rect, ('difficulty', key)))
        self.text(self.t('02 / 打法风格', '02 / PLAY STYLE'), 183, 470, 17, GOLD)
        if self.difficulty == 'nightmare':
            self.text(self.t('极难模式不区分打法。', 'Play style does not apply to Nightmare.'), 445, 470, 16, RED)
        for i, (key, values) in enumerate(STYLES.items()):
            rect = pg.Rect(180+i*370, 508, 340, 162)
            self.panel(rect, PANEL, GOLD if self.style == key else LINE)
            self.text(self.t(values[0], values[1]), rect.x+24, rect.y+21, 25, INK, True)
            self.wrap(self.t(values[2], values[3]), pg.Rect(rect.x+24, rect.y+70, 292, 79), 17)
            self.buttons.append((rect, ('style', key)))
        self.button((180, 714, 245, 58), self.t('返回大厅', 'Back to lobby'), 'menu')
        self.button((445, 714, 815, 58), self.t('开始对战   →', 'Start practice   →'), 'solo_start', True)
        self.text(self.t('对手无法看到你的底牌。', 'Your opponent cannot see your hidden card.'), 183, 802, 17, MUTED)

    def waiting(self):
        if self.net_room:
            self.panel((220,160,1000,625))
            self.text(self.t('房间码：','ROOM CODE: ')+self.net_room,260,195,38,GOLD,True)
            lobby=self.net_lobby or {}
            self.text(self.t('已入座 / 已准备：','Seated / ready: ')+f"{lobby.get('players',1)} / {len(lobby.get('ready',[]))}",260,265,23)
            settings=lobby.get('rules',{}).get('game_settings',{})
            self.text(self.t('生命 / 目标 / 起手王牌：','Health / target / starting trumps: ')+f"{settings.get('max_hp','—')} / {settings.get('target_score','—')} / {settings.get('initial_trumps_count',0)+1}",260,323,21)
            self.wrap(self.t('双方确认规则后点击准备。服务器统一配置，掉线可在 30 秒内自动重连。','Review the server rules and ready up. Automatic reconnect reserves your seat for 30 seconds.'),pg.Rect(260,390,900,80),21)
            self.button((260,500,430,55),self.t('查看全部规则','Review all rules'),'network_rules')
            self.button((710,500,430,55),self.t('已准备','Ready') if self.pid in lobby.get('ready',[]) else self.t('准备开始','Ready up'),'network_ready',True,enabled=self.pid not in lobby.get('ready',[]))
            self.text(self.net_status,260,600,20,RED)
            self.button((260,675,880,55),self.t('离开房间','Leave room'),'menu')
            return
        self.panel((350, 240, 740, 390))
        self.text('· · ·', 661, 276, 48, GOLD)
        title = self.t('正在准备 AI 对手', 'Preparing your AI opponent') if self.solo else self.t('等待对手入座', 'Waiting for an opponent') if self.scene == 'waiting' else self.t('正在连接房间', 'Connecting to the room')
        self.text(title, 420, 362, 34, INK, True)
        self.wrap(self.t('对手即将入座，无需邀请其他玩家。', 'Your opponent will take the second seat automatically.') if self.solo else self.t('让对手输入你的局域网或虚拟 IP，即可加入牌局。', 'Ask your opponent to join using your LAN or virtual IP address.') if self.host else self.t('连接成功后，双方入座便会自动开始。', 'The game starts automatically when both players are seated.'), pg.Rect(420, 424, 600, 80), 19)
        self.button((420, 548, 600, 48), self.t('取消并返回', 'Cancel and return'), 'menu')

    def health(self, value, maximum, x, y, width=145):
        ratio = max(0, min(1, value / max(1, maximum)))
        pg.draw.rect(self.canvas, (43, 11, 12), (x, y, width, 11))
        filled = int(width*ratio)
        if filled:
            pg.draw.rect(self.canvas, (158, 25, 31), (x, y, filled, 11))
            pg.draw.line(self.canvas, (222, 65, 64), (x, y+1), (x+filled-1, y+1), 2)
            # A restrained wet lower edge keeps the health amount easy to read.
            pg.draw.line(self.canvas, (105, 13, 20), (x, y+10), (x+filled-1, y+10), 2)

    def player_status(self, pid, x, y):
        gs = self.gs
        hp = getattr(gs, f'p{pid}_fingers')
        damaged = gs.phase in ('RESULT', 'GAMEOVER') and gs.round_winner == 3-pid and gs.round_damage > 0
        if hp <= 0:
            self.text('DEAD', x, y, 26, RED, True)
        elif not damaged:
            label = self.t('已停牌', 'STAYING') if getattr(gs, f'p{pid}_stop') else (self.t('王牌', 'TRUMPS')+f" · {len(getattr(gs, f'p{pid}_trumps'))}") if pid != self.pid else self.t('生命值', 'VITALITY')
            self.text(label, x, y, 14, MUTED)
        if damaged:
            self.text(f'-{gs.round_damage}', x+94 if hp <= 0 else x, y-6, 34, RED, True, 90)

    def hand(self, hand, x, y, width, opponent=False):
        card_w = min(83, max(33, width // max(1, len(hand))-9))
        for index, value in enumerate(hand):
            pid = 3-self.pid if opponent else self.pid
            if self.motion.hides((self.gs.round_id, pid, index, tuple(hand[1:] if opponent and self.gs.phase == 'ACTION' else hand))):
                continue
            self.number_card(value, x+index*(card_w+9), y, card_w, 107,
                             hidden=opponent and index == 0 and self.gs.phase == 'ACTION',
                             secret=not opponent and index == 0 and self.gs.phase == 'ACTION')

    def can_act(self):
        return bool(self.gs and self.gs.phase == 'ACTION' and self.gs.turn == self.pid and time.monotonic() >= self.cooldown and not self.demo and not getattr(self.gs,'network_paused',False) and not self.net_status)

    def trump_allowed(self, card):
        if any(t['owner'] != self.pid and t['type'] == 'DESTROY_BLOCK' for t in self.gs.active_trumps):
            return False
        # The original protocol does not sync table limit; leave that limit to the server.
        return True

    def game(self):
        gs = self.gs
        mine = getattr(gs, f'p{self.pid}_hand')
        theirs = getattr(gs, f'p{3-self.pid}_hand')
        trumps = getattr(gs, f'p{self.pid}_trumps')
        my_hp = getattr(gs, f'p{self.pid}_fingers')
        opp_hp = getattr(gs, f'p{3-self.pid}_fingers')
        self.text(self.t(f'第 {gs.round_id:02} 回合', f'ROUND {gs.round_id:02}'), 34, 110, 18, MUTED)
        if self.solo:
            difficulty = DIFFICULTIES[self.difficulty][0 if self.zh else 1]
            style = STYLES[self.style][0 if self.zh else 1]
            mood = ''
            if self.style == 'swing' and self.bot_mood:
                mood = ' / '+STYLES[self.bot_mood][0 if self.zh else 1]
            self.text(f'AI · {difficulty}'+('' if self.difficulty=='nightmare' else f' · {style}{mood}'), 230, 112, 16, GOLD, width=565)
        turn = self.t('你的行动', 'YOUR TURN') if gs.turn == self.pid else self.t('对手行动中', 'OPPONENT’S TURN')
        if gs.phase != 'ACTION':
            turn = self.t('本局结算', 'ROUND RESULT')
        self.text(turn, 824, 110, 18, GOLD)
        self.panel((32, 151, 978, 479), (33, 27, 21), (93, 66, 43), 24)
        self.canvas.blit(self.theme.accumulated_blood(getattr(gs, 'blood_loss', {}), self.pid), (32, 151))
        self.text(self.t('AI 对手', 'AI OPPONENT') if self.solo else self.t('对手', 'OPPONENT'), 60, 175, 20, INK, True)
        self.text(f'{max(0, opp_hp)} / {gs.max_hp_limit}', 60, 211, 18, MUTED)
        self.health(opp_hp, gs.max_hp_limit, 60, 245)
        opp_total = f'? + {sum(theirs[1:])}' if gs.phase == 'ACTION' else str(sum(theirs))
        self.text(opp_total, 838, 184, 28, GOLD, True, 142)
        self.text(self.t('明牌点数', 'VISIBLE TOTAL') if gs.phase == 'ACTION' else self.t('总点数', 'TOTAL'), 838, 230, 13, MUTED)
        self.hand(theirs, 236, 176, 580, True)
        self.player_status(3-self.pid, 60, 280)
        pg.draw.line(self.canvas, (82, 56, 38), (60, 322), (982, 322))
        if gs.phase == 'ACTION':
            self.table_effects()
        pg.draw.line(self.canvas, (82, 56, 38), (60, 411), (982, 411))
        self.text(self.t('你', 'YOU'), 60, 439, 20, INK, True)
        self.text(f'{max(0, my_hp)} / {gs.max_hp_limit}', 60, 477, 18, MUTED)
        self.health(my_hp, gs.max_hp_limit, 60, 513)
        self.player_status(self.pid, 60, 548)
        self.hand(mine, 236, 447, 580)
        total = sum(mine)
        self.text(f'{total}', 838, 455, 40, RED if total > gs.target_score else GOLD, True)
        self.text(self.t('爆牌', 'BUST') if total > gs.target_score else self.t('当前点数', 'YOUR TOTAL'), 838, 513, 15, RED if total > gs.target_score else MUTED)
        hidden_note = self.t('第一张牌仅你可见', 'Your first card is hidden from your opponent') if gs.phase == 'ACTION' else self.t('双方已亮牌，可直接核对点数', 'Both hands revealed — compare the totals')
        self.text(hidden_note, 237, 575, 14, MUTED)
        recent = next((entry for entry in reversed(getattr(gs, 'action_log', [])) if entry['round'] == gs.round_id and entry['event'] in ('trump', 'discard', 'hit', 'stay', 'timeout')), None)
        if recent:
            message = describe(recent, self.zh, self.pid)
            for player in (1, 2):
                message = message.replace(f'玩家 {player}' if self.zh else f'Player {player}', self.t('你', 'You') if player == self.pid else self.t('对手', 'Opponent'))
            self.text(self.t('最近：', 'Latest: ')+message, 237, 600, 16, GOLD, width=558)
        remaining = getattr(gs, 'clock_remaining', {})
        active = getattr(gs, 'clock_active', 0)
        if getattr(gs, 'clock_config', {}).get('enabled'):
            elapsed = 0 if getattr(gs,'network_paused',False) or self.net_status else min(2, max(0, time.monotonic()-self.state_received_at))
            for pid, y in ((3-self.pid, 270), (self.pid, 555)):
                preparing = getattr(gs,'preparation_active',0)==pid
                budget = getattr(gs,'preparation_remaining',{}).get(pid) if preparing else remaining.get(pid)
                ticking = preparing or active==pid
                seconds = None if budget is None else max(0,budget-(elapsed if ticking else 0))
                color = RED if seconds is not None and seconds<10 else INK if ticking else MUTED
                label = '∞' if seconds is None else f'{int(seconds)//60:02}:{int(seconds)%60:02}'
                self.text(label,838,y-3,30,color)
                if preparing: self.text(self.t('准备','READY'),930,y+6,15,GOLD)
                pg.draw.line(self.canvas, (91,34,28) if ticking else LINE,(840,y+36),(973,y+36),2)
        if getattr(gs, 'draw_offer', 0) == 3-self.pid:
            self.button((460, 96, 325, 43), self.t('对手申请平局 · 查看', 'Draw offered · respond'), 'match_options', True)
        self.sidebar()
        self.text(self.t('你的王牌', 'YOUR TRUMPS'), 34, 655, 21, INK, True)
        self.text(f'{len(trumps):02}', 207, 659, 16, GOLD)
        self.text(self.t('向上拖至牌桌出牌 · 水平右拖弃牌', 'Drag up to play · Drag right to discard'), 241, 660, 16, MUTED)
        pages = max(1, (len(trumps)+5)//6)
        self.page = min(self.page, pages-1)
        self.button((787, 649, 48, 37), '‹', 'prev', enabled=self.page > 0)
        self.text(f'{self.page+1} / {pages}', 853, 657, 16, MUTED)
        self.button((937, 649, 48, 37), '›', 'next', enabled=self.page+1 < pages)
        for i, card in enumerate(trumps[self.page*6:self.page*6+6]):
            idx = self.page*6+i
            if not (self.drag and self.drag['index'] == idx and self.drag_moved()):
                self.trump(card[0], pg.Rect(32+i*165, 706, 153, 140), self.selected == idx, ('select', idx))
        if not trumps:
            self.text(self.t('手中暂无王牌。抽牌或新回合可能带来转机。', 'No trumps in hand. A draw or a new round may change that.'), 48, 757, 18, MUTED)
        if gs.phase in ('RESULT', 'GAMEOVER'):
            self.result()

    def table_effects(self):
        cards = self.gs.active_trumps
        pages = max(1, (len(cards)+5)//6)
        self.effect_page = min(self.effect_page, pages-1)
        self.text(self.t('场上王牌', 'IN PLAY'), 60, 333, 16, GOLD, True)
        self.button((60, 370, 39, 29), '‹', 'effect_prev', enabled=self.effect_page > 0)
        self.text(f'{self.effect_page+1}/{pages}', 108, 375, 12, MUTED)
        self.button((153, 370, 39, 29), '›', 'effect_next', enabled=self.effect_page+1 < pages)
        for i, card in enumerate(cards[self.effect_page*6:self.effect_page*6+6]):
            rect = pg.Rect(223+i*127, 330, 117, 72)
            mine = card['owner'] == self.pid
            self.panel(rect, (48, 33, 24) if mine else (49, 22, 21), (113, 82, 51) if mine else (121, 43, 35))
            name = info(card['name'])[0] if self.zh else english_name(card['name'])
            self.text(self.t('你的', 'YOURS') if mine else self.t('对手', 'OPPONENT'), rect.x+9, rect.y+7, 11, GOLD if mine else RED)
            self.text(name, rect.x+9, rect.y+29, 16, INK, True, 100)
            self.text(self.t('查看效果', 'Inspect'), rect.x+9, rect.y+54, 10, MUTED)
            self.buttons.append((rect, ('effect', card['name'])))
        if not cards:
            self.text(self.t('牌桌上暂无王牌', 'No trumps in play'), 237, 354, 18, MUTED)

    def observe_notices(self, state):
        entries = getattr(state, 'action_log', [])
        match_id = getattr(state, 'match_id', None)
        cursor = self.notice_cursor
        if cursor and cursor[0] == match_id:
            if len(cursor) > 2 and cursor[2] != state.round_id:
                self.notice_queue = []
                self.notice = None
            for entry in entries:
                if entry['id'] > cursor[1] and entry['round'] == state.round_id and entry['event'] == 'trump' and entry['pid'] == 3-self.pid:

                    if not self.detail_manual: self.notice_queue.append(entry['card'])
            self.notice_queue = self.notice_queue[-12:]
        elif cursor:
            self.notice_queue = []
            self.notice = None
        self.notice_cursor = (match_id, max((entry['id'] for entry in entries), default=0), state.round_id)

    def trump(self, name, rect, selected, action, floating=False, disabled=False):
        zh, category, _, _ = info(name)
        cat_zh, cat_en, color, symbol = CATEGORIES[category]
        hover = rect.collidepoint(self.mouse)
        self.panel(rect, (59, 32, 24) if selected or hover else PANEL, RED if selected else LINE, 12)
        pg.draw.line(self.canvas, color, (rect.x+16, rect.y+1), (rect.right-16, rect.y+1), 2)
        self.text(cat_zh if self.zh else cat_en, rect.x+14, rect.y+13, 12, color)
        self.text(symbol, rect.right-38, rect.y+7, 24, color)
        self.text(zh if self.zh else english_name(name), rect.x+14, rect.y+54, 18, INK, True, rect.w-25)
        self.text(english_name(name) if self.zh else zh, rect.x+14, rect.y+87, 12, MUTED, width=rect.w-25)
        if selected and not floating:
            self.text(self.t('已选择', 'SELECTED'), rect.x+14, rect.bottom-24, 11, GOLD)
        if disabled:
            patch = self.canvas.subsurface(rect).copy()
            patch = pg.transform.grayscale(patch)
            patch.fill((120, 120, 120), special_flags=pg.BLEND_RGB_MULT)
            self.canvas.blit(patch, rect)
            self.text(self.t('未启用', 'DISABLED'), rect.x+14, rect.bottom-23, 12, (175, 175, 175), True)
        if action is not None:
            self.buttons.append((rect, action))

    def sidebar(self):
        gs = self.gs
        self.panel((1034, 110, 374, 202))
        self.text(self.t('本局目标', 'ROUND TARGET'), 1060, 128, 14, GOLD)
        self.text(gs.target_score, 1056, 154, 59, INK, True)
        self.text(self.t('牌堆剩余', 'DECK LEFT'), 1239, 154, 13, MUTED)
        self.text(len(gs.deck), 1239, 178, 30, INK, True)
        pg.draw.line(self.canvas, LINE, (1060, 239), (1382, 239))
        self.text(self.t('对手赌注 / 你的赌注', 'THEIR BET / YOUR BET'), 1060, 256, 14, MUTED)
        self.text(f'{gs.calculate_potential_damage(3-self.pid)} / {gs.calculate_potential_damage(self.pid)}', 1281, 251, 25, GOLD, True, 102)
        self.panel((1034, 326, 374, 151))
        allowed = self.can_act()
        locked = any(t['owner'] != self.pid and t['type'] in ('GAMBLE', 'SILENCE') for t in gs.active_trumps)
        hit = allowed and not gs.check_bust(self.pid) and not locked and bool(gs.deck)
        self.button((1054, 349, 161, 55), self.t('抽牌', 'HIT'), 'hit', True, hit)
        self.button((1227, 349, 161, 55), self.t('停牌', 'STAY'), 'stay', enabled=allowed)
        message = self.t('抽牌已被封锁', 'Drawing is locked') if locked else self.t('双方停牌后结算 · 王牌不结束行动', 'Both stay to settle · Trumps keep your turn')
        self.wrap(message, pg.Rect(1057, 420, 320, 45), 14)
        self.panel((1034, 491, 374, 355))
        if getattr(gs, 'last_result', None) and self.selected is not None:
            self.button((1220, 502, 170, 35), self.t('回看上一局', 'Last round'), 'last_result')
        trumps = getattr(gs, f'p{self.pid}_trumps')
        if self.notice and not self.detail_manual and gs.phase == 'ACTION':
            name = self.notice[0]
            zh, cat, desc, en = info(name)
            self.text(self.t('对手打出了王牌', 'OPPONENT PLAYED'), 1056, 513, 18, RED, True)
            self.text(zh if self.zh else english_name(name), 1056, 556, 30, INK, True, 326)
            self.wrap(desc if self.zh else en, pg.Rect(1056, 620, 326, 132), 19, INK)
            self.button((1056, 780, 326, 44), self.t('知道了', 'Continue'), 'dismiss_notice')
        elif self.effect_selected:
            zh, cat, desc, en = info(self.effect_selected)
            self.text(self.t('场上王牌', 'IN PLAY'), 1056, 513, 16, GOLD)
            self.text(zh if self.zh else english_name(self.effect_selected), 1056, 554, 27, INK, True, 326)
            self.wrap(desc if self.zh else en, pg.Rect(1056, 620, 326, 132), 19)
            self.button((1056, 780, 326, 44), self.t('关闭详情', 'Close details'), 'close_effect')
        elif self.selected is not None and self.selected < len(trumps):
            name = trumps[self.selected][0]
            zh, cat, desc, en = info(name)
            color = CATEGORIES[cat][2]
            self.text(self.t('王牌效果', 'TRUMP CARD'), 1060, 513, 13, color)
            self.text(zh if self.zh else english_name(name), 1060, 546, 26, INK, True, 319)
            self.text(english_name(name) if self.zh else zh, 1060, 590, 14, MUTED)
            self.wrap(desc if self.zh else en, pg.Rect(1060, 628, 320, 126), 18)
            self.button((1054, 779, 161, 47), self.t('使用王牌', 'Play trump'), 'play', True, allowed and self.trump_allowed(trumps[self.selected]))
            self.button((1227, 779, 161, 47), self.t('弃置', 'Discard'), 'discard', enabled=allowed, danger=True)
        elif self.review_result and getattr(gs, 'last_result', None):
            self.result_summary(gs.last_result)
        else:
            if getattr(gs, 'last_result', None):
                self.button((1056, 780, 326, 44), self.t('回看上一局', 'Review last round'), 'last_result')
            self.text(self.t('你的王牌', 'YOUR TRUMP CARDS'), 1060, 552, 25, INK, True)
            self.wrap(self.t('拖向牌桌：使用\n向右拖动：弃牌\n点击：查看效果', 'Drag to the table to play.\nDrag right to discard.\nClick to inspect.'), pg.Rect(1060, 614, 318, 125), 18)

    def result_explanation(self, data):
        a, b = [sum(hand) for hand in data['hands']]
        target = data['target']
        if data.get('escape'):
            return self.t('逃脱效果触发，本场以平局结束。', 'Escape ends the match in a draw.')
        if a == b:
            return self.t('双方点数相同，平局。', 'Equal totals: a draw.')
        if a > target and b > target:
            return self.t('双方均爆牌，点数较小的一方获胜。', 'Both busted; the lower total wins.')
        if a > target or b > target:
            who = self.t('你', 'You') if (1 if a > target else 2) == self.pid else self.t('对手', 'Opponent')
            return self.t(f'{who}超过目标 {target}，爆牌落败。', f'{who} exceeded {target} and lost by busting.')
        return self.t('双方均未爆牌，更接近目标的一方获胜。', 'Neither busted; the total closer to the target wins.')

    def result_data(self):
        gs = self.gs
        return dict(round=gs.round_id, hands=[gs.p1_hand, gs.p2_hand], target=gs.target_score, winner=gs.round_winner, damage=gs.round_damage, escape=gs.is_escape_end)

    def result_summary(self, data, y=510):
        winner = data['winner']
        title = self.t('平局', 'DRAW') if not winner else self.t('你获胜', 'YOU WON') if winner == self.pid else self.t('你落败', 'YOU LOST')
        self.text(self.t(f"第 {data['round']} 局 · ", f"ROUND {data['round']} · ")+title, 1056, y, 25, GOLD, True, 326)
        for i, pid in enumerate((self.pid, 3-self.pid)):
            hand = data['hands'][pid-1]
            label = self.t('你', 'YOU') if pid == self.pid else self.t('对手', 'OPP')
            self.text(label+'  '+' + '.join(map(str, hand))+' = '+str(sum(hand)), 1056, y+50+i*36, 18, INK, width=326)
        self.wrap(self.result_explanation(data), pg.Rect(1056, y+136, 326, 90), 18, INK)
        self.text(self.t(f"目标 {data['target']} · 本局伤害 {data['damage']}", f"Target {data['target']} · Damage {data['damage']}"), 1056, y+228, 16, MUTED, width=326)

    def result(self):
        gs = self.gs
        self.buttons = [b for b in self.buttons if b[1] in ('book', 'language', 'audio_toggle', 'history', 'match_options')]
        self.panel((1034, 326, 374, 520))
        title = 'DRAW' if not gs.round_winner else 'YOU WIN' if gs.round_winner == self.pid else 'YOU LOSE'
        if self.solo and self.difficulty=='nightmare' and gs.phase=='GAMEOVER' and gs.round_winner:
            title = 'YOU SURVIVED' if gs.round_winner==self.pid else 'YOU DIED'
        color = GOLD if gs.round_winner == self.pid else RED if gs.round_winner else INK
        stamp = self.font(62 if title=='YOU SURVIVED' else 68, True).render(title, True, color)
        self.canvas.blit(stamp, stamp.get_rect(center=(522, 366)))
        if self.solo and self.difficulty == 'nightmare' and gs.phase == 'GAMEOVER' and gs.round_winner:
            won = gs.round_winner == self.pid
            # An engraved end card in the sidebar leaves both revealed hands clear.
            self.panel((1048, 340, 345, 320), (27, 17, 15), GOLD if won else RED)
            self.text('NIGHTMARE', 1070, 365, 21, MUTED, True)
            self.text(self.t('挑战成功', 'SURVIVAL COMPLETE') if won else self.t('你死了', 'YOU ARE DEAD'), 1070, 412, 34, GOLD if won else RED, True, 305)
            pg.draw.line(self.canvas,GOLD if won else RED,(1070,474),(1371,474),2)
            self.wrap(self.t('极难挑战完成。\n你活下来了。','Nightmare cleared.\nYou survived.') if won else self.t('游戏结束。','Game over.'),pg.Rect(1070,505,300,115),20,INK)
        reason = getattr(gs, 'end_reason', '')
        special_end = self.solo and self.difficulty == 'nightmare' and gs.phase == 'GAMEOVER' and gs.round_winner
        if reason and not special_end:
            label = {'surrender': ('投降结束', 'SURRENDER'), 'agreement': ('双方同意平局', 'DRAW AGREED'), 'timeout': ('总时间耗尽', 'TIME FORFEIT'), 'preparation_timeout': ('准备超时', 'PREPARATION EXPIRED')}.get(reason, ('', ''))
            self.text(self.t(*label), 1056, 362, 24, GOLD, True, 326)
        elif gs.round_damage and not special_end:
            self.text(self.t('本局扣血', 'ROUND DAMAGE'), 1056, 355, 17, MUTED)
            self.text(f'-{gs.round_damage}', 1056, 387, 72, RED, True)
            loser = 3-gs.round_winner
            self.text(self.t('你受伤', 'YOU TOOK DAMAGE') if loser == self.pid else self.t('对手受伤', 'OPPONENT TOOK DAMAGE'), 1056, 486, 18, INK)
            if getattr(gs, f'p{loser}_fingers') <= 0:
                self.text(self.t('生命耗尽', 'NO LIFE REMAINING'), 1056, 530, 27, RED, True)
        if gs.phase == 'GAMEOVER':
            self.button((1054, 696, 334, 54), self.t('等待对方同意', 'Waiting for opponent') if self.rematch else self.t('再来一局', 'Play again'), 'rematch', True, not self.rematch and not self.demo)
            self.button((1054, 770, 334, 54), self.t('返回大厅', 'Return to lobby'), 'menu')
        else:
            seconds = max(0, int(gs.result_timer-time.time())+1)
            ended = gs.p1_fingers <= 0 or gs.p2_fingers <= 0 or gs.is_escape_end
            label = self.t(f'{seconds} 秒后结束对局', f'Match ends in {seconds}s') if ended else self.t(f'{seconds} 秒后下一局', f'Next round in {seconds}s')
            self.text(label, 1056, 725, 19, MUTED)
            self.text(self.t('双方亮牌', 'HANDS REVEALED'), 1056, 773, 16, MUTED)

    def catalog(self):
        self.buttons = []
        overlay = pg.Surface((W, H), pg.SRCALPHA)
        overlay.fill((4, 9, 8, 235))
        self.canvas.blit(overlay, (0, 0))
        self.panel((100, 55, 1240, 790), PANEL, LINE, 22)
        self.text(self.t('王牌档案', 'THE TRUMP ARCHIVE'), 136, 86, 32, INK, True)
        self.text(self.t('已启用的牌优先显示 · 对局中以房主配置为准', 'Enabled cards first · Host configuration applies during a match'), 137, 137, 16, MUTED)
        self.button((1190, 83, 111, 44), self.t('关闭', 'Close'), 'book')
        enabled = getattr(self.gs, 'enabled_cards', None) if self.gs else None
        if enabled is None:
            enabled = enabled_cards(CARDS, engine.WEIGHTS, engine.SETTINGS)
        names = sorted(CARDS, key=lambda name: not enabled.get(name, False))
        pages = (len(names)+14)//15
        for i, name in enumerate(names[self.book_page*15:self.book_page*15+15]):
            self.trump(name, pg.Rect(136+(i%5)*164, 190+(i//5)*158, 151, 140), self.book_selected == name, ('inspect', name), disabled=not enabled.get(name, False))
        self.panel((976, 190, 328, 457), BG)
        name = self.book_selected
        if name:
            self.text(self.t('已启用', 'ENABLED') if enabled.get(name, False) else self.t('抽取概率为 0', 'DRAW CHANCE: 0'), 1001, 616, 14, GOLD)
            zh, _, desc, english = info(name)
            self.text(zh if self.zh else english_name(name), 1001, 217, 25, GOLD, True, 278)
            self.text(english_name(name) if self.zh else zh, 1001, 261, 16, MUTED, width=278)
            self.wrap(desc if self.zh else english, pg.Rect(1001, 315, 277, 277), 19)
        else:
            self.wrap(self.t('选择一张王牌。', 'Select a trump card.'), pg.Rect(1001, 239, 276, 140), 21)
        self.wrap(self.t('灰色王牌不会在当前规则下抽到。', 'Greyed-out cards are disabled under the current rules.'), pg.Rect(137, 688, 1100, 49), 16, MUTED)
        self.button((136, 758, 150, 46), self.t('上一页', 'Previous'), 'book_prev', enabled=self.book_page > 0)
        self.text(f'{self.book_page+1} / {pages}   ·   {len(CARDS)} '+self.t('张王牌', 'trumps'), 318, 770, 17, GOLD)
        self.button((1153, 758, 150, 46), self.t('下一页', 'Next'), 'book_next', enabled=self.book_page+1 < pages)

    def card_sprite(self, value=None, name=None, width=83, hidden=False):
        original = self.canvas
        sprite = pg.Surface((153, 140) if name else (width, 107), pg.SRCALPHA)
        self.canvas = sprite
        try:
            if name:
                self.trump(name, sprite.get_rect(), False, None, floating=True)
            else:
                self.number_card(value, 0, 0, width, 107, hidden=hidden)
        finally:
            self.canvas = original
        return sprite

    def animate_state(self, before, after):
        fresh = before is None or before.round_id != after.round_id or getattr(before, 'match_id', None) != getattr(after, 'match_id', None)
        if fresh:
            self.motion.items.clear()
            self.pending_motion = None
        for pid in (1, 2):
            opponent = pid != self.pid
            hand = getattr(after, f'p{pid}_hand')
            prior = getattr(before, f'p{pid}_hand') if before and not fresh else []
            width = min(83, max(33, 580//max(1,len(hand))-9))
            # Hidden values never enter animation sprites or identity keys.
            identity = tuple(hand[1:] if opponent and after.phase == 'ACTION' else hand)
            for index, value in enumerate(hand):
                flip = opponent and index == 0 and before and before.phase == 'ACTION' and after.phase != 'ACTION' and not fresh
                if index < len(prior) and not flip:
                    continue
                hidden = opponent and index == 0 and after.phase == 'ACTION'
                target = (236+index*(width+9),176 if opponent else 447)
                sprite = self.card_sprite(None if hidden else value,width=width,hidden=hidden)
                self.motion.add(sprite,target if flip else (1239,178),target,
                                'flip' if flip else 'deal', (after.round_id,pid,index,identity),
                                min(index*.055,.22) if fresh else 0,
                                self.card_sprite(width=width,hidden=True) if flip else None)
        if before and not fresh:
            seen = max((entry['id'] for entry in getattr(before,'action_log',[])),default=0)
            for entry in getattr(after,'action_log',[]):
                if entry['id'] <= seen or entry['event'] not in ('trump','discard'):
                    continue
                mine = entry['pid'] == self.pid
                pending = self.pending_motion if mine and self.pending_motion and self.pending_motion[2] == after.round_id else None
                name = entry.get('card') or (pending[0] if pending else None)
                if not name:
                    continue
                origin = pending[1] if pending else ((420,706) if mine else (420,176))
                discard = entry['event'] == 'discard'
                target = (min(1400,origin[0]+270),origin[1]+35) if discard else (465,325)
                self.motion.add(self.card_sprite(name=name),origin,target,'discard' if discard else 'play')
                if mine:
                    self.pending_motion = None

    def receive(self):
        latest = None
        sound_events = []
        while True:
            try:
                generation, event, value = self.connection.events.get_nowait()
            except queue.Empty:
                break
            if generation != self.connection.generation:
                continue
            if event == 'id':
                self.pid = value
                self.scene = 'waiting'
                sound_events.append('connected')
            elif event == 'state':
                self.net_status=self.t('对手掉线，等待重连（最多 30 秒）','Opponent disconnected; waiting up to 30s') if getattr(value,'network_paused',False) else ''
                self.observe_notices(value)
                self.history.ingest(value, self.pid)
                sound_events.extend(self.sound_tracker.update(value, self.pid))
                latest = value
            elif event == 'mood':
                self.bot_mood = value
            elif event == 'room': self.net_room=value;self.net_status=''
            elif event == 'lobby': self.net_lobby=value
            elif event == 'latency': self.net_latency=value
            elif event == 'reconnecting': self.net_status=self.t('连接中断，正在重连… ','Reconnecting… ')+str(value)+'s'
            elif event == 'error':
                self.connection.close()
                self.scene = 'menu'
                self.error = self.t('端口 6666 已被占用，请关闭已有房间。', 'Port 6666 is in use. Close the existing room.') if value == 'port' else self.t('连接已中断或失败，请确认房主 IP、组网和房间状态。', 'Connection failed or closed. Check the host IP, network and room.')
                if value=='certificate': self.error=self.t('服务器证书验证失败，请联系服主检查域名、有效期和证书链。','TLS certificate verification failed. Ask the operator to check the hostname and certificate.')
                elif value=='tls_required': self.error=self.t('公网房间须使用 tls://域名:端口，不接受明文或忽略证书。','Internet rooms require tls://host:port with a valid certificate.')
                elif value=='room_connection': self.error=self.t('房间连接失败：检查服务器地址、双方版本、房间码、密码和房间状态。','Room unavailable: check server, versions, code, password and room status.')
                latest = None
                self.sound_tracker.reset()
                sound_events = ['error']
        if latest:
            if self.gs and latest.round_id != self.gs.round_id:
                self.effect_page = 0
                self.detail_manual = False
                self.effect_selected = None
            if not self.detail_manual and self.effect_selected and not any(card['name'] == self.effect_selected for card in latest.active_trumps):
                self.effect_selected = None
            self.state_received_at = time.monotonic()
            token = (latest.round_id, tuple(getattr(latest, f'p{self.pid}_trumps')))
            if token != self.selection_token or latest.phase != 'ACTION' or latest.turn != self.pid:
                self.drag = None
            if token != self.selection_token:
                prior = getattr(self.gs, f'p{self.pid}_trumps', []) if self.gs else []
                card = prior[self.selected] if self.selected is not None and self.selected < len(prior) else None
                self.selected = token[1].index(card) if self.detail_manual and card in token[1] and self.gs.round_id == latest.round_id else None
                self.selection_token = token
            self.animate_state(self.gs, latest)
            self.gs = latest
            self.scene = 'game'
            if latest.phase == 'ACTION':
                self.rematch = False
        self.sound.play_many(sound_events)

    def command(self, name):
        if self.demo or not self.gs:
            return
        if name not in ('REMATCH', 'SURRENDER', 'DRAW_OFFER', 'DRAW_ACCEPT', 'DRAW_DECLINE') and not self.can_act():
            return
        if self.connection.send(name, self.gs.round_id):
            if name.startswith(('TRUMP:', 'DISCARD:')):
                idx = int(name.split(':')[1])
                cards = getattr(self.gs, f'p{self.pid}_trumps')
                if 0 <= idx < len(cards):
                    self.pending_motion = (cards[idx][0], self.animation_origin or (32+(idx%6)*165, 706), self.gs.round_id)
            self.animation_origin = None
            self.sound_tracker.sent(name, self.gs, self.pid)
            self.cooldown = time.monotonic()+.55
        else:
            self.connection.events.put((self.connection.generation, 'error', 'connection'))

    def action(self, action):
        if action=='network': self.overlay='network';return
        if action=='network_ready': self.connection.ready();return
        if action=='network_rules': self.overlay='network_rules';return
        if isinstance(action,tuple) and action[0]=='rules_page': self.net_rules_page=max(0,self.net_rules_page+action[1]);return
        if isinstance(action,tuple) and action[0]=='net':
            try: self.network.action(self,action[1],action[2])
            except (ValueError,OSError) as exc: self.network.message=str(exc)
            return
        if action == 'config':
            try:
                if self.editor is None:
                    self.editor = ConfigEditor(self.data_root, engine.GAME_CONFIG)
                self.editor.timer_only = False
                if self.editor.tab == 'timer': self.editor.tab = 'game'
                self.overlay = 'config'
            except (OSError, ValueError) as exc:
                self.error = str(exc)
            return
        if isinstance(action, tuple) and action[0] == 'cfg':
            try:
                file = self.editor.action(action)
                if file == 'config.json':
                    engine.GAME_CONFIG = engine.load_config()
                    engine.SETTINGS = engine.GAME_CONFIG['game_settings']
                    engine.WEIGHTS = engine.GAME_CONFIG['trump_weights']
                    engine.MAX_HP = engine.SETTINGS['max_hp']
                    engine.MAX_TRUMPS = engine.SETTINGS['max_trumps_hand_size']
                    engine.MAX_TABLE_SLOTS = engine.SETTINGS['max_active_trumps_on_table']
                elif file == 'timer.json': self.timer = load_timer(self.data_root)
                elif file == 'audio.json': self.sound = SoundManager(self.data_root)
            except (OSError, ValueError, TypeError) as exc:
                self.editor.message = str(exc)
            return
        if action in ('updates', 'timers', 'history', 'match_options'):
            self.overlay = action
            return
        if action in ('SURRENDER', 'DRAW_OFFER', 'DRAW_ACCEPT', 'DRAW_DECLINE'):
            self.command(action)
            self.overlay = None
            return
        if action == 'last_result':
            self.review_result = True
            self.effect_selected = None
            self.selected = None
            return
        if action in ('effect_prev', 'effect_next'):
            self.effect_page = max(0, self.effect_page+(-1 if action == 'effect_prev' else 1))
            return
        if action == 'dismiss_notice':
            self.notice = None
            return
        if action == 'close_effect':
            self.effect_selected = None
            return
        if action == 'close_overlay':
            self.overlay = 'timers' if self.overlay=='config' and self.editor and self.editor.timer_only else None
            return
        if action == 'check_update':
            self.updater.start()
            return
        if action == 'download_update':
            self.updater.start(download=True)
            return
        if action == 'launch_update' and self.updater.executable:
            try:
                subprocess.Popen([str(self.updater.executable)], cwd=self.updater.executable.parent,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.running = False
            except OSError as exc:
                self.updater.status, self.updater.error = 'error', str(exc)
            return
        if action in ('log_prev', 'log_next'):
            self.log_page = max(0, self.log_page+(-1 if action == 'log_prev' else 1))
            return
        if action == 'audio_toggle':
            self.sound.toggle()
            self.sound.play('ui_click')
            return
        self.sound.play('card_select' if isinstance(action, tuple) and action[0] in ('select', 'inspect') else 'ui_click')
        if isinstance(action, tuple):
            kind, value = action
            if kind == 'effect':
                self.detail_manual = True
                self.notice = None
                self.notice_queue.clear()
                self.selected = None
                self.effect_selected = value
                self.review_result = False
            elif kind == 'select':
                self.detail_manual = True
                self.notice = None
                self.notice_queue.clear()
                self.effect_selected = None
                self.review_result = False
                self.selected = value
            elif kind == 'inspect':
                self.book_selected = value
            elif kind == 'difficulty':
                self.difficulty = value
            elif kind == 'style':
                self.style = value
            elif kind == 'timer':
                if self.editor is None: self.editor = ConfigEditor(self.data_root, engine.GAME_CONFIG)
                self.editor.timer_only = True
                self.editor.tab, self.editor.page = 'timer', 0
                if value == 'custom':
                    self.overlay = 'config'
                else:
                    config = dict(self.timer)
                    if value == 'off': config['enabled'] = False
                    else:
                        minutes, increment = map(int,value.split('+'))
                        config.update(enabled=True,mode='fischer',initial_minutes=minutes,increment_seconds=increment,preparation_seconds=30)
                    self.editor.docs['timer.json'] = timer_config(config)
                    try:
                        self.editor.save()
                        self.timer = load_timer(self.data_root)
                    except (ValueError,OSError) as exc: self.error=str(exc)
            return
        if action == 'language':
            self.zh = not self.zh
        elif action == 'book':
            self.book = not self.book
        elif action == 'focus':
            self.focus = True
            self.replace_input = True
        elif action == 'solo_setup':
            self.scene = 'solo_setup'
            self.focus = False
        elif action in ('host', 'join', 'solo_start','room_create','room_join'):
            self.net_room='';self.net_lobby=None;self.net_status='';self.net_latency=None
            self.sound_tracker.reset()
            self.error = ''
            self.gs = None
            self.notice_cursor = None
            self.notice_queue = []
            self.notice = None
            self.effect_selected = None
            self.effect_page = 0
            self.review_result = False
            self.selected = None
            self.page = 0
            self.focus = False
            self.demo = False
            self.solo = action == 'solo_start'
            self.bot_mood = None
            self.host = action in ('host', 'solo_start')
            self.scene = 'connecting'
            if action in ('room_create','room_join'):
                from config_editor import GAME
                rules=dict(game_settings={row[0]:engine.SETTINGS[row[0]] for row in GAME},trump_weights={k:v for k,v in engine.WEIGHTS.items() if type(v) in (int,float)})
                fields=self.network.values
                self.connection.open_room(fields['server'],action=='room_create',fields['code'],fields['password'],rules,self.timer)
                return
            self.connection.open('127.0.0.1' if self.host else self.ip.strip(), self.host,
                                 (self.difficulty, self.style) if self.solo else None, self.timer)
        elif action == 'menu':
            self.motion.items.clear()
            self.pending_motion = None
            self.overlay = None
            self.drag = None
            self.sound_tracker.reset()
            self.connection.close()
            self.gs = None
            self.demo = False
            self.scene = 'menu'
            self.error = ''
            self.solo = False
            self.leave_prompt = False
        elif action == 'leave':
            self.leave_prompt = True
        elif action == 'continue':
            self.leave_prompt = False
        elif action == 'hit':
            self.command('HIT')
        elif action == 'stay':
            self.command('STAY')
        elif action in ('play', 'discard') and self.selected is not None:
            self.command(('TRUMP' if action == 'play' else 'DISCARD') + f':{self.selected}')
            self.selected = None
        elif action == 'rematch':
            self.command('REMATCH')
            self.rematch = True
        elif action == 'prev':
            self.page = max(0, self.page-1)
        elif action == 'next':
            self.page += 1
        elif action == 'book_prev':
            self.book_page = max(0, self.book_page-1)
        elif action == 'book_next':
            self.book_page = min((len(CARDS)-1)//15, self.book_page+1)

    def utility_panel(self):
        if self.overlay=='network': self.network.render(self);return
        if self.overlay=='network_rules':
            self.buttons=[];self.panel((120,100,1200,730))
            self.text(self.t('房间规则（服务器统一）','SERVER RULES'),155,130,30,INK,True)
            from config_editor import GAME, TIMER
            settings=(self.net_lobby or {}).get('rules',{}).get('game_settings',{})
            timer=(self.net_lobby or {}).get('timer',{})
            rows=[(self.t(row[1],row[2]),settings.get(row[0],'—')) for row in GAME]+[(self.t(row[1],row[2]),timer.get(row[0],'—')) for row in TIMER]
            weights=(self.net_lobby or {}).get('rules',{}).get('trump_weights',{})
            rows += [(self.t(info(name)[0],english_name(name)),value) for name,value in sorted(weights.items(),key=lambda item:(item[1]==0,item[0])) if type(value) in (int,float)]
            pages=max(1,(len(rows)+10)//11);self.net_rules_page=min(self.net_rules_page,pages-1)
            for i,(label,value) in enumerate(rows[self.net_rules_page*11:self.net_rules_page*11+11]): self.text(label+': '+str(value),155,195+i*42,20,INK)
            self.button((155,680,160,40),'‹',('rules_page',-1),enabled=self.net_rules_page>0)
            self.text(f'{self.net_rules_page+1} / {pages}',345,690,18,GOLD)
            self.button((460,680,160,40),'›',('rules_page',1),enabled=self.net_rules_page+1<pages)
            self.button((155,740,1110,55),self.t('返回房间','Back to room'),'close_overlay');return
        if self.overlay == 'config':
            self.editor.render(self)
            return
        self.buttons = []
        shade = pg.Surface((W, H), pg.SRCALPHA)
        shade.fill((4, 9, 8, 235))
        self.canvas.blit(shade, (0, 0))
        self.panel((100, 55, 1240, 790), PANEL, LINE, 22)
        titles = {'updates': ('手动更新', 'MANUAL UPDATE'), 'timers': ('计时设置', 'TIME CONTROL'), 'history': ('战斗日志', 'ACTION LOG'), 'match_options': ('对局选项', 'MATCH OPTIONS')}
        self.text(self.t(*titles[self.overlay]), 136, 86, 32, INK, True)
        self.button((1180, 82, 124, 44), self.t('关闭', 'Close'), 'close_overlay')
        if self.overlay == 'match_options':
            live = self.gs and self.gs.phase == 'ACTION' and not self.demo
            offer = getattr(self.gs, 'draw_offer', 0)
            self.wrap(self.t('投降将立即结束整场对局。申请平局需要对方同意，等待期间正常计时。每人每回合可申请一次。', 'Surrender ends the match. A draw requires your opponent’s agreement. Time continues while waiting. One offer per player per round.'), pg.Rect(136, 165, 1120, 110), 22)
            self.button((136, 320, 520, 70), self.t('确认投降', 'Confirm surrender'), 'SURRENDER', danger=True, enabled=live)
            self.button((700, 320, 520, 70), self.t('已申请，等待对方', 'Offer pending') if offer == self.pid else self.t('申请平局', 'Offer draw'), 'DRAW_OFFER', enabled=live and not offer)
            if offer == 3-self.pid:
                self.text(self.t('对手希望以平局结束。', 'Your opponent offers a draw.'), 136, 445, 26, GOLD)
                self.button((136, 510, 520, 70), self.t('同意平局', 'Accept draw'), 'DRAW_ACCEPT', True, live)
                self.button((700, 510, 520, 70), self.t('拒绝，继续对战', 'Decline draw'), 'DRAW_DECLINE', enabled=live)
            self.button((136, 705, 520, 60), self.t('返回大厅 / 断开连接', 'Leave and disconnect'), 'menu')
        elif self.overlay == 'timers':
            self.wrap(self.t('房主统一计时；选择后用于下一场对局。双方入座才开始，结算时暂停。', 'The host controls the clock for the next match. Waiting and settlement do not consume time.'), pg.Rect(136, 145, 1120, 62), 19)
            options = [('1+0','子弹牌','Bullet'),('2+1','子弹牌','Bullet'),('3+0','超快牌','Blitz'),
                       ('3+2','超快牌','Blitz'),('5+0','超快牌','Blitz'),('5+3','超快牌','Blitz'),
                       ('10+0','快牌','Rapid'),('10+5','快牌','Rapid'),('15+10','快牌','Rapid'),
                       ('30+0','慢牌','Classical'),('off','不限时','Unlimited'),('custom','自定义','Custom')]
            for i,(key,zh,en) in enumerate(options):
                x,y=136+(i%3)*390,216+(i//3)*126
                self.button((x,y,370,112),'',('timer',key),primary=self.clock_label()==key)
                label=key if '+' in key else self.t(zh,en)
                self.text(label,x+(370-self.font(31).size(label)[0])//2,y+19,31,INK)
                if '+' in key:
                    label=self.t(zh,en)
                    self.text(label,x+(370-self.font(19).size(label)[0])//2,y+68,19,MUTED)
            self.text(self.t('当前：','Current: ')+self.clock_label(),136,737,21,GOLD)
            self.wrap(self.t('启用计时后，每局首次行动各有准备时间（默认 30 秒），超时判负。只有抽牌、停牌加秒。','Each round begins with 30 seconds to make your first move; expiry loses the match. Only HIT / STAY earn an increment.'),pg.Rect(136,778,1140,55),17)
        elif self.overlay == 'history':
            entries = list(reversed(self.history.entries))
            pages = max(1, (len(entries)+11)//12)
            self.log_page = min(self.log_page, pages-1)
            self.text(self.t('最近动作在前 · 你的底牌可见，对方未亮首牌为 ? · 查看日志不暂停计时', 'Newest first · Your opening card is visible; unrevealed opponent card is ? · Clock keeps running'), 136, 143, 17, MUTED)
            for i, entry in enumerate(entries[self.log_page*12:self.log_page*12+12]):
                y = 191+i*40
                self.text(f'R{entry["round"]:02}', 138, y, 16, GOLD)
                self.text(describe(entry, self.zh, self.pid), 210, y, 18, INK, width=1080)
            if not entries:
                self.text(self.t('暂无日志；需要新版房主提供对局记录。', 'No history yet. A current host is needed to provide action logs.'), 138, 236, 20, MUTED)
            self.text(self.t('日志保存失败；本次记录仍可在这里查看。', 'Could not save the file; history remains available here.') if self.history.error else self.t('日志自动保存，可随问题反馈附上：'+str(self.history.path.parent.relative_to(self.data_root)), 'Logs saved for bug reports: '+str(self.history.path.parent.relative_to(self.data_root))), 138, 696, 16, RED if self.history.error else MUTED)
            self.button((136, 758, 150, 46), self.t('上一页', 'Previous'), 'log_prev', enabled=self.log_page > 0)
            self.text(f'{self.log_page+1} / {pages}', 322, 770, 17, GOLD)
            self.button((1153, 758, 150, 46), self.t('下一页', 'Next'), 'log_next', enabled=self.log_page+1 < pages)
        else:
            self.text(self.t('当前版本 ', 'Installed version ')+self.updater.current, 138, 155, 22, GOLD)
            statuses = {
                'idle': ('从你的 GitHub Release 检查新版本。', 'Check your GitHub Releases for a new version.'),
                'checking': ('正在检查版本…', 'Checking for updates…'), 'downloading': ('正在下载、校验并准备新版…', 'Downloading, verifying and preparing the update…'),
                'available': ('发现新版本：', 'New version: '), 'current': ('已经是最新版本。', 'You are up to date.'),
                'installed': ('新版已准备好。打开后关闭当前窗口，旧版本仍保留。', 'The update is ready. Open it to close this window; the old version is preserved.'),
                'error': ('更新未完成。', 'Update could not be completed.'),
            }
            status = self.t(*statuses[self.updater.status])
            if self.updater.status == 'available':
                status += self.updater.release['tag']
            self.wrap(status, pg.Rect(138, 227, 1135, 90), 26, INK)
            if self.updater.status == 'error':
                errors = {'configure_repository': ('请在 updates.json 的 repository 填写发布仓库，例如 owner/repo。', 'Set repository in updates.json to your release repository, for example owner/repo.'),
                          'missing_asset': ('该 Release 没有配置的 Windows 压缩包。', 'This release has no matching Windows archive.'),
                          'missing_digest': ('该下载没有 SHA-256 校验值，已停止更新。', 'The asset has no SHA-256 digest. Update stopped.'),
                          'checksum_failed': ('下载文件校验失败，旧版本未改动。', 'Checksum failed. The old version was not modified.')}
                self.wrap(self.t(*errors.get(self.updater.error, ('请检查网络、公开仓库和 Release 配置后重试。', 'Check your network, public repository and release configuration, then retry.'))), pg.Rect(138, 330, 1120, 95), 20, RED)
            self.wrap(self.t('下载源由 updates.json 指定。更新会校验 GitHub 提供的 SHA-256，并安装到独立的新目录。\n自动保留游戏、计时、声音、更新配置及 sounds 文件。新版本默认配置另存于 userdata/package-defaults。\n原程序与旧配置不被覆盖；更新失败时仍可继续使用当前版本。', 'The download source is set in updates.json. Archives are verified against GitHub’s SHA-256 and installed into a separate folder.\nGame, clock, audio and update settings and sounds are preserved. New defaults are kept in userdata/package-defaults.\nThe current program is never overwritten and remains usable if an update fails.'), pg.Rect(138, 471, 1125, 178), 19)
            self.button((138, 711, 300, 61), self.t('检查更新', 'Check for updates'), 'check_update', enabled=not self.updater.busy)
            if self.updater.status == 'available':
                self.button((462, 711, 400, 61), self.t('下载并准备新版', 'Download and prepare'), 'download_update', True)
            elif self.updater.status == 'installed':
                self.button((462, 711, 400, 61), self.t('打开新版', 'Open updated version'), 'launch_update', True)

    def render(self):
        self.updater.poll()
        self.motion.prune()
        if self.notice and time.monotonic() >= self.notice[1]:
            self.notice = None
        if not self.detail_manual and not self.notice and self.notice_queue:
            self.notice = (self.notice_queue.pop(0), time.monotonic()+4)
        self.buttons = []
        self.canvas.blit(self.theme.background, (0, 0))
        self.header()
        if self.scene == 'menu':
            self.menu()
        elif self.scene == 'solo_setup':
            self.solo_setup()
        elif self.scene in ('connecting', 'waiting'):
            self.waiting()
        else:
            self.game()
        self.text('RE7 / 21   —   THE BASEMENT', 33, 875, 11, MUTED)
        if self.demo:
            self.text(self.t('界面预览 · 非真实对局', 'UI PREVIEW · NOT A LIVE GAME'), 1015, 872, 14, GOLD)
        else:
            self.text(self.t('中文 / EN   ·   原版规则', '中文 / EN   ·   ORIGINAL RULES'), 1117, 875, 11, MUTED)
        if self.scene == 'game' and not self.book and not self.overlay and not self.leave_prompt:
            self.motion.draw(self.canvas)
            if self.net_room:
                self.text(self.net_status or self.t('房间服务 · ','Room service · ')+self.net_room+(f' · {self.net_latency} ms' if self.net_latency is not None else ''),280,875,14,RED if self.net_status else GOLD,width=800)
        if self.book:
            self.catalog()
        if self.overlay:
            self.utility_panel()
        if self.leave_prompt:
            self.buttons = []
            overlay = pg.Surface((W, H), pg.SRCALPHA)
            overlay.fill((5, 10, 10, 210))
            self.canvas.blit(overlay, (0, 0))
            self.panel((420, 300, 600, 278), PANEL, GOLD)
            self.text(self.t('结束这场练习？', 'End this practice game?'), 456, 339, 29, INK, True)
            self.text(self.t('本次对局进度不会保留。', 'This game’s progress will not be saved.'), 456, 404, 18, MUTED)
            self.button((456, 482, 246, 54), self.t('继续对战', 'Keep playing'), 'continue', True)
            self.button((722, 482, 260, 54), self.t('返回大厅', 'Return to lobby'), 'menu')
        if self.drag and self.drag_moved() and not self.book and not self.overlay and not self.leave_prompt:
            index = self.drag['index']
            cards = getattr(self.gs, f'p{self.pid}_trumps')
            if index < len(cards):
                origin = self.drag.get('origin', (32+(index%6)*165, 706))
                dx, dy = self.mouse[0]-self.drag['start'][0], self.mouse[1]-self.drag['start'][1]
                rect = pg.Rect(origin[0]+dx, origin[1]+dy, 153, 140)
                shadow = pg.Surface((159, 146), pg.SRCALPHA)
                shadow.fill((0, 0, 0, 95))
                self.canvas.blit(shadow, (rect.x+5, rect.y+8))
                self.trump(cards[index][0], rect, True, None, floating=True)
        self.canvas.blit(self.theme.scan, (0, 0))

    def present(self):
        size = self.window.get_size()
        self.scale = min(size[0]/W, size[1]/H)
        scaled = (max(1, int(W*self.scale)), max(1, int(H*self.scale)))
        self.viewport = pg.Rect((size[0]-scaled[0])//2, (size[1]-scaled[1])//2, *scaled)
        self.window.fill(BG)
        self.window.blit(pg.transform.smoothscale(self.canvas, scaled), self.viewport)
        pg.display.flip()

    def drag_moved(self):
        return bool(self.drag and abs(self.mouse[0]-self.drag['start'][0])+abs(self.mouse[1]-self.drag['start'][1]) > 12)

    def drag_target(self, point):
        if not self.drag:
            return None
        dx, dy = point[0]-self.drag['start'][0], point[1]-self.drag['start'][1]
        if dx >= 100 and abs(dy) <= 65:
            return 'DISCARD'
        if dy <= -70 and pg.Rect(32, 151, 978, 479).collidepoint(point):
            return 'TRUMP'
        return None

    def release_drag(self, point):
        drag, target = self.drag, self.drag_target(point)
        self.drag = None
        if not drag or not target or not self.can_act() or self.book or self.overlay or self.leave_prompt:
            return
        trumps = tuple(getattr(self.gs, f'p{self.pid}_trumps'))
        if (self.gs.round_id, trumps) != drag['token']:
            return
        if target == 'TRUMP' and not self.trump_allowed(trumps[drag['index']]):
            self.sound.play('error')
            return
        self.animation_origin = (point[0]-76, point[1]-70)
        self.command(f"{target}:{drag['index']}")
        self.selected = None

    def run(self):
        try:
            while self.running:
                self.clock.tick(60)
                self.receive()
                mx, my = pg.mouse.get_pos()
                self.mouse = ((mx-self.viewport.x)/self.scale, (my-self.viewport.y)/self.scale)
                self.render()
                for event in pg.event.get():
                    if event.type == pg.QUIT:
                        self.running = False
                    elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                        point = ((event.pos[0]-self.viewport.x)/self.scale, (event.pos[1]-self.viewport.y)/self.scale)
                        self.focus = False
                        for rect, action in reversed(self.buttons):
                            if rect.collidepoint(point):
                                self.action(action)
                                if isinstance(action, tuple) and action[0] == 'select' and self.can_act() and not self.book and not self.overlay:
                                    self.drag = dict(index=action[1], start=point, origin=rect.topleft, token=(self.gs.round_id, tuple(getattr(self.gs, f'p{self.pid}_trumps'))))
                                break
                    elif event.type == pg.MOUSEBUTTONUP and event.button == 1:
                        point = ((event.pos[0]-self.viewport.x)/self.scale, (event.pos[1]-self.viewport.y)/self.scale)
                        self.release_drag(point)
                    elif event.type == pg.WINDOWFOCUSLOST:
                        self.drag = None
                    elif event.type == pg.KEYDOWN:
                        if self.overlay=='network' and self.network.editing is not None:
                            self.network.key(event);continue
                        if self.overlay == 'config' and self.editor.editing is not None:
                            try:
                                self.editor.key(event)
                            except (ValueError, TypeError) as exc:
                                self.editor.message = str(exc)
                            continue
                        if event.key == pg.K_ESCAPE:
                            self.drag = None
                            if self.overlay:
                                self.overlay = None
                            elif self.leave_prompt:
                                self.leave_prompt = False
                            elif self.book:
                                self.book = False
                            else:
                                self.focus = False
                        elif self.focus and self.scene == 'menu' and not self.book:
                            if event.key == pg.K_BACKSPACE:
                                self.ip = '' if self.replace_input else self.ip[:-1]
                                self.replace_input = False
                            elif event.key == pg.K_a and event.mod & pg.KMOD_CTRL:
                                self.replace_input = True
                            elif event.key == pg.K_v and event.mod & pg.KMOD_CTRL:
                                try:
                                    pg.scrap.init()
                                    pasted = pg.scrap.get_text().strip()
                                    self.ip = ''.join(c for c in pasted if c.isascii() and (c.isalnum() or c in '.-:'))[:253]
                                    self.replace_input = False
                                except pg.error:
                                    pass
                            elif event.key == pg.K_RETURN and self.ip:
                                self.action('join')
                            elif event.unicode and event.unicode.isascii() and (event.unicode.isalnum() or event.unicode in '.-:'):
                                self.ip = ('' if self.replace_input else self.ip) + event.unicode
                                self.ip = self.ip[:253]
                                self.replace_input = False
                self.present()
        finally:
            self.connection.close()
            pg.quit()

    def preview(self):
        self.demo = True
        self.scene = 'game'
        self.gs = GameState()
        self.gs.p1_hand = [7, 4, 6]
        self.gs.p2_hand = [3, 8]
        self.gs.deck = [n for n in range(1, 12) if n not in self.gs.p1_hand+self.gs.p2_hand]
        self.gs.p1_fingers = 8
        self.gs.round_id = 3
        self.gs.p1_trumps = [('Perfect', 'PERFECT', 0), ('Shield+', 'SHIELD', 2), ('Go 24', 'TARGET', 24), ('Return', 'RETURN', 0), ('Trump+', 'TRUMP_EXCHANGE', 0), ('Add 2', 'ADD', 2)]
        self.gs.active_trumps = [{'name': 'Shield', 'owner': 1, 'type': 'SHIELD', 'val': 1}, {'name': 'Add 1', 'owner': 2, 'type': 'ADD', 'val': 1}]
        self.selected = 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true', help='View an explicitly labelled UI sample, without connecting')
    parser.add_argument('--screenshot', type=Path, help='Save the current UI and exit')
    parser.add_argument('--english', action='store_true')
    parser.add_argument('--server', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--timer', help=argparse.SUPPRESS)
    parser.add_argument('--room-server', metavar='CONFIG',help='Run the TLS room service using a local JSON configuration')
    args = parser.parse_args()
    if args.room_server:
        import asyncio
        from room_server import serve
        asyncio.run(serve(json.loads(Path(args.room_server).read_text(encoding='utf-8-sig'))))
        sys.exit(0)
    if args.server:
        config_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT
        server_worker(json.loads(args.timer) if args.timer else load_timer(config_root))
        sys.exit(0)
    app = App()
    app.zh = not args.english
    if args.preview:
        app.preview()
    if args.screenshot:
        app.render()
        pg.image.save(app.canvas, args.screenshot)
        pg.quit()
    else:
        app.run()
