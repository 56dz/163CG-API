"""
cgapi.py — 网易云游戏 (cg.163.com) 移动端 H5 接口 SDK

覆盖：首页推荐 / 游戏详情 / 启动排队 / 游玩态 WebSocket / 悬浮球画质 / 我的

设计要点
--------
* 所有 HTTP 走网关 https://n.cg.163.com/，路径形如 api/v1/xxx、api/v2/xxx
* 必须带 X-Platform: 1 (web-mobile) 才能拿到移动端数据
* v2 接口需要登录 token + offset 加密；v1 接口匿名可用
* 响应统一 {data, errcode, errmsg, errmsgcn}；v2 未登录返回 HTTP 401 errcode=1006

用法
----
    api = CGAPI(proxy="http://127.0.0.1:10808")     # 有代理就填
    home = api.home()                                # 首页全部数据
    games = api.games()                              # 游戏全量列表
    detail = api.game("wow_cn_high")                 # 游戏详情

    api.login_token(token="...", encrypt="...")      # 有登录凭据后可访问 v2

    ws = PlaySession(api, gateway_url="gw-xxx.cg.163.com")
    ws.connect(); ws.auth(game_code="wow_cn_high", quality="high")
    ws.touch_down(0.5, 0.5); ws.tap(0.3, 0.7)
    ws.set_quality("bluray", name="蓝光")

仅用于学习与研究，请遵守网易云游戏的服务条款。
"""
from __future__ import annotations

import base64
import json
import random
import socket
import ssl
import struct
import subprocess
import time
from typing import Any
from urllib.parse import urlencode

GATEWAY = "https://n.cg.163.com/"
GATEWAY_NEW = "https://nr.cg.163.com/"
PUSH_WS = "wss://n.cg.163.com/push/ws"

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# X-Platform 取值表（getXPlatform）
PLATFORMS = {
    "web-pc": 0, "web-mobile": 1, "android-mobile-platform": 2,
    "android-mobile-apk": 3, "ios-mobile-platform": 4, "android-tv-apk": 5,
    "android-tv-platform": 6, "pc-micro": 7, "pc-native-platform": 8,
    "ios-native-platform": 9, "ios-micro": 10, "android-mobile-pc": 12,
    "mac-native-platform": 13, "harmonyos-h5-platform": 14,
}

# 画质档位（QUALITIES 常量）
QUALITIES = [
    {"name": "标清", "key": "low"},
    {"name": "高清", "key": "middle"},
    {"name": "超清", "key": "high"},
    {"name": "蓝光", "key": "bluray"},
]
QUALITY_KEYS = [q["key"] for q in QUALITIES]

# 反查：X-Platform 数值 → 平台名（getXPlatformStr）
PLATFORM_NAMES = {v: k for k, v in PLATFORMS.items()}


# ============================================================ 加密算法复刻
def random_split(s: str) -> str:
    """'a:12,b:34' -> '12d34'"""
    return "d".join(part.split(":")[1] for part in s.split(","))


def _rand2() -> str:
    return str(random.randint(0, 99)).zfill(2)


def cookie_set(s: str) -> str:
    """每个字符转 charCode 后插入 2 位随机串，用 '-' 连接。"""
    return "-".join(f"{ord(c)}-{_rand2()}" for c in s)


def cookie_get(s: str) -> str:
    """cookieSet 的逆运算：取偶数位还原 charCode。"""
    parts = s.split("-")
    return "".join(chr(int(x)) for x in parts[0::2] if x)


def get_offset(a: int, b: int, c: int) -> int:
    return (a | b) % 256 + c


def get_offset2(s: str) -> int:
    nums = [int(x) for x in s.split("d")]
    return get_offset(*nums[1:4])


def offset_from_encrypt(encrypt: str) -> int:
    """从登录响应里的 encrypt 字段推导 0..255 偏移量。"""
    return get_offset2(cookie_get(cookie_set(random_split(encrypt))))


def offset_en(off: int, text: str) -> str:
    raw = text.encode("utf-8")
    return base64.b64encode(bytes((b + off) % 256 for b in raw)).decode()


def offset_out(off: int, b64: str) -> str:
    raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
    return bytes((b - off) % 256 for b in raw).decode("utf-8", "replace")


# ============================================================ HTTP 客户端
class CGError(Exception):
    def __init__(self, status: int, payload: dict):
        self.status = status
        self.payload = payload
        super().__init__(
            f"HTTP {status} errcode={payload.get('errcode')} "
            f"{payload.get('errmsgcn') or payload.get('errmsg')}"
        )


class CGAPI:
    """移动端只读 + 登录后读写接口。"""

    def __init__(self, proxy: str | None = None, platform: int = 1,
                 channel: str = "web", ver: str = "0.0.0", ua: str = MOBILE_UA,
                 timeout: int = 30):
        self.proxy = proxy
        self.platform = platform
        self.channel = channel
        self.ver = ver
        self.ua = ua
        self.timeout = timeout
        self.token: str | None = None
        self.uid: str | None = None
        self.offset: int | None = None
        self.encrypt: str | None = None

    # -------------------------------------------------- 底层请求
    def _headers(self, v2: bool, body: bytes | None) -> dict[str, str]:
        h = {
            "X-Platform": str(self.platform),
            "X-Channel": self.channel,
            "X-Source-Type": self.channel,
            "X-Ver": self.ver,
            "User-Agent": self.ua,
            "Referer": "https://cg.163.com/",
            "Origin": "https://cg.163.com",
            "Accept": "application/json, text/plain, */*",
        }
        if v2:
            h["Authorization"] = f"Bearer {self.token or ''}"
            h["content-type"] = "application/octet-stream"
        elif body is not None:
            h["content-type"] = "application/json;charset=UTF-8"
        return h

    def request(self, method: str, path: str, params: dict | None = None,
                body: Any = None, v2: bool | None = None) -> Any:
        """发一个请求并返回 data 字段。v2 自动加密请求体 / 解密响应。"""
        if v2 is None:
            v2 = "/v2/" in path
        url = GATEWAY + path.lstrip("/")
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += ("&" if "?" in url else "?") + urlencode(clean, doseq=True)

        payload: bytes | None = None
        if body is not None:
            text = json.dumps(body, ensure_ascii=False)
            if v2 and self.offset is not None:
                payload = offset_en(self.offset, text).encode()
            else:
                payload = text.encode("utf-8")

        args = ["curl", "-sS", "-X", method, "--max-time", str(self.timeout),
                "-o", "-", "-w", "\n@@HTTPCODE:%{http_code}", url]
        for k, v in self._headers(v2, payload).items():
            args += ["-H", f"{k}: {v}"]
        if self.proxy:
            args += ["-x", self.proxy]
        if payload is not None:
            args += ["--data-binary", "@-"]

        proc = subprocess.run(args, input=payload or b"", capture_output=True)
        out = proc.stdout.decode("utf-8", "replace")
        if "@@HTTPCODE:" not in out:
            raise CGError(0, {"errcode": -1,
                              "errmsgcn": proc.stderr.decode("utf-8", "replace")[:400]})
        text, status_s = out.rsplit("@@HTTPCODE:", 1)
        status = int(status_s.strip() or 0)

        text = text.strip()
        # v2 响应体是裸 Base64（不是 JSON 字符串），需先解密；
        # 少数情况下是带引号的 JSON 字符串，两种都兼容。
        if v2 and self.offset is not None and text:
            plain = None
            if text.startswith("<"):                      # html 错误页，跳过
                pass
            elif text.startswith('"'):
                try:
                    plain = offset_out(self.offset, json.loads(text))
                except Exception:
                    plain = None
            else:
                try:
                    plain = offset_out(self.offset, text)
                except Exception:
                    plain = None
            if plain and plain.lstrip()[:1] in ("{", "["):
                text = plain
        try:
            data = json.loads(text) if text else {}
        except Exception:
            data = {"raw": text[:2000]}

        if status >= 400:
            raise CGError(status, data if isinstance(data, dict) else {"raw": data})
        if isinstance(data, dict) and "errcode" in data and data.get("errcode") not in (0, None):
            raise CGError(status, data)
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, body=body)

    def patch(self, path: str, body: Any = None) -> Any:
        return self.request("PATCH", path, body=body)

    def delete(self, path: str, body: Any = None) -> Any:
        return self.request("DELETE", path, body=body)

    # -------------------------------------------------- 登录态
    def login_token(self, token: str, encrypt: str | None = None,
                    uid: str | None = None) -> None:
        """注入已抓到的登录态。encrypt 用于推导 offset（缺省时按 0 处理）。"""
        self.token = token
        self.uid = uid
        self.encrypt = encrypt
        self.offset = offset_from_encrypt(encrypt) if encrypt else 0

    def _apply_login(self, r: dict) -> dict:
        """把 token 响应的登录态注入本实例。"""
        self.token = r.get("token")
        self.uid = str(r.get("user_id") or "")
        self.encrypt = r.get("encrypt")
        self.offset = offset_from_encrypt(self.encrypt) if self.encrypt else 0
        return r

    # ---- 手机号探测（只读，不发短信）----
    def check_phone(self, phone: str, cc: str = "86") -> Any:
        """查手机号是否注册。注意路径是 <ctcode>-<phone>（区号在前）。

        已实测：/86-138****0000 → 200 {ctcode, phone, user_need_negotiate_manually,
        user_need_yixin_validate}；号码不存在 → 404 errcode=1104「电话号码不存在」；
        参数顺序写反 → 400 errcode=1103「无效区号」。
        """
        return self.get(f"api/v1/phones-registered/{cc}-{phone}")

    def pwd_info(self, phone: str, cc: str = "86") -> Any:
        """查号码的密码登录参数（含 3DES 需要的 salt/key/iv）。

        返回 {has_pwd, is_registered, ignore_pwd_popup, login_iv,
        login_random_text, pwd_encrypt_key, user_pwd_login_salt}。
        """
        return self.get("api/v1/user-pwd-info", phone=phone, ctcode=cc)

    # ---- 短信验证码 ----
    def request_sms_code(self, phone: str, cc: str = "86",
                         invite_code: str = "", validate: str = "") -> Any:
        """申请短信验证码。POST（GET 会 405）。路径同样是 <ctcode>-<phone>。

        :param validate: 极验（yidun）校验串，风控命中时才需要。
        返回 {captcha}；频率过高 → errcode=1100「短信验证码申请太频繁」。
        """
        return self.post(f"api/v1/phone-captchas/{cc}-{phone}",
                         {"extra": {"invite_code": invite_code},
                          "etc": {"validate": validate}})

    def login_sms(self, phone: str, code: str, cc: str = "86") -> dict:
        """短信验证码登录。返回 {token, user_id, encrypt}，并自动注入。"""
        return self._apply_login(self.post("api/v1/tokens", {
            "auth_method": "phone-captcha",
            "ctcode": cc,
            "phone": phone,
            "captcha": code,
            "login_mode": "sms_code",
        }))

    # ---- 密码登录 ----
    def encrypt_password(self, password: str, info: dict) -> str:
        """复刻前端密码加密，需要 pycryptodome 或 cryptography。

        JS 原文：
            o = SHA256(salt + password)             // hex
            key = Utf8.parse(pwd_encrypt_key)       // 24 字节 → 3DES
            iv  = Hex.parse(login_iv.toString(16).padStart(16, "0"))
            c   = TripleDES(o + login_random_text).encrypt(key, iv, CBC, Pkcs7)
            pwd = c.ciphertext.toString(Base64)
        """
        import hashlib
        secret = hashlib.sha256(
            (info["user_pwd_login_salt"] + password).encode()).hexdigest()
        plain = (secret + info["login_random_text"]).encode()
        key = info["pwd_encrypt_key"].encode()
        iv = bytes.fromhex(format(int(info["login_iv"]), "x").rjust(16, "0")[-16:])
        try:
            from Crypto.Cipher import DES3
            from Crypto.Util.Padding import pad
            c = DES3.new(key, DES3.MODE_CBC, iv)
            return base64.b64encode(c.encrypt(pad(plain, 8))).decode()
        except ImportError:
            pass
        try:
            from cryptography.hazmat.primitives.ciphers import (
                Cipher, algorithms, modes)
            from cryptography.hazmat.primitives import padding
            p = padding.PKCS7(64).padder()
            data = p.update(plain) + p.finalize()
            enc = Cipher(algorithms.TripleDES(key), modes.CBC(iv)).encryptor()
            return base64.b64encode(enc.update(data) + enc.finalize()).decode()
        except ImportError:
            pass
        # 两个库都没有时退回自带的纯标准库实现（已用 pycryptodome 交叉验证）
        from _des3 import des3_cbc_encrypt_pkcs7
        return base64.b64encode(
            des3_cbc_encrypt_pkcs7(plain, key, iv)).decode()

    def login_password(self, phone: str, password: str, cc: str = "86",
                       yidun_validate_str: str | None = None) -> dict:
        """密码登录。先取 pwd_info 拿到本轮 salt，再 POST api/v1/user-pwd-tokens。"""
        info = self.pwd_info(phone, cc)
        body = {"phone": phone, "ctcode": cc,
                "pwd": self.encrypt_password(password, info),
                "login_mode": "password"}
        if yidun_validate_str:
            body["yidun_validate_str"] = yidun_validate_str
        return self._apply_login(self.post("api/v1/user-pwd-tokens", body))

    # ---- 扫码登录 ----
    def login_qrcode_create(self) -> Any:
        """创建扫码登录二维码。

        ⚠️ 实测：匿名 POST 返回 401 errcode=1006，该接口需要已登录态
        （前端只在「免费时长耗尽、想让 TV 端用户扫码注册」时调用）。
        真正的登录二维码在 PC 端 index 页由另一个入口生成。
        """
        return self.post("api/v2/login-qrcodes", {})

    def login_qrcode_poll(self, qrcode_id: str) -> dict:
        """轮询扫码结果。未扫/无效 → 400 errcode=1218「二维码无效」。"""
        r = self.get("api/v1/tokens", qrcode_id=qrcode_id)
        if r.get("token"):
            self._apply_login(r)
        return r

    def login_qrcode_poll(self, qrcode_id: str) -> dict:
        r = self.get("api/v1/tokens", qrcode_id=qrcode_id)
        if r.get("token"):
            self.token = r["token"]
            self.uid = str(r.get("user_id") or "")
            self.encrypt = r.get("encrypt")
            self.offset = offset_from_encrypt(self.encrypt) if self.encrypt else 0
        return r

    def logout(self) -> Any:
        return self.delete("api/v2/tokens/@current")

    # -------------------------------------------------- 1. 首页推荐
    def home_tabs(self) -> Any:
        """顶部分类 tab：推荐/手机游戏/PC游戏/限免专区/云电脑云手机/必玩榜"""
        return self.get("api/v1/home_page_tabs")

    def home(self, tab_id: str | None = None) -> list:
        """首页推荐流（模块数组）。"""
        return self.get("api/v1/main_recommendations",
                        main_recommendation_type="mobile_home", tab_id=tab_id)

    def banners(self, banner_type: str = "mobile_home") -> Any:
        return self.get("api/v1/banners", banner_type=banner_type)

    def pendants(self, pendant_type: str = "mobile_home") -> Any:
        return self.get("api/v1/pendant_list", pendant_type=pendant_type)

    def popups(self) -> Any:
        return self.get("api/v1/popups")

    def notices(self) -> Any:
        return self.get("api/v1/notices/list")

    def notice_current(self) -> Any:
        return self.get("api/v1/notices/@current")

    def text_res_positions(self) -> Any:
        return self.get("api/v1/text_res_position_items")

    def search_hints(self) -> Any:
        return self.get("api/v1/search_keyword_hints")

    def hot_searches(self) -> Any:
        return self.get("api/v1/hot-searches")

    def resource_icons(self, type_: str = "main_page") -> Any:
        return self.get("api/v1/resource_icons", type=type_)

    def speed_url(self, game_type: str = "mobile") -> dict:
        """默认画质与各档建议码率。"""
        return self.get("api/v1/speed_url", game_type=game_type)

    def client_settings(self) -> Any:
        return self.get("api/v1/client-settings/@current")

    def app_version(self, package: str = "H5") -> Any:
        return self.get("api/v1/versions/@current", package_name=package)

    def app_versions(self, **kw) -> Any:
        params = {"need_latest": 1}
        params.update(kw)
        return self.get("api/v1/versions/infos", **params)

    def customize_settings(self, ver: str = "v1", groups: list[str] | None = None) -> Any:
        """运营自定义配置（文案/灰度/开关）。"""
        return self.post(f"api/{ver}/group-customize-settings",
                         {"groups": groups or ["realname", "QQ_games",
                                               "gray_style_h5", "multi_cloud_pc_tag"]})

    def customize_setting(self, group: str, key: str, ver: str = "v1") -> Any:
        """单条运营配置：api/{ver}/customize-settings/<group>/<key> → {"val": "..."}。

        分辨率相关的 key 都在 pc_game 组下（v1 匿名可读，v2 需登录）：
          platform_resolution          "<platform>##<res>##<fps>##<默认res>##<默认fps>;..."
          resolution_auto_2k_enable    "1" / "0"
          resolution_auto_2k_whitelist "game_code,game_code,..."
          resolution_tips              切换分辨率前的提示文案
        """
        return self.get(f"api/{ver}/customize-settings/{group}/{key}")

    def platform_resolution(self, ver: str = "v1") -> dict[str, dict]:
        """各平台可用的分辨率/帧率档位，解析成 {platform: {...}}。"""
        raw = (self.customize_setting("pc_game", "platform_resolution", ver) or {}).get("val") or ""
        out: dict[str, dict] = {}
        for item in raw.split(";"):
            parts = item.split("##")
            if len(parts) < 5:
                continue
            out[parts[0]] = {
                "resolutions": [x for x in parts[1].split(",") if x],
                "fps": [x for x in parts[2].split(",") if x],
                "default_resolution": parts[3],
                "default_fps": parts[4],
            }
        return out

    def resolution_options(self, game_code: str, platform: int | str | None = None) -> dict:
        """某游戏在某平台上可用的分辨率/帧率档位，以及「跟随本机」是否开放。

        platform 可传 X-Platform 数值（1=web-mobile、4=ios-mobile-platform …）或平台名，
        不传则用本实例的 platform。取值优先级：
          游戏详情 resolution_configs[platform]  →  运营配置 platform_resolution[platform]
        「跟随本机(auto)」只有 allow_auto_game_resolution 为真才出现在可选列表里。
        """
        if platform is None:
            platform = self.platform
        if isinstance(platform, int):
            platform = PLATFORM_NAMES.get(platform, "web-mobile")

        detail = self.game(game_code)
        cfg: dict = {}
        for c in detail.get("resolution_configs") or []:
            if c.get("platform") == platform:
                cfg = c
                break
        fallback = self.platform_resolution().get(platform, {})

        fps_supports = list(cfg.get("fps_supports") or fallback.get("fps") or [])
        res_supports = list(cfg.get("resolution_supports") or fallback.get("resolutions") or [])
        if not fps_supports:                       # 手游只在顶层给
            fps_supports = list(detail.get("fps_supports") or [])
        if not res_supports:
            res_supports = list(detail.get("resolution_supports") or [])

        auto_on, whitelist = False, ""
        try:
            auto_on = int(float((self.customize_setting("pc_game", "resolution_auto_2k_enable") or {}).get("val") or 0)) == 1
            whitelist = str((self.customize_setting("pc_game", "resolution_auto_2k_whitelist") or {}).get("val") or "")
        except (TypeError, ValueError, CGError):
            pass
        allow_auto = auto_on or game_code in whitelist.split(",")

        return {
            "game_code": game_code,
            "game_type": detail.get("game_type"),
            "platform": platform,
            "enable_resolution_config": bool(detail.get("enable_resolution_config")),
            "switchable_in_game": bool(detail.get("enable_resolution_config")),
            "fps_supports": fps_supports,
            "fps_default": cfg.get("fps_default") or fallback.get("default_fps") or detail.get("fps_default"),
            "resolution_supports": res_supports,
            "resolution_default": (cfg.get("resolution_default") or fallback.get("default_resolution")
                                   or detail.get("resolution_default")),
            "allow_auto_game_resolution": allow_auto,
            "visible_resolutions": [r for r in res_supports if r != "auto" or allow_auto],
        }

    # iPhone 刘海/灵动岛机型的安全区（按物理像素识别，来源 getIphoneXSaftArea）
    _SAFE_AREAS = {
        (1170, 2532): 44, (1284, 2778): 44, (1080, 2340): 44,
        (1179, 2556): 54, (1290, 2796): 54, (1206, 2622): 54,
    }

    @classmethod
    def iphone_x_safe_area(cls, screen_w: int, screen_h: int, dpr: float = 3) -> int:
        """按物理像素判断是否刘海机型，返回安全区像素（0 表示不需要扣）。"""
        px = tuple(sorted((round(screen_w * dpr), round(screen_h * dpr))))
        return cls._SAFE_AREAS.get(px, 0)

    @staticmethod
    def video_screen(base_w: int = 1280, base_h: int = 720, is_portrait: bool = False,
                     game_type: str = "mobile", screen_w: int = 390, screen_h: int = 844,
                     dpr: float = 3, standalone: bool = False, safe_area: int | None = None) -> dict:
        """复刻 getVideoScreen()：把设备屏幕换算成要推给机位的画布尺寸。

        - 手游(mobile)：高度固定 base_h(720)，宽度 = floor(长边 × 720 / 短边)，
          即按设备宽高比反算 —— 所以「适配各种屏幕」是自动的，不需要调接口。
        - 端游(pc)：宽 = (长边 - 安全区) × dpr，高 = 短边 × dpr；
          安全区按 iPhone 刘海机型自动识别（1170×2532 等 → 44px，1179×2556 等 → 54px）。
        - standalone(PWA 全屏) 会额外扣 20px。

        实测对照（iPhone 14，390×844、dpr 3）：
            video_screen(game_type="pc")     -> {"w": 2400, "h": 1170}  (= auth 里真实发的值)
            video_screen(game_type="mobile") -> {"w": 1558, "h": 720}
        """
        long_side, short_side = max(screen_w, screen_h), min(screen_w, screen_h)
        if game_type == "pc":
            cut = CGAPI.iphone_x_safe_area(screen_w, screen_h, dpr) if safe_area is None else safe_area
            w = round((long_side - cut) * dpr)
            h = round((short_side - (20 if standalone else 0)) * dpr)
            return {"w": w, "h": h}
        long_side -= 20 if standalone else 0
        return {"w": int(long_side * base_h / short_side), "h": base_h}

    def ctcodes(self) -> Any:
        return self.get("api/v1/ctcodes")

    def questions(self) -> Any:
        """FAQ / 帮助与反馈全部问答。"""
        return self.get("api/v1/questions")

    def home_all(self) -> dict:
        """一次性拉齐首页所需全部数据（并发度靠顺序请求，够用）。"""
        out: dict[str, Any] = {}
        for name, fn in [
            ("tabs", self.home_tabs),
            ("banners", self.banners),
            ("pendants", self.pendants),
            ("popups", self.popups),
            ("notices", self.notices),
            ("text_res", self.text_res_positions),
            ("search_hints", self.search_hints),
            ("speed_url_mobile", lambda: self.speed_url("mobile")),
            ("speed_url_pc", lambda: self.speed_url("pc")),
            ("client_settings", self.client_settings),
        ]:
            try:
                out[name] = fn()
            except CGError as e:
                out[name] = {"error": str(e)}
        try:
            out["recommend"] = self.home()
        except CGError as e:
            out["recommend"] = {"error": str(e)}
        return out

    # -------------------------------------------------- 2. 游戏选择与启动
    def games(self, v2: bool = False, **params) -> Any:
        """全量游戏列表（v1 匿名可用，约 1800 条 / 6MB）。"""
        return self.get(f"api/{'v2' if v2 else 'v1'}/games", **params)

    def game(self, game_code: str, v2: bool = False) -> dict:
        """游戏详情（v1 匿名可用，142 字段）。"""
        return self.get(f"api/{'v2' if v2 else 'v1'}/games/{game_code}")

    def guess_like(self) -> Any:
        return self.get("api/v2/guess_user_like_games")

    def exit_recommendations(self, scene: str = "guess_you_like") -> Any:
        return self.get("api/v2/game_exit_recommendations", scene=scene)

    def playing_histories(self) -> Any:
        return self.get("api/v2/users/@me/game-playing-histories")

    def hide_history(self, game_code: str) -> Any:
        return self.post(f"api/v2/users/@me/game-playing-histories-hidden/{game_code}")

    def game_time_remain(self, game_code: str) -> Any:
        """今日剩余免费时长。"""
        return self.get("api/v2/game_time_remain", game_code=game_code)

    def run_page_popup(self, game_code: str) -> Any:
        return self.get(f"api/v2/games/{game_code}/run_page_popup")

    def start_game_popup(self, game_code: str) -> Any:
        return self.get(f"api/v2/start_game_popup/game_code/{game_code}")

    def default_keymap(self, game_code: str) -> Any:
        return self.get(f"api/v2/games/{game_code}/key_mapping")

    # --- 启动核心 ---
    def post_ticket(self, game_code: str, regions: list[str] | None = None,
                    codecs: list[str] | None = None, width: int = 1280,
                    height: int = 720, extra: dict | None = None) -> dict:
        """申请游戏机位。

        返回（成功）: {gateway_url, game_type, game_tags, region, region_name,
                       lock_detail, screen, expires, ...}
        返回（需要排队）: 无 expires，改走 push ws 排队协议。
        """
        body = {
            "regions": regions or [],
            "game_code": game_code,
            "codecs": codecs or ["h264"],
        }
        if width and height:
            body["width"] = width
            body["height"] = height
        if extra:
            body["extra"] = extra
        return self.post("api/v2/tickets", body)

    def network_test(self, latency: float, bandwidth: float, ua: str,
                     details: Any = None, quality: str | None = None) -> Any:
        """测速上报，影响选服。"""
        return self.post("api/v2/network-tests", {
            "latency": latency, "bandwidth": bandwidth,
            "details": details or [], "ua": ua, "quality": quality,
        })

    def game_requests(self, **kw) -> Any:
        return self.post("api/v2/game-requests", kw)

    def games_playing(self) -> Any:
        """当前正在玩的游戏（含 gateway_url，可直接重连）。"""
        return self.get("api/v2/users/@me/games-playing")

    def stop_game(self, game_code: str) -> Any:
        return self.delete(f"api/v2/users/@me/games-playing/{game_code}")

    def patch_playing(self, game_code: str, body: dict) -> Any:
        return self.patch(f"api/v2/users/@me/games-playing/{game_code}", body)

    def restart_game(self, game_code: str) -> Any:
        return self.post(f"api/v2/restart_game/{game_code}")

    def media_servers(self, game_code: str | None = None) -> Any:
        """可用媒体服列表（切服时挑 delay 最低的）。game_code 为必填 query。"""
        if game_code:
            return self.get("api/v2/media-servers", game_code=game_code)
        return self.get("api/v2/media-servers")

    def patch_media_server(self, body: dict) -> Any:
        return self.patch("api/v2/users/@me/media-servers/user", body)

    def turn_credentials(self, region: str) -> dict:
        """WebRTC TURN 凭据 {uris, username, credential}。"""
        return self.post("api/v2/turn-credentials", {"region": region})

    def switch_queue(self) -> Any:
        return self.post("api/v2/users/switch_queue")

    def queue_popup(self, trigger_scene: int = 0) -> Any:
        return self.get("api/v2/users/queue_popup", trigger_scene=trigger_scene)

    def vip_queue_rights(self) -> Any:
        return self.post("api/v2/vip_queue_rights")

    def free_interval(self) -> Any:
        return self.get("api/v2/get_now_user_free_interval")

    def hang_up(self, game_code: str, enable: bool = True) -> Any:
        return self.post("api/v2/game-hang-up",
                         {"game_code": game_code, "is_hang_up": enable})

    # -------------------------------------------------- 4. 悬浮球 / 画质 / 键位
    def common_setting(self, game_code: str) -> dict:
        """游戏级本地配置（画质、帧率、灵敏度、菜单键、全屏等）。"""
        return self.get(f"api/v2/users/@me/games/{game_code}/common_setting")

    def save_common_setting(self, game_code: str, body: dict) -> Any:
        return self.post(f"api/v2/users/@me/games/{game_code}/common_setting", body)

    def keymaps(self, game_code: str, simple: bool = True) -> Any:
        kind = "key_mappings_simple" if simple else "key_mappings_new"
        return self.get(f"api/v2/users/@me/games/{game_code}/{kind}")

    def save_keymap(self, game_code: str, body: dict) -> Any:
        return self.post(f"api/v2/users/@me/games/{game_code}/key_mappings", body)

    def update_keymap(self, game_code: str, mapping_id: str, body: dict) -> Any:
        return self.patch(f"api/v2/users/@me/games/{game_code}/key_mappings/{mapping_id}",
                          body)

    def delete_keymap(self, game_code: str, mapping_id: str) -> Any:
        return self.delete(f"api/v2/users/@me/games/{game_code}/key_mappings/{mapping_id}")

    def select_keymap(self, game_code: str, body: dict) -> Any:
        return self.post(
            f"api/v2/users/@me/games/{game_code}/key_mapping_select_upsert", body)

    def gamepads_count(self) -> Any:
        return self.get("api/v2/users/@me/gamepads-count")

    def ball_red(self, game_code: str) -> Any:
        """悬浮球红点。"""
        return self.get("api/v2/ball_res", game_code=game_code)

    # -------------------------------------------------- 5. 我的
    def me(self) -> dict:
        return self.get("api/v2/users/@me")

    def update_me(self, body: dict) -> Any:
        return self.patch("api/v2/users/@me", body)

    def my_status(self) -> Any:
        """登录后轮询：返回 queue_status / games_playing / ticket。"""
        return self.get("api/v2/users/@me/status")

    def vip_statistics(self) -> Any:
        return self.get("api/v2/users/@me/vip-statistics")

    def level_info(self) -> Any:
        return self.get("api/v2/users/@me/level_info")

    def growth_history(self) -> Any:
        return self.get("api/v2/users/@me/growth_value_histories")

    def orders(self, count: int = 10, order_type: str = "all") -> Any:
        return self.get("api/v2/orders", count=count, order_type=order_type)

    def order(self, order_no: str) -> Any:
        return self.get(f"api/v2/orders/{order_no}")

    def recharges(self) -> Any:
        return self.get("api/v2/recharges")

    def first_price(self) -> Any:
        return self.get("api/v2/first-price")

    def coins_changes(self) -> Any:
        return self.get("api/v2/users/@me/coins-changes")

    def sign_info(self) -> Any:
        return self.get("api/v2/sign-user-info")

    def sign_today(self) -> Any:
        return self.post("api/v2/sign-today")

    def user_config(self) -> Any:
        return self.get("api/v2/user_config/@me")

    def my_coupons(self) -> Any:
        return self.get("api/v2/my_coupons")

    def pc_free_time(self) -> Any:
        return self.get("api/v2/users/@me/pc_free_time_details")

    def cancel_check(self) -> Any:
        return self.get("api/v2/cancellation_btn/check")

    def parent_ctrl(self) -> Any:
        return self.get("api/v2/parent-ctrl/info")

    def push_msgs(self, count: int = 20) -> Any:
        return self.get("api/v2/push_msgs", count=count)

    def system_msgs(self, count: int = 20) -> Any:
        return self.get("api/v2/system_msg_push_msgs", count=count)

    def my_notices(self) -> Any:
        return self.get("api/v2/notices/list")

    def gray_and_version(self) -> Any:
        return self.get("api/v2/user_gray_and_version")

    def welfare(self, tab: str = "") -> Any:
        return self.get(f"api/v2/welfare_center/{tab}")

    def new_user_benefits(self) -> Any:
        return self.get("api/v2/new-user-benefits")

    # -------------------------------------------------- 6. 云电脑 / 社区 / SEO
    def cloud_pc_instances(self) -> Any:
        return self.get("api/v2/cloud-pc/multi-instances")

    def cloud_pc_config(self) -> Any:
        return self.get("api/v2/cloud-pc/multi-instance-config")

    def cloud_pc_price(self) -> Any:
        return self.get("api/v2/cloud-pc-price")

    def cloud_pc_my_pcs(self) -> Any:
        return self.get("api/v2/cloud-pc/my-pcs")

    def topics(self, skip: int = 0, limit: int = 18) -> Any:
        return self.get(f"api/v1/community/topic_list/{skip}/{limit}")

    def community_recommend(self) -> Any:
        return self.post("api/v1/community/recommend/article")

    def live_rooms(self, **params) -> Any:
        return self.get("api/v2/live-rooms", **params)

    def live_room(self, room_id: str) -> Any:
        return self.get(f"api/v2/live-room/{room_id}")

    def seo_games(self) -> Any:
        return self.get("api/v1/seo/games")

    def seo_contents(self, per_page: int = 4, page: int = 1) -> Any:
        return self.get("api/v1/seo/metis_contents", per_page=per_page, page=page)

    def seo_game_contents(self, page: int = 0, per_page: int = 10) -> Any:
        return self.get("api/v1/seo/game_contents", page=page, per_page=per_page)

    def behaviors(self, body: dict) -> Any:
        """埋点上报（POST）。"""
        return self.post("api/v1/behaviors", body)

    def client_error(self, body: dict) -> Any:
        return self.post("api/v2/client-errors", body)

    # -------------------------------------------------- 补充：c() 形式的接口
    # 这批接口由 api 模块里的 c(e,t) = s+"api/".concat(t||"v1|v2").concat(e)
    # 拼出来，早期抽取脚本只匹配 s+"api/..." 所以漏了。全部已实测。
    def feedback_game_types(self) -> Any:
        """反馈表单里的游戏类型下拉（匿名可读）。"""
        return self.get("api/v1/feedback_game_types")

    def sign_info_v2(self) -> Any:
        """签到信息 v2：连签天数、今日是否已签、奖励档位（匿名可读）。"""
        return self.get("api/v1/sign-info-v2")

    def gift_packs(self) -> Any:
        """礼包列表（匿名可读）。"""
        return self.get("api/v1/gift-packs")

    def upload_tokens(self) -> Any:
        """取七牛上传凭证（匿名即可，返回 {token}，Policy+Signature 形式）。"""
        return self.post("api/v1/upload-tokens", {})

    def task_status(self) -> Any:
        """每日任务状态（匿名返回模板，登录后带 finished 进度）。"""
        return self.get("api/v1/users/@me/task_status")

    def daily_task_detail(self) -> Any:
        """直播/云玩每日任务详情（匿名可读）。"""
        return self.get("api/v1/users/@me/live_room/daily_task_status_v2")

    def activity_banners(self) -> Any:
        """活动 Banner（匿名可读，字段比首页 banners 多 channel_names/banners）。"""
        return self.get("api/v1/activity-banners")

    def merchant_paas_configs(self) -> Any:
        return self.get("api/v1/merchant_paas_configs")

    def cloud_pc_info(self) -> Any:
        """云电脑实例信息（需登录，v2）。"""
        return self.get("api/v2/users/@me/cloud-pc")

    def cloud_pc_config(self) -> Any:
        """云电脑配置（需登录，v2）。"""
        return self.get("api/v2/users/@me/cloud_pc_config")


# ============================================================ 游玩态 WebSocket
INPUT_EVENTS = {
    "down": 1, "move": 2, "up": 3, "cancel": 4, "text": 5, "key": 6, "tap": 8,
    "mousedown": 100, "mouseup": 101, "mousemove": 102, "mousewheel": 103,
    "keydown": 104, "keyup": 105, "connect": 110, "disconnect": 111,
    "press": 112, "hold": 113, "release": 114, "keyon": 120, "keyoff": 121,
    "clipboard": 130,
}


class WSClient:
    """极简 WebSocket 客户端（RFC6455，仅文本帧，够用且无第三方依赖）。"""

    def __init__(self, url: str, proxy: str | None = None, timeout: int = 20):
        self.url = url
        self.proxy = proxy
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def _handshake(self):
        from urllib.parse import urlparse
        u = urlparse(self.url)
        host = u.hostname
        port = u.port or (443 if u.scheme in ("wss", "https") else 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query

        if self.proxy:
            from urllib.parse import urlparse as _up
            p = _up(self.proxy if "://" in self.proxy else "http://" + self.proxy)
            raw = socket.create_connection((p.hostname, p.port or 8080), self.timeout)
            raw.sendall(
                f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()
            )
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += raw.recv(4096)
            if b" 200" not in buf.split(b"\r\n")[0]:
                raise RuntimeError(f"proxy CONNECT failed: {buf[:200]!r}")
            raw = raw
        else:
            raw = socket.create_connection((host, port), self.timeout)

        if u.scheme in ("wss", "https"):
            ctx = ssl._create_unverified_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)

        key = base64.b64encode(bytes(random.getrandbits(8) for _ in range(16))).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Origin: https://cg.163.com\r\n"
            f"User-Agent: {MOBILE_UA}\r\n\r\n"
        )
        raw.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = raw.recv(4096)
            if not chunk:
                raise RuntimeError(f"ws handshake closed: {resp[:300]!r}")
            resp += chunk
        if b" 101" not in resp.split(b"\r\n")[0]:
            raise RuntimeError(f"ws handshake failed: {resp.split(b'\r\n')[0]!r}")
        self.sock = raw

    def connect(self):
        self._handshake()
        return self

    def send(self, text: str):
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = bytes(random.getrandbits(8) for _ in range(4))
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ws closed")
            buf += chunk
        return buf

    def recv(self, timeout: float | None = None) -> str:
        if timeout:
            self.sock.settimeout(timeout)
        b1, b2 = self._recv_exact(2)
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b"\0\0\0\0"
        data = self._recv_exact(n)
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 0x8:
            raise ConnectionError("ws close frame")
        if opcode == 0x9:  # ping
            self.sock.sendall(b"\x8a\x80" + bytes(4))
            return self.recv(timeout)
        return data.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.sendall(b"\x88\x80" + bytes(4))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class PlaySession:
    """游玩态会话：封装 auth 握手、输入指令、画质切换。

    协议要点
    --------
    * 连接 wss://<gateway_url>
    * 首包 op="auth"，携带 user_id/token/game_code/w/h/quality/codecs/platform
    * 服务端回 op=result（成功）/ op=offer（WebRTC SDP）/ op=error
    * 之后所有指令 op="input"，data={cmd:"<code> <args>"}
    * 画质切换是单独的 op="game_set_quality"
    * 所有包体用 offsetEn(offset, json) 加密
    """

    def __init__(self, api: CGAPI, gateway_url: str, offset: int | None = None,
                 debug: bool = False):
        self.api = api
        self.gateway_url = self._normalize(gateway_url)
        self.offset = offset if offset is not None else (api.offset or 0)
        self.debug = debug
        self.ws: WSClient | None = None
        self.width = 1280
        self.height = 720
        self.video_w = 1280
        self.video_h = 720
        self.last_error: dict | None = None
        self.offer: dict | None = None

    @staticmethod
    def _normalize(gw: str) -> str:
        if gw.startswith("wss://") or gw.startswith("ws://"):
            return gw
        return "wss://" + gw.lstrip("/")

    # -------------------------------------------------- 收发
    def _send(self, op: str, data: dict | None = None, plain: bool = False) -> None:
        """plain=True 时发明文 JSON —— auth 包必须明文。

        依据 run.cbc92ca7.js socketSend(e,t,i,n)：第 4 个参数 n=true 时
        `this.$ws.send(n||s ? c : offsetEn(...))`，即直接用包对象 c 序列化；
        onOpen 里 socketSend("auth", i, cb, true) 正是这样。其余 op 走 offsetEn。
        """
        packet = {"id": str(int(time.time() * 1000)), "op": op, "data": data or {}}
        text = json.dumps(packet, ensure_ascii=False)
        payload = text if (plain or self.debug) else offset_en(self.offset, text)
        self.ws.send(payload)

    def _recv(self, timeout: float = 15.0) -> dict:
        raw = self.ws.recv(timeout)
        try:
            return json.loads(offset_out(self.offset, raw))
        except Exception:
            return json.loads(raw)

    def connect(self) -> "PlaySession":
        self.ws = WSClient(self.gateway_url, proxy=self.api.proxy).connect()
        return self

    # -------------------------------------------------- 握手
    def auth(self, game_code: str, quality: str = "high", codecs: list[str] | None = None,
             fps: str | None = None, res: str | None = None,
             platform: int | None = None, token: str | None = None,
             user_id: str | None = None, wait: bool = True) -> dict:
        body = {
            "user_id": user_id or self.api.uid or "",
            "token": token or self.api.token or "",
            "game_code": game_code,
            "w": self.width,
            "h": self.height,
            "quality": quality,
            "codecs": codecs or ["h264"],
            "platform": platform if platform is not None else self.api.platform,
        }
        if fps:
            body["fps"] = fps
        if res:
            body["res"] = res
        self._send("auth", body, plain=True)   # 服务端只接受明文 auth
        if not wait:
            return {}
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                msg = self._recv(5)
            except Exception:
                continue
            op = msg.get("op")
            if op == "offer":
                # 服务端先推 offer（WebRTC SDP），再推 result
                self.offer = msg
                continue
            if op == "result":
                return msg
            if op == "error":
                self.last_error = msg
                raise RuntimeError(f"auth failed: {msg.get('data')}")
        raise TimeoutError("auth 超时")

    def answer(self, sdp: str) -> None:
        self._send("answer", {"sdp": sdp})

    def set_quality(self, quality: str, name: str | None = None,
                    wait: bool = True, timeout: float = 10) -> dict:
        """切换画质：low / middle / high / bluray。"""
        if quality not in QUALITY_KEYS:
            raise ValueError(f"quality 必须是 {QUALITY_KEYS} 之一")
        self._send("game_set_quality", {"quality": quality})
        if not wait:
            return {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self._recv(min(3, timeout))
            except Exception:
                continue
            if msg.get("op") in ("result", "error"):
                return msg
        return {}

    # -------------------------------------------------- 输入
    def raw(self, cmd: str) -> None:
        self._send("input", {"cmd": cmd})

    def heartbeat(self) -> None:
        self.raw(f"0 {int(time.time() * 1000)}")

    def _pct(self, x: float, y: float, video_w: int | None = None,
             video_h: int | None = None) -> tuple[int, int]:
        vw = video_w or self.video_w
        vh = video_h or self.video_h
        return int(65535 * x / vw + 0.999), int(65535 * y / vh + 0.999)

    def touch_down(self, x: float, y: float, touch_id: int = 0,
                   video_w: int | None = None, video_h: int | None = None) -> None:
        px, py = self._pct(x, y, video_w, video_h)
        self.raw(f"1 {px} {py} {touch_id}")

    def touch_move(self, x: float, y: float, touch_id: int = 0,
                   video_w: int | None = None, video_h: int | None = None) -> None:
        px, py = self._pct(x, y, video_w, video_h)
        self.raw(f"2 {px} {py} {touch_id}")

    def touch_up(self, x: float, y: float, touch_id: int = 0,
                 video_w: int | None = None, video_h: int | None = None) -> None:
        px, py = self._pct(x, y, video_w, video_h)
        self.raw(f"3 {px} {py} {touch_id}")

    def tap(self, x: float, y: float, touch_id: int = 0,
            video_w: int | None = None, video_h: int | None = None) -> None:
        px, py = self._pct(x, y, video_w, video_h)
        self.raw(f"8 {px} {py} {touch_id}")

    def swipe(self, x1: float, y1: float, x2: float, y2: float, steps: int = 10,
              touch_id: int = 0, video_w: int | None = None,
              video_h: int | None = None, delay: float = 0.02) -> None:
        self.touch_down(x1, y1, touch_id, video_w, video_h)
        for i in range(1, steps + 1):
            t = i / steps
            self.touch_move(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t,
                            touch_id, video_w, video_h)
            time.sleep(delay)
        self.touch_up(x2, y2, touch_id, video_w, video_h)

    def cancel_touch(self) -> None:
        self.raw("4")

    def send_text(self, text: str) -> None:
        self.raw(f"5 {text}")

    def send_key(self, key_code: int) -> None:
        self.raw(f"6 {key_code}")

    def gps(self, longitude: float, latitude: float) -> None:
        self.raw(f"13 {longitude} {latitude}")

    def shake(self, gamma: float = 0, beta: float = 0, alpha: float = 0) -> None:
        self.raw(f"26 {gamma} {beta} {alpha} 0 {int(time.time() * 1e6)}")

    def vibrate_shake(self) -> None:
        self.raw("28")

    def paste_to_cloud(self, text: str) -> None:
        self.raw(f"29 {text}")

    def set_input_mode(self, keyboard_mouse: bool = False) -> None:
        self.raw("25 " + ("keymouse" if keyboard_mouse else "tochscreen"))

    def platform_info(self, platform_tag: str = "") -> None:
        self.raw(f"10 {platform_tag}")

    def view_transform(self, value: float) -> None:
        self.raw(f"14 {value}")

    def sleep_ms(self, ms: int) -> None:
        self.raw(f"133 {ms}")

    # PC 端输入（game_type == "pc" 时）
    def pc_mousedown(self, btn: int, x: float, y: float, state: int = 0,
                     pos_type: int = 1, video_w: int | None = None,
                     video_h: int | None = None) -> None:
        px, py = self._pct(x, y, video_w, video_h)
        self.raw(f"100 {btn} {pos_type} {px} {py} {state}")

    def pc_mouseup(self, btn: int, x: float, y: float, state: int = 0,
                   pos_type: int = 1, video_w: int | None = None,
                   video_h: int | None = None) -> None:
        px, py = self._pct(x, y, video_w, video_h)
        self.raw(f"101 {btn} {pos_type} {px} {py} {state}")

    def pc_mousemove(self, x: float, y: float, state: int = 0, pos_type: int = 1,
                     video_w: int | None = None, video_h: int | None = None) -> None:
        px, py = self._pct(x, y, video_w, video_h)
        self.raw(f"102 {pos_type} {px} {py} {state}")

    def pc_keydown(self, key_code: int, key_name: str, state: int = 2) -> None:
        self.raw(f"104 {key_code} {key_name} {state}")

    def pc_keyup(self, key_code: int, key_name: str, state: int = 0) -> None:
        self.raw(f"105 {key_code} {key_name} {state}")

    # -------------------------------------------------- 收包循环
    def pump(self, on_message=None, timeout: float = 60) -> None:
        """持续收包，回调 (op, data)。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self._recv(5)
            except Exception:
                return
            if on_message:
                on_message(msg.get("op"), msg.get("data") or {})

    def close(self) -> None:
        if self.ws:
            self.ws.close()


# ============================================================ 便捷入口
DEFAULT_PROXY = "http://127.0.0.1:10808"


def probe_proxy(proxy: str = DEFAULT_PROXY, timeout: int = 3) -> bool:
    """探测本地 HTTP 代理是否可用（能连上端口即算可用）。"""
    try:
        host, port = proxy.split("//", 1)[-1].rstrip("/").rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def make_api(proxy: str | None = None, auto_proxy: bool = True, **kw) -> CGAPI:
    """构造 CGAPI。

    :param proxy: 显式代理，传 "" 或 None 表示不走代理。
    :param auto_proxy: 未显式指定代理时，若本机 10808 在监听就走它；
        否则直连。这样在「校园网需要代理」和「已经能直连」两种环境下
        都不用改代码。
    """
    if proxy is None and auto_proxy and probe_proxy():
        proxy = DEFAULT_PROXY
    return CGAPI(proxy=proxy or None, **kw)


if __name__ == "__main__":
    api = make_api()
    print("== 首页 tabs ==")
    for t in api.home_tabs()["tabs"]:
        print(f"  {t['rank']:>2} {t['tab_type']:<10} {t['title']:<12} {t['tab_id']}")

    print("\n== 推荐流模块数 ==", len(api.home()))

    print("\n== 游戏详情 wow_cn_high ==")
    d = api.game("wow_cn_high")
    print("  ", d["name"], "|", d["game_type"], "|", d["screen"],
          "| fps", d["fps_supports"], "| res", d["resolution_supports"])

    print("\n== 画质档位默认码率 ==")
    print("  ", api.speed_url("mobile"))
