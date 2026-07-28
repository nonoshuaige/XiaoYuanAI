from __future__ import annotations

import json
import unittest

import httpx
from langchain_core.messages import AIMessage

from model_audit import (
    capture_model_call,
    create_audited_http_client,
    serialize_ai_message,
)


class ModelAuditTests(unittest.TestCase):
    def test_captures_exact_provider_response_and_request_ids(self):
        raw_body = json.dumps(
            {
                "id": "chatcmpl-provider-response",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "完整回复",
                            "reasoning_content": "推理字段",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=raw_body,
                headers=[
                    ("content-type", "application/json"),
                    ("x-request-id", "provider-request-123"),
                    ("x-extra", "first"),
                    ("x-extra", "second"),
                ],
                request=request,
            )

        client = create_audited_http_client(
            "test-provider",
            transport=httpx.MockTransport(handler),
        )
        with capture_model_call("test-model") as capture:
            response = client.post(
                "https://provider.example/v1/chat/completions",
                json={"model": "test-model"},
            )
        client.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(capture.provider_responses), 1)
        provider_response = capture.provider_responses[0]
        self.assertEqual(provider_response["provider_id"], "test-provider")
        self.assertEqual(provider_response["status_code"], 200)
        self.assertEqual(
            provider_response["request_id"],
            "provider-request-123",
        )
        self.assertEqual(
            provider_response["response_id"],
            "chatcmpl-provider-response",
        )
        self.assertEqual(provider_response["raw_body"], raw_body)
        self.assertEqual(
            provider_response["json_body"]["choices"][0]["message"][
                "reasoning_content"
            ],
            "推理字段",
        )
        self.assertEqual(
            [
                header
                for header in provider_response["headers"]
                if header[0] == "x-extra"
            ],
            [["x-extra", "first"], ["x-extra", "second"]],
        )

    def test_serializes_every_ai_message_field(self):
        message = AIMessage(
            content=[{"type": "text", "text": "结构化回复"}],
            additional_kwargs={"reasoning_content": "模型推理"},
            response_metadata={"finish_reason": "length"},
            id="lc-message-1",
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
            },
        )

        serialized = serialize_ai_message(message)

        self.assertEqual(serialized["content"][0]["text"], "结构化回复")
        self.assertEqual(
            serialized["additional_kwargs"]["reasoning_content"],
            "模型推理",
        )
        self.assertEqual(
            serialized["response_metadata"]["finish_reason"],
            "length",
        )
        self.assertEqual(serialized["usage_metadata"]["output_tokens"], 20)
        self.assertEqual(serialized["id"], "lc-message-1")


if __name__ == "__main__":
    unittest.main()
