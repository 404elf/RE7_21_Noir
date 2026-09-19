"""Render a reproducible trailer from the actual UI and a legal scripted match.

No desktop capture, external footage, copyrighted music, network play, or changes
to player settings. Output must be a new directory. Install imageio-ffmpeg and
numpy in a separate production environment; they are not game dependencies.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import types
import wave

os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'build/media-deps'))
runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/site-packages'
if runtime.exists():sys.path.append(str(runtime))
import numpy as np
import pygame as pg
import imageio_ffmpeg
import main
from match import Match
from config_editor import ConfigEditor
from history import History

FPS=30
DURATION=60
SIZE=(1920,1080)
INK=(231,224,204)
GOLD=(197,164,111)
RED=(195,59,45)
CHAPTERS=[
 (0,4,'入座之前','这张牌，你敢要吗？'),
 (4,10,'01 / 共用牌堆','1—11，每个数字只有一张。'),
 (10,16,'02 / 局势反转','你刚抽到 20，对手却把目标改成了 17。'),
 (16,22,'03 / 留住后手','归还最后一张牌。现在，轮到你加注。'),
 (22,29,'04 / 亮牌','赢下来的，未必是点数最大的那个人。'),
 (29,36,'05 / 人机对战','四档难度，从熟悉规则，到挑战极难。'),
 (36,43,'06 / 自定义牌局','选预设，改权重，把牌局配成你喜欢的样子。'),
 (43,48,'07 / 你的节奏','不限时，或者 5+3。节奏由你决定。'),
 (48,53,'08 / 好友对战','局域网、地址直连、自建房间。'),
 (53,60,'RE7 / 21 — NOIR','免费游玩 · MIT 开源 · 中文 / English'),
]


def run(output,preview=False):
    output.mkdir(parents=True,exist_ok=False)
    work=output/'剪辑源文件';work.mkdir()
    now=[10000.]
    main.time=types.SimpleNamespace(monotonic=lambda:now[0],time=lambda:now[0])
    random.seed(21)
    app=main.App()
    app.sound.enabled=False
    app.motion.clock=lambda:now[0]
    app.history=History(work)
    app.scene='game';app.solo=False;app.demo=False
    match=Match(dict(enabled=False,settlement_seconds=3),lambda:now[0],lambda:now[0])
    g=match.gs
    # Deliberately curated starting hands, with a single copy of every number.
    g.p1_hand=[11,6];g.p2_hand=[10,8]
    g.p1_trumps=[('Return','RETURN',0),('Add 2','ADD',2),('Shield','SHIELD',1),('Perfect','PERFECT',0)]
    g.p2_trumps=[('Go 17','TARGET',17)]
    g.deck=[1,2,4,5,7,9,3]
    g.p1_fingers=g.p2_fingers=10;g.max_hp_limit=10
    match.log=[];match.sequence=0;match.record('round_start');match.publish()
    assert sorted(g.p1_hand+g.p2_hand+g.deck)==list(range(1,12))
    cues=[];actions=[]

    def snapshot():
        match.publish();after=copy.deepcopy(g)
        app.observe_notices(after);app.animate_state(app.gs,after)
        app.gs=after;app.state_received_at=now[0]
        app.selection_token=(g.round_id,tuple(g.p1_trumps))

    def command(pid,cmd,t):
        if cmd.startswith('TRUMP:') and pid==1:
            idx=int(cmd.split(':')[1]);name=g.p1_trumps[idx][0]
            app.pending_motion=(name,(450,390),g.round_id)
        assert match.command(pid,f'{cmd}:{g.round_id}'),(pid,cmd)
        assert sorted(g.p1_hand+g.p2_hand+g.deck)==list(range(1,12))
        actions.append(dict(time=t,player=pid,command=cmd,hands=[g.p1_hand.copy(),g.p2_hand.copy()],target=g.target_score))
        cue='card_draw' if cmd=='HIT' else 'trump_play' if cmd.startswith('TRUMP') else 'stay'
        cues.append((t,cue));snapshot()
        if g.phase=='RESULT':cues.extend([(t,'round_win'),(t+.1,'damage')])

    schedule=[(9,1,'HIT'),(12,2,'TRUMP:0'),(14,2,'STAY'),(18,1,'TRUMP:0'),(21,1,'TRUMP:0'),(23,1,'STAY'),(25,2,'STAY')]
    applied=set()
    snapshot()
    frames={}
    fonts={}
    def text(surface,s,pos,size=36,color=INK,bold=False,center=False):
        key=(size,bold)
        if key not in fonts:fonts[key]=app.font(size,bold)
        rendered=fonts[key].render(s,True,color)
        surface.blit(rendered,rendered.get_rect(center=pos) if center else pos)

    def base(t):
        frame=pg.Surface(SIZE);frame.fill((8,7,6))
        frame.blit(pg.transform.smoothscale(app.canvas,(1584,990)),(168,42))
        return frame

    def overlay(t,frame):
        chapter=next(c for c in CHAPTERS if c[0]<=t<c[1])
        if t<4 or t>=53:
            veil=pg.Surface(SIZE,pg.SRCALPHA);veil.fill((5,4,3,224));frame.blit(veil,(0,0))
            pg.draw.line(frame,RED,(120,194),(270,194),4)
            text(frame,'21',(1370,165),350,GOLD,True)
            text(frame,'RE7 / 21 — NOIR',(120,239),42,GOLD,True)
            if t<4:
                text(frame,'这张牌，',(118,353),94,INK,True)
                text(frame,'你敢要吗？',(118,482),94,INK,True)
                text(frame,'一张底牌。一手王牌。一场生死局。',(125,672),32,GOLD)
            else:
                text(frame,'入座。',(118,349),134,INK,True)
                text(frame,'免费游玩  /  MIT 开源',(126,556),38,GOLD)
                text(frame,'Windows · 解压即玩 · 中文 / English',(127,622),29)
                text(frame,'github.com/404elf/RE7_21_Noir',(128,733),37,GOLD)
                text(frame,'下载地址见简介',(128,795),26)
            text(frame,'404elf · 非官方爱好者项目，与 CAPCOM 无隶属关系',(125,976),21,(137,126,107))
        else:
            pg.draw.rect(frame,(9,8,7),(0,0,1920,67))
            pg.draw.rect(frame,(9,8,7),(0,992,1920,88))
            pg.draw.line(frame,RED,(88,26),(88,49),3)
            text(frame,chapter[2],(105,23),26,GOLD)
            text(frame,'游戏内画面 · 演示牌局',(1555,26),19,(134,124,109))
            text(frame,chapter[3],(960,1033),33,INK,True,True)
            if 48<=t<53:
                text(frame,'异地房间需可用的自建服务器',(960,954),22,GOLD,center=True)
        # Quiet cuts through black; no flashing/glitch sequence.
        edge=min(t-chapter[0],chapter[1]-t)
        if edge<.20:
            fade=pg.Surface(SIZE,pg.SRCALPHA);fade.fill((0,0,0,int(255*(1-edge/.20))));frame.blit(fade,(0,0))
        return frame

    def encoder(path):
        return subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-n',
            '-f','rawvideo','-pix_fmt','rgb24','-s','1920x1080','-r',str(FPS),'-i','pipe:0','-an',
            '-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],
            stdin=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    cuts={1,6,11,13,17,19,24,26,31,38,41,45,50,56}
    clean=edited=None
    if not preview:
        clean=encoder(work/'无字幕画面.mp4');edited=encoder(work/'字幕画面.mp4')
    try:
        for index in range(0,DURATION*FPS,6 if preview else 1):
            t=index/FPS;now[0]=10000+t
            if index==4*FPS:snapshot();cues.append((t,'round_start'))
            for k,(at,pid,cmd) in enumerate(schedule):
                if k not in applied and t>=at:
                    command(pid,cmd,t);applied.add(k)
            app.overlay=None;app.book=False;app.drag=None;app.scene='game'
            if 16.8<=t<18 or 19.8<=t<21:
                f=(t-(16.8 if t<18 else 19.8))/1.2
                app.selected=0
                app.drag=dict(index=0,start=(105,756),origin=(32,706),token=app.selection_token)
                app.mouse=(105+430*f,756-370*f)
            if t>=29:
                app.gs=None;app.selected=None;app.motion.items=[];app.notice=None;app.notice_queue=[]
                app.scene='solo_setup';app.difficulty='nightmare'
            if 36<=t<43:
                app.scene='menu';app.overlay='config'
                if app.editor is None:app.editor=ConfigEditor(ROOT,main.engine.GAME_CONFIG)
                app.editor.tab='presets' if t<39.5 else 'weights'
                app.editor.page=0
            if 43<=t<48:
                app.scene='menu';app.overlay='timers'
            if 48<=t<53:
                app.scene='menu';app.overlay='network';app.network.mode='lan' if t<50.5 else 'rooms'
            app.render()
            frame=base(t)
            if clean:clean.stdin.write(pg.image.tobytes(frame,'RGB'))
            frame=overlay(t,frame)
            if edited:edited.stdin.write(pg.image.tobytes(frame,'RGB'))
            if index%FPS==0 and int(t) in cuts:
                pg.image.save(frame,work/f'检查-{int(t):02}.png');frames[int(t)]=frame.copy()
            if index%(FPS*10)==0:print(f'Rendered {int(t)}/{DURATION}s',flush=True)
    finally:
        for process in (clean,edited):
            if process:
                process.stdin.close()
                if process.wait(timeout=60):raise RuntimeError('Video encoder failed')
        app.connection.close()

    # Cover is new typography over the project's procedurally drawn UI.
    cover=pg.Surface(SIZE);cover.fill((8,7,6))
    cover.blit(frames[19],(0,0))
    veil=pg.Surface(SIZE,pg.SRCALPHA);veil.fill((6,4,3,212));cover.blit(veil,(0,0))
    text(cover,'21',(1410,205),355,GOLD,True)
    text(cover,'RE7 / 21 — NOIR',(124,115),42,GOLD,True)
    text(cover,'这张牌，',(118,265),112,INK,True)
    text(cover,'你敢要吗？',(118,408),112,INK,True)
    pg.draw.rect(cover,(137,37,29),(128,632,566,65))
    text(cover,'极难 AI  /  自定义牌局',(150,646),35,INK,True)
    text(cover,'免费游玩 · MIT 开源',(128,750),34,GOLD)
    text(cover,'非官方爱好者项目',(128,946),24,(154,140,119))
    pg.image.save(cover,output/'封面-1920x1080.png')
    grid=pg.Surface((1920,810));grid.fill((0,0,0))
    for i,k in enumerate(sorted(frames)[:12]):grid.blit(pg.transform.smoothscale(frames[k],(480,270)),((i%4)*480,(i//4)*270))
    pg.image.save(grid,work/'分镜检查.png')

    def timestamp(t):return f'00:{int(t)//60:02}:{int(t)%60:02},000'
    srt='\n\n'.join(f'{i}\n{timestamp(a)} --> {timestamp(b)}\n{caption}' for i,(a,b,title,caption) in enumerate(CHAPTERS,1))+'\n'
    (output/'字幕.srt').write_text(srt,encoding='utf-8')
    (work/'剪辑时间线.json').write_text(json.dumps(dict(fps=FPS,width=1920,height=1080,duration=DURATION,chapters=CHAPTERS,actions=actions,sounds=cues,source='tools/make_promo.py',capture='Scripted rule demonstration using the actual Match engine and App renderer'),ensure_ascii=False,indent=2),encoding='utf-8')
    pg.quit()
    if preview:return

    # Soft, non-percussive original ambience, mixed with the project's own cues.
    rate=44100;x=np.arange(DURATION*rate,dtype=np.float64)/rate
    envelope=np.minimum(1,x/3)*np.minimum(1,(DURATION-x)/3)
    mix=envelope*(.022*np.sin(2*np.pi*55*x)+.009*np.sin(2*np.pi*82.4*x)+.007*np.sin(2*np.pi*110.2*x))
    rng=np.random.default_rng(21)
    noise=rng.normal(0,.0008,len(x));mix+=noise*envelope
    config=json.loads((ROOT/'audio.json').read_text(encoding='utf-8'))
    for t,cue in cues:
        file=ROOT/config['events'][cue]['file']
        with wave.open(str(file),'rb') as w:
            assert w.getframerate()==rate and w.getsampwidth()==2
            samples=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float64)/32768
            if w.getnchannels()==2:samples=samples.reshape(-1,2).mean(axis=1)
        start=int(t*rate);size=min(len(samples),len(mix)-start)
        mix[start:start+size]+=samples[:size]*.34
    mix*=3
    peak=float(np.abs(mix).max());mix*=min(1,.68/max(peak,1e-9))
    stereo=np.column_stack((mix,mix)).astype(np.float64)
    audio=work/'原创音轨.wav'
    with wave.open(str(audio),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(rate)
        w.writeframes((stereo*32767).astype('<i2').tobytes())
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-n',
        '-i',str(work/'字幕画面.mp4'),'-i',str(audio),'-c:v','copy','-c:a','aac','-b:a','192k',
        '-movflags','+faststart','-shortest',str(output/'RE7-21-NOIR-宣传片-1080p.mp4')],check=True)
    print('Completed:',output,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--preview',action='store_true')
    args=parser.parse_args();run(args.output.resolve(),args.preview)
