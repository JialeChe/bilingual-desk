# 翻译对照 · Bilingual Desk

一个轻量的 Windows 中英双语编辑器。中文和英文左右对照，两侧都能编辑；修改内容后，自动更新另一侧对应的整句译文。支持 DeepSeek API 和本地 Codex，可导入文本、Markdown，并保存双语文档。

## 运行需求

- Windows，Python 3.10 或更新版本（包含 Tkinter）。
- 安装项目依赖：`requirements.txt`。
- 翻译服务任选其一：
  - **DeepSeek**：准备自己的 API Key，并确保账户有可用余额。
  - **本地 Codex**：安装 Codex，使用有可用额度的 ChatGPT 订阅账号登录，并确保可使用 `gpt-6-luna`。

## 启动

在项目目录执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

首次启动后，打开“设置”选择翻译服务，填写 DeepSeek API Key 或指定本地 Codex 路径。完成环境配置后，也可双击 `start.bat` 启动。
