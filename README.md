# Voice2Text

按住一个键说话，松手就把你说的话转成文字、用 AI 润色好、自动粘贴到光标处。一个常驻桌面的本地语音输入工具。

## 功能

- **按住即说**：按住热键录音，松开自动转写
- **AI 润色**：转写结果交给 AI 修正错别字、补标点、纠正专有名词
- **自动粘贴**：润色后的文字直接出现在你光标所在的任何输入框
- **桌面悬浮球**：颜色显示状态（灰＝空闲 / 红＝录音 / 橙＝处理 / 紫＝出错），可拖动
- **历史面板**：单击小球查看所有历史，可重新粘贴任意一条
- **多 AI 后端**：Claude 订阅 / Anthropic API / OpenAI 兼容（DeepSeek 等）/ 不清洗
- **多 STT 引擎**：SenseVoice（默认 · 首次自动下载约 230MB）/ faster-whisper / 火山引擎云端
- **个性化术语表**：把高频专有名词写进 `glossary.txt`，识别更准

## 工作原理

```
按住热键 → 麦克风录音 → STT 本地转写
  → AI 清洗（修正错字 / 补标点 / 纠正术语）→ 自动粘贴到光标
```

语音识别在本地运行，不上传云端；AI 清洗的后端可在 `config.toml` 里切换。

## 系统要求

- Windows 10 / 11，或 macOS
- Python 3.11
- Node.js（`claude-sdk` 后端需要）
- 内存建议 16GB 以上

## 安装

**Windows：**

```powershell
git clone https://github.com/ct1021/voice2text.git
cd voice2text
.\install.ps1
```

**macOS：**

```bash
git clone https://github.com/ct1021/voice2text.git
cd voice2text
chmod +x install.sh run.sh
./install.sh
```

macOS 首次运行需在「系统设置 → 隐私与安全性 → 辅助功能」里授权运行的终端 / Python——全局热键依赖此权限。

首次启动时会自动下载语音模型（默认 SenseVoice，约 230MB）。

## 使用

1. 启动：Windows 双击 `start.bat`；macOS 运行 `./run.sh`
2. 桌面出现悬浮小球，把光标放到任何输入框
3. **按住热键** 说话，**松开** 结束
4. 等几秒，润色后的文字自动粘贴
5. **单击小球** 打开历史面板；**右键小球** 打开菜单

默认热键：Windows 为 CapsLock。**想换别的键** —— 右键小球 →「设置录音热键」，按一下你想用的键即可，即时生效（macOS 建议用右 Cmd / 右 Option，CapsLock 在 Mac 会切大写）。

## 配置

所有设置在 `config.toml`（首次启动自动从 `config.example.toml` 生成）。

**录音热键** —— 不必改文件，**右键小球 →「设置录音热键」**，按一下想用的键即可，立即生效并记住。也可直接改 `[hotkey] key`。Windows 用 CapsLock 即可；macOS 上 pynput 不抑制按键，需用按下无副作用的键（右 Cmd / 右 Option）。

**换 AI 后端** —— 没有 Claude 订阅？用 DeepSeek 等 OpenAI 兼容接口：

```toml
[ai]
backend = "openai-compatible"
```

API key 填在项目根目录的 `.env` 文件里（首次把 `.env.example` 复制成 `.env`，填入 `DEEPSEEK_API_KEY`）。

**换 STT 引擎** —— 默认就是 SenseVoice（中文又快又准，模型首次自动下载）。想用 faster-whisper 设 `[stt] backend = "faster-whisper"`（首次自动下载约 1.5GB）。

**个性化术语表** —— 编辑 `glossary.txt`，把你工作中的人名、产品名、技术栈加进去。

## SenseVoice 模型自动下载失败时

SenseVoice 是默认引擎，首次启动会自动下载模型。若因网络问题下载失败，可手动下：

1. 下载 sherpa-onnx 官方 SenseVoice int8 模型包
   `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2`（GitHub releases / asr-models）
2. 解压后把里面的 `model.int8.onnx` 和 `tokens.txt` 放进 `models/sensevoice/`
3. 重启即可；或把 `[stt] backend` 改成 `faster-whisper` 换另一个引擎

## 已知限制

- `claude-sdk` 后端每次清洗约 7–10 秒；用 API 后端更快
- 运行期间，作为热键的按键会被占用（Windows 上 CapsLock 不能切大写）
- macOS 支持为新增，作者主力在 Windows 测试，Mac 上如遇问题欢迎提 issue

## License

[MIT](LICENSE)
