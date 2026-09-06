"""In-game configuration editor with validated values and verified atomic backups."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
import pygame as pg
from cards import CARDS, info
from presentation_rules import DEFAULT_WEIGHTS, NUMBER_NAMES
from match import DEFAULT_TIMER

# key, Chinese, English, minimum, maximum, increment, integer
GAME = [
 ('max_hp','最大生命值','Maximum health',1,999,1,True),
 ('max_trumps_hand_size','手持王牌上限','Trump hand limit',1,100,1,True),
 ('max_active_trumps_on_table','场上王牌上限','Table trump limit',1,100,1,True),
 ('target_score','默认目标点数','Default target',1,999,1,True),
 ('initial_trumps_count','开局王牌数量（另加 1 张）','Starting trumps (+1)',0,100,1,True),
 ('round_reward_trumps_count','每回合补充王牌','Round trump reward',0,100,1,True),
 ('hit_draw_trump_probability','抽牌时获得王牌概率（0–1）','Trump chance on hit (0–1)',0,1,.05,False),
 ('number_card_draw_probability','普通数字王牌概率（0–1）','Number trump chance (0–1)',0,1,.05,False),
 ('deck_range_start','牌堆最小点数','Lowest number card',1,100,1,True),
 ('deck_range_end','牌堆最大点数','Highest number card',1,100,1,True),
]
TIMER = [
 ('enabled','启用计时','Enable clock','bool'),
 ('mode','计时方式','Clock mode',('turn','round','fischer')),
 ('turn_seconds','每次行动秒数','Seconds per turn',1,3600,5,False),
 ('round_seconds','每人每局秒数','Seconds per player / round',1,86400,10,False),
 ('initial_minutes','整场初始分钟','Match minutes',1,1440,1,False),
 ('increment_seconds','交接行动增加秒数','Increment seconds',0,300,1,False),
 ('settlement_seconds','亮牌等待秒数（0 为跳过）','Reveal wait (0 skips)',0,60,.5,False),
]
AUDIO = [('enabled','启用音效','Enable sound','bool')]+[(k,zh,en,0,1,.05,False) for k,zh,en in [
 ('master_volume','总音量','Master volume'),('ui_volume','按钮音量','UI volume'),
 ('game_volume','对局音量','Game volume'),('result_volume','结算音量','Result volume')]]


class ConfigEditor:
    def __init__(self, root, game_defaults):
        self.root = Path(root)
        self.docs, self.original = {}, {}
        defaults = {'config.json': game_defaults, 'timer.json': DEFAULT_TIMER,
                    'audio.json': dict(enabled=True, master_volume=.65,ui_volume=.45,game_volume=.75,result_volume=.8)}
        for file, fallback in defaults.items():
            path = self.root/file
            raw = path.read_bytes() if path.exists() else None
            data = json.loads(raw.decode('utf-8-sig')) if raw is not None else copy.deepcopy(fallback)
            if not isinstance(data, dict):
                raise ValueError(file+' must contain an object')
            for key, value in fallback.items():
                if key not in data: data[key] = copy.deepcopy(value)
                elif isinstance(value,dict) and isinstance(data[key],dict):
                    for nested, default in value.items(): data[key].setdefault(nested,copy.deepcopy(default))
            self.original[file] = raw
            self.docs[file] = data
        self.docs['config.json'].setdefault('game_settings', copy.deepcopy(game_defaults['game_settings']))
        self.docs['config.json'].setdefault('trump_weights', {})
        weights=self.docs['config.json']['trump_weights']
        self.weight_rows=[('Return+' if name == 'ADD2+' else name, info(name)[0]+' / '+name, name,0,10000,1,False) for name in CARDS if name not in NUMBER_NAMES]
        self.weight_rows.sort(key=lambda row: weights.get(row[0],DEFAULT_WEIGHTS.get(row[0],0)) <= 0)
        self.tab, self.page, self.editing = 'game', 0, None
        self.buffer, self.message, self.replace = '', '', True

    def file(self):
        return 'config.json' if self.tab in ('game','weights') else self.tab+'.json'

    def rows(self):
        if self.tab == 'game': return GAME
        if self.tab == 'timer': return TIMER
        if self.tab == 'audio': return AUDIO
        return self.weight_rows

    def values(self):
        doc = self.docs[self.file()]
        return doc['game_settings' if self.tab == 'game' else 'trump_weights'] if self.tab in ('game','weights') else doc

    def get(self, row):
        default = DEFAULT_WEIGHTS.get(row[0],0) if self.tab == 'weights' else DEFAULT_TIMER.get(row[0],1)
        return self.values().get(row[0],default)

    def commit_field(self):
        if self.editing is None: return
        row = self.rows()[self.editing]
        value = float(self.buffer)
        if not math.isfinite(value) or not row[3] <= value <= row[4] or (row[6] and value != int(value)):
            raise ValueError(f'{row[1]}: {row[3]}–{row[4]}'+('，需要整数' if row[6] else ''))
        self.values()[row[0]] = int(value) if row[6] else value
        self.editing = None

    def validate(self):
        groups = [('game', GAME), ('weights', self.rows() if self.tab == 'weights' else [('Return+' if name == 'ADD2+' else name, info(name)[0],name,0,10000,1,False) for name in CARDS if name not in NUMBER_NAMES])] if self.file() == 'config.json' else [(self.tab,self.rows())]
        for group, rows in groups:
            for row in rows:
                values = self.docs['config.json']['game_settings' if group == 'game' else 'trump_weights'] if group in ('game','weights') else self.docs[self.file()]
                v = values.get(row[0], DEFAULT_WEIGHTS.get(row[0],0))
                self.validate_value(row,v)
        if self.file() == 'config.json':
            conf=self.docs['config.json']['game_settings']
            if conf['deck_range_end']-conf['deck_range_start'] < 3:
                raise ValueError('牌堆范围至少需要 4 个不同点数 / At least 4 number cards required')

    def validate_value(self,row,v):
        if row[3] == 'bool':
            if not isinstance(v,bool): raise ValueError(row[1]+'需要开关值')
        elif isinstance(row[3],tuple):
            if v not in row[3]: raise ValueError(row[1]+'无效')
        elif isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not row[3] <= v <= row[4] or (row[6] and int(v)!=v):
            raise ValueError(row[1]+'数值无效')

    def save(self):
        self.commit_field()
        self.validate()
        file=self.file(); path=self.root/file
        current=path.read_bytes() if path.exists() else None
        if current != self.original[file]:
            raise ValueError('文件已被外部修改，请重开游戏后读取，避免覆盖 / File changed externally')
        data=(json.dumps(self.docs[file],ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        if current is not None:
            backups=self.root/'config-backups'; backups.mkdir(exist_ok=True)
            backup=backups/(file+'.'+uuid.uuid4().hex+'.bak')
            backup.write_bytes(current)
            if backup.read_bytes()!=current: raise OSError('Backup verification failed')
        fd,tmp=tempfile.mkstemp(prefix=file+'.',suffix='.tmp',dir=self.root)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        self.original[file]=data
        self.message='已保存并备份；音量立即生效 / Saved; audio applied now' if file=='audio.json' else '已保存并备份原配置；下一场生效 / Saved for next match'
        return file

    def action(self, action):
        kind,value=action[1],action[2]
        if kind=='save': return self.save()
        self.commit_field()
        self.message=''
        if kind=='tab': self.tab,self.page=value,0
        elif kind=='page': self.page=max(0,self.page+value)
        elif kind=='field':
            row=self.rows()[value]
            if row[3]=='bool': self.values()[row[0]]=not self.get(row)
            elif isinstance(row[3],tuple):
                choices=row[3]; self.values()[row[0]]=choices[(choices.index(self.get(row))+1)%len(choices)]
            else:
                self.editing=value; self.buffer=str(self.get(row)); self.replace=True
        elif kind in ('plus','minus','zero'):
            row=self.rows()[value]
            new=0 if kind=='zero' else min(row[4],max(row[3],self.get(row)+row[5]*(1 if kind=='plus' else -1)))
            self.values()[row[0]]=int(new) if row[6] else round(new,6)

    def key(self,event):
        if self.editing is None: return
        if event.key==pg.K_ESCAPE: self.editing=None
        elif event.key in (pg.K_RETURN,pg.K_KP_ENTER,pg.K_TAB): self.commit_field()
        elif event.key==pg.K_a and event.mod & pg.KMOD_CTRL: self.replace=True
        elif event.key==pg.K_BACKSPACE:
            self.buffer='' if self.replace else self.buffer[:-1]; self.replace=False
        elif event.unicode and event.unicode in '0123456789.-':
            self.buffer=('' if self.replace else self.buffer)+event.unicode; self.replace=False

    def render(self, app):
        app.buttons=[]
        shade=pg.Surface((1440,900),pg.SRCALPHA);shade.fill((5,5,5,220));app.canvas.blit(shade,(0,0))
        app.panel((100,55,1240,790))
        app.text(app.t('游戏配置','GAME SETTINGS'),136,84,32,(229,220,201),True)
        app.button((1110,80,190,45),app.t('返回（暂不保存）','Close without saving'),'close_overlay')
        for i,(key,zh,en) in enumerate([('game','对局规则','Rules'),('weights','王牌权重','Trump weights'),('timer','计时与亮牌','Timing'),('audio','音效音量','Audio')]):
            app.button((136+i*289,147,273,48),app.t(zh,en),('cfg','tab',key),primary=self.tab==key)
        hint=app.t('点击数字直接输入，也可用 ± 微调。保存后生效（规则和权重一并保存）。','Click a value to type, or use ±. Save the current category to apply.')
        if self.tab=='weights': hint=app.t('权重越大越常见；0 为禁用。普通数字牌概率在“对局规则”中设置。','Higher weight means more frequent; 0 disables. Number-card chance is in Rules.')
        app.text(hint,136,212,17,(165,152,133),width=1160)
        rows=self.rows(); pages=max(1,(len(rows)+5)//6);self.page=min(self.page,pages-1)
        for i,row in enumerate(rows[self.page*6:self.page*6+6]):
            idx=self.page*6+i;y=258+i*68
            app.text(app.t(row[1],row[2]),146,y+12,20,(229,220,201),width=600)
            value=self.get(row)
            if self.editing==idx: label=self.buffer+'|'
            elif row[3]=='bool': label=app.t('开','On') if value else app.t('关','Off')
            elif isinstance(row[3],tuple): label={'turn':app.t('每次行动','Per turn'),'round':app.t('每人每局','Per round'),'fischer':app.t('整场 + 加秒','Match + increment')}.get(value,str(value))
            else: label=f'{value:g}'
            app.button((827,y,245,44),label,('cfg','field',idx),primary=self.editing==idx)
            if len(row)>4:
                app.button((759,y,50,44),'−',('cfg','minus',idx))
                app.button((1090,y,50,44),'+',('cfg','plus',idx))
                if self.tab=='weights': app.button((1158,y,119,44),app.t('禁用','Disable'),('cfg','zero',idx))
        app.text(self.message,136,677,16,(232,104,83),width=1160)
        app.text(app.t('规则与计时用于下一场；音量立即生效。未保存草稿关闭面板后仍保留。','Rules/timing apply next match; audio applies now. Unsaved drafts remain while this app is open.'),136,708,15,(165,152,133),width=1160)
        app.button((136,756,100,48),'‹',('cfg','page',-1),enabled=self.page>0)
        app.text(f'{self.page+1} / {pages}',263,767,18,(165,152,133))
        app.button((354,756,100,48),'›',('cfg','page',1),enabled=self.page+1<pages)
        app.button((892,750,386,58),app.t('保存设置','Save settings'),('cfg','save',None),True)
