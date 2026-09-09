"""Paired-seat comparison: the same deal seed is played with both AI assignments."""
import argparse
import json
import os
from pathlib import Path
import random
import sys
import types
import zipfile
os.environ.setdefault('SDL_VIDEODRIVER','dummy')
os.environ.setdefault('SDL_AUDIODRIVER','dummy')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot import Strategy,observe
from match import Match
from tactics import Planner


def previous(path):
    bot=types.ModuleType('wait_baseline_bot');sys.modules[bot.__name__]=bot
    tactics=types.ModuleType('wait_baseline_tactics');sys.modules[tactics.__name__]=tactics
    with zipfile.ZipFile(path) as archive:
        exec(compile(archive.read('tactics.py'),'wait_baseline_tactics.py','exec'),tactics.__dict__)
        source=archive.read('bot.py').decode('utf-8').replace('from tactics import Planner','from wait_baseline_tactics import Planner')
        exec(compile(source,'wait_baseline_bot.py','exec'),bot.__dict__)
    return bot.Strategy


def game(seed,seat,difficulty,style,rival):
    random.seed(89000+seed)
    now=[100000.]
    m=Match({'settlement_seconds':0},monotonic=lambda:now[0],wall=lambda:now[0])
    bots={p:(Strategy if p==seat else rival)(difficulty,style,seed*2+p) for p in (1,2)}
    extra={1:0,2:0};eligible=0;waits=0
    for step in range(2500):
        now[0]+=1;m.tick();gs=m.gs
        if gs.phase=='GAMEOVER':
            return dict(seed=seed,seat=seat,result='win' if gs.round_winner==seat else 'draw' if gs.round_winner==0 else 'loss',decisions=step,eligible=eligible,waits=waits)
        if gs.phase!='ACTION':continue
        pid=gs.turn;v=observe(gs,pid)
        waiting=pid==seat and Planner.certainly_ahead(v)
        action=bots[pid].choose(v,extra[pid])
        if waiting:eligible+=1;waits+=action=='STAY'
        if not m.command(pid,f'{action}:{gs.round_id}'):raise AssertionError((seed,seat,step,action))
        numbers=gs.p1_hand+gs.p2_hand+gs.deck
        if len(numbers)!=len(set(numbers)):raise AssertionError('duplicate number')
        if action in ('HIT','STAY') or gs.phase!='ACTION':extra={1:0,2:0}
        else:extra[pid]+=1
    raise AssertionError((seed,seat,'unfinished match'))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--previous',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pairs',type=int,default=8)
    parser.add_argument('--seed-offset',type=int,default=0)
    parser.add_argument('--difficulty',choices=('hard','nightmare'))
    args=parser.parse_args();rival=previous(args.previous)
    groups=[('nightmare','swing')]+[('hard',s) for s in ('conservative','gambler','swing')]
    with args.output.open('x',encoding='utf-8') as out:
        for difficulty,style in groups:
            if args.difficulty and difficulty!=args.difficulty:continue
            counts=dict(win=0,loss=0,draw=0)
            for seed in range(args.seed_offset,args.seed_offset+args.pairs):
                for seat in (1,2):
                    row=game(seed,seat,difficulty,style,rival)
                    row.update(difficulty=difficulty,style=style)
                    counts[row['result']]+=1
                    out.write(json.dumps(row)+'\n');out.flush()
                print(json.dumps(dict(difficulty=difficulty,style=style,pairs=seed-args.seed_offset+1,**counts)),flush=True)
