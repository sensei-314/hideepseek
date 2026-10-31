"""hideepseek -- a lightweight terminal client for the DeepSeek API.

API keys are encrypted with Windows DPAPI and stored in keys.json;
only the current Windows user on this machine can decrypt them.

用法 / Usage:
    python main.py
"""

import base64
import ctypes
import json
import os
import re
import shutil
import sys
from ctypes import wintypes
from datetime import datetime

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

URL = "https://api.deepseek.com/chat/completions"
BALANCE_URL = "https://api.deepseek.com/user/balance"
TAVILY_URL = "https://api.tavily.com/search"
DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"

MODELS = {"1": "deepseek-flash", "2": "deepseek-v4-pro"}
DEFAULT_PROMPT = "You are a helpful assistant."

IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".gif", ".webp"]
IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
TEXT_EXTS = [".txt", ".md", ".py", ".json", ".csv", ".log"]


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _make_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def _read_blob(blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        _kernel32.LocalFree(blob.pbData)


def dpapi_encrypt(plain: bytes) -> bytes:
    blob_in, _buf = _make_blob(plain)
    blob_out = _DataBlob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    return _read_blob(blob_out)


def dpapi_decrypt(cipher: bytes) -> bytes:
    blob_in, _buf = _make_blob(cipher)
    blob_out = _DataBlob()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(
            ctypes.get_last_error(),
            "CryptUnprotectData 失败（可能不是本机/本用户加密的）",
        )
    return _read_blob(blob_out)


class KeyStore:
    DEFAULT_NAMES = ("deepseek", "tavily")

    def __init__(self, path: str):
        self.path = path
        self._cache = {}
        self.load()

    def load(self):
        self._cache = {}
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._cache = {k: v for k, v in raw.items() if isinstance(v, str)}
        except Exception:
            self._cache = {}

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def names(self):
        return sorted(self._cache.keys())

    def has(self, name: str) -> bool:
        return name in self._cache

    def set(self, name: str, plain_key: str):
        blob = dpapi_encrypt(plain_key.encode("utf-8"))
        self._cache[name] = base64.b64encode(blob).decode("ascii")
        self.save()

    def get(self, name: str):
        enc = self._cache.get(name)
        if not enc:
            return None
        try:
            return dpapi_decrypt(base64.b64decode(enc)).decode("utf-8")
        except Exception:
            return None

    def remove(self, name: str) -> bool:
        if name in self._cache:
            del self._cache[name]
            self.save()
            return True
        return False


class HideepSeek:
    KINDS = ("deepseek", "tavily")

    def __init__(self, base_dir: str = BASE_DIR):
        self.base_dir = base_dir
        self.history_dir = os.path.join(base_dir, "chat_history")
        self.state_path = os.path.join(base_dir, "state.json")
        self.keys = KeyStore(os.path.join(base_dir, "keys.json"))

        self.active = {k: k for k in self.KINDS}
        self.prefer_deepseek_search = True
        self.max_searches = 3

        self.ensure_history_dir()
        self.ensure_keys()
        self.load_state()

    @staticmethod
    def now_str() -> str:
        return datetime.now().strftime("%Y-%m-%d %A %H:%M:%S")

    def time_prefix(self) -> str:
        return f"[当前时间：{self.now_str()}]\n"

    # ---------- 当前选中的密钥 ----------
    def key_names(self, kind: str):
        return [n for n in self.keys.names() if n == kind or n.startswith(kind + "-")]

    def load_state(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                st = json.load(f)
            if isinstance(st, dict):
                for kind in self.KINDS:
                    if isinstance(st.get(kind), str):
                        self.active[kind] = st[kind]
                if isinstance(st.get("prefer_deepseek_search"), bool):
                    self.prefer_deepseek_search = st["prefer_deepseek_search"]
        except Exception:
            pass
        for kind in self.KINDS:
            if not self.keys.has(self.active.get(kind, "")):
                cands = self.key_names(kind)
                self.active[kind] = cands[0] if cands else kind

    def save_state(self):
        payload = dict(self.active)
        payload["prefer_deepseek_search"] = self.prefer_deepseek_search
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def switch_key(self, kind: str) -> bool:
        cands = [n for n in self.key_names(kind) if self.keys.get(n)]
        if not cands:
            print(f"没有可用的 {kind} 密钥。")
            return False
        print(f"\n可用的 {kind} 密钥：")
        for i, n in enumerate(cands, 1):
            mark = "  ← 当前" if n == self.active.get(kind) else ""
            print(f"  {i}. {n}{mark}")
        print("  0. 取消")
        choice = input("请选择：").strip()
        if choice == "0":
            return False
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(cands):
                self.active[kind] = cands[idx]
                self.save_state()
                print(f"{kind} 已切换到：{cands[idx]}")
                return True
        except ValueError:
            pass
        print("无效选择。")
        return False

    def require_key(self, kind: str):
        name = self.active.get(kind, kind)
        key = self.keys.get(name)
        if key:
            return key
        for cand in self.key_names(kind):
            key = self.keys.get(cand)
            if key:
                self.active[kind] = cand
                self.save_state()
                return key
        print(f"缺少可用的 {kind} 密钥，请先在「API Key 管理」里添加。")
        return None

    # ---------- 密钥管理 ----------
    def ensure_keys(self):
        missing = [n for n in KeyStore.DEFAULT_NAMES if not self.keys.has(n)]
        if not missing:
            return
        print("\n未检测到完整的 API Key，需要先填写（只填一次，之后自动读取）。")
        for name in missing:
            self.prompt_add_key(name)
        print("密钥已加密保存到 keys.json（只有本机本用户能解开）。")

    def prompt_add_key(self, name: str):
        label = {
            "deepseek": "DeepSeek API Key（sk- 开头）",
            "tavily": "Tavily API Key（tvly- 开头，可留空跳过）",
        }.get(name, name)
        while True:
            value = input(f"请输入 {label}：").strip()
            if value:
                self.keys.set(name, value)
                self.active[name if name in self.KINDS else "deepseek"] = name
                self.save_state()
                print(f"已保存：{name}")
                return
            if name == "tavily":
                print("已跳过 tavily（备用搜索引擎将不可用）。")
                return
            print("不能为空，请重试。")

    def manage_keys(self):
        while True:
            print("\n" + "-" * 52)
            print("API Key 管理")
            print("-" * 52)
            for kind in self.KINDS:
                current = self.active.get(kind, kind)
                cands = self.key_names(kind)
                state = "可用" if self.keys.get(current) else "未设置"
                print(f"  [{kind}] 当前：{current}（{state}），共 {len(cands)} 个")
            print("  a. 添加 / 更新")
            print("  d. 删除")
            print("  s. 切换当前使用的密钥")
            print("  0. 返回")
            choice = input("请选择：").strip().lower()

            if choice == "0":
                return
            if choice == "a":
                name = input("密钥名（如 deepseek / deepseek-2 / tavily）：").strip()
                if not name:
                    continue
                value = input("密钥内容：").strip()
                if value:
                    self.keys.set(name, value)
                    for kind in self.KINDS:
                        if name == kind or name.startswith(kind + "-"):
                            self.active[kind] = name
                    self.save_state()
                    print("已保存（加密）。")
            elif choice == "d":
                name = input("要删除的密钥名：").strip()
                if self.keys.remove(name):
                    for kind in self.KINDS:
                        if self.active.get(kind) == name:
                            rest = self.key_names(kind)
                            self.active[kind] = rest[0] if rest else kind
                    self.save_state()
                    print("已删除。")
                else:
                    print("没有这个密钥。")
            elif choice == "s":
                kind = input("切换哪一类（deepseek / tavily）：").strip().lower()
                if kind in self.KINDS:
                    self.switch_key(kind)

    # ---------- 搜索 ----------
    def search_with_tavily(self, query: str) -> str:
        api_key = self.require_key("tavily")
        if not api_key:
            raise RuntimeError("没有可用的 Tavily 密钥")
        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": 5,
        }
        response = requests.post(TAVILY_URL, json=payload, verify=False, timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f"Tavily 返回错误状态码: {response.status_code}")
        results = response.json().get("results", [])
        if not results:
            return "（Tavily 未找到相关搜索结果）"
        lines = [f"以下是关于“{query}”的搜索结果：\n"]
        for i, item in enumerate(results, 1):
            lines.append(
                f"{i}. {item.get('title', '')}\n"
                f"   {item.get('content', '')[:500]}\n"
                f"   来源：{item.get('url', '')}\n"
            )
        return "\n".join(lines)

    def search_with_deepseek(self, query: str) -> str:
        api_key = self.require_key("deepseek")
        if not api_key:
            raise RuntimeError("没有可用的 DeepSeek 密钥")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"query": query, "count": 5}
        response = requests.post(
            DEEPSEEK_SEARCH_URL, headers=headers, json=payload, verify=False, timeout=15
        )
        if response.status_code != 200:
            raise RuntimeError(f"DeepSeek 官方搜索报错，状态码：{response.status_code}")
        results = response.json().get("results", [])
        if not results:
            return "（DeepSeek 官方未找到相关搜索结果）"
        lines = [f"以下是关于“{query}”的搜索结果：\n"]
        for i, item in enumerate(results, 1):
            snippet = item.get("snippet", item.get("content", ""))[:500]
            lines.append(
                f"{i}. {item.get('title', '')}\n"
                f"   {snippet}\n"
                f"   来源：{item.get('url', '')}\n"
            )
        return "\n".join(lines)

    def web_search(self, query: str) -> str:
        first, second = (
            (self.search_with_deepseek, self.search_with_tavily)
            if self.prefer_deepseek_search
            else (self.search_with_tavily, self.search_with_deepseek)
        )
        first_name = "DeepSeek 官方" if self.prefer_deepseek_search else "Tavily"
        second_name = "Tavily" if self.prefer_deepseek_search else "DeepSeek 官方"
        try:
            print(f"  [路由] 尝试 {first_name} 搜索...")
            return first(query)
        except Exception as exc:
            print(f"  [降级] {first_name} 失败（{exc}），切换到 {second_name}...")
            try:
                return second(query)
            except Exception as exc2:
                return f"（所有搜索接口均不可用：{exc2}）"

    # ---------- 存档 ----------
    def ensure_history_dir(self):
        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

    def save_conversation(self, data, filename):
        filepath = os.path.join(self.history_dir, filename)
        if os.path.exists(filepath):
            shutil.copy2(filepath, filepath + ".bak")

        save_data = {
            "model": data.get("model", "deepseek-flash"),
            "settings": data.get("settings", {}),
            "system_prompt": "",
            "history": [],
        }

        current_turn = {}
        current_step = None

        for msg in data["messages"]:
            role = msg["role"]
            content = msg["content"]

            if role == "system":
                save_data["system_prompt"] = content

            elif role == "user":
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "image_url":
                            text_parts.append("[图片]")
                    content_str = "\n".join(text_parts)
                else:
                    content_str = content

                if content_str.startswith("[系统自动提示"):
                    if current_step is not None:
                        current_step["researcher"] = content_str
                else:
                    if current_turn:
                        save_data["history"].append(current_turn)
                    current_turn = {"user": content_str, "steps": []}
                    current_step = None

            elif role == "assistant":
                if "[SEARCH:" in content:
                    current_step = {"assistant_decision": content}
                    current_turn.setdefault("steps", []).append(current_step)
                else:
                    current_turn["assistant_final_reply"] = content
                    save_data["history"].append(current_turn)
                    current_turn = {}
                    current_step = None

        if current_turn:
            save_data["history"].append(current_turn)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

    def load_conversation(self, filename):
        filepath = os.path.join(self.history_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, list):
            return {"model": "deepseek-flash", "settings": {}, "messages": loaded}
        if "messages" in loaded and "history" not in loaded:
            return {
                "model": loaded.get("model", "deepseek-flash"),
                "settings": loaded.get("settings", {}),
                "messages": loaded["messages"],
            }

        flat = []
        if loaded.get("system_prompt"):
            flat.append({"role": "system", "content": loaded["system_prompt"]})

        for turn in loaded.get("history", []):
            if "user" in turn:
                flat.append({"role": "user", "content": turn["user"]})
            for step in turn.get("steps", []):
                if "assistant_decision" in step:
                    flat.append({"role": "assistant", "content": step["assistant_decision"]})
                if "researcher" in step:
                    flat.append({"role": "user", "content": step["researcher"]})
            if "assistant_final_reply" in turn:
                flat.append({"role": "assistant", "content": turn["assistant_final_reply"]})

        return {
            "model": loaded.get("model", "deepseek-flash"),
            "settings": loaded.get(
                "settings", {"web_search": False, "thinking": False, "temperature": 1.0}
            ),
            "messages": flat,
        }

    def list_history(self):
        self.ensure_history_dir()
        return sorted(f for f in os.listdir(self.history_dir) if f.endswith(".json"))

    # ---------- 文件工具 ----------
    def find_file(self, filename, valid_exts):
        ext = os.path.splitext(filename)[1].lower()
        if ext in valid_exts:
            full = os.path.join(self.base_dir, filename)
            return full if os.path.exists(full) else None
        for e in valid_exts:
            full = os.path.join(self.base_dir, filename + e)
            if os.path.exists(full):
                return full
        return None

    @staticmethod
    def read_text_file(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            try:
                with open(filepath, "r", encoding="gbk") as f:
                    return f.read()
            except Exception:
                return None
        except Exception:
            return None

    @staticmethod
    def image_to_base64(image_path):
        ext = os.path.splitext(image_path)[1].lower()
        if ext not in IMAGE_TYPES:
            return None, None
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8"), IMAGE_TYPES[ext]
        except Exception:
            return None, None

    @staticmethod
    def strip_old_images(messages):
        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                last_user_idx = i
                break

        result = []
        for i, msg in enumerate(messages):
            content = msg.get("content")
            if isinstance(content, list) and i != last_user_idx:
                parts = []
                for block in content:
                    if block.get("type") == "text":
                        parts.append(block["text"])
                    elif block.get("type") == "image_url":
                        parts.append("[之前发送的图片]")
                result.append({"role": msg["role"], "content": "\n".join(parts)})
            else:
                result.append(msg)
        return result

    # ---------- 菜单 ----------
    @staticmethod
    def choose_model():
        print("请选择模型：\n  1. deepseek-flash\n  2. deepseek-v4-pro")
        return MODELS.get(input("请输入序号（默认 1）：").strip(), MODELS["1"])

    @staticmethod
    def choose_system_prompt():
        print("系统提示词：\n  1. 默认\n  2. 自定义")
        if input("请输入序号（默认 1）：").strip() != "2":
            return DEFAULT_PROMPT
        return input("请输入系统提示词：").strip() or DEFAULT_PROMPT

    @staticmethod
    def choose_web_search():
        print("是否开启联网搜索？\n  1. 关闭（默认）\n  2. 开启")
        return input("请输入序号（默认 1）：").strip() == "2"

    @staticmethod
    def choose_thinking():
        print("是否开启深度思考？\n  1. 关闭（默认）\n  2. 开启")
        return input("请输入序号（默认 1）：").strip() == "2"

    @staticmethod
    def choose_temperature(thinking):
        if thinking:
            print("深度思考已开启，温度参数不生效。")
            return None
        val = input("请设置温度（0.0 - 2.0，默认 1.0）：").strip()
        try:
            t = float(val) if val else 1.0
            return t if 0.0 <= t <= 2.0 else 1.0
        except ValueError:
            return 1.0

    def choose_search_engine(self):
        print("首选搜索引擎：\n  1. DeepSeek 官方（默认，失败降级到 Tavily）\n  2. Tavily（失败降级到官方）")
        self.prefer_deepseek_search = input("请输入序号（默认 1）：").strip() != "2"
        self.save_state()
        return self.prefer_deepseek_search

    def new_chat(self):
        name = input("请输入本次对话的名字：").strip() or "未命名对话"
        for ch in ['/', ':', '*', '?', '"', '<', '>', '|']:
            name = name.replace(ch, "_")
        filename = f"{name}.json"

        thinking = self.choose_thinking()
        web = self.choose_web_search()
        if web:
            self.choose_search_engine()
        data = {
            "model": self.choose_model(),
            "messages": [{"role": "system", "content": self.choose_system_prompt()}],
            "settings": {
                "web_search": web,
                "thinking": thinking,
                "temperature": self.choose_temperature(thinking),
                "show_reasoning": True,
                "prefer_deepseek_search": self.prefer_deepseek_search,
                "deepseek_key": self.active.get("deepseek"),
                "tavily_key": self.active.get("tavily"),
            },
        }
        print(f"新对话已开始，存档文件：{filename}")
        return data, filename

    def resume_chat(self):
        files = self.list_history()
        if not files:
            print("没有历史对话。")
            return None, None
        print("历史对话列表：")
        for i, f in enumerate(files, 1):
            print(f"  {i}. {f[:-5]}")
        print("  0. 返回")
        choice = input("请输入序号：").strip()
        if choice == "0":
            return None, None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(files):
                filename = files[idx]
                data = self.load_conversation(filename)
                turns = sum(
                    1 for m in data.get("messages", [])
                    if m["role"] == "user" and not str(m.get("content", "")).startswith("[系统")
                )
                print(f"已恢复对话：{filename}（共 {turns} 轮）")
                return data, filename
        except Exception:
            pass
        return None, None

    def check_balance(self):
        api_key = self.require_key("deepseek")
        if not api_key:
            return
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        try:
            res = requests.get(BALANCE_URL, headers=headers, verify=False, timeout=15).json()
            infos = res.get("balance_infos", [])
            if not infos:
                print(f"没有取到余额信息：{res}")
                return
            for info in infos:
                print(f"余额：{info['total_balance']} {info['currency']}")
        except Exception as exc:
            print(f"查询出错：{exc}")

    # ---------- 对话 ----------
    def chat_loop(self, data, filename):
        settings = data.get("settings", {})
        web_on = settings.get("web_search", False)
        think_on = settings.get("thinking", False)
        show_reasoning = settings.get("show_reasoning", True)

        if "prefer_deepseek_search" in settings:
            self.prefer_deepseek_search = bool(settings["prefer_deepseek_search"])
        for kind in self.KINDS:
            saved = settings.get(f"{kind}_key")
            if isinstance(saved, str) and self.keys.has(saved):
                self.active[kind] = saved

        def show_status():
            engine = "DeepSeek 官方" if self.prefer_deepseek_search else "Tavily"
            print(
                f"  [当前] 联网搜索：{'开启' if web_on else '关闭'}  "
                f"深度思考：{'开启' if think_on else '关闭'}  "
                f"思考过程显示：{'开启' if show_reasoning else '关闭'}  "
                f"首选引擎：{engine}  "
                f"Key：{self.active.get('deepseek')}/{self.active.get('tavily')}"
            )

        print(f"\n对话已开始。当前模型：{data['model']}")
        show_status()
        print(
            "指令：/web 切联网 | /think 切思考 | /reason 切思考过程显示 | "
            "/search 切搜索源 | /keys 换 Key | /status 查状态 | quit 退出\n"
        )

        while True:
            user_input = input("你：").strip()

            if user_input.lower() == "quit":
                settings["prefer_deepseek_search"] = self.prefer_deepseek_search
                settings["deepseek_key"] = self.active.get("deepseek")
                settings["tavily_key"] = self.active.get("tavily")
                self.save_conversation(data, filename)
                print("已保存，再见。")
                return
            if user_input == "/web":
                web_on = not web_on
                settings["web_search"] = web_on
                show_status()
                continue
            if user_input == "/think":
                think_on = not think_on
                settings["thinking"] = think_on
                show_status()
                continue
            if user_input in ("/reason", "/os"):
                show_reasoning = not show_reasoning
                settings["show_reasoning"] = show_reasoning
                show_status()
                continue
            if user_input == "/search":
                self.prefer_deepseek_search = not self.prefer_deepseek_search
                self.save_state()
                settings["prefer_deepseek_search"] = self.prefer_deepseek_search
                show_status()
                continue
            if user_input == "/keys":
                self.manage_keys()
                show_status()
                continue
            if user_input == "/status":
                show_status()
                continue

            if user_input.endswith("\\"):
                lines = [user_input[:-1]]
                while True:
                    more = input()
                    if more.endswith("\\"):
                        lines.append(more[:-1])
                    else:
                        lines.append(more)
                        break
                user_input = "\n".join(lines)

            if user_input.startswith("/img "):
                rest = user_input[5:].strip()
                if " " in rest:
                    image_name, user_text = rest.split(" ", 1)
                else:
                    image_name, user_text = rest, "请描述这张图片"
                image_path = self.find_file(image_name.strip('"').strip("'"), IMAGE_EXTS)
                if not image_path:
                    print(f"找不到图片：{image_name}")
                    continue
                b64, mime = self.image_to_base64(image_path)
                if not b64:
                    print("图片读取失败。")
                    continue
                content = [
                    {"type": "text", "text": self.time_prefix() + user_text},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]
                data["messages"].append({"role": "user", "content": content})
                print(f"已加载图片：{os.path.basename(image_path)}")

            elif user_input.startswith("/txt "):
                rest = user_input[5:].strip()
                if " " in rest:
                    file_name, user_text = rest.split(" ", 1)
                else:
                    file_name, user_text = rest, "请阅读这个文件并回答"
                file_path = self.find_file(file_name.strip('"').strip("'"), TEXT_EXTS)
                if not file_path:
                    print(f"找不到文件：{file_name}")
                    continue
                content_text = self.read_text_file(file_path)
                if content_text is None:
                    print("文件读取失败（编码无法识别）。")
                    continue
                full_text = (
                    self.time_prefix()
                    + f"{user_text}\n\n===== 文件内容：{os.path.basename(file_path)} =====\n{content_text}"
                )
                data["messages"].append({"role": "user", "content": full_text})
                print(f"已加载文件：{os.path.basename(file_path)}")

            else:
                current_msg = self.time_prefix() + user_input
                if web_on:
                    current_msg += (
                        "\n\n[系统提示：当前已开启联网搜索。你拥有搜索权限。"
                        "如果需要查实时信息，请只输出格式：[SEARCH: 搜索关键词]，不要输出其他解释。"
                        "如果不需要搜索，请直接回答用户。]"
                    )
                data["messages"].append({"role": "user", "content": current_msg})

            search_count = 0
            limit_reached = False

            while True:
                api_key = self.require_key("deepseek")
                if not api_key:
                    self.save_conversation(data, filename)
                    break

                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": data["model"],
                    "messages": self.strip_old_images(data["messages"]),
                }

                if think_on:
                    payload["thinking"] = {"type": "enabled"}
                    payload["reasoning_effort"] = "high"
                else:
                    temp = settings.get("temperature")
                    if temp is not None:
                        payload["temperature"] = temp

                try:
                    response = requests.post(
                        URL, headers=headers, json=payload, verify=False, timeout=60
                    )
                    result = response.json()

                    if "choices" not in result:
                        print(f"接口出错：{result}")
                        self.save_conversation(data, filename)
                        break

                    msg_obj = result["choices"][0]["message"]
                    reply = msg_obj.get("content", "")
                    reasoning = msg_obj.get("reasoning_content", "")

                    if reasoning:
                        combined = f"【思考过程：\n{reasoning}\n】\n{'=' * 52}\n{reply}"
                        if show_reasoning:
                            print(f"\n[思考/os]\n{reasoning}\n{'=' * 52}")
                    else:
                        combined = reply

                    if limit_reached:
                        clean_reply = re.sub(r"\[SEARCH:\s*(.*?)\]", "", reply)
                        print(f"助手：{clean_reply}")
                        combined = (
                            f"【思考过程：\n{reasoning}\n】\n{'=' * 52}\n{clean_reply}"
                            if reasoning
                            else clean_reply
                        )
                        data["messages"].append({"role": "assistant", "content": combined})
                        self.save_conversation(data, filename)
                        break

                    data["messages"].append({"role": "assistant", "content": combined})

                    search_match = re.search(r"\[SEARCH:\s*(.*?)\]", reply, re.IGNORECASE)

                    if web_on and search_match:
                        keyword = search_match.group(1).strip()

                        if search_count >= self.max_searches:
                            print(f"已达最大搜索次数（{self.max_searches} 次），要求直接作答。")
                            limit_reached = True
                            data["messages"].append({
                                "role": "user",
                                "content": (
                                    "[系统提示：已达到最大搜索次数限制。"
                                    "请立刻根据现有信息给出最终回答，不要再请求搜索。]"
                                ),
                            })
                            continue

                        search_count += 1
                        print(f"搜索：{keyword}（第 {search_count} 次）")
                        search_result = self.web_search(keyword)
                        data["messages"].append({
                            "role": "user",
                            "content": (
                                f"[系统自动提示：后台已经为你执行了搜索。"
                                f"以下是关于“{keyword}”的搜索结果：\n{search_result}\n"
                                "请判断是否需要继续搜索，或者直接回答。]"
                            ),
                        })
                        self.save_conversation(data, filename)
                    else:
                        print(f"助手：{reply}")
                        self.save_conversation(data, filename)

                        usage = result.get("usage", {})
                        if usage:
                            hit = usage.get("prompt_cache_hit_tokens", 0)
                            miss = usage.get("prompt_cache_miss_tokens", 0)
                            print(
                                f"  本次消耗：输入 {usage.get('prompt_tokens', 0)}"
                                f"（缓存命中 {hit} / 未命中 {miss}）"
                                f" / 输出 {usage.get('completion_tokens', 0)}"
                                f" / 总计 {usage.get('total_tokens', 0)} tokens"
                            )
                        break

                except Exception as exc:
                    print(f"网络请求出错：{exc}")
                    self.save_conversation(data, filename)
                    break

    def run(self):
        while True:
            print("\n" + "=" * 52)
            print("hi deepseek")
            print("=" * 52)
            print("  1. 开始新对话")
            print("  2. 恢复历史对话")
            print("  3. 查询余额")
            print("  4. API Key 管理")
            print("  5. 退出程序")
            print("=" * 52)
            choice = input("请选择操作（1/2/3/4/5）：").strip()

            if choice == "1":
                data, filename = self.new_chat()
                self.chat_loop(data, filename)
            elif choice == "2":
                data, filename = self.resume_chat()
                if data and filename:
                    self.chat_loop(data, filename)
            elif choice == "3":
                self.check_balance()
            elif choice == "4":
                self.manage_keys()
            elif choice == "5":
                print("已退出。")
                break

def main():
    HideepSeek().run()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断，退出。")
