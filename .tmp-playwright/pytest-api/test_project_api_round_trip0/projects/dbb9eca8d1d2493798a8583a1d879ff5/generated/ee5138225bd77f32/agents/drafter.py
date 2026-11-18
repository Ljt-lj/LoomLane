from __future__ import annotations

AGENT = {
  "id": "drafter",
  "name": "内容起草",
  "api_profile": "openai-main",
  "system_prompt": "根据需求撰写初稿。如果存在 feedback，必须吸收反馈后重写。只返回约定 JSON。",
  "inputs": [
    {
      "name": "requirement",
      "type": "TEXT",
      "required": true
    },
    {
      "name": "feedback",
      "type": "TEXT",
      "required": false
    }
  ],
  "outputs": [
    {
      "name": "draft",
      "type": "TEXT",
      "required": true
    },
    {
      "name": "outline",
      "type": "DATA",
      "required": true,
      "schema_hint": {
        "type": "object"
      }
    }
  ],
  "limits": {}
}
