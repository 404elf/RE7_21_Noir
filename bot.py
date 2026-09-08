"""Fair-information opponent. Strategy never receives a GameState or hidden cards."""
from dataclasses import dataclass, replace
import random
import socket
import threading
import time

import re7_21 as engine

DIFFICULTIES = {
    'easy': ('入门', 'Easy', '偶尔误判，简单使用王牌。', 'Occasional mistakes and simple trump play.'),
    'normal': ('标准', 'Normal', '估算爆牌风险，寻找实用组合。', 'Estimates bust risk and uses useful trumps.'),
    'hard': ('困难', 'Hard', '比较行动收益，结合明牌与生命决策。', 'Compares outcomes using visible cards and health.'),
}
STYLES = {
    'gambler': ('赌徒', 'Gambler', '追求高点数，愿意冒险翻盘。', 'Chases high totals and risky comebacks.'),
    'conservative': ('保守', 'Cautious', '优先保命，倾向提前停牌。', 'Protects health and tends to stay early.'),
    'swing': ('摇摆型', 'Wildcard', '每隔 2–4 次决策，在冒险与稳健间切换。', 'Switches between bold and cautious every 2–4 decisions.'),
}


@dataclass(frozen=True)
class Observation:
    hand: tuple
    opponent_visible: tuple
    trumps: tuple
    opponent_stopped: bool
    hp: int
    opponent_hp: int
    max_hp: int
    target: int
    deck_count: int
    numbers: tuple
    draw_locked: bool = False
    trump_locked: bool = False
    opponent_draw_locked: bool = False
    table_full: bool = False
    max_trumps: int = 20
    incoming: int = 1
    outgoing: int = 1
    enemy_effects: tuple = ()


def observe(state, pid=2):
    """Explicit allowlist: exclude the opponent's first card, trumps and deck order."""
    opponent = 3-pid
    table = state.active_trumps
    return Observation(
        tuple(getattr(state, f'p{pid}_hand')),
        tuple(getattr(state, f'p{opponent}_hand')[1:]),
        tuple(getattr(state, f'p{pid}_trumps')),
        getattr(state, f'p{opponent}_stop'),
        getattr(state, f'p{pid}_fingers'), getattr(state, f'p{opponent}_fingers'),
        state.max_hp_limit, state.target_score, len(state.deck),
        tuple(range(engine.SETTINGS['deck_range_start'], engine.SETTINGS['deck_range_end']+1)),
        any(t['owner'] == opponent and t['type'] in ('GAMBLE', 'SILENCE') for t in table),
        any(t['owner'] == opponent and t['type'] == 'DESTROY_BLOCK' for t in table),
        any(t['owner'] == pid and t['type'] in ('GAMBLE', 'SILENCE') for t in table),
        sum(t['owner'] == pid for t in table) >= engine.MAX_TABLE_SLOTS,
        engine.MAX_TRUMPS,
        max(0, 1+sum(t['val'] for t in table if t['owner']==opponent and t['type'] in ('ADD','RETURN_PLUS','PERFECT_PLUS','DEATH_DESTROY'))-sum(t['val'] for t in table if t['owner']==pid and t['type']=='SHIELD')),
        max(0, 1+sum(t['val'] for t in table if t['owner']==pid and t['type'] in ('ADD','RETURN_PLUS','PERFECT_PLUS','DEATH_DESTROY'))-sum(t['val'] for t in table if t['owner']==opponent and t['type']=='SHIELD')),
        tuple((t['type'], t['val']) for t in table if t['owner'] == opponent),
    )


class Strategy:
    def __init__(self, difficulty='normal', style='swing', seed=None):
        if difficulty not in DIFFICULTIES or style not in STYLES:
            raise ValueError('Unknown difficulty or style')
        self.difficulty = difficulty
        self.style = style
        self.rng = random.Random(seed)
        self.mood = self.rng.choice(('gambler', 'conservative')) if style == 'swing' else style
        self.remaining = self.rng.randint(2, 4)

    def unknown(self, view):
        known = set(view.hand + view.opponent_visible)
        return tuple(n for n in view.numbers if n not in known)

    def utility(self, view, hand=None, visible=None, hidden_pool=None):
        hand = view.hand if hand is None else hand
        visible = view.opponent_visible if visible is None else visible
        pool = self.unknown(view) if hidden_pool is None else hidden_pool
        total = sum(hand)
        # No opponent hidden-card access: average over values still unobserved.
        scores = []
        def result(opponent):
            if total > view.target and opponent > view.target:
                return (opponent > total)-(opponent < total)
            if total > view.target: return -1
            if opponent > view.target: return 1
            return (total > opponent)-(total < opponent)

        def future(opponent, available, depth=0):
            if opponent >= view.target*.82 or not available or depth>=3:
                return result(opponent)
            # Bound work for custom large decks; use evenly spaced public hypotheses.
            choices=available if len(available)<=8 else tuple(available[i*(len(available)-1)//7] for i in range(8))
            return sum(future(opponent+n,tuple(v for v in available if v!=n),depth+1) for n in choices)/len(choices)
        for hidden in pool or (0,):
            opponent = sum(visible)+hidden
            if total > view.target and opponent > view.target:
                outcome = (opponent > total)-(opponent < total)
            elif total > view.target:
                outcome = -1
            elif opponent > view.target:
                outcome = 1
            else:
                outcome = (total > opponent)-(total < opponent)
            if view.opponent_stopped:
                score = float(outcome)
            elif self.difficulty == 'hard':
                # Account for the opponent improving their hand, rather than assuming
                # that a low current total is enough to protect a lead.
                score = future(opponent,tuple(n for n in pool if n!=hidden and n not in hand))
            else:
                # Opponent may keep drawing: do not settle merely because ahead now.
                proximity = min(total/view.target, 1) if total <= view.target else -1-(total-view.target)/view.target
                score = .35*outcome + .65*proximity
            if total > view.target:
                penalty = .12 if self.mood == 'gambler' else .5
                if self.difficulty == 'hard':
                    penalty = .04 if self.mood == 'gambler' else .10
                score -= penalty
            scores.append(score)
        return sum(scores)/len(scores)

    def card_value(self, view, card):
        _, kind, value = card
        hand = view.hand
        total = sum(hand)
        pool = self.unknown(view)
        baseline = self.utility(view)
        win = max(0., min(1., (self.utility(replace(view, opponent_stopped=True))+1)/2))
        if kind == 'TARGET':
            return self.utility(replace(view, target=value))-baseline
        if kind in ('ADD', 'ADD_21'):
            if kind == 'ADD_21' and total != 21: return 0
            return .08 + .35*win*min(value, max(0, view.opponent_hp-view.outgoing))
        if kind == 'SHIELD':
            return .5*(1-win)*min(value, view.incoming, view.hp)
        if kind in ('DESTROY_SINGLE', 'DESTROY_ALL', 'DESTROY_BLOCK'):
            harmful = sum(k in ('ADD','SHIELD','GAMBLE','SILENCE','DESIRE','DESIRE_PLUS','PERFECT_PLUS','RETURN_PLUS') for k,v in view.enemy_effects)
            return .24*min(harmful, 1 if kind == 'DESTROY_SINGLE' else harmful)
        if kind == 'SILENCE':
            return .4*win if not view.opponent_stopped and not view.opponent_draw_locked else 0
        if kind == 'GAMBLE':
            return .8*(win-.65) if total <= view.target else 0
        if kind == 'RETURN' and len(hand) > 1:
            return self.utility(view, hand[:-1])-baseline
        if kind in ('REMOVE', 'RETURN_PLUS') and view.opponent_visible:
            return self.utility(view, visible=view.opponent_visible[:-1])-baseline
        if kind == 'CHANGE' and len(hand) > 1 and view.opponent_visible:
            return self.utility(view, hand[:-1]+view.opponent_visible[-1:], view.opponent_visible[:-1]+hand[-1:])-baseline
        if kind in ('DRAW_SPEC', 'DRAW_SPEC_PLUS'):
            gain = 0
            if not view.draw_locked and view.deck_count and value in pool:
                gain = self.utility(view, hand+(value,), hidden_pool=tuple(n for n in pool if n != value))-baseline
                gain *= max(0, (len(pool)-1)/len(pool))
            if kind == 'DRAW_SPEC_PLUS':
                gain += .09
            return gain
        if kind in ('PERFECT', 'PERFECT_PLUS', 'ULTIMATE_DRAW', 'DEATH_DESTROY'):
            gain = 0
            if not view.draw_locked and view.deck_count and len(pool) > 1:
                outcomes = []
                # Sample possible unseen decks, never the actual server deck.
                for _ in range(18 if self.difficulty == 'hard' else 6):
                    hidden = self.rng.choice(pool)
                    available = [n for n in pool if n != hidden]
                    deck = self.rng.sample(available, min(view.deck_count, len(available)))
                    safe = [n for n in deck if total+n <= view.target]
                    if deck:
                        draw = max(safe) if safe else min(deck)
                        outcomes.append(self.utility(view, hand+(draw,), hidden_pool=(hidden,)))
                if outcomes:
                    gain = sum(outcomes)/len(outcomes)-baseline
            if kind == 'ULTIMATE_DRAW':
                gain += .15
            if kind == 'DEATH_DESTROY':
                gain -= .08*((len(view.trumps)-1)//2)
            return gain
        if kind in ('LOVE', 'CURSE') and not view.opponent_draw_locked and view.deck_count and len(pool) > 1:
            outcomes = []
            for hidden in pool:
                opponent = sum(view.opponent_visible)+hidden
                if opponent > view.target:
                    outcomes.append(baseline)
                    continue
                available = [n for n in pool if n != hidden]
                safe = [n for n in available if opponent+n <= view.target]
                draw = max(available) if kind == 'CURSE' else max(safe) if safe else min(available)
                outcomes.append(self.utility(view, visible=view.opponent_visible+(draw,), hidden_pool=(hidden,)))
            return sum(outcomes)/len(outcomes)-baseline-(.07 if kind == 'CURSE' else 0)
        room = max(0, view.max_trumps-len(view.trumps)+1)
        if kind == 'MAGIC_DRAW':
            return .09*min(3, room)
        if kind == 'TRUMP_EXCHANGE_PLUS':
            return .07*min(4, room+1)-(.04 if len(view.trumps) > 1 else 0)
        if kind == 'TRUMP_EXCHANGE' and len(view.trumps) >= 3:
            return .11
        if kind == 'HAPPINESS':
            return .045 if room else 0
        if kind == 'OBLIVION':
            return .6 if baseline < -.65 else 0
        # Unmodelled effects are held rather than played without an estimated benefit.
        return 0

    def choose(self, view, extra_actions=0):
        if self.style == 'swing' and self.remaining == 0:
            self.mood = 'conservative' if self.mood == 'gambler' else 'gambler'
            self.remaining = self.rng.randint(2, 4)
        self.remaining = max(0, self.remaining-1)
        pool = self.unknown(view)
        total = sum(view.hand)
        max_extra = {'easy': 1, 'normal': 2, 'hard': 4}[self.difficulty]
        if not view.trump_locked and not view.table_full and extra_actions < max_extra:
            scored = [(self.card_value(view, card), i) for i, card in enumerate(view.trumps)]
            if scored:
                gain, index = max(scored)
                threshold = {'easy': .16, 'normal': .07, 'hard': .025}[self.difficulty]
                willingness = {'easy': .38, 'normal': .82, 'hard': 1}[self.difficulty]
                if gain > threshold and self.rng.random() < willingness:
                    return f'TRUMP:{index}'
        if extra_actions < max_extra and len(view.trumps) >= view.max_trumps and view.trumps:
            scored = [(self.card_value(view, card), i) for i, card in enumerate(view.trumps)]
            gain, index = min(scored)
            if gain <= 0:
                return f'DISCARD:{index}'
        if total > view.target or view.draw_locked or not view.deck_count or not pool:
            return 'STAY'
        bust = sum(total+n > view.target for n in pool)/len(pool)
        cap = .72 if self.mood == 'gambler' else .22
        if self.difficulty == 'easy':
            threshold = view.target*(.9 if self.mood == 'gambler' else .72)
            if self.rng.random() < .18:
                threshold += self.rng.choice((-3, 3))
            return 'HIT' if total < threshold and bust <= cap+.12 else 'STAY'
        if self.difficulty == 'normal':
            threshold = view.target*(.93 if self.mood == 'gambler' else .78)
            return 'HIT' if total < threshold and bust <= cap else 'STAY'
        if self.mood == 'conservative': cap = .5
        baseline = self.utility(view)
        if self.mood == 'gambler' and total < view.target and bust <= cap:
            # A deliberate risk appetite, independent of the cautious utility penalty.
            if self.rng.random() < .42 and (total < view.target-1 or view.opponent_stopped):
                return 'HIT'
        expected = sum(self.utility(view, view.hand+(n,), hidden_pool=tuple(h for h in pool if h != n)) for n in pool)/len(pool)
        # Bold opponents accept near-break-even draws; cautious ones need a margin.
        margin = -.09 if self.mood == 'gambler' else .015
        if self.mood == 'gambler' and view.hp < view.opponent_hp:
            margin -= .04
        if view.opponent_stopped and baseline < -.25:
            cap = max(cap, .8)  # A likely loss can justify a last attempt.
        return 'HIT' if bust <= cap and expected > baseline+margin else 'STAY'


class BotSession:
    """A real second client; speaks the same commands and obeys server cooldowns."""
    def __init__(self, difficulty, style, on_error=lambda: None, on_mood=lambda mood: None):
        self.strategy = Strategy(difficulty, style)
        self.on_error, self.on_mood = on_error, on_mood
        self.stop = threading.Event()
        self.sock = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        sock = self.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)

    def run(self):
        try:
            self.sock = socket.create_connection(('127.0.0.1', engine.DEFAULT_PORT), timeout=3)
            if self.stop.is_set():
                return
            if engine.recv_msg(self.sock) != 'ID:2':
                raise ConnectionError('Bot must take the second seat')
            self.sock.settimeout(None)
            ready_at = time.monotonic()+1
            last_round = None
            extra = 0
            rematch_sent = False
            while not self.stop.is_set():
                state = engine.recv_msg(self.sock)
                if not isinstance(state, engine.GameState):
                    raise ConnectionError('Bot disconnected')
                now = time.monotonic()
                if state.phase == 'GAMEOVER':
                    if state.p1_req_rematch and not rematch_sent and now >= ready_at:
                        engine.send_msg(self.sock, f'REMATCH:{state.round_id}')
                        rematch_sent = True
                    continue
                rematch_sent = False
                if getattr(state, 'draw_offer', 0) == 1:
                    # No hidden information: accept when behind in health, decline otherwise.
                    answer = 'DRAW_ACCEPT' if state.p2_fingers <= state.p1_fingers else 'DRAW_DECLINE'
                    engine.send_msg(self.sock, f'{answer}:{state.round_id}')
                if state.round_id != last_round or state.turn != 2 or state.phase != 'ACTION':
                    extra = 0
                    ready_at = now+.85
                    last_round = state.round_id
                    continue
                if now < ready_at:
                    continue
                view = observe(state)
                command = self.strategy.choose(view, extra)
                self.on_mood(self.strategy.mood)
                if not engine.send_msg(self.sock, f'{command}:{state.round_id}'):
                    raise ConnectionError('Bot send failed')
                if command.startswith(('TRUMP:', 'DISCARD:')):
                    extra += 1
                ready_at = now+{'easy': 1.25, 'normal': 1., 'hard': .8}[self.strategy.difficulty]
        except (OSError, ConnectionError):
            if not self.stop.is_set():
                self.on_error()
        finally:
            if self.sock:
                self.sock.close()
