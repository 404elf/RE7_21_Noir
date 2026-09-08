"""Bounded information-set planning using the unchanged engine on hypothetical worlds.

No live GameState enters this module. Worlds contain a hypothesized hidden card,
shuffled unseen deck and sampled opponent trumps, never server secrets. Engine
functions are rebound to private globals so planning cannot consume game RNG.
"""
import math
import random
import types

import re7_21 as engine


def simulator(seed):
    scope = dict(vars(engine), random=random.Random(seed), SETTINGS=dict(engine.SETTINGS), WEIGHTS=dict(engine.WEIGHTS))
    methods = {name: types.FunctionType(fn.__code__, scope, name, fn.__defaults__, fn.__closure__)
               for name, fn in vars(engine.GameState).items() if isinstance(fn, types.FunctionType)}
    return type('Hypothesis', (engine.GameState,), methods)


def clone(gs):
    out = object.__new__(type(gs))
    out.__dict__ = dict(gs.__dict__)
    for key in ('deck','p1_hand','p2_hand','p1_trumps','p2_trumps'):
        setattr(out,key,list(getattr(gs,key)))
    out.active_trumps = [dict(t) for t in gs.active_trumps]
    return out


def legal(gs,pid,card=None):
    enemy = [t for t in gs.active_trumps if t['owner']!=pid]
    if card is None:
        return bool(gs.deck) and not gs.check_bust(pid) and not any(t['type'] in ('SILENCE','GAMBLE') for t in enemy)
    if any(t['type']=='DESTROY_BLOCK' for t in enemy): return False
    own = [t for t in gs.active_trumps if t['owner']==pid]
    return len(own)<engine.MAX_TABLE_SLOTS or card[1] in ('SHIELD_ATTACK','SHIELD_ATTACK_PLUS','OBLIVION') or (card[1]=='TARGET' and any(t['type']=='TARGET' for t in own))


def play(gs,pid,action):
    out = clone(gs)
    hand = getattr(out,f'p{pid}_trumps')
    kind,card = action
    if kind=='TRUMP':
        if card not in hand or not legal(out,pid,card): return None
        out.use_trump(pid,hand.index(card))
        if any(t['owner']==pid and t['type']=='HARVEST' for t in out.active_trumps): out.give_trump(pid,1)
        setattr(out,f'p{3-pid}_stop',False)
    elif kind=='DISCARD':
        if card not in hand: return None
        out.discard_trump(pid,hand.index(card))
        setattr(out,f'p{pid}_stop',False)
    elif kind=='HIT':
        if not legal(out,pid): return None
        out.draw_card(pid)
        setattr(out,f'p{pid}_stop',False)
    else:
        setattr(out,f'p{pid}_stop',True)
    return out


def reserve(card):
    # Valuable rescue/counter cards survive small, safe rounds for later use.
    return {'PERFECT':.28,'RETURN':.25,'TARGET':.24,'DESTROY':.28,'DESTROY_BLOCK':.4,
            'CHANGE':.24,'OBLIVION':.35,'CURSE':.3,'SHIELD':.13,'ADD':.10}.get(card[1],.15)


def score(gs):
    a,b = sum(gs.p1_hand),sum(gs.p2_hand)
    target = gs.target_score
    if a>target and b>target: winner=(b>a)-(a>b)
    elif a>target: winner=-1
    elif b>target: winner=1
    else: winner=(a>b)-(a<b)
    outgoing,incoming = gs.calculate_potential_damage(2),gs.calculate_potential_damage(1)
    gain = min(gs.p2_fingers,outgoing)+ (12 if outgoing>=gs.p2_fingers else 0)
    loss = min(gs.p1_fingers,incoming)+ (15 if incoming>=gs.p1_fingers else 0)
    value = gain if winner>0 else -loss if winner<0 else 0
    if any(t['type']=='ESCAPE' for t in gs.active_trumps): value=-1.5
    ours,theirs = gs.p1_trumps,gs.p2_trumps
    for t in gs.active_trumps:
        if t['type'] in ('FORCE_CONSUME','FORCE_CONSUME_PLUS'):
            factor = 1 if t['type'].endswith('PLUS') else .5
            value += (.16*len(theirs) if t['owner']==1 else -.20*len(ours))*factor
    value += getattr(gs,'reserve_scale',1.)*(sum(map(reserve,ours))-.75*sum(map(reserve,theirs)))
    # A small tie-breaker, never a substitute for winning/damage/lethal outcomes.
    value += .015*((a if a<=target else -a)-(b if b<=target else -b))
    return value


class Planner:
    def __init__(self,rng,nightmare=False):
        self.rng,self.nightmare=rng,nightmare
        self.key=None
        self.seen=set()
        self.belief={}
        self.last_plan=()

    def infer(self,v):
        pool=tuple(n for n in v.numbers if n not in v.hand+v.opponent_visible)
        if self.key!=v.round_key:
            self.key=v.round_key;self.seen=set();self.belief={n:1. for n in v.numbers}
        if self.nightmare:
            for event in v.events:
                eid,actor,action,kind,val,own,opp,after_own,after_opp,target,locked=event
                if eid in self.seen: continue
                self.seen.add(eid)
                for hidden in self.belief:
                    likelihood=1.
                    available=[n for n in v.numbers if n not in own+opp and n not in (v.hand[0],hidden)]
                    before=own if actor==1 else opp
                    after=after_own if actor==1 else after_opp
                    total=sum(before)+(v.hand[0] if actor==1 else hidden)
                    if action=='trump' and not locked:
                        if kind in ('PERFECT','PERFECT_PLUS','ULTIMATE_DRAW') and len(after)>len(before):
                            safe=[n for n in available if n+total<=target]
                            predicted=max(safe) if safe else min(available,default=0)
                            likelihood=1. if after[-1]==predicted else .005
                        elif kind in ('DRAW_SPEC','DRAW_SPEC_PLUS'):
                            drawn=len(after)>len(before)
                            likelihood=1. if drawn==(val in available) else .005
                    # Actions are noisy signals, not proof: never eliminate a bluff.
                    if actor==2 and action=='stay':
                        likelihood*=.45 if sum(v.opponent_visible)+hidden<target*.68 else 1.15
                    if actor==2 and kind in ('ADD','GAMBLE','ADD_21'):
                        likelihood*=1.25 if total>=target*.8 and total<=target else .8
                    if actor==2 and kind=='SHIELD':
                        likelihood*=1.1 if total<target*.85 else .95
                    self.belief[hidden]*=likelihood
        weights={n:max(1e-12,self.belief.get(n,1.)) for n in pool}
        normal=sum(weights.values()) or 1
        return {n:w/normal for n,w in weights.items()}

    def worlds(self,v):
        belief=self.infer(v)
        if not belief: return []
        cls=simulator(self.rng.randrange(2**32))
        base=cls()
        hidden=list(belief)
        count=8 if self.nightmare else 5
        if len(hidden)>count:
            # Weighted strata rather than a single optimistic hidden-card guess.
            cumulative=[];s=0
            for n in hidden: s+=belief[n];cumulative.append((s,n))
            selected=[next(n for c,n in cumulative if c>=(i+.5)/count) for i in range(count)]
            weights={n:selected.count(n)/count for n in set(selected)}
        else: weights=belief
        worlds=[]
        for n,weight in weights.items():
            gs=clone(base)
            gs.p1_hand=list(v.hand);gs.p2_hand=[n]+list(v.opponent_visible)
            gs.deck=[x for x in v.numbers if x not in gs.p1_hand+gs.p2_hand]
            self.rng.shuffle(gs.deck)
            gs.deck=gs.deck[:v.deck_count]
            gs.p1_trumps=list(v.trumps);gs.p2_trumps=[]
            gs.give_trump(2,v.opponent_count)
            gs.p1_fingers=v.hp;gs.p2_fingers=v.opponent_hp
            gs.reserve_scale=3.0 if self.nightmare else 2.4
            gs.target_score=v.target;gs.p1_stop=False;gs.p2_stop=v.opponent_stopped
            gs.active_trumps=[dict(owner=p,name=name,type=k,val=value,counter=c) for p,name,k,value,c in v.table]
            # Observation constructors used by tests/custom callers may omit table.
            if v.draw_locked and not any(t['owner']==2 and t['type'] in ('SILENCE','GAMBLE') for t in gs.active_trumps):
                gs.active_trumps.append(dict(owner=2,name='Silence',type='SILENCE',val=0))
            if not v.table:
                for victim,damage in ((1,v.incoming),(2,v.outgoing)):
                    if damage>1: gs.active_trumps.append(dict(owner=3-victim,name='Stake',type='ADD',val=damage-1))
            worlds.append((gs,weight))
        return worlds

    def expected(self,worlds):
        values=[(score(gs),w) for gs,w in worlds]
        mean=sum(x*w for x,w in values)
        # Nightmare protects against dangerous uncertain outcomes without refusing
        # a necessary gamble when all alternatives lose.
        return mean-(.10*sum(w*max(0,mean-x) for x,w in values) if self.nightmare else 0)

    def responses(self,gs):
        """Pessimistic counterplay on a sampled opponent hand; bounded to two trumps.

        Opponent knows more inside this hypothetical evaluation than a real player.
        That deliberately discounts fragile combos; it does not reveal real secrets.
        """
        gs=clone(gs);gs.cleanup_player_instants(2)
        natural=clone(gs)
        for _ in range(3):
            known=set(natural.p2_hand+natural.p1_hand[1:])
            unseen=[n for n in range(engine.SETTINGS['deck_range_start'],engine.SETTINGS['deck_range_end']+1) if n not in known]
            total=sum(natural.p2_hand)
            bust=sum(total+n>natural.target_score for n in unseen)/max(1,len(unseen))
            if not legal(natural,2) or total>=natural.target_score*.86 or bust>.5: break
            natural=play(natural,2,('HIT',None))
            if not natural.p1_stop: break
        natural=play(natural,2,('STAY',None))
        candidates=[gs]
        for _ in range(2 if self.nightmare else 1):
            expanded=list(candidates)
            for state in candidates:
                for card in sorted(set(state.p2_trumps)):
                    if card[1]=='OBLIVION': continue
                    child=play(state,2,('TRUMP',card))
                    if child is not None: expanded.append(child)
            candidates=sorted(expanded,key=score)[:2]
        outcomes=[]
        for state in candidates:
            outcomes.append(play(state,2,('STAY',None)))
            if legal(state,2):
                # Evaluate a random draw in this shuffled hypothesis.
                drawn=play(state,2,('HIT',None))
                outcomes.append(drawn)
        enemy=min(outcomes,key=score)
        if self.nightmare:
            # Our retained counter on the following turn makes bait-and-switch
            # target plans and reserve cards useful, instead of blindly cashing out.
            future=clone(enemy);future.cleanup_player_instants(1)
            counter=[future]
            for card in sorted(set(future.p1_trumps)):
                if card[1]=='OBLIVION': continue
                child=play(future,1,('TRUMP',card))
                if child is not None: counter.append(child)
            # Both stopped settles before a future response is possible.
            if not (enemy.p1_stop and enemy.p2_stop):
                best=max(counter,key=score)
                return .65*score(natural)+.20*score(enemy)+.15*score(best)
        return .65*score(natural)+.35*score(enemy)

    def choose(self,v,extra,mood):
        worlds=self.worlds(v)
        if not worlds: return 'STAY'
        max_extra=12 if self.nightmare else 4
        if extra<max_extra and len(v.trumps)>=v.max_trumps and v.incoming<=1:
            shields=[i for i,c in enumerate(v.trumps) if c[1]=='SHIELD']
            if len(shields)>2 and sum(v.hand)>=sum(v.opponent_visible)+max(self.infer(v),default=0):
                return f'DISCARD:{shields[0]}'
        baseline=self.expected(worlds)
        if extra<max_extra and not v.trump_locked and not v.table_full:
            guaranteed = sum(w for gs,w in worlds if sum(gs.p1_hand)<=gs.target_score and (sum(gs.p1_hand)>sum(gs.p2_hand) or sum(gs.p2_hand)>gs.target_score))
            for i,c in enumerate(v.trumps):
                if c[1]=='ADD' and guaranteed>=.95 and v.outgoing<v.opponent_hp: return f'TRUMP:{i}'
                if c[1]=='SHIELD' and v.hp<=v.incoming and guaranteed<.5: return f'TRUMP:{i}'
        belief=self.infer(v)
        def information(path):
            if not self.nightmare: return 0.
            bonus=0.
            for action,card in path:
                if action=='TRUMP' and card[1] in ('DRAW_SPEC','DRAW_SPEC_PLUS') and not v.draw_locked:
                    p=belief.get(card[2],0.)
                    if 0<p<1:
                        # A successful/failed specific draw splits the possible
                        # hidden cards. Small information value can justify a probe.
                        bonus+=.22*(-p*math.log2(p)-(1-p)*math.log2(1-p))
            return bonus
        terminal=[]
        frontier=[(worlds,())]
        depth=min(3 if self.nightmare else 2,max(0,max_extra-extra))
        for level in range(depth+1):
            expanded=[]
            for states,path in frontier:
                for end in ('STAY','HIT'):
                    if end=='HIT' and (v.draw_locked or any(not legal(gs,1) for gs,w in states)): continue
                    after=[(play(gs,1,(end,None)),w) for gs,w in states]
                    # Holding has a future opponent turn too, unless both stopped.
                    value=sum(w*(score(gs) if end=='STAY' and gs.p2_stop else self.responses(gs)) for gs,w in after)
                    if not self.nightmare and end=='HIT':
                        value += .10 if mood=='gambler' else -.04
                    terminal.append((value+information(path),path+((end,None),)))
                if level==depth or v.trump_locked or v.table_full: continue
                common=set(states[0][0].p1_trumps)
                for gs,w in states[1:]: common.intersection_update(gs.p1_trumps)
                for card in sorted(common):
                    actions=[('TRUMP',card)]
                    if len(states[0][0].p1_trumps)>=v.max_trumps or any(k in ('DESIRE','DESIRE_PLUS') for k,val in v.enemy_effects):
                        actions.append(('DISCARD',card))
                    for action in actions:
                        children=[(play(gs,1,action),w) for gs,w in states]
                        if any(gs is None for gs,w in children): continue
                        value=self.expected(children)+information(path+(action,))
                        # Resetting is a rescue, not a free imagined fresh lucky hand.
                        if card[1]=='OBLIVION' and action[0]=='TRUMP':
                            terminal.append((-0.4,path+(action,)));continue
                        expanded.append((value,children,path+(action,)))
            if not expanded: break
            # Reserve beam slots for diverse first moves, including setups whose
            # single-card value is low but whose next card completes a combo.
            ranked=sorted(expanded,key=lambda item:item[0],reverse=True)
            frontier=[];firsts=set()
            width=4 if self.nightmare else 2
            for value,states,path in ranked:
                if path[0] in firsts: continue
                frontier.append((states,path));firsts.add(path[0])
                if len(frontier)>=width: break
        value,path=max(terminal,key=lambda item:item[0])
        self.last_plan=path
        action,card=path[0]
        if action in ('TRUMP','DISCARD'): return f'{action}:{v.trumps.index(card)}'
        if not self.nightmare and mood=='gambler' and action=='STAY' and not v.opponent_stopped and not v.draw_locked and v.deck_count and sum(v.hand)<v.target:
            pool=tuple(self.infer(v))
            if pool and sum(sum(v.hand)+n>v.target for n in pool)/len(pool)<=.75 and self.rng.random()<.32:
                return 'HIT'
        # Make room when genuinely worthless cards clog the hand. No forced waste
        # of a useful recovery card merely because a slot is full.
        if extra<max_extra and len(v.trumps)>=v.max_trumps and v.trumps:
            useless=[(reserve(c),i) for i,c in enumerate(v.trumps) if c[1]=='SHIELD' and v.incoming<=1 and baseline>=0]
            if useless: return f'DISCARD:{min(useless)[1]}'
        return action
