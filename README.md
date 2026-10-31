# 翻译对照

一个给自己和朋友使用的轻量 Windows 双语编辑器。左侧中文，右侧英文，两侧都可编辑；修改任意一侧后，通过 DeepSeek 官方 API 或本地 Codex Luna 更新另一侧对应内容。界面使用 Python 自带的 Tkinter / ttk。

## 运行

需要 Windows Python 3.10+。DeepSeek 模式需要 DeepSeek API Key 和 API 余额；本地 Codex 模式需要已用 ChatGPT 订阅登录的 Codex。使用 DeepSeek 时无需安装 Codex。

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

也可以双击 `start.bat`。如自动查找不到 Codex，在“设置”中选择 `codex.exe`。本项目不打包或重新分发 Codex 本体，也不复制登录凭据；每个使用者使用自己的本地登录和额度。

检查连接：

```powershell
.\.venv\Scripts\python.exe run.py --check-codex
```

## 使用

默认引擎为 DeepSeek API · Flash。点击左上角常驻的“设置”，填写 DeepSeek API Key，点击“保存”；也可手动选择本地 Codex，已保存的引擎选择会在重启后保留。API 模式无需连接步骤，翻译时直接发送请求，不预先请求模型列表。密钥在 Windows 上通过当前用户的 DPAPI 加密后保存到 `data/deepseek_key.bin`，不会写入普通设置文件、文档或测速日志。也可以留空并使用 `DEEPSEEK_API_KEY` 环境变量。更换 Windows 账号或电脑后需要重新填写密钥。

主界面没有连接按钮。选择本地 Codex Luna 时，启动程序或切换引擎后自动尝试连接；失败后可点击“重试翻译”。“自动翻译”开关直接显示在主界面，设置中也可调整，默认开启；关闭后使用“开始翻译 / 更新译文”或 `Ctrl+Enter` 手动更新。只修改这个开关会保留当前引擎和会话。首次打开的空白页提供输入提示和翻译设置入口。

翻译时按钮变为“取消翻译”，状态栏显示当前阶段、已等待秒数，以及是否有新修改等待更新；活动指示条不代表完成百分比。取消会停止当前请求并保留原文、现有译文，取消后的返回结果不会写入文档。取消不会自动重试同一版本；继续编辑、手动更新或重新开启自动翻译可继续。DeepSeek 的网络等待和流式接收均可取消，仍复用 HTTP 连接。取消不保证退回服务方已经消耗的额度。

DeepSeek 使用官方 `https://api.deepseek.com` 的 `deepseek-flash`，每次请求明确传入 `thinking.type=disabled` 和 `reasoning_effort=none` 来关闭思考；不提供高强度思考选项。[DeepSeek 官方接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)支持关闭思考，以及开启时的 `low/high/max` 强度。本项目优先使用关闭思考的设置。

HTTPX 连接会复用，流式接收完成后才校验并提交译文。修改后发送涉及的完整句子，包括修改前后文本和对应的现有译文；模型返回整句译文，程序按句子编号替换。切换 Codex / DeepSeek 保留现有中英文和对应关系，后续编辑继续按整句更新。

- 在左侧输入中文，或在右侧输入英文，默认停止输入 800 毫秒后向另一侧同步。翻译保留语义和语气，包括俚语、粗口和成人表达。
- 两侧修改不同句子时，程序依次完成两个方向的更新，保留另一侧尚未处理的手动修改。同一句两侧均有修改时，暂停同步并显示“以中文为准 / 以英文为准”；选择前先自动备份，仅冲突句子使用所选一侧的版本。首次建立对应关系前两栏都有内容，也需明确选择。
- 程序检测明显照抄的外语句子并拒绝将其标为翻译成功，避免静默漏译；保留必要的专有名词、缩写、代码和链接。旧文档存在此类漏译时，可直接点击“翻译 / 更新”修复，不必改动原文。
- 首次翻译建立短句 / 分句的中英文对应关系。大文档按最多 1800 字符或 60 个片段分批翻译，显示已完成批数；每批校验完整性，全部通过后一次提交。中文译英文时自动补齐片段间所需空格，保留换行及代码。
- 失败或取消后，同一文档内容不变时重试会复用本次运行已校验的批次；关闭程序、切换引擎或改变内容后重新分批处理。设置里的等待时间按每批计算，整份文档可能需要数分钟。
- 纯空白片段不发送翻译，非空片段两端的空格、缩进和换行由程序保留并恢复；英文单词之间的空格仍用于理解语义。只增删空格、制表符或换行时，本地同步布局、保持译文内容，不调用模型。混合修改时，只有内容变化的片段可被模型修订。
- 后续修改按整句更新另一侧：即使只改一个词，也发送修改涉及的完整句子并接收整句译文。旧文档中按逗号、分号或 160 字符拆分的片段会合并到完整句子，普通换行不会截断同一段内的句子。跨句合并、拆句或原有对应关系较粗时，更新范围包含相关的完整句组。
- 完整删除任意一侧的句子，由程序删除另一侧对应内容，无需请求翻译服务；删除部分分句时重新翻译剩余整句。
- 两侧均可编辑和撤销。浅蓝色标记待更新内容及新更新的译文。顶部显示同步状态及待更新方向，失败和取消后可直接重试。
- 中文在左，英文在右；左右都是连续文本区，顶部各保留一个标题，没有逐段编号和独立输入框。已有对应关系的段落采用相同显示高度，较短侧补显示留白，两侧同步滚动；编辑、调整字号和窗口宽度后自动重新对齐。留白不进入复制、保存或导出内容。段内换行位置可以不同；未同步的换行无法单独配对时，先与相邻内容合并对齐。
- 两侧支持 Markdown 源文编辑：标题、列表、引用、加粗和代码使用轻量样式显示，原始标记仍可直接编辑。支持全文选择、跨段删除 / 粘贴和原生撤销；翻译回包通过局部替换更新控件，保留光标、选区和撤销记录。
- 后端识别 Markdown 块结构，先匹配完整句子再分析句内差异；界面只应用样式和显示结果，不插入虚拟换行或改变原文。
- 支持 UTF-8 文本 / Markdown 导入、JSON 文档保存、草稿恢复；“文档”菜单提供最近打开的 8 个文件和可预览的备份恢复窗口。重启恢复草稿时也恢复原 JSON 保存路径，继续保存无需重新选位置。底部独立区分草稿保存状态和原文件是否有未保存修改。
- 主界面可一键复制英文；“文档”菜单可分别复制、导出中文或英文 TXT / Markdown。有未同步修改时，复制和导出会提示。
- `Ctrl+A` 全选当前栏，`Ctrl+Z` 撤销当前栏修改，`Ctrl+Y` 重做。
- `Ctrl+S` 保存，`Ctrl+O` 打开，`Ctrl+N` 新建，`Ctrl+Enter` 翻译 / 更新。
- `Ctrl+Shift+L` 打开排查日志窗口，翻译中也可使用。支持实时查看、只看错误、复制全部和导出文本；`Esc` 关闭日志窗口。
- 白底阅读界面、较大的衬线正文、宽松留白和中间细分隔线；只有右侧显示滚动条，仍可在两栏内用滚轮联动滚动。顶栏“文档”菜单提供新建、保存、翻译设置和排查日志。
- 段落末尾增加显示间距，列表保持紧凑；这是样式，不会往文档中添加横线或编号。
- 顶栏放大镜或 `Ctrl+F` 查找两侧文字，`Enter` / `Shift+Enter` 跳到下一处 / 上一处，`Esc` 关闭查找。`Aa` 可调整正文大小，字号会在重启后保留。

排查日志自动写入本地 `data/debug.jsonl`，单文件上限约 4 MB，另保留一份 `debug.jsonl.1`。窗口展示本次运行的最近 500 条记录，包括请求句子、模型原始返回、耗时、错误堆栈和整句校验结果；缺句或额外返回会记录预期及实际句子编号。日志文件写入失败不影响翻译，窗口仍支持复制、导出。日志不记录认证请求或请求头，并隐藏已配置的 API Key；日志含原文和译文，分享前请检查内容。重启前的记录保留在日志文件中。

导入新的文本 / Markdown 文档时，默认保留原始排版并直接翻译。在设置中开启“导入时自动整理段落（可撤销）”后，会先调用当前引擎分析整体段落结构，再首次翻译。DeepSeek 使用关闭思考的 API；Codex 使用同一份文档会话。模型只能选择已有句段边界，程序按选择补充段落换行，保留原有文字、空格和换行，不改写或删除原文。代码块和行内代码不允许被拆开。整理作为一次可撤销的编辑应用，不清空已有撤销记录，也不把光标重置到开头。关闭自动更新时，手动点击翻译也会完成这两阶段。结构完成后首次翻译失败，可直接重试翻译；草稿保存初始化状态，重启也不必重复分析。翻译期间的过期结果不会覆盖新输入。已有译文的 JSON 文档继续局部更新。

文档保留纯文本和 Markdown 源文，基础样式由原生 Tk 文本控件显示。句段边界的空白会本地同步；在已有句子内部插入换行且缺少更细对应关系时，保留已有中文和配对，不猜测中文字符的切分位置。

每份文档使用一个独立 Codex 会话，后续更新复用该会话；切换回文档或重启程序后恢复原会话。关联仅在本地 `codex_sessions.json` 保存文档 ID 和会话 ID，翻译历史由本地 Codex 管理。只有会话确实不存在时才重新创建，认证、额度或超时错误不会另开会话绕过。

如果出现“会话被其他进程占用”，同一个会话可能已被另一个翻译实例或 Codex 桌面加载。可关闭占用的会话后重试，或使用错误发生时显示的“重建会话”按钮，为当前文档创建新的翻译会话。该操作只替换会话关联，旧会话不会删除，英文、现有中文和对应关系保持不变；后续编辑继续按整句更新。

模型固定为 `gpt-6-luna`，推理强度为 `low`。复用会话减少重复创建和装载，但实际响应时间仍受模型服务、网络和历史长度影响。

### 后续更新速度

“设置”中的“后续局部更新使用 Fast 模式”默认关闭，开启后仅局部更新请求使用账号模型列表提供的 Fast 档位；首次翻译继续使用标准速度。当前本地模型列表将 Luna Fast 标注为 `1.5x speed`，实际端到端耗时需测量。[官方 OpenAI 文档](https://learn.chatgpt.com/docs/agent-configuration/speed)说明，支持的模型在 Fast 模式下按普通模式的 2.5 倍消耗包含的订阅额度；购买的 credits 按 2 倍计费。

程序显示本次更新的总耗时，并在 `data/translation_timings.jsonl` 记录连接、会话装载、提交、等待首个输出、生成总耗时和连接重试次数。记录不含文档 ID、英文、中文或登录信息。首个输出时间包含网络等待及模型处理，不能单凭它区分服务排队和模型推理。JSONL 中 `outcome=completed` 表示引擎已返回合法格式，译文仍须通过完整性和版本校验才会写入。

Fast 无法消除网络重连的等待，也不保证达到实时更新。可在设置中切换到 DeepSeek API；API 模式同样显示总耗时，测速记录中的 `thinking=disabled` 和 `effort=none` 对应关闭思考的设置。

## 局部修改约束

两侧相对上次同步版本的差异由程序计算，并扩展到涉及的完整句子。引擎收到每句的 `segment_id`、`source_before`、`source_after` 和 `target_text`，仅返回 `translations: [{segment_id, target}]`。程序按已知对应关系替换整句，无需模型提供字符插入位置或前后锚点。检查句子编号完整且无重复、译文非空、文档版本及两侧快照后，再一次写入。任何一侧出现新编辑，旧请求的结果都会被忽略。

模型缺句、返回空译文、返回额外句子或返回过期版本时，保留两侧文本并显示原因。一次修改可以跨多个句子和段落，无需人为拆成小修改。整句删除直接移除对应译文，纯排版改动仍在本地处理；删除与重写混合时只请求模型翻译仍有内容的受影响句子。

Luna 使用本地 `codex app-server` 的 ChatGPT 订阅认证，会消耗 Codex 额度。程序拒绝 API Key 登录和其他模型的自动回退。对翻译子进程禁用不相关的编码工具 / MCP 服务，不改用户的全局 Codex 配置。连接协议遵循 [官方 Codex app-server 文档](https://learn.chatgpt.com/docs/app-server)。

## 结构

```text
ui/                  桌面窗口、编辑器、设置及日志弹窗
backend/diagnostics.py  本地日志、脱敏和文件轮转
backend/models.py    所有引擎共用的请求、返回和文档结构
backend/bilingual.py  双向差异、冲突检测及双侧快照校验
backend/batching.py   首次翻译分批、完整性校验与内存续传
backend/sentences.py  整句范围、完整译文校验及按句替换
backend/translation.py  初始化、差异投影及旧单方向服务兼容
backend/layout.py     空白与内容分离、本地布局同步
backend/structure.py  导入结构初始化校验及段落换行
backend/diff.py       完整句子优先的差异定位
backend/markdown.py   无损 Markdown 结构和样式分析
backend/paragraphs.py  不修改文档的中英文段落配对
backend/storage.py   本地 JSON 保存
backend/credentials.py  Windows 加密密钥保存
backend/jobs.py      后台任务与主线程结果队列
backend/providers/base.py   TranslationEngine 统一父类
backend/providers/codex.py  本地 Codex Luna 实现
backend/providers/deepseek.py  DeepSeek 官方 API 实现
backend/providers/protocol.py  两个引擎共用的提示词、JSON 格式及解析
backend/providers/rpc.py    stdio JSON-RPC 连接
backend/providers/sessions.py  文档与 Codex 会话关联的保存和恢复
tests/               增量修订、过期结果和连接协议测试
run.py               应用入口
```

两个引擎都继承 `TranslationEngine`，复用 `TranslationRequest` / `TranslationResponse` 和整句校验。DeepSeek 直接使用已有 HTTPX，不依赖 OpenAI SDK；本地 Codex 路径不使用 HTTPX。

开发时，文档与设置放在 `data/`；打包后放在用户的 `%LOCALAPPDATA%\BilingualDesktop`。新建或打开文档前会在 `data/recovery/` 保存当前文档副本。JSON 保存格式为 v2，包含两侧未同步编辑；可读取旧版 v1 文档，内部 `source` 仍表示英文、`target` 表示中文。`data/` 和 `.venv/` 不提交 Git。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

自动测试使用标准库 `unittest`，不调用真实模型。包括双向编辑、冲突选择、过期回包、10 万字符以上文档的双向分批初始化、缺失片段、取消及重试续传。

整句更新的合成文本真实接口检查：`packaging/verify_sentence_updates.py`；可用 `--data-dir` 指定保存密钥的应用数据目录。验证中英两个方向、句末增加分句、旧大文档兼容及无关句子保留，结果写入 `build/sentence_updates/report.json`。

真实接口验证（使用项目 `data/deepseek_key.bin` 中的密钥，会消耗 API 额度）：

```powershell
.\.venv\Scripts\python.exe packaging\verify_large_document.py
```

脚本使用 80 节合成中文文本，检查每个片段、章节标记、英文空格与后续双向修改，结果写入 `build/large_document/`。测试覆盖完整返回和基本同步正确性，不替代逐句人工译文审校。

## Windows 打包

在 Windows 64 位环境中安装运行和打包依赖，再执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\build.ps1
```

生成 `dist/翻译对照.exe` 和包含使用说明的 `dist/翻译对照-Windows-x64.zip`。这是无需安装 Python 的单文件程序，不包含开发目录的草稿、日志、密钥或 Codex 本体。打包版使用 `%LOCALAPPDATA%\BilingualDesktop` 保存用户数据；开发版文档可先保存为 JSON，再在打包版打开。DeepSeek 使用者填写自己的 API Key；本地 Codex 使用者安装并登录自己的 Codex。
