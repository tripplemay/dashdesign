# 图片修改语义 Agent

## 工作流

桌面端「图片修改」默认执行：原图快照 → 多模态 Agent 理解用户要求 → 结构化校验 → 编译图片提示词 → Images Edit API。

- Agent 同时读取原图预览、原始要求、尺寸和已有澄清回答。转写成功后自动出图，无需再次确认提示词。
- 「AI 理解结果」可展开查看中文摘要和最终提示词，不覆盖用户原始要求。
- 目标不明确、要求冲突或缺少必要文字时，显示一个中文问题；回答后点击「补充并继续」。仍有歧义可再次询问，不自动循环请求。
- Agent 失败、超时或结构校验失败即停止，不直接提交原始要求，不调用图片模型。可手动重试转写。
- 支持多项修改及明确要求的全局风格、布局变化；未指定的内容应保留。文字替换按用户原文校验，中文、数字、价格和标点不翻译。
- 这是模型辅助编辑，不保证语义判断绝对正确、逐像素保真或最终文字完全准确，成品仍需视觉验收。

仅升级图片编辑，不改变整幅海报基线、背景生成等工作流。旧 CLI 默认行为不变，需显式启用 Agent。

## 配置

`edit_agent_model` 为独立的多模态文本模型：云端非空值 > 本机非空值 > 当前有效 `baseline_model`。与图片模型共用 API 地址和凭据，图片模型仍单独使用 `image_model`。

Agent 模型必须兼容 `/chat/completions` 的图片输入和 `response_format: json_object`。请求超时为 90 秒，无自动重试。不支持该协议或未获授权会失败停止，不静默切换模型。

云端管理员设置和本机设置均提供该字段。升级顺序为后端、桌面端；JSON 配置无需数据库迁移。旧客户端省略该字段时不会清空云端已配置值。尚未配置独立值时沿用文本模型，但仍需验证其多模态能力。

## CLI

设置 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 后执行：

```sh
python scripts/gpt_image_rebuild.py poster.png \
  --api-mode edit --optimize-prompt --agent-model YOUR_VISION_MODEL \
  --model YOUR_IMAGE_MODEL --width-cm 80 --height-cm 180 \
  --description '把第四个板块的标题和正文左边缘与前三个板块对齐' \
  --output-dir workflow_samples/agent_edits --execute
```

不加 `--execute` 只保存输入快照和待转写任务，零模型调用，不会产生有效编辑提示词。保留 `--agent-model`，确保任务恢复时模型固定。

遇到 `needs_input`（退出码 4），读取任务包内 `edit_state.json` 的 `question` 和 `revision`，准备 UTF-8 JSON：

```json
{"revision": 1, "answer": "只修改第四个板块，前三个板块保持不变。"}
```

```sh
python scripts/gpt_image_rebuild.py --api-mode edit \
  --resume-package workflow_samples/agent_edits/poster_agent_edit \
  --answer-file answer.json --execute
```

`prepared` 或 `agent_failed` 状态的任务可省略 `--answer-file` 恢复。恢复时使用任务包已保存的原图、尺寸、原始要求、模型；若需改动这些输入，应新建任务。桌面端改动输入会取消当前澄清绑定。

## 可追溯产物和防重复机制

- `input/source.*`：冻结原图；每次恢复校验 SHA-256，重新生成 Agent 预览。
- `edit_state.json`：版本、阶段、修订号、问题、回答历史、原图哈希和模型；不保存凭据。
- `agent_result_###.json` / `agent_result.json`：历次与最新结构化解释或脱敏错误。
- `prompt.md` / `image_edit_request.json`：最终提示词和图片请求参数。
- `generation_record.json` / `status.json`：模型与最终结果；Agent 成功不计为图片成功。
- `gpt_image_edit_master.png`：成功编辑的主图。

同一任务有独占锁，旧修订号回答拒绝执行。图片请求开始前先落盘 `image_request_started`，此后无论成功、失败或超时都不能恢复为第二次图片请求。用户取消或强制终止后不要盲目重发，上游可能已经执行或计费。强制终止遗留锁时应先确认旧进程结束及上游状态，再新建任务；程序不会自动清理锁或重试。

CLI 支持进程重启后恢复；桌面端当前只在本次会话内提供澄清续跑入口，不自动扫描历史待处理任务。
