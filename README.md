# Voice2Text

按住一个键说话，松手就把你说的话转成文字、用 AI 润色好、自动粘贴到光标处。一个常驻桌面的本地语音输入小工具。

## 功能

- **按住即说**：按住 CapsLock 录音，松开自动转写
- **AI 润色**：转写结果交给 Claude 修正错别字、补标点、纠正专有名词
- **自动粘贴**：润色后的文字直接出现在你光标所在的任何输入框
- **桌面悬浮球**：常驻桌面的小球，颜色显示状态（灰＝空闲 / 红＝录音 / 橙＝处理）
- **历史面板**：单击小球查看所有历史记录，可重新粘贴任意一条
- **个性化术语表**：把你的高频专有名词写进 `glossary.txt`，识别更准

## 工作原理

```
按住 CapsLock → 麦克风录音 → faster-whisper 本地转写
  → Claude 清洗（修正错字 / 补标点 / 纠正术语）→ 自动粘贴到光标
```

语音识别在本地运行，不上传云端；AI 清洗通过 Claude Agent SDK 调用，走你本机的 Claude 订阅。

## 系统要求

- Windows 10 / 11
- Python 3.11
- [uv](https://docs.astral.sh/uv/) — Python 包管理器
- Node.js — Claude Agent SDK 依赖
- 已安装并登录 Claude Code（AI 清洗走你的订阅额度）
- 内存建议 16GB 以上（medium 模型常驻约 2GB）

## 安装

```powershell
# 1. 克隆仓库
git clone https://github.com/<your-name>/voice2text.git
cd voice2text

# 2. 建虚拟环境并装依赖
uv venv --python 3.11
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt

# 3. 准备术语表（可选但推荐）
copy glossary.example.txt glossary.txt
# 然后编辑 glossary.txt，加入你的常用专有名词

# 4. 启动
.\run.ps1
```

首次启动会自动下载 faster-whisper medium 模型（约 1.5GB），请耐心等待。

## 使用

1. 启动后桌面右下角出现一个悬浮小球
2. 把光标放到任何输入框
3. **按住 CapsLock** 说话，**松开** 结束
4. 等几秒，润色后的文字自动粘贴到光标处
5. **单击小球** 打开历史面板，可回看和重新粘贴
6. **右键小球**：显示历史 / 打开日志 / 打开文件夹 / 退出

## 配置

- `glossary.txt` — 你的个性化术语表（已被 `.gitignore` 排除，不会上传）
- 想换录音热键、STT 模型大小，编辑 `voice2text.py` 顶部的常量

## 已知限制

- AI 清洗每次约 7–10 秒（Claude Agent SDK 启动开销）
- 脚本运行期间 CapsLock 被用作录音键，不能切大写
- 目前仅在 Windows 上测试过

## License

[MIT](LICENSE)
