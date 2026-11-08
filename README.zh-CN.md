# hideepseek

一个轻量的 DeepSeek API 终端客户端。

中文 · [English](README.md)

## 亮点

- **Windows 7 也能跑。** 只要 Python 3.8+——Win7 SP1 用户停在 Python 3.8 / 3.9 就行，
  不需要碰更高版本。
- **体积极小。** 第三方依赖只有两个（`requests`、`urllib3`），其余全是 Python 标准库。
- **两套搜索引擎，自动降级。** 默认先用 DeepSeek 官方搜索，失败自动切 Tavily；
  也可以反过来。对话里用 `/search` 随时切换。
- **密钥加密存放。** API Key 用 **Windows DPAPI** 加密后存进 `keys.json`，
  这个文件换台电脑、换个 Windows 账户都解不开。
- **支持多把 Key，对话中随时切换。** 命名成 `deepseek`、`deepseek-2`、`tavily`……
  然后用 `/keys` 切换。
- **本地 JSON 存档。** 每一轮按 `用户 → 步骤 → 回答` 存下来，多步搜索的过程不会被压平。
- **图片和文本文件直接进对话。** `/img <路径>`、`/txt <路径>`。
- **每次请求的 token 统计**，包括上下文缓存命中 / 未命中数量。

## 环境要求

- **Windows** —— 密钥加密用的是 Windows DPAPI（暂不支持 Linux / macOS）
- **Python 3.8+** —— Windows 7 SP1 请用 Python 3.8 或 3.9（3.10 起不再支持 Win7）
- 第三方包只有两个（都在 `requirements.txt` 里）：
  - `requests` —— 请求 DeepSeek 和 Tavily 接口
  - `urllib3` —— 关掉自签名证书的警告

## 安装

```bash
git clone https://github.com/sensei-314/hideepseek.git
cd hideepseek
pip install -r requirements.txt
```

`requirements.txt` 里只有两行——`requests` 和 `urllib3`——所以上面这条命令只会装这两个包。

## 用法

```bash
python main.py
```

对话里的指令：

| 指令 | 作用 |
| --- | --- |
| `/web` | 开关联网搜索 |
| `/think` | 开关深度思考 |
| `/reason` | 开关思考过程显示 |
| `/search` | 切换首选搜索引擎 |
| `/keys` | 添加 / 删除 / 切换 API Key |
| `/status` | 查看当前设置 |
| `/img <路径> [文字]` | 附带图片 |
| `/txt <路径> [文字]` | 附带文本文件 |
| `quit` | 保存并退出 |

## 配置

取密钥的顺序是：

1. **环境变量** —— `DEEPSEEK_API_KEY`、`TAVILY_API_KEY`。
   适合 CI、脚本，或者一台你不想往上写东西的机器。
2. **`keys.json`** —— 用 **Windows DPAPI** 加密；密文只有「本机的当前 Windows 用户」
   能解开。第一次运行时会问你填，之后不再打扰。

不想往磁盘上落任何东西，就用环境变量；日常本地用 `keys.json`。
**环境变量优先。**

`keys.json` 和 `state.json` 已经写进 `.gitignore`——**绝对不要提交它们**。

## 理念

> 工具是给人用的。但工具里跑着的那段「会说话的东西」，也值得被认真对待。
## 许可证

MIT
