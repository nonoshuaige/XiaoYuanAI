from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.context_window import (
    aggregate_message_usage,
    plan_context,
    resolve_request_usage,
    validate_context_window_tokens,
)


class ContextWindowTests(unittest.TestCase):
    def test_only_supported_context_windows_are_accepted(self):
        self.assertEqual(validate_context_window_tokens(8_192), 8_192)
        self.assertEqual(validate_context_window_tokens(None), 16_384)
        with self.assertRaisesRegex(ValueError, "上下文限制只支持"):
            validate_context_window_tokens(12_345)

    def test_oldest_complete_rounds_are_trimmed_first(self):
        oldest = [
            HumanMessage(content="最旧用户" + "甲" * 2_500),
            AIMessage(content="最旧助手" + "乙" * 2_500),
        ]
        middle = [
            HumanMessage(content="中间用户" + "丙" * 2_500),
            AIMessage(content="中间助手" + "丁" * 2_500),
        ]
        newest = [
            HumanMessage(content="最新用户" + "戊" * 2_500),
            AIMessage(content="最新助手" + "己" * 2_500),
        ]

        plan = plan_context(
            window_tokens=8_192,
            system_prompt="系统规则",
            tools=[],
            fixed_messages=[SystemMessage(content="当前时间")],
            summary_message=None,
            history_groups=[oldest, middle, newest],
            current_message=HumanMessage(content="继续"),
        )

        contents = [str(message.content) for message in plan.messages]
        self.assertTrue(plan.truncated)
        self.assertEqual(plan.dropped_rounds, 2)
        self.assertFalse(any(content.startswith("最旧") for content in contents))
        self.assertFalse(any(content.startswith("中间") for content in contents))
        self.assertTrue(any(content.startswith("最新用户") for content in contents))
        self.assertLessEqual(plan.estimated_input_tokens, 8_192)

    def test_usage_is_aggregated_across_agent_model_steps(self):
        usage = aggregate_message_usage(
            [
                AIMessage(
                    content="调用工具",
                    usage_metadata={
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "total_tokens": 120,
                    },
                ),
                AIMessage(
                    content="最终回答",
                    usage_metadata={
                        "input_tokens": 180,
                        "output_tokens": 30,
                        "total_tokens": 210,
                    },
                ),
            ]
        )

        self.assertIsNotNone(usage)
        self.assertEqual(usage.input_tokens, 280)
        self.assertEqual(usage.output_tokens, 50)
        self.assertEqual(usage.total_tokens, 330)

    def test_missing_provider_usage_falls_back_to_local_estimate(self):
        initial = [HumanMessage(content="测试请求")]
        final = AIMessage(content="测试完成")

        usage, estimated = resolve_request_usage(
            [*initial, final],
            initial_input_tokens=120,
            initial_message_count=len(initial),
        )

        self.assertTrue(estimated)
        self.assertIsNotNone(usage)
        self.assertEqual(usage.input_tokens, 120)
        self.assertGreater(usage.output_tokens, 0)
        self.assertEqual(
            usage.total_tokens,
            usage.input_tokens + usage.output_tokens,
        )


if __name__ == "__main__":
    unittest.main()
