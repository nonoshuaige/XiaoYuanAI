from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import config


class ModelConfigTests(unittest.TestCase):
    def test_coder_model_uses_coding_plan_provider(self):
        with (
            patch.object(
                config,
                "_discover_coder_catalog",
                return_value=config.ModelCatalog(
                    models=("glm-5",),
                    discovered_models=frozenset({"glm-5"}),
                    source="live",
                ),
            ),
            patch.dict(
                os.environ,
                {
                    "DASHSCOPE_API_KEY": "test-coder-key",
                    "OPENAI_API_BASE": "https://coder.example/v1",
                },
                clear=True,
            ),
        ):
            model = config.get_llm("glm-5")

        self.assertEqual(model.model_name, "glm-5")
        self.assertEqual(
            str(model.openai_api_base),
            "https://coder.example/v1",
        )

    def test_qwen3d_model_keeps_its_separate_provider(self):
        with patch.dict(
            os.environ,
            {
                "QWEN3D6_API_KEY": "test-3d-key",
                "QWEN3D6_API_BASE": "https://3d.example/v1",
            },
            clear=True,
        ):
            model = config.get_llm(config.QWEN3D_MODEL)

        self.assertEqual(model.model_name, config.QWEN3D_MODEL)
        self.assertEqual(
            str(model.openai_api_base),
            "https://3d.example/v1",
        )

    def test_model_options_expose_discovered_coder_models(self):
        with (
            patch.object(
                config,
                "_discover_coder_catalog",
                return_value=config.ModelCatalog(
                    models=("glm-5", "qwen3-coder-plus"),
                    discovered_models=frozenset(
                        {"glm-5", "qwen3-coder-plus"}
                    ),
                    source="live",
                ),
            ),
            patch.dict(
                os.environ,
                {
                    "DASHSCOPE_API_KEY": "test-coder-key",
                    "QWEN3D6_API_KEY": "test-3d-key",
                },
                clear=True,
            ),
        ):
            options = config.get_model_options()

        self.assertEqual(
            [option["id"] for option in options],
            ["glm-5", "qwen3-coder-plus", config.QWEN3D_MODEL],
        )
        self.assertEqual(options[0]["provider"], "Coding Plan")
        self.assertEqual(options[-1]["provider"], "Qwen3D")
        self.assertTrue(options[0]["discovered"])
        self.assertTrue(options[0]["callable"])
        self.assertEqual(options[0]["source"], "live")
        self.assertFalse(options[-1]["discovered"])
        self.assertEqual(options[-1]["source"], "configured")

    def test_model_options_hide_unconfigured_providers(self):
        with patch.dict(
            os.environ,
            {"QWEN3D6_API_KEY": "test-3d-key"},
            clear=True,
        ):
            options = config.get_model_options()

        self.assertEqual(
            [option["id"] for option in options],
            [config.QWEN3D_MODEL],
        )
        self.assertEqual(
            options[0]["providerId"],
            config.QWEN3D_PROVIDER.id,
        )
        self.assertTrue(options[0]["default"])

    def test_unknown_model_is_rejected(self):
        with (
            patch.object(
                config,
                "_discover_coder_catalog",
                return_value=config.ModelCatalog(
                    models=("qwen3-coder-plus",),
                    discovered_models=frozenset({"qwen3-coder-plus"}),
                    source="live",
                ),
            ),
            patch.dict(
                os.environ,
                {"DASHSCOPE_API_KEY": "test-coder-key"},
                clear=True,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "不可调用"):
                config.get_llm("unknown-model")


if __name__ == "__main__":
    unittest.main()
