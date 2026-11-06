# 163CG-API

网易云游戏 `cg.163.com` 移动端（H5 / 触摸版）接口逆向与可运行 SDK。

把「首页推荐 → 选游戏 → 申请机位 → 连网关 → WebRTC 收画面 → 触摸操控 → 悬浮球调画质」
这整条链路拆开、写清楚、并且能跑起来。

**272 条接口** · **135 条实测用例（111 通过）** · **23 份脱敏响应样本** · **6 张真机截图**

---

## 免责声明

本项目仅用于**学习与安全研究**：了解一个成熟的 Web 云游戏产品是怎么组织的 ——
自定义传输加密、WebRTC 收流、WebSocket 信令、输入指令编码、排队状态机。

- 所有接口、协议、密钥常量均来自**公开可下载的前端静态资源**（浏览器里 F12 就能看到的 JS）。
- 代码按 GPL-3.0 发布，**不提供**任何绕过付费、绕过实名、批量注册、刷时长之类的能力。
- 请勿用于商业用途或对服务方造成负担。要玩请正常登录、正常付费。
- 如果你是这个服务的开发者并认为本项目不妥，开 issue，我会删。

---

## 它能做什么

```python
import sys; sys.path.insert(0, 'sdk')
from cgapi import make_api, PlaySession, cookie_get, get_offset2

api = make_api()                          # 可选 proxy='http://127.0.0.1:10808'

tabs   = api.home_tabs()                  # 首页 6 个 tab
feed   = api.home(tab_id='66051b810d5fa1f0204c294f')   # 推荐流，8 个模块
detail = api.game('wow_cn_high')          # 游戏详情，142 个字段
rates  = api.speed_url('mobile')          # 各画质档位的建议码率
```

登录之后还能：

```python
api.token  = '<NCG-token>'
api.offset = get_offset2(cookie_get('<NCG-eid>')) % 256
api.uid    = '<JWT 里的 identity>'

me = api.me()                             # 我的（96 个字段）
cs = api.common_setting('mrfzzmd')        # 悬浮球位置 / 画质设置
tk = api.post_ticket('mrfzzmd', ['ghnfs'])   # 申请机位

ps = PlaySession(api, tk['gateway_url']).connect()
ps.auth('mrfzzmd', quality='high', codecs=['h264'])
ps.set_quality('middle')                  # 悬浮球里的画质切换
ps.tap(0.5, 0.5)                          # 触摸，坐标自动换算
```

`PlaySession` 还封了 `touch_down / touch_move / touch_up / swipe / send_text /
send_key / gps / shake / paste_to_cloud / set_input_mode / pc_mousedown / pc_mouseup /
pc_mousemove / pc_keydown / pc_keyup / pump()`。

只用标准库，HTTP 走 `curl` 子进程，WebSocket 是自己实现的 RFC6455 客户端
（因为要支持 HTTP CONNECT 代理 + 自定义加密帧）。

---

## 按需求查接口

### 1. 首页游戏推荐

```
GET api/v1/main_recommendations?main_recommendation_type=mobile_home[&tab_id=<id>]
```

返回**模块数组**（实测 8 个），每模块 `{create_time, delay, games[], include_channels[], …}`，
卡片是 `{card_type:"game", btn_text:"秒玩", display_game:{game_code, carousels[], free, daily_free_time, …}}`。

首页首屏一共打 17 个接口，全部匿名可读：

| 顺序 | 接口 | 作用 |
|---|---|---|
| 1 | `api/v1/versions/@current?package_name=H5` | 客户端版本 / 强更 |
| 2 | `api/v1/versions/infos?need_latest=1&platforms=0..9` | 全端版本 |
| 3 | `api/v1/client-settings/@current` | 免时长、排队提醒等 |
| 4 | `POST api/v1/group-customize-settings` ×4 | 灰度配置分组 |
| 5 | `api/v1/search_keyword_hints` | 搜索联想 |
| 6 | `api/v1/home_page_tabs` | 顶部 tab |
| 7 | `api/v1/notices/list` | 公告 |
| 8 | `api/v1/resource_icons?type=main_page` | 图标资源 |
| 9 | `api/v1/text_res_position_items` | 文字资源位 |
| 10 | `api/v1/banners?banner_type=mobile_home` | 轮播 |
| 11 | `api/v1/pendant_list?pendant_type=mobile_home` | 悬浮挂件 |
| 12 | `api/v1/popups` | 弹窗 |
| 13 | `api/v1/main_recommendations?…mobile_home` | 推荐流主体 |
| 14 | 同上 + `&tab_id=` | 切 tab 重拉 |
| 15-16 | `api/v1/speed_url?game_type=mobile` / `pc` | 测速 + 默认画质 |
| 17 | `api/v1/hot-searches` | 热搜 |

`home_page_tabs` 的 6 个 tab 实测值：

| rank | tab_type | title | tab_id |
|---|---|---|---|
| 1 | `recommend` | 推荐 | `66051b810d5fa1f0204c294f` |
| 2 | `mobile` | 手机游戏 | `66051b810d5fa1f0204c2950` |
| 3 | `pc` | PC游戏 | `66051b810d5fa1f0204c2951` |
| 3 | `tag_game_list` | 限免专区 | `689dba31e52a16c8c74352b1` |
| 5 | `more_game` | 云电脑/云手机 | `66051b830d5fa1f0204c2953` |
| 6 | `qq_game` | 免时长 | `66051b810d5fa1f0204c2952` |

### 2. 游戏选择与启动

```
GET  api/v1/games?page=0&per_page=10&game_type=mobile&src=4   # 列表（分页！）
GET  api/v1/games/<game_code>                                 # 详情，142 字段
POST api/v2/tickets                                           # 申请机位 → gateway_url
```

**分页参数别漏。** 不带 `page`/`per_page` 会一次吐 1,826 条 / 6 MB；带上就是 59 KB / 10 条。

游戏库实测：mobile 996 条、pc 829 条、cloud-mobile 1 条；横屏 1,616、竖屏 210；免费 557 条。

申请机位的最小可用体：

```python
api.post('api/v2/tickets', {
    'regions': ['ghnfs'],                     # 必填具体 region code，空数组会 404/1288
    'game_code': 'mrfzzmd',
    'codecs': ['h264'],
    'width': 1170, 'height': 2532,
    'extra': {'dpi': 3, 'resolution': '1170x2532', 'source_type': 'web',
              'network_test_info_list': [{'region': 'ghnfs', 'latency': 600, 'bandwidth': 60}]},
    'apk_decoder': {'model': '', 'decoder': []}})
```

响应里 `gateway_url` 就是游玩网关，`expires` 是机位保留秒数。
没 `expires` 说明进了排队，得连 `wss://n.cg.163.com/push/ws` 等。

11 个 region：`ghbtj ghdjn ghdnb ghnfs ghzcs gxbxa gxncq zmdhdcz zmdhdnb zmdhnfs zmdxbxa`。

### 3. 游玩时的交互与画面

```
wss://<gateway_url>   首包 auth（明文）→ offer(SDP) → answer → WebRTC 收流
                     之后所有操控走 {"op":"input","data":{"cmd":"<code> <args>"}}
```

实测报文时序：

```
→ {"op":"auth","data":{"user_id":"…","token":"…","game_code":"mrfzzmd",
                       "w":1170,"h":2532,"quality":"high",
                       "codecs":["h264"],"platform":1}}
← {"op":"offer","data":{"sdp":"v=0…"}}           # 2593 字节
← {"op":"result","data":{"container":"…","encoder":"high,None","host":"…"}}
← {"op":"input","data":{"cmd":"8 0"}}                       # 横屏
← {"op":"input","data":{"cmd":"2"}}                         # 隐藏键盘
← {"op":"input","data":{"cmd":"23 com.hypergryph.endfield/…"}}  # 前台应用
← {"op":"input","data":{"cmd":"9 27643,1,4f8e,eyJtIjoi…"}}   # DataChannel 就绪握手
```

输入指令（坐标统一 `Math.ceil(65535 * x / videoWidth)`）：

| cmd | 含义 |
|---|---|
| `1 / 2 / 3 <x> <y> <id>` | 触摸 按下 / 移动 / 抬起（多指 0..19） |
| `8 <x> <y> <id>` | 点击 tap |
| `4` | 取消全部触点 |
| `5 <text>` / `6 <keycode>` | 文本输入 / 单键 |
| `0 <ts>` | 心跳 |
| `25 tochscreen\|keymouse` | 输入模式 |
| `26 <γ> <β> <α> 0 <ts*1e6>` | 摇一摇；`28` 触发 |
| `29 <text>` | 贴文本到云端 |
| `100 / 101 <btn> <posType> <x> <y> <state>` | PC 鼠标按下 / 抬起 |
| `102 <posType> <x> <y> <state>` | PC 鼠标移动 |
| `103 <posType> <x> <y> <delta> <state>` | 滚轮 |
| `104 / 105 <keyCode> <keyName> <state>` | 键盘按下 / 抬起 |
| `110 / 111 / 112 / 113 / 114` | 手柄 连接 / 断开 / 按钮 / 摇杆 / 释放 |

### 4. 悬浮球，含画质调整

悬浮球是个可拖拽的 50×50 圆点，松手吸附左右边缘，Y 轴钳制在屏幕高度 15%~75%。
位置存 `POST api/v2/users/@me/games/<code>/common_setting`，body `{ball_x: 0|100, ball_y: 0..100}`。

菜单实测 DOM（`section.mobilemenu`，左栏图标条 + 右栏面板）：

```
section.mobilemenu
├─ p.mobilemenu-stats            华南三区（当前选区）
├─ ul.mobilemenu-list            一级（横向可滚）
│   ├─ 常用操作        icon_options      ← 默认展开右栏
│   ├─ 官方充值        （动态图标）
│   ├─ 云玩抽奖        （动态图标）
│   ├─ 福利中心        icon_gamewelfare + 「1个活动待参与」
│   └─ 退出游戏        icon_exit
│   └─ 免费时长 21分钟 · ID:138****0000
└─ div.mobilemenu-panel          右栏
    ├─ 显示网络状态        switch on
    ├─ 后台挂机（红点）    switch
    ├─ 麦克风              switch
    ├─ 操作时隐藏悬浮球    switch
    ├─ 画质  li[name] = low 标清 / middle 高清 / high 超清(on) / bluray 蓝光
    └─ 工具 → 小窗模式 icon_menu_pip / 分享给朋友 icon-share
```

画质走 WebSocket，四个档位：

```json
{"op":"game_set_quality","data":{"quality":"low"}}      // 标清
{"op":"game_set_quality","data":{"quality":"middle"}}   // 高清
{"op":"game_set_quality","data":{"quality":"high"}}     // 超清
{"op":"game_set_quality","data":{"quality":"bluray"}}   // 蓝光
```

服务端回 `{"op":"result","data":{}}`。

**蓝光有门槛**：`user.bluray === false` 时前端在发帧之前就拦截，弹「成为会员即可享受蓝光极致画质」。
没有权限的账号切蓝光**一个字节都不会发出去**，别以为是接口不支持。

**帧率 / 分辨率不是热切换**：`setFps` / `setResolution` 会 `resetGateWaty()` 重连整个 WS
重新 auth。移动端 H5 的 `resolution_configs` 里没有 `web-mobile` 条目，
所以移动端菜单只有「画质」一栏，端游才会多出「画质帧率」。

默认画质来自 `api/v1/speed_url?game_type=mobile` 的 `default_quality`（实测 `high`）。

### 5. 首页 - 我的

`#/center` 一次打 **22 个接口**，核心是：

| 接口 | 内容 |
|---|---|
| `GET api/v2/users/@me` | **96 个字段**：云币、免费时长、VIP、实名、成长值、悬浮球位置… |
| `GET api/v2/users/@me/level_info` | 等级 + 10 条奖励 |
| `GET api/v2/users/@me/game-playing-histories` | 游玩历史（实测 12 条） |
| `GET api/v2/sign-info-v2` | 签到，7 档奖励 |
| `GET api/v2/users/@me/cloud-pc` | 云电脑额度 |
| `GET api/v2/push_msgs` | 消息未读数 |
| `GET api/v2/daily_vip/get_daily_vip_reward_status` | 每日会员奖励 |

外加 `orders` / `recharges` / `coins-changes` / `my_coupons` / `promotion_coupons` /
`parent-ctrl/info` / `bottom_config_items` / `pendants` / `banners` 等。

页面结构（见 `docs/img/mine-center.png`）：
用户头（头像 + 昵称 + VIP + 一起玩特权 + 关注/粉丝/广播/成长值）→ 时长卡 →
会员卡（无限时长 / 快速排队 / 手游挂机 / 会员云手机）→ 游玩历史横滑 →
功能入口（订单详情 / 云上商城 / 帮助与反馈 / 邀请有礼 / 设置 / 兑换）→ 退出。

### 6. 其余接口

从 3 个 bundle 自动抽出 **272 条**，分类：

| 分类 | 数量 | | 分类 | 数量 |
|---|---|---|---|---|
| 我的/账户 | 96 | | 登录/鉴权 | 17 |
| 社区/直播 | 52 | | 启动/排队 | 13 |
| 其他 | 35 | | 云电脑/云手机 | 9 |
| 游戏/详情 | 22 | | 悬浮球/键位/画质 | 4 |
| 首页/推荐 | 22 | | SEO | 2 |

全量清单见 [`docs/接口清单.csv`](docs/接口清单.csv)，
带分类 / Method / Path / 客户端函数名 / 参数 / 源码偏移，可以直接 grep 定位。

---

## 核心实现细节

### 网关与版本策略

所有 XHR 走 `https://n.cg.163.com/`（备用 `nr.cg.163.com`）。

- `api/v1/*` —— 明文，**匿名可读**（首页、游戏、社区、SEO 全在这层）
- `api/v2/*` —— 必须 `Authorization: Bearer <token>`，请求体和响应体都加密

移动端标识：`X-Platform: 1`（web-mobile）。iOS H5 是 `4`，端游 web 是 `0`。
另有 `X-Channel` / `X-Source-Type` / `X-Ver`。

### 传输加密

不是标准 AES，是「偏移量异或 + Base64」：

```js
randomSplit = e => e.split(",").map(x => x.split(":")[1]).join("d")
cookieSet   = e => e.split("").map(c => c.charCodeAt() + "-" + rand2()).join("-")
cookieGet   = e => String.fromCharCode(...e.split("-").filter((_, i) => i % 2 === 0))
getOffset2  = e => getOffset(...e.split("d").map(Number).slice(1))
getOffset   = (a, b, c) => (a | b) % 256 + c

offsetEn  = (off, txt) => base64(utf8(txt).map(b => (b + off) % 256))
offsetOut = (off, b64) => utf8(base64decode(b64).map(b => (b - off + 256) % 256))
```

`OFFSET` 是登录响应里 `encrypt` 字段推导出来的：`cookieSet(randomSplit(encrypt))` 写进
localStorage 的 `NCG-eid`，之后每次 `cookieGet` → `getOffset2` → **`% 256`** 还原。

### 三个踩过的坑

**① 网关 WS 的 `auth` 包必须发明文。**

看这份代码：

```js
socketSend(e, t, i, n) {
  const c = {id: …, op: e, data: t || {}};
  this.$ws.send(n || s ? c : offsetEn(this.OFFSET, JSON.stringify(c)));
}
```

`n` 为真时直接发对象。`onOpen` 里是 `socketSend("auth", i, cb, !0)` —— 第四个参数就是 `true`。
按「全帧加密」发会得到 `{"errcode":400,"errmsg":"format invalid"}`，排查了很久才发现。
`answer` / `input` / `game_set_quality` 才走 `offsetEn`。

**② v2 响应体是裸 Base64，不是 JSON 字符串。**

`Content-Type: application/octet-stream; method=1`，body 直接就是 Base64 文本。
早期代码只处理了 `startswith('"')` 的情况，所以一直拿到 `{"raw": …}`。
另外 `b64decode` 必须补 padding，否则异常被上层吞掉后静默降级。

**③ `run.html` 的 localStorage 有 `{"data": <值>}` 信封。**

站点的 storage 封装是 `set: (k, v) => JSON.stringify({data: v})`，读的时候取 `.data`。
直接写裸值会被读成 `null` → 判定没有 token → 弹回首页。
用浏览器自动化进游玩页时，必须写 `localStorage.setItem(k, JSON.stringify({data: v}))`，
而且要先 `goto` 到 `cg.163.com` 源下再写。

### 启动必须登录，登录链路如下

匿名点「秒玩」会跳 `#/login?backTo=%2Fpay`。这部分没有绕过手段，也不需要：
`POST api/v2/tickets` 只要求 `Authorization: Bearer`，拿一次 token 就能长期复用。
三条链路都实测过，错误码是从响应体里原样抄的。

**① 短信验证码**

```
POST api/v1/phone-captchas/<ctcode>-<phone>          # 区号在前！
     {"extra":{"invite_code":""},"etc":{"validate":""}}
POST api/v1/tokens
     {"auth_method":"phone-captcha","ctcode":"86","phone":"…",
      "captcha":"123456","login_mode":"sms_code"}
```

路径参数顺序是个坑：`86-18664598726` → 200，反过来 → 400 `errcode=1103 无效区号`，
未注册号 → 404 `errcode=1104 电话号码不存在`。同一号码 60 秒内重复请求 →
`errcode=1100 短信验证码申请太频繁`。验证码错 → **HTTP 401** `errcode=1102`；
错太多次 → `errcode=1109 错误次数过多，验证码已失效`。

**② 密码登录（三级加密）**

```
GET api/v1/user-pwd-info?phone=…&ctcode=86
→ {"has_pwd":true,"is_registered":true,
   "login_iv":1742291771803,"login_random_text":"FET4GtMg",
   "pwd_encrypt_key":"67d9433b785d5705bcbdcb83",
   "user_pwd_login_salt":"m2R4iYuF"}
```

```js
o   = SHA256(salt + password)                     // 64 字符 hex
key = Utf8.parse(pwd_encrypt_key)                 // 24 字节 → 3DES
iv  = Hex.parse(login_iv.toString(16).padStart(16, "0"))
pwd = TripleDES.encrypt(o + login_random_text, key, {iv, CBC, Pkcs7}).toString(Base64)
```

`login_random_text` 和 salt **每次请求都会变**，所以 `pwd_info` 和
`POST api/v1/user-pwd-tokens` 必须连着发。`sdk/cgapi.py` 的 `login_password()` 已封装；
`encrypt_password()` 优先用 pycryptodome / cryptography，两者都没有时退回
`sdk/_des3.py`（纯标准库 3DES，已用 pycryptodome 对同一组 key/iv/明文逐字节交叉验证）。

**③ 扫码登录 —— 匿名拿不到**

`POST api/v2/login-qrcodes` 未登录时对任何 `X-Platform` 都返回 401 `errcode=1006`；
只有 `X-Platform: 6`（TV）返回不同的 `errcode=1025`。原因是它只用在
「TV 端免费时长耗尽、引导扫码注册」的场景（`showExceed()` → `dispatch("postQr")`）。
PC 首页那个登录二维码走的是另一个入口，不在移动端范围内。
轮询 `GET api/v1/tokens?qrcode_id=` 本身匿名可用，无效 id 报 `errcode=1218 二维码无效`。

**拿到 token 之后**

```js
t = cookieSet(randomSplit(resp.encrypt))
s = cookieGet(t)               // "<a>:<num>,<b>:<num>"
o = getOffset2(s) % 256        // 注意 %256，getOffset2 可能大于 255
localStorage: NCG-token / NCG-eid(=t) / NCG-id / NCG-encrypt
```

`user_id` 也可以直接从 JWT 的 `identity` 字段取。

### 排队协议

`wss://n.cg.163.com/push/ws`，客户端只发 4 个 op：

```
auth            {user_id, token, platform, apk, ua}
enqueue
dequeue
destroy_ticket
```

服务端推 33 种 op，关键是这几个：

```
queue_status      → 「当前最佳选区：华南三区，当前排第 N 位」
queue_success     → 「N 秒后自动放弃」,开始计时
queue_success_soon
queue_canceled
queue_fail        → 「由于排队成功后未及时确认进入游戏，本次排队无效」
ticket_destroyed
order_paid / coupon / growth_value_changed / has_new_followers …
```

排队人数展示上限：普通 500（`YS_QUEUE_NUM`），PC VIP 300（`YS_QUEUE_VIP_NUM`）。

兜底轮询：`GET api/v2/users/@me/status` 每 10 秒，页面隐藏时暂停。

---

## 目录

```
.
├── README.md
├── LICENSE                   GPL-3.0
├── report.html               单页可视化报告（272 行可搜索接口表 + 真机截图）
├── docs/
│   ├── API文档.md            主文档，14 章，约 1500 行
│   ├── 接口清单.csv
│   └── img/                  真机截图（已打码）
├── sdk/
│   ├── cgapi.py              SDK：HTTP 全接口 + WS 客户端 + 输入封装，约 1200 行
│   └── _des3.py              纯标准库 3DES-CBC + PKCS7（密码登录用，无第三方依赖）
└── samples/                  23 份匿名接口的真实响应样本（原始 101 份，脱敏后保留无个人信息的部分）
```

想看接口怎么一步步挖出来的，主文档 `docs/API文档.md` 里每章都有源码偏移量和实测报文。

---

## 实测数据

四轮，都是真发请求：

| 轮次 | 身份 | 用例 | 通过 | 说明 |
|---|---|---|---|---|
| 1 | 匿名 | 37 | 29 | 覆盖首页/游戏/社区/SEO；失败 8 条全是 v2，401 未登录，属预期 |
| 2 | 登录 | 60 | 49 | 悬浮球/键位、启动排队、云电脑、社区 |
| 3 | 登录 | 22 | 17 | 「我的」页真实抓包复现 |
| 4 | 匿名（直连） | 16 | 16 | 登录链路：注册检查 / 密码前置 / 扫码轮询 / 登出 / 6 个补充接口 |
| **合计** | | **135** | **111** | |

**没通过的 24 条没有一条是接口不存在**，全是缺必填 query 或 HTTP 方法错。举几个：

| 接口 | 报错 | 正确用法 |
|---|---|---|
| `api/v2/media-servers` | 400 `'game_code' is a required property` | `?game_code=<code>` |
| `api/v2/game-hang-up` | 500 | `?game_code=<code>` → 200（耗时 16.8s） |
| `api/v2/ads/open_game_ads_display` | 400 | `?game_code=<code>` → `{"display": true}` |
| `api/v2/user_gray_and_version` | 400 | `?min_version_key=H5&package_name=H5` |
| `api/v2/pc_vip/games` | 500（后端超时） | `?page=1&page_size=10` → 200 |
| `api/v2/level_reward_acceptions` | 405 | 必须 POST |
| `api/v2/vip_queue_rights` | 405 / 404 | 必须 POST；无排队时返回「无排队信息」 |
| `api/v2/new-user-benefits` | 400 | 老账号正常返回「不是新用户」 |
| `api/v2/cloud-pc-price` | 400 | 需 `month` + `storage` 两个必填 |
| `api/v2/welfare_center/<x>` | 404 | 该路径不存在，真实入口是 `welfare_center_coupons` |

细节见 `docs/API文档.md` 第 12.3 / 12.4 节。

---

## 已知限制

- **只覆盖移动端 H5**。端游 web（`X-Platform: 0`）路径相同但部分参数不同，没深挖。
- **`api/v2/groups` 系列、礼物/活动类接口只做到「能列出来」**，业务语义没逐个验证。
- **DataChannel 没实装**。`common_setting.enable_data_channel` 默认 `false`，输入全走 WS；
  服务端会周期性推 `9 <port>,1,<hex>,<base64>` 催你建 DataChannel，但这条路径没验证过。
- **iOS 原生 App / 小程序端没碰**，`NCGJsBridge` 那条分支只是读代码了解。
- **登录链路只验证到「取到 token 前」**：短信/密码两种方式的接口、参数、加密流程都已实测，
  但为了不给测试号发短信、不碰别人的密码，没有真正完成一次登录；后续调用用的 token 是从
  已登录浏览器的 localStorage 复用的。密码加密结果已用 pycryptodome 逐字节交叉验证。
- 接口会变。这份快照是 2026-11-06 的，前端发版后 add/remove 字段很正常。

---

## 协议

GPL-3.0，见 [LICENSE](LICENSE)。

用到了这些思路，一并致谢：Chrome DevTools Protocol（抓包）、RFC 6455（自己实现的 WS 客户端）、
WebRTC 的 SDP 与 ICE（理解收流部分）。
