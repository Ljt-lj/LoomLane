from __future__ import annotations

from collections.abc import Callable

import pytest

from loomlane.models import SDL


@pytest.fixture
def legacy_workflow() -> Callable[[str], SDL]:
    def build(base_url: str = "https://api.openai.com/v1") -> SDL:
        return SDL.model_validate(
            {
                "sdl_version": "0.1",
                "workflow": {
                    "id": "content-review",
                    "name": "内容审查流水线",
                    "global_context": {
                        "text": (
                            "You are part of a content production system. "
                            "Be concise and concrete."
                        ),
                        "injection": "prefix",
                        "applies_to": "all",
                    },
                    "inputs": [
                        {
                            "name": "requirement",
                            "type": "TEXT",
                            "required": True,
                        }
                    ],
                    "outputs": [
                        {
                            "name": "approved_draft",
                            "agent": "drafter",
                            "port": "draft",
                        }
                    ],
                    "execution": {
                        "feedback_limit_default": 3,
                        "max_steps": 200,
                    },
                },
                "start": ["drafter"],
                "api_profiles": [
                    {
                        "id": "openai-main",
                        "name": "OpenAI Compatible",
                        "provider": "openai_compatible",
                        "base_url": base_url,
                        "model": "gpt-4o-mini",
                        "request": {
                            "timeout_seconds": 60,
                            "max_retries": 2,
                            "temperature": 0.2,
                        },
                        "capabilities": {
                            "function_calling": False,
                            "vision": False,
                            "long_context": False,
                            "structured_output": True,
                        },
                    }
                ],
                "agents": [
                    {
                        "id": "drafter",
                        "name": "内容起草",
                        "api_profile": "openai-main",
                        "system_prompt": (
                            "根据需求撰写初稿。如果存在 feedback，必须吸收反馈后重写。"
                            "只返回约定 JSON。"
                        ),
                        "inputs": [
                            {
                                "name": "requirement",
                                "type": "TEXT",
                                "required": True,
                            },
                            {
                                "name": "feedback",
                                "type": "TEXT",
                                "required": False,
                            },
                        ],
                        "outputs": [
                            {"name": "draft", "type": "TEXT"},
                            {
                                "name": "outline",
                                "type": "DATA",
                                "schema_hint": {"type": "object"},
                            },
                        ],
                        "json_output": {
                            "enabled": True,
                            "template": {
                                "draft": "完整的 Markdown 草稿文本",
                                "outline": {
                                    "sections": "章节标题字符串数组",
                                },
                            },
                        },
                    },
                    {
                        "id": "reviewer",
                        "name": "质量审查",
                        "api_profile": "openai-main",
                        "system_prompt": (
                            "审查初稿，指出问题并给出 0 到 1 的评分。只返回约定 JSON。"
                        ),
                        "inputs": [
                            {"name": "draft", "type": "TEXT", "required": True}
                        ],
                        "outputs": [
                            {"name": "review", "type": "TEXT"},
                            {
                                "name": "score",
                                "type": "DATA",
                                "schema_hint": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                },
                            },
                        ],
                        "json_output": {
                            "enabled": True,
                            "template": {
                                "review": "具体、可执行的审查意见",
                                "score": "0 到 1 之间的数字",
                            },
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "draft-to-review",
                        "from": "drafter",
                        "to": "reviewer",
                        "type": "PIPE",
                        "mappings": [{"from": "draft", "to": "draft"}],
                    },
                    {
                        "id": "review-feedback",
                        "from": "reviewer",
                        "to": "drafter",
                        "type": "FEEDBACK",
                        "mappings": [{"from": "review", "to": "feedback"}],
                        "condition": {
                            "op": "lt",
                            "left": "$.agents.reviewer.outputs.score",
                            "right": 0.8,
                        },
                        "guard": {"max_traversals": 3},
                    },
                ],
            }
        )

    return build
