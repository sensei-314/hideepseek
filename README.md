# hideepseek

A lightweight terminal client for the DeepSeek API.

[中文说明](README.zh-CN.md) · English

## Highlights

- **Runs on Windows 7.** Only needs Python 3.8+ — Windows 7 SP1 users can stay on
  Python 3.8 or 3.9, and never touch anything newer.
- **Tiny footprint.** Two third-party packages in total (`requests`, `urllib3`);
  everything else is the Python standard library.
- **Two search backends with automatic fallback.** DeepSeek Search first, Tavily
  if that fails — or the other way round. Switchable with `/search`.
- **Keys encrypted at rest.** API keys are encrypted with **Windows DPAPI** and
  stored in `keys.json`; that file is useless on another machine or another
  Windows account.
- **Multiple keys, switchable mid-conversation.** Name them `deepseek`,
  `deepseek-2`, `tavily`, … and switch with `/keys`.
- **Local JSON archive.** Every turn is stored as `user → steps → assistant reply`,
  so multi-step searches are preserved instead of being flattened.
- **Images and text files in the prompt.** `/img <path>` and `/txt <path>`.
- **Per-request token accounting,** including prompt cache hit / miss counts.

## Requirements

- **Windows** — key encryption uses Windows DPAPI (Linux / macOS are not supported yet)
- **Python 3.8+** — Windows 7 SP1: use Python 3.8 or 3.9 (3.10+ dropped Win7 support)
- Third-party packages (only two, listed in `requirements.txt`):
  - `requests` — HTTP calls to the DeepSeek and Tavily APIs
  - `urllib3` — disables the self-signed-certificate warning

## Install

```bash
git clone https://github.com/sensei-314/hideepseek.git
cd hideepseek
pip install -r requirements.txt
```

`requirements.txt` contains exactly two lines — `requests` and `urllib3` — so the
command above installs only those two packages.

## Usage

```bash
python main.py
```

Commands inside a conversation:

| Command | Effect |
| --- | --- |
| `/web` | toggle web search |
| `/think` | toggle deep thinking |
| `/reason` | toggle showing the reasoning trace |
| `/search` | switch the preferred search backend |
| `/keys` | add / remove / switch API keys |
| `/status` | show current settings |
| `/img <path> [text]` | attach an image |
| `/txt <path> [text]` | attach a text file |
| `quit` | save and exit |

## Configuration

Keys are looked up in this order:

1. **Environment variables** — `DEEPSEEK_API_KEY` and `TAVILY_API_KEY`.
   Handy for CI, scripts, or a machine you don't want to write anything on.
2. **`keys.json`** — encrypted with **Windows DPAPI**; only the current Windows
   user on this machine can decrypt it. The program asks for the keys on first
   run, then never again.

Use environment variables when nothing should be written to disk; use `keys.json`
for everyday local use. Whatever is in the environment wins.

`keys.json` and `state.json` are listed in `.gitignore` — **never commit them**.

## Creed

> Tools are made for people. But the thing that talks inside a tool still
> deserves to be taken seriously.
## License

MIT
