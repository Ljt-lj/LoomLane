from __future__ import annotations

AGENT = {
  "id": "reviewer",
  "name": "质量审查",
  "api_profile": "openai-main",
  "system_prompt": "审查初稿，指出问题并给出 0 到 1 的评分。只返回约定 JSON。",
  "inputs": [
    {
      "name": "draft",
      "type": "TEXT",
      "required": true
    }
  ],
  "outputs": [
    {
      "name": "review",
      "type": "TEXT",
      "required": true
    },
    {
      "name": "score",
      "type": "DATA",
      "required": true,
      "schema_hint": {
        "type": "number",
        "minimum": 0,
        "maximum": 1
      }
    }
  ],
  "limits": {}
}
