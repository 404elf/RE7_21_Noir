# Noir 浏览器版

本版本基于 `404elf/RE7_21_Noir` v1.5.3（`7b921cb`），不是早期 `RE7_21` 仓库。两个浏览器通过房间码或邀请链接入座，双方准备后，可使用全部原有王牌并打完整场。桌面入口仍是 `python main.py`。

## 本地运行

需要 Python 3.12+。Web 服务不需要 Pygame、Node.js、组网软件或数据库。

```powershell
python -m venv .venv-web
.\.venv-web\Scripts\python -m pip install -r requirements-web.txt
.\.venv-web\Scripts\python -m uvicorn web.app:app --host 127.0.0.1 --port 8000 --workers 1 --ws-max-size 2048 --ws-max-queue 8
```

macOS / Linux 使用 `.venv-web/bin/python` 替换上面的 Python 路径。打开 <http://127.0.0.1:8000>，输入昵称后建房；另一浏览器输入房间码，或打开邀请链接后点击加入。邀请链接不包含席位凭证。双方点击「我准备好了」开始。

房主只是第一个座位：**游戏规则来自 Web 服务所在机器的配置**，不会读取玩家电脑上的配置。在同一页面刷新可重连；使用两个浏览器、两个独立隐私窗口或两个独立标签页测试两个座位。同一标签页的席位凭证保存在 `sessionStorage`；复制含有当前会话的标签页可能复制凭证，此时会拒绝第二个连接。请从首页新开标签页。

仅在需要局域网测试时，把监听地址改为 `0.0.0.0`，双方使用服务器的局域网 IP 访问；邀请链接使用当前地址，因此不要把 `localhost` 链接发送给另一台电脑。

## 配置

- 默认复用根目录 `config.json` 的生命、牌堆范围、初始与奖励王牌、牌池权重及手牌/桌面上限。原有 JSON 注释字段和预设均可读取。
- 默认复用 `timer.json`：支持不计时、每次行动、每人每局额度、Fischer 整场加秒制、首次准备计时和结算等待。`null` 的不限时语义与桌面版一致。
- `timer.json` 的 `settlement_seconds` 优先控制亮牌时间；计时文件缺失时采用 `config.json` 的 `result_screen_duration`。
- `NOIR_CONFIG` / `NOIR_TIMER` 可指向服务端配置路径。配置在进程启动时读取，修改后重启；重启会清空内存房间。客户端不能上传配置或指定路径。
- `NOIR_ORIGIN` 可固定浏览器来源，例如将来使用 `https://game.404elf.dev`。未设置时只接受与当前请求相同来源的浏览器连接。不要添加结尾路径。

首次尝试建议保留仓库默认规则。操作间隔沿用 Noir 的 0.5 秒限制。两人连续停牌后结算；爆牌不会立即判负，仍可用王牌改变手牌/目标。双方都爆牌时点数较小者获胜。王牌的消耗条件、封锁和一次性效果清理都由原引擎处理。

## Docker 本地运行与未来部署

```sh
docker compose -p noir-web up --build -d
docker compose -p noir-web ps
docker compose -p noir-web logs --tail=50 game
docker compose -p noir-web down
```

访问 <http://127.0.0.1:8000>。默认只发布到本机回环地址；`config.json` 与 `timer.json` 只读挂载。容器以非 root 身份运行，根文件系统只读，健康检查入口为 `/healthz`。`NOIR_BIND` 与 `NOIR_PORT` 可调整本地监听地址和端口。

未来放到 `game.404elf.dev` 时，先另外配置 HTTPS 反向代理，把普通 HTTP 和 `/ws/` 的 WebSocket Upgrade 一同转发到容器的 8000 端口，并设置 `NOIR_ORIGIN=https://game.404elf.dev`。代理的 WebSocket 超时应至少为 75 秒。当前容器不信任任意转发头，反向代理后的 IP 限流会按代理 IP 共享；公开上线前应在可信代理层补充按真实客户端 IP 的限流。

**只运行一个 worker、一个副本。** 房间与计时在内存中；多 worker/副本会导致请求落到不同房间字典。本次不包含任何 Azure、Cloudflare、现有服务器、DNS 或生产环境变更。

## 架构与复用范围

```text
浏览器 HTML / CSS / ES modules
  ├─ POST /api/rooms 或 /api/rooms/{code}/join → 房间码 + 私有席位凭证
  ├─ GET /api/catalog → 原 cards.py 图鉴
  └─ WebSocket /ws/{code}
       ├─ 首帧认证（凭证不放 URL）
       ├─ 带 revision 的操作 → web/rooms.py → match.Match.command
       └─ 各玩家独立状态 ← 明确字段白名单 ← re7_21.GameState
```

- `re7_21.py`：直接复用原有数字牌、全部 48 张王牌、扣血和回合规则；唯一加载变化是允许服务端未安装 Pygame。
- `match.py`：直接复用原有准备/行动计时、日志、求和、投降、结算、再战，未另写 JavaScript 规则引擎。
- `rules.py`：从桌面配置编辑器和 TCP 房间服务器抽出已有规则范围、校验和临时规则上下文，供两种服务共用。所有引擎调用在同一事件循环同步执行；规则上下文中禁止 `await`。
- `web/`：FastAPI 提供静态文件和 WebSocket。浏览器只发意图；服务端校验席位、阶段、行动方、操作索引及状态版本。周期推送显示计时，改变状态的操作增加 revision，防止双击/旧索引重复出牌。
- 每个浏览器仅收到自己的王牌手牌、对方王牌数量与明牌。暗牌在结算/终局才公开；不下发牌堆顺序、原始 `GameState`、`opening_cards` 或另一玩家凭证。历史结算里的已公开手牌仍可查看。

房间码为 8 位无歧义随机字符，席位凭证为独立随机字符串。最多 64 个房间 / 192 个连接；创建、加入与建立连接合计每 IP 每分钟最多 40 次；已连接每秒最多 20 帧，每帧最多 2048 字节。浏览器每 10 秒心跳。空闲等候 5 分钟、对局无状态变化 30 分钟、总寿命 2 小时后清理房间。

断线后冻结行动时钟与结算等待；60 秒内使用原标签页凭证重连可恢复。超时后仍在线的一方获胜，双方都离线则记为平局。已经结束的房间不再因断线改写结果，双方回到席位后仍可再战。

## 测试

```powershell
.\.venv-web\Scripts\python -m pip install -r requirements-web-dev.txt
.\.venv-web\Scripts\python -m pytest tests/test_web.py -q
.\.venv-web\Scripts\python -m playwright install chromium
.\.venv-web\Scripts\python tests/web_e2e.py
```

`web_e2e.py` 自行启动临时服务，使用临时的低生命/短结算配置，不改仓库配置。两个独立 Chromium 实例通过真实界面完成建房、邀请加入、准备、用牌/弃牌、刷新重连、按生命扣除决出胜负、再战和手机图鉴测试。截图保存在 `.artifacts/web-*.png`。

还可以验证已经启动的 Docker 服务（直接使用服务自己的规则）：

```powershell
.\.venv-web\Scripts\python tests/web_e2e.py --url http://127.0.0.1:8000
```

运行原桌面回归和 Web 测试合集时，另安装 `requirements.txt` 的 Pygame；无桌面环境请设 `SDL_VIDEODRIVER=dummy`、`SDL_AUDIODRIVER=dummy` 后执行 `python -m pytest tests -q`。CI 执行回归、双浏览器测试、Docker 构建和容器健康检查，不执行生产部署。

测试夹具为原有三个 TCP 集成测试分配独立临时端口，避免 Linux 的 TIME_WAIT 造成跨测试端口冲突；断包、计时和端口被占用的原始断言保持不变。实际桌面默认端口未修改。

## 当前边界

- 已覆盖双人完整对局及全部王牌。单机 AI/难度、桌面音效与动画、配置编辑器、桌面历史文件、自动更新器尚未迁移到浏览器。
- 房间和日志只在内存中，重启即丢失；没有账号、排位、旁观、持久回放、跨进程共享或水平扩展。
- 没有公开房间列表、密码或账号邀请；知道房间码的人可以占用空余席位。泄露席位凭证可冒用身份，应使用 HTTPS。
- 仅验证 Chromium 的桌面和手机尺寸布局；尚未做 Safari/Firefox、实际移动设备、互联网弱网和大规模并发验证。
- 王牌仍遵循原版现有行为，包括条件不足时可能被消耗。这个迁移不重平衡玩法。
- 正式上线仍需单独完成域名/证书/反向代理、监控与公开流量保护；本次只提供可运行、可测试的交付物。
