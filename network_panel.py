"""Network choices and user-owned server preferences; no auto-connect on discovery."""
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid
import pygame as pg
from networking import discover, endpoint

class NetworkPanel:
    def __init__(self,root):
        self.root=Path(root);self.mode='lan';self.editing=None;self.replace=True
        self.values=dict(server='',address='127.0.0.1:6666',code='',password='')
        self.favorites=[];self.found=[];self.scanning=False;self.message=''
        path=self.root/'network.json'
        self.original=path.read_bytes() if path.exists() else None
        if self.original:
            try:
                data=json.loads(self.original)
                for key in ('server','address'):
                    if isinstance(data.get(key),str) and len(data[key])<=253: self.values[key]=data[key]
                self.favorites=[s for s in data.get('favorites',[]) if isinstance(s,str) and len(s)<=253][:12]
            except (ValueError,TypeError,AttributeError): self.message='network.json 格式无效，使用默认值'

    def save(self):
        endpoint(self.values['server'],7443)
        path=self.root/'network.json'
        if (path.read_bytes() if path.exists() else None)!=self.original: raise ValueError('配置已被外部修改，请重启后再保存')
        if self.values['server'] not in self.favorites: self.favorites=(self.favorites+[self.values['server']])[-12:]
        data=json.dumps(dict(server=self.values['server'],address=self.values['address'],favorites=self.favorites),ensure_ascii=False,indent=2).encode('utf-8')
        if self.original:
            folder=self.root/'config-backups';folder.mkdir(exist_ok=True)
            backup=folder/('network-'+uuid.uuid4().hex+'.json');backup.write_bytes(self.original)
            if backup.read_bytes()!=self.original: raise OSError('Backup failed')
        fd,temp=tempfile.mkstemp(dir=self.root,prefix='network-',suffix='.tmp')
        try:
            with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
            os.replace(temp,path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        self.original=data;self.message='已收藏服务器；密码不会保存 / Server saved; password is never stored'

    def action(self,app,kind,value):
        self.message=''
        if kind=='mode': self.mode=value;self.editing=None
        elif kind=='field': self.editing=value;self.replace=True
        elif kind=='scan' and not self.scanning:
            self.scanning=True
            def scan():
                try: self.found=discover()
                except OSError: self.found=[]
                finally: self.scanning=False
            threading.Thread(target=scan,daemon=True).start()
        elif kind=='discovered':
            app.ip=value;app.overlay=None;app.action('join')
        elif kind=='favorite': self.values['server']=value
        elif kind=='save': self.save()
        elif kind=='direct':
            endpoint(self.values['address']);app.ip=self.values['address'];app.overlay=None;app.action('join')
        elif kind=='host': app.overlay=None;app.action('host')
        elif kind in ('create','join'):
            if not self.values['server']: raise ValueError('请填写已部署的房间服务器地址 / Configure a room server first')
            endpoint(self.values['server'],7443)
            if kind=='join' and (len(self.values['code'])!=8 or any(c not in '0123456789abcdefABCDEF' for c in self.values['code'])): raise ValueError('请输入 8 位房间码 / Enter the 8-character room code')
            app.overlay=None;app.action('room_'+kind)

    def key(self,event):
        if self.editing is None:return
        if event.key in (pg.K_RETURN,pg.K_ESCAPE): self.editing=None;return
        key=self.editing
        if event.key==pg.K_a and event.mod&pg.KMOD_CTRL: self.replace=True
        elif event.key==pg.K_BACKSPACE:
            self.values[key]='' if self.replace else self.values[key][:-1];self.replace=False
        elif event.unicode and event.unicode.isprintable():
            size=64 if key=='password' else 8 if key=='code' else 253
            text=('' if self.replace else self.values[key])+event.unicode
            self.values[key]=text[:size];self.replace=False

    def render(self,app):
        app.buttons=[]
        shade=pg.Surface((1440,900),pg.SRCALPHA);shade.fill((5,5,5,235));app.canvas.blit(shade,(0,0))
        app.panel((100,55,1240,790))
        app.text(app.t('联机方式','MULTIPLAYER'),136,84,32,(229,220,201),True)
        app.button((1140,80,160,45),app.t('返回','Back'),'close_overlay')
        for i,(key,zh,en) in enumerate((('lan','局域网','LAN'),('rooms','房间码','Room code'),('direct','地址直连','Direct'),('server','自建服务器','Your server'))):
            app.button((136+i*289,147,273,48),app.t(zh,en),('net','mode',key),primary=self.mode==key)
        def field(key,label,y):
            app.text(label,146,y,18,(205,172,120))
            value=self.values[key]
            if key=='password':value='*'*len(value)
            app.button((146,y+30,1115,48),value+('|' if self.editing==key else '') or app.t('点击输入','Click to type'),('net','field',key))
        if self.mode=='lan':
            app.text(app.t('同一 Wi-Fi / 热点；发现房间后点击加入。','Same Wi-Fi / hotspot. Select a discovered room to join.'),146,224,20,(229,220,201))
            app.button((146,279,530,56),app.t('创建局域网房间','Host LAN room'),('net','host',None),True)
            app.button((698,279,563,56),app.t('搜索中…','Searching…') if self.scanning else app.t('搜索房间','Find rooms'),('net','scan',None),enabled=not self.scanning)
            for i,address in enumerate(self.found[:6]):app.button((146,365+i*49,1115,42),address,('net','discovered',address))
            if not self.found:app.text(app.t('未发现房间时，可用「地址直连」。局域网隔离可能阻止自动发现。','No rooms? Use Direct. Network isolation can block discovery.'),146,690,18,(165,152,133),width=1115)
        elif self.mode in ('rooms','server'):
            field('server',app.t('房间服务器，例如 tls://play.example.com:7443','Room server, e.g. tls://play.example.com:7443'),216)
            if self.mode=='rooms':
                field('code',app.t('加入时填写房间码','Room code for joining'),310)
                field('password',app.t('房间密码（可选；创建与加入须一致）','Optional room password'),405)
                app.button((146,520,530,54),app.t('创建房间','Create room'),('net','create',None),True)
                app.button((698,520,563,54),app.t('加入房间','Join room'),('net','join',None))
                app.wrap(app.t('双方确认规则并准备后开始。掉线保留座位 30 秒，暂停计时并自动重连。\n此版本没有预设公共服务器；可填写朋友或社区部署的服务。','Both players review rules and ready up. Reconnect within 30 seconds with clocks paused.\nNo public service is preconfigured; use a friend or community server.'),pg.Rect(146,600,1115,110),19)
            else:
                app.button((146,315,1115,50),app.t('收藏此服务器','Save server'),('net','save',None))
                for i,address in enumerate(self.favorites[-4:]):app.button((146,385+i*47,1115,40),address,('net','favorite',address))
                app.wrap(app.t('部署说明：NETWORKING.md。下载包内附房间服务端启动脚本及配置示例。\n公网监听必须配置 TLS 证书；无需给游戏管理员权限。\n收藏后切换到「房间码」创建或加入。','See NETWORKING.md for the bundled room service and configuration.\nPublic listeners require TLS. The game does not need administrator rights.\nUse Room code after selecting a server.'),pg.Rect(146,590,1115,125),19)
        else:
            field('address',app.t('房主地址：IP / 域名，可带端口','Host: IP / domain, optional port'),230)
            app.button((146,355,1115,56),app.t('连接此地址','Connect'),('net','direct',None),True)
            app.wrap(app.t('支持 IPv4、[IPv6]:端口，以及 tls://域名:端口。\n普通直连不加密，建议用于可信局域网；异地请优先选择带 TLS 的房间服务。\n只建立游戏连接，不自动改路由器、防火墙或安装组网工具。','Supports IPv4, [IPv6]:port and tls://host:port.\nPlain direct connections are intended for trusted LANs; prefer TLS rooms over the Internet.\nNo router/firewall changes or extra networking tools.'),pg.Rect(146,465,1115,175),21)
        app.text(self.message,146,766,18,(232,104,83),width=1115)
