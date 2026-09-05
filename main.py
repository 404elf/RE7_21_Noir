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
import re7_21 as engine
from re7_21 import GameState  # legacy pickle compatibility: __main__.GameState
from cards import CARDS, CATEGORIES, info
from bot import BotSession, DIFFICULTIES, STYLES
from sound import SoundManager, SoundTracker
from match import load_timer, timer_config
from session_server import server_worker
from updater import Updater
from history import History, describe
from horror_theme import HorrorTheme

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
    """Owns only the UI connection and optional legacy server subprocess."""
    def __init__(self):
        self.events = queue.Queue()
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
                        self.server = subprocess.Popen(
                            argv, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                deadline = time.monotonic() + 5
                while generation == self.generation:
                    try:
                        connected = socket.create_connection((address, engine.DEFAULT_PORT), timeout=1)
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
                connected.settimeout(None)
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
        return engine.send_msg(self.sock, f'{command}:{round_id}')


class App:
    def __init__(self):
        pg.init()
        audio_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT
        self.sound = SoundManager(audio_root)
        self.sound_tracker = SoundTracker()
        self.data_root = audio_root
        self.timer = load_timer(audio_root)
        self.updater = Updater(audio_root)
        self.history = History(audio_root)
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
        self.selection_token = None
        self.page = 0
        self.book = False
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
            return f'{self.timer["initial_minutes"]:g}+{self.timer["increment_seconds"]:g}'
        seconds = self.timer[mode+'_seconds']
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
        self.text(self.t('地下室 · 生存牌局', 'THE BASEMENT'), 99, 55, 12, RED)
        if self.scene == 'menu':
            self.button((300, 27, 120, 42), self.t('手动更新', 'Updates'), 'updates')
        if self.scene in ('menu', 'solo_setup'):
            self.button((434, 27, 154, 42), self.t('计时设置', 'Time control'), 'timers')
        elif self.scene == 'game':
            self.button((434, 27, 154, 42), self.t('战斗日志', 'Action log'), 'history')
        self.button((1080, 27, 142, 42), self.t('卡牌图鉴', 'Card guide'), 'book')
        self.button((1234, 27, 174, 42), '中文  /  EN', 'language')
        sound_label = self.t('音效：开', 'Sound: on') if self.sound.enabled else self.t('音效：关', 'Sound: off')
        if not self.sound.available:
            sound_label = self.t('音效不可用', 'No audio device')
        self.button((722, 27, 162, 42), sound_label, 'audio_toggle', enabled=self.sound.available)
        if self.solo and self.scene == 'game' and not self.demo:
            self.button((900, 27, 164, 42), self.t('结束练习', 'End practice'), 'leave')
        pg.draw.line(self.canvas, LINE, (32, 88), (1408, 88))

    def number_card(self, value, x, y, width=83, height=113, hidden=False, secret=False):
        rect = pg.Rect(x, y, width, height)
        self.panel(rect.move(3, 6), (5, 4, 3), None, 9)
        self.panel(rect, (67, 24, 19) if hidden else (199, 182, 146), (120, 65, 42) if hidden else (142, 117, 81), 9)
        if hidden:
            pg.draw.rect(self.canvas, (129, 66, 44), rect.inflate(-12, -12), 1)
            for dy in range(12, height-12, 10):
                pg.draw.line(self.canvas, (79, 32, 24), (x+9, y+dy), (x+width-9, y+dy+6))
            cx, cy = rect.center
            pg.draw.polygon(self.canvas, GOLD, [(cx, cy-25), (cx+17, cy), (cx, cy+25), (cx-17, cy)], 1)
            self.text('?', cx-7, cy-13, 22, GOLD)
        else:
            self.text(value, x+10, y+6, 17, (70, 39, 25), True)
            surf = self.font(min(42, height//3), True).render(str(value), True, (49, 24, 17))
            self.canvas.blit(surf, surf.get_rect(center=rect.center))
            self.text(self.t('暗牌', 'HIDDEN') if secret else '· 21 ·', x+10, y+height-24, 11, (93, 65, 43))

    def menu(self):
        self.text(self.t('地下室录像 / 最后一场游戏', 'BASEMENT TAPE / THE LAST GAME'), 70, 151, 17, RED)
        self.text(self.t('生 死 二 十 一', 'TWENTY ONE'), 64, 206, 64, INK, True)
        self.text(self.t('下一张，可能就是代价。', 'Every hand has a price.'), 70, 310, 29, RED)
        self.wrap(self.t('灯还亮着。牌已经发下。\n靠近二十一点，或者把命运交给下一张牌。', 'The light is still on. The cards are dealt.\nGet close to twenty-one. Leave the rest to chance.'), pg.Rect(72, 379, 590, 105), 20)
        self.number_card(7, 105, 518, 142, 193)
        self.number_card(3, 272, 492, 142, 193, hidden=True)
        self.number_card(11, 439, 518, 142, 193)
        self.text('01 / SURVIVE', 75, 760, 14, GOLD)
        self.text('02 / ADAPT', 282, 760, 14, MUTED)
        self.text('03 / OUTPLAY', 476, 760, 14, MUTED)
        self.panel((780, 144, 590, 654))
        self.text(self.t('欢迎来到游戏', 'WELCOME TO THE GAME'), 820, 178, 28, INK, True)
        self.text(self.t('坐下。看看谁能撑到最后。', 'Take a seat. See who makes it out.'), 822, 231, 17, MUTED)
        self.button((822, 285, 244, 60), self.t('人机对战   →', 'Play against AI   →'), 'solo_setup', True)
        self.button((1082, 285, 246, 60), self.t('创建联机房间', 'Host multiplayer'), 'host')
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
        self.text(self.t('选择实力，再选择性格。每一位对手，都有自己的节奏。', 'Choose their skill. Choose their character. Find your next rival.'), 182, 186, 20, MUTED)
        self.text(self.t('01 / 难度', '01 / DIFFICULTY'), 183, 246, 17, GOLD)
        for i, (key, values) in enumerate(DIFFICULTIES.items()):
            rect = pg.Rect(180+i*370, 284, 340, 151)
            self.panel(rect, PANEL, GOLD if self.difficulty == key else LINE)
            self.text(self.t(values[0], values[1]), rect.x+24, rect.y+21, 25, INK, True)
            self.wrap(self.t(values[2], values[3]), pg.Rect(rect.x+24, rect.y+71, 292, 63), 17)
            self.buttons.append((rect, ('difficulty', key)))
        self.text(self.t('02 / 打法风格', '02 / PLAY STYLE'), 183, 470, 17, GOLD)
        for i, (key, values) in enumerate(STYLES.items()):
            rect = pg.Rect(180+i*370, 508, 340, 162)
            self.panel(rect, PANEL, GOLD if self.style == key else LINE)
            self.text(self.t(values[0], values[1]), rect.x+24, rect.y+21, 25, INK, True)
            self.wrap(self.t(values[2], values[3]), pg.Rect(rect.x+24, rect.y+70, 292, 79), 17)
            self.buttons.append((rect, ('style', key)))
        self.button((180, 714, 245, 58), self.t('返回大厅', 'Back to lobby'), 'menu')
        self.button((445, 714, 815, 58), self.t('开始对战   →', 'Start practice   →'), 'solo_start', True)
        self.text(self.t('公平对局 · AI 看不到你的暗牌 · 沿用你的自定义规则', 'Fair play · AI cannot see your hidden card · Your custom rules apply'), 183, 802, 17, MUTED)

    def waiting(self):
        self.panel((350, 240, 740, 390))
        self.text('· · ·', 661, 276, 48, GOLD)
        title = self.t('正在准备 AI 对手', 'Preparing your AI opponent') if self.solo else self.t('等待对手入座', 'Waiting for an opponent') if self.scene == 'waiting' else self.t('正在连接房间', 'Connecting to the room')
        self.text(title, 420, 362, 34, INK, True)
        self.wrap(self.t('对手即将入座，无需邀请其他玩家。', 'Your opponent will take the second seat automatically.') if self.solo else self.t('让对手输入你的局域网或虚拟 IP，即可加入牌局。', 'Ask your opponent to join using your LAN or virtual IP address.') if self.host else self.t('连接成功后，双方入座便会自动开始。', 'The game starts automatically when both players are seated.'), pg.Rect(420, 424, 600, 80), 19)
        self.button((420, 548, 600, 48), self.t('取消并返回', 'Cancel and return'), 'menu')

    def health(self, value, maximum, x, y, width=145):
        self.panel((x, y, width, 5), (51, 27, 23), None, 2)
        ratio = max(0, min(1, value / max(1, maximum)))
        if ratio:
            pg.draw.rect(self.canvas, GREEN if ratio > .3 else RED, (x, y, max(1, int(width*ratio)), 5), border_radius=2)

    def hand(self, hand, x, y, width, opponent=False):
        card_w = min(83, max(33, width // max(1, len(hand))-9))
        for index, value in enumerate(hand):
            self.number_card(value, x+index*(card_w+9), y, card_w, 107,
                             hidden=opponent and index == 0 and self.gs.phase == 'ACTION',
                             secret=not opponent and index == 0)

    def can_act(self):
        return bool(self.gs and self.gs.phase == 'ACTION' and self.gs.turn == self.pid and time.monotonic() >= self.cooldown and not self.demo)

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
            self.text(f'AI · {difficulty} · {style}{mood}', 230, 112, 16, GOLD, width=565)
        turn = self.t('你的行动', 'YOUR TURN') if gs.turn == self.pid else self.t('对手行动中', 'OPPONENT’S TURN')
        if gs.phase != 'ACTION':
            turn = self.t('本局结算', 'ROUND RESULT')
        self.text(turn, 824, 110, 18, GOLD)
        self.panel((32, 151, 978, 479), (33, 27, 21), (93, 66, 43), 24)
        self.text(self.t('AI 对手', 'AI OPPONENT') if self.solo else self.t('对手', 'OPPONENT'), 60, 175, 20, INK, True)
        self.text(f'{max(0, opp_hp)} / {gs.max_hp_limit}', 60, 211, 18, MUTED)
        self.health(opp_hp, gs.max_hp_limit, 60, 245)
        opp_total = f'? + {sum(theirs[1:])}' if gs.phase == 'ACTION' else str(sum(theirs))
        self.text(opp_total, 838, 184, 28, GOLD, True, 142)
        self.text(self.t('明牌点数', 'VISIBLE TOTAL') if gs.phase == 'ACTION' else self.t('总点数', 'TOTAL'), 838, 230, 13, MUTED)
        self.hand(theirs, 236, 176, 580, True)
        self.text(self.t('已停牌', 'STAYING') if getattr(gs, f'p{3-self.pid}_stop') else self.t('王牌', 'TRUMPS')+f' · {len(getattr(gs, f"p{3-self.pid}_trumps"))}', 60, 274, 15, MUTED)
        pg.draw.line(self.canvas, (82, 56, 38), (60, 322), (982, 322))
        self.text(self.t('场上效果', 'TABLE EFFECTS'), 60, 339, 14, MUTED)
        active = gs.active_trumps
        table_page = int(time.monotonic()/5) % max(1, (len(active)+5)//6)
        for i, card in enumerate(active[table_page*6:table_page*6+6]):
            x = 227 + (i%3)*249
            y = 331 + (i//3)*36
            name = info(card['name'])[0] if self.zh else card['name']
            owner = self.t('我', 'YOU') if card['owner'] == self.pid else self.t('敌', 'OPP')
            self.text(f'{owner} · {name}', x, y, 15, GREEN if card['owner'] == self.pid else RED, width=240)
        if not active:
            self.text(self.t('暂无持续效果', 'No active effects'), 236, 339, 16, MUTED)
        pg.draw.line(self.canvas, (82, 56, 38), (60, 411), (982, 411))
        self.text(self.t('你', 'YOU'), 60, 439, 20, INK, True)
        self.text(f'{max(0, my_hp)} / {gs.max_hp_limit}', 60, 477, 18, MUTED)
        self.health(my_hp, gs.max_hp_limit, 60, 513)
        self.text(self.t('已停牌', 'STAYING') if getattr(gs, f'p{self.pid}_stop') else self.t('生命值', 'VITALITY'), 60, 540, 15, MUTED)
        self.hand(mine, 236, 447, 580)
        total = sum(mine)
        self.text(f'{total}', 838, 455, 40, RED if total > gs.target_score else GOLD, True)
        self.text(self.t('爆牌', 'BUST') if total > gs.target_score else self.t('当前点数', 'YOUR TOTAL'), 838, 513, 15, RED if total > gs.target_score else MUTED)
        self.text(self.t('第一张牌仅你可见', 'Your first card is hidden from your opponent'), 237, 579, 14, MUTED)
        self.sidebar()
        self.text(self.t('你的王牌', 'YOUR TRUMPS'), 34, 655, 21, INK, True)
        self.text(f'{len(trumps):02}', 207, 659, 16, GOLD)
        self.text(self.t('选牌查看说明，再决定使用或弃置', 'Select a card to inspect, play or discard'), 241, 660, 16, MUTED)
        pages = max(1, (len(trumps)+5)//6)
        self.page = min(self.page, pages-1)
        self.button((787, 649, 48, 37), '‹', 'prev', enabled=self.page > 0)
        self.text(f'{self.page+1} / {pages}', 853, 657, 16, MUTED)
        self.button((937, 649, 48, 37), '›', 'next', enabled=self.page+1 < pages)
        for i, card in enumerate(trumps[self.page*6:self.page*6+6]):
            idx = self.page*6+i
            self.trump(card[0], pg.Rect(32+i*165, 706, 153, 140), self.selected == idx, ('select', idx))
        if not trumps:
            self.text(self.t('手中暂无王牌。抽牌或新回合可能带来转机。', 'No trumps in hand. A draw or a new round may change that.'), 48, 757, 18, MUTED)
        if gs.phase in ('RESULT', 'GAMEOVER'):
            self.result()

    def trump(self, name, rect, selected, action):
        zh, category, _, _ = info(name)
        cat_zh, cat_en, color, symbol = CATEGORIES[category]
        hover = rect.collidepoint(self.mouse)
        self.panel(rect, (59, 32, 24) if selected or hover else PANEL, RED if selected else LINE, 12)
        pg.draw.line(self.canvas, color, (rect.x+16, rect.y+1), (rect.right-16, rect.y+1), 2)
        self.text(cat_zh if self.zh else cat_en, rect.x+14, rect.y+13, 12, color)
        self.text(symbol, rect.right-38, rect.y+7, 24, color)
        self.text(zh if self.zh else name, rect.x+14, rect.y+54, 18, INK, True, rect.w-25)
        self.text(name if self.zh else zh, rect.x+14, rect.y+87, 12, MUTED, width=rect.w-25)
        if selected:
            self.text(self.t('已选择', 'SELECTED'), rect.x+14, rect.bottom-24, 11, GOLD)
        self.buttons.append((rect, action))

    def sidebar(self):
        gs = self.gs
        remaining = getattr(gs, 'clock_remaining', {})
        active = getattr(gs, 'clock_active', 0)
        if getattr(gs, 'clock_config', {}).get('enabled'):
            elapsed = min(2, max(0, time.monotonic()-self.state_received_at))
            values = [max(0, remaining.get(pid, 0)-(elapsed if active == pid else 0)) for pid in (self.pid, 3-self.pid)]
            times = [f'{int(value)//60:02}:{int(value)%60:02}' for value in values]
            self.text(self.t('棋钟 我 / 敌  ', 'CLOCK YOU / OPP  ')+f'{times[0]} / {times[1]}', 1060, 88, 14, RED if values[0] < 10 else GOLD)
        self.panel((1034, 110, 374, 202))
        self.text(self.t('本局目标', 'ROUND TARGET'), 1060, 128, 14, GOLD)
        self.text(gs.target_score, 1056, 154, 59, INK, True)
        self.text(self.t('牌堆剩余', 'DECK LEFT'), 1239, 154, 13, MUTED)
        self.text(len(gs.deck), 1239, 178, 30, INK, True)
        pg.draw.line(self.canvas, LINE, (1060, 239), (1382, 239))
        self.text(self.t('预计造成 / 承受伤害', 'DAMAGE OUT / IN'), 1060, 256, 14, MUTED)
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
        trumps = getattr(gs, f'p{self.pid}_trumps')
        if self.selected is not None and self.selected < len(trumps):
            name = trumps[self.selected][0]
            zh, cat, desc, en = info(name)
            color = CATEGORIES[cat][2]
            self.text(self.t('卡牌详情', 'CARD DETAIL'), 1060, 513, 13, color)
            self.text(zh if self.zh else name, 1060, 546, 26, INK, True, 319)
            self.text(name if self.zh else zh, 1060, 590, 14, MUTED)
            self.wrap(desc if self.zh else en, pg.Rect(1060, 628, 320, 126), 18)
            self.button((1054, 779, 161, 47), self.t('使用王牌', 'Play trump'), 'play', True, allowed and self.trump_allowed(trumps[self.selected]))
            self.button((1227, 779, 161, 47), self.t('弃置', 'Discard'), 'discard', enabled=allowed, danger=True)
        else:
            self.text('◇', 1189, 556, 51, GOLD)
            self.text(self.t('下一步，由你决定', 'Your next move'), 1060, 652, 25, INK, True)
            self.wrap(self.t('点击手中的王牌查看效果。使用与弃牌分别操作，避免误触。', 'Select a trump to read its effect. Playing and discarding are separate actions.'), pg.Rect(1060, 704, 318, 95), 18)

    def result(self):
        gs = self.gs
        self.buttons = [b for b in self.buttons if b[1] in ('book', 'language', 'audio_toggle', 'history')]
        overlay = pg.Surface((W, H), pg.SRCALPHA)
        overlay.fill((5, 10, 10, 185))
        self.canvas.blit(overlay, (0, 89))
        self.panel((412, 247, 616, 394), (35, 24, 20), RED, 22)
        title = self.t('平 局', 'DRAW') if gs.round_winner == 0 else self.t('本局获胜', 'ROUND WON') if gs.round_winner == self.pid else self.t('本局落败', 'ROUND LOST')
        self.text(self.t('棋钟耗尽 · 判负', 'TIME FORFEIT') if getattr(gs, 'end_reason', '') == 'timeout' else self.t('牌 局 结 算', 'THE TABLE HAS SPOKEN'), 455, 278, 16, GOLD)
        self.text(title, 455, 322, 48, INK, True)
        self.text(f'{sum(gs.p1_hand)}  /  {sum(gs.p2_hand)}', 455, 398, 27, MUTED)
        self.text(self.t(f'玩家 1 / 玩家 2 · 本局伤害 {gs.round_damage}', f'Player 1 / Player 2 · Damage {gs.round_damage}'), 455, 447, 17, MUTED)
        if gs.phase == 'GAMEOVER':
            self.button((454, 539, 256, 54), self.t('等待对方同意', 'Waiting for opponent') if self.rematch else self.t('再来一局', 'Play again'), 'rematch', True, not self.rematch and not self.demo)
            self.button((729, 539, 256, 54), self.t('返回大厅', 'Return to lobby'), 'menu')
        else:
            seconds = max(0, int(gs.result_timer-time.time())+1)
            self.text(self.t(f'{seconds} 秒后继续', f'Continuing in {seconds}s'), 455, 549, 21, GOLD)

    def catalog(self):
        self.buttons = []
        overlay = pg.Surface((W, H), pg.SRCALPHA)
        overlay.fill((4, 9, 8, 235))
        self.canvas.blit(overlay, (0, 0))
        self.panel((100, 55, 1240, 790), PANEL, LINE, 22)
        self.text(self.t('王牌档案', 'THE TRUMP ARCHIVE'), 136, 86, 32, INK, True)
        self.text(self.t('名称对照与效果速查 · 实际结算沿用原版代码', 'Names & reference effects · The original engine decides all outcomes'), 137, 137, 16, MUTED)
        self.button((1190, 83, 111, 44), self.t('关闭', 'Close'), 'book')
        names = list(CARDS)
        pages = (len(names)+14)//15
        for i, name in enumerate(names[self.book_page*15:self.book_page*15+15]):
            self.trump(name, pg.Rect(136+(i%5)*164, 190+(i//5)*158, 151, 140), self.book_selected == name, ('inspect', name))
        self.panel((976, 190, 328, 457), BG)
        name = self.book_selected
        if name:
            zh, _, desc, english = info(name)
            self.text(zh if self.zh else name, 1001, 217, 25, GOLD, True, 278)
            self.text(name if self.zh else zh, 1001, 261, 16, MUTED, width=278)
            self.wrap(desc if self.zh else english, pg.Rect(1001, 315, 277, 277), 19)
        else:
            self.wrap(self.t('选择任意王牌，查看中文名称与效果说明。', 'Choose a trump to read its name and effect.'), pg.Rect(1001, 239, 276, 140), 21)
        self.wrap(self.t('“图鉴效果”表示参考图片中的设计；已知原版实现差异详见 README。', '“Reference” describes the card sheet. Known engine differences are documented in README.'), pg.Rect(137, 688, 1100, 49), 16, MUTED)
        self.button((136, 758, 150, 46), self.t('上一页', 'Previous'), 'book_prev', enabled=self.book_page > 0)
        self.text(f'{self.book_page+1} / {pages}   ·   {len(CARDS)} '+self.t('张王牌', 'trumps'), 318, 770, 17, GOLD)
        self.button((1153, 758, 150, 46), self.t('下一页', 'Next'), 'book_next', enabled=self.book_page+1 < pages)

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
                self.history.ingest(value)
                sound_events.extend(self.sound_tracker.update(value, self.pid))
                latest = value
            elif event == 'mood':
                self.bot_mood = value
            elif event == 'error':
                self.connection.close()
                self.scene = 'menu'
                self.error = self.t('端口 6666 已被占用，请关闭已有房间。', 'Port 6666 is in use. Close the existing room.') if value == 'port' else self.t('连接已中断或失败，请确认房主 IP、组网和房间状态。', 'Connection failed or closed. Check the host IP, network and room.')
                latest = None
                self.sound_tracker.reset()
                sound_events = ['error']
        if latest:
            self.state_received_at = time.monotonic()
            token = (latest.round_id, tuple(getattr(latest, f'p{self.pid}_trumps')))
            if token != self.selection_token:
                self.selected = None
                self.selection_token = token
            self.gs = latest
            self.scene = 'game'
            if latest.phase == 'ACTION':
                self.rematch = False
        self.sound.play_many(sound_events)

    def command(self, name):
        if self.demo or not self.gs:
            return
        if name != 'REMATCH' and not self.can_act():
            return
        if self.connection.send(name, self.gs.round_id):
            self.sound_tracker.sent(name, self.gs, self.pid)
            self.cooldown = time.monotonic()+.55
        else:
            self.connection.events.put((self.connection.generation, 'error', 'connection'))

    def action(self, action):
        if action in ('updates', 'timers', 'history'):
            self.overlay = action
            return
        if action == 'close_overlay':
            self.overlay = None
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
            if kind == 'select':
                self.selected = value
            elif kind == 'inspect':
                self.book_selected = value
            elif kind == 'difficulty':
                self.difficulty = value
            elif kind == 'style':
                self.style = value
            elif kind == 'timer':
                config = load_timer(self.data_root)
                presets = {
                    'off': dict(enabled=False), '30s': dict(enabled=True, mode='turn', turn_seconds=30),
                    '60s': dict(enabled=True, mode='turn', turn_seconds=60),
                    '3+3': dict(enabled=True, mode='fischer', initial_minutes=3, increment_seconds=3),
                    '5+3': dict(enabled=True, mode='fischer', initial_minutes=5, increment_seconds=3),
                }
                config.update(presets.get(value, {}))
                self.timer = timer_config(config)
                self.overlay = None
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
        elif action in ('host', 'join', 'solo_start'):
            self.sound_tracker.reset()
            self.error = ''
            self.gs = None
            self.selected = None
            self.page = 0
            self.focus = False
            self.demo = False
            self.solo = action == 'solo_start'
            self.bot_mood = None
            self.host = action in ('host', 'solo_start')
            self.scene = 'connecting'
            self.connection.open('127.0.0.1' if self.host else self.ip.strip(), self.host,
                                 (self.difficulty, self.style) if self.solo else None, self.timer)
        elif action == 'menu':
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
        self.buttons = []
        shade = pg.Surface((W, H), pg.SRCALPHA)
        shade.fill((4, 9, 8, 235))
        self.canvas.blit(shade, (0, 0))
        self.panel((100, 55, 1240, 790), PANEL, LINE, 22)
        titles = {'updates': ('手动更新', 'MANUAL UPDATE'), 'timers': ('计时设置', 'TIME CONTROL'), 'history': ('战斗日志', 'ACTION LOG')}
        self.text(self.t(*titles[self.overlay]), 136, 86, 32, INK, True)
        self.button((1180, 82, 124, 44), self.t('关闭', 'Close'), 'close_overlay')
        if self.overlay == 'timers':
            self.wrap(self.t('房主统一计时；选择后用于下一场对局。双方入座才开始，结算时暂停。', 'The host controls the clock for the next match. Waiting and settlement do not consume time.'), pg.Rect(136, 145, 1120, 62), 19)
            options = [('off', '不限时', 'No clock'), ('30s', '每次行动 30 秒', '30 seconds per turn'),
                       ('60s', '每次行动 60 秒', '60 seconds per turn'), ('3+3', '棋钟 3 分钟 + 3 秒', '3 minutes + 3 seconds'),
                       ('5+3', '棋钟 5 分钟 + 3 秒', '5 minutes + 3 seconds'), ('custom', '读取 timer.json 自定义', 'Load custom timer.json')]
            for i, (key, zh, en) in enumerate(options):
                self.button((136+(i%2)*584, 244+(i//2)*105, 558, 80), self.t(zh, en), ('timer', key))
            mode = self.timer['mode'] if self.timer['enabled'] else 'off'
            self.text(self.t('当前设置：', 'Current: ')+self.clock_label(), 136, 601, 22, GOLD)
            self.wrap(self.t('单次行动 / 每局额度耗尽：自动停牌。棋钟耗尽：整场判负。\n3+3 表示每人整场 3 分钟，抽牌或停牌交接后加 3 秒。使用、弃置王牌不加秒。\ntimer.json 还可设置每位玩家每局独立时间额度（round）。', 'Turn or per-round timeout: automatic stay. Fischer timeout: match loss.\n3+3 gives each player 3 minutes, plus 3 seconds after handing over with HIT or STAY. Trumps and discards earn no increment.\nUse timer.json for custom times and per-round budgets.'), pg.Rect(136, 650, 1140, 155), 18)
        elif self.overlay == 'history':
            entries = list(reversed(self.history.entries))
            pages = max(1, (len(entries)+11)//12)
            self.log_page = min(self.log_page, pages-1)
            self.text(self.t('最近动作在前 · 只包含公开信息 · 查看日志不暂停计时', 'Newest first · Public information only · The clock keeps running'), 136, 143, 18, MUTED)
            for i, entry in enumerate(entries[self.log_page*12:self.log_page*12+12]):
                y = 191+i*40
                self.text(f'R{entry["round"]:02}', 138, y, 16, GOLD)
                self.text(describe(entry, self.zh), 210, y, 18, INK, width=1080)
            if not entries:
                self.text(self.t('暂无日志；需要新版房主提供对局记录。', 'No history yet. A current host is needed to provide action logs.'), 138, 236, 20, MUTED)
            self.text(self.t('日志保存失败；本次记录仍可在这里查看。', 'Could not save the file; history remains available here.') if self.history.error else self.t('自动保存到游戏目录的 logs 文件夹，可随问题反馈附上。', 'Automatically saved in the game’s logs folder for bug reports.'), 138, 696, 16, RED if self.history.error else MUTED)
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
            self.wrap(self.t('下载源由 updates.json 指定。更新会校验 GitHub 提供的 SHA-256，并安装到独立的新目录。\n自动保留游戏、计时、声音、更新配置及 sounds 文件。新版本默认配置另存为 package-defaults。\n原程序与旧配置不被覆盖；更新失败时仍可继续使用当前版本。', 'The download source is set in updates.json. Archives are verified against GitHub’s SHA-256 and installed into a separate folder.\nGame, clock, audio and update settings and sounds are preserved. New defaults are kept in package-defaults.\nThe current program is never overwritten and remains usable if an update fails.'), pg.Rect(138, 471, 1125, 178), 19)
            self.button((138, 711, 300, 61), self.t('检查更新', 'Check for updates'), 'check_update', enabled=not self.updater.busy)
            if self.updater.status == 'available':
                self.button((462, 711, 400, 61), self.t('下载并准备新版', 'Download and prepare'), 'download_update', True)
            elif self.updater.status == 'installed':
                self.button((462, 711, 400, 61), self.t('打开新版', 'Open updated version'), 'launch_update', True)

    def render(self):
        self.updater.poll()
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
        self.canvas.blit(self.theme.scan, (0, 0))

    def present(self):
        size = self.window.get_size()
        self.scale = min(size[0]/W, size[1]/H)
        scaled = (max(1, int(W*self.scale)), max(1, int(H*self.scale)))
        self.viewport = pg.Rect((size[0]-scaled[0])//2, (size[1]-scaled[1])//2, *scaled)
        self.window.fill(BG)
        self.window.blit(pg.transform.smoothscale(self.canvas, scaled), self.viewport)
        pg.display.flip()

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
                                break
                    elif event.type == pg.KEYDOWN:
                        if event.key == pg.K_ESCAPE:
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
    args = parser.parse_args()
    if args.server:
        GameState.__module__ = '__main__'
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
