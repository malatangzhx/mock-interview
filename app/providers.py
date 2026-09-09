"""Public provider defaults. All addresses/models remain editable in the UI."""

PROVIDER_PRESETS = {
    "minimax": {"label": "MiniMax", "base_url": "https://api.minimax.cn/v1", "model": "MiniMax-M2.5", "models": ["MiniMax-M2.5", "MiniMax-M2.7", "MiniMax-M3"], "wire_api": "chat"},
    "openai": {"label": "OpenAI 官方 · Chat", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "models": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"], "wire_api": "chat", "voice_mode": "native", "voice_model": "gpt-realtime-2.1"},
    "openai_responses": {"label": "OpenAI 官方 · Responses", "base_url": "https://api.openai.com/v1", "model": "gpt-5.5", "models": ["gpt-5.5"], "wire_api": "responses"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-flash", "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"], "wire_api": "chat", "voice_mode": "cascade", "voice_model": "浏览器实时转写 + DeepSeek + 系统自然音色"},
    "qwen": {"label": "通义千问 · 百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "models": ["qwen-plus", "qwen-flash", "qwen-max"], "wire_api": "chat", "voice_mode": "native", "voice_model": "qwen3.5-omni-flash-realtime", "realtime_url": "", "note": "文本 Key 和地址必须属于同一地域。原生语音还需填写百炼业务空间 WebRTC 地址，例如 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/webrtc/realtime。未填写时自动使用级联语音。"},
    "anthropic": {"label": "Claude · Anthropic", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-6", "models": ["claude-sonnet-4-6", "claude-opus-4-6"], "wire_api": "anthropic"},
    "gemini": {"label": "Gemini · Google", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash", "models": ["gemini-2.5-flash", "gemini-2.5-pro"], "wire_api": "chat"},
    "moonshot": {"label": "Kimi · 月之暗面", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2.6", "models": ["kimi-k2.6", "kimi-k3"], "wire_api": "chat"},
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.3", "models": ["glm-5.3", "glm-5.3-flash"], "wire_api": "chat"},
    "doubao": {"label": "豆包 · 火山方舟", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-seed-2-0-lite-260215", "models": ["doubao-seed-2-0-lite-260215"], "wire_api": "responses", "voice_mode": "cascade", "voice_model": "浏览器实时转写 + 豆包 + 系统自然音色", "note": "方舟 API Key 可用于自动级联语音。豆包端到端 S2S 使用独立的豆包语音 AppID、Access Key、App Key 和 Resource ID，不能与方舟单 Key 混用。"},
    "siliconflow": {"label": "硅基流动 · 多模型", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V4-Flash", "models": ["deepseek-ai/DeepSeek-V4-Flash"], "wire_api": "chat", "note": "模型 ID 需包含平台要求的组织前缀，可接入平台托管的其他文本模型。"},
    "apiznyl": {"label": "apiznyl · 第三方中转", "base_url": "https://apiznyl.com/v1", "model": "gpt-5.5", "models": ["gpt-5.5"], "wire_api": "auto", "note": "这是第三方服务，需要该站签发的密钥；支持哪些模型由该站决定。"},
    "custom": {"label": "自定义 / 其他兼容服务", "base_url": "", "model": "", "models": [], "wire_api": "auto", "note": "填写服务商提供的 API 地址和准确模型 ID，支持 Chat、Responses 和 Anthropic Messages。"},
}
