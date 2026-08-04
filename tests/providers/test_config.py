from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.providers import config


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.ollama_catalog_patcher = patch.object(
            config,
            "_discover_ollama_catalog",
            return_value=config.ModelCatalog(
                models=(),
                discovered_models=frozenset(),
                source="unavailable",
            ),
        )
        self.ollama_catalog_patcher.start()

    def tearDown(self):
        self.ollama_catalog_patcher.stop()

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

    def test_ollama_models_are_discovered_without_an_api_key(self):
        with (
            patch.object(
                config,
                "_discover_ollama_catalog",
                return_value=config.ModelCatalog(
                    models=("gemma4:12b", "qwen3.5:9b"),
                    discovered_models=frozenset(
                        {"gemma4:12b", "qwen3.5:9b"}
                    ),
                    source="live",
                ),
            ),
            patch.dict(os.environ, {}, clear=True),
        ):
            options = config.get_model_options()

        self.assertEqual(
            [option["id"] for option in options],
            ["ollama::gemma4:12b", "ollama::qwen3.5:9b"],
        )
        self.assertTrue(all(option["callable"] for option in options))
        self.assertTrue(all(option["discovered"] for option in options))
        self.assertEqual(
            {option["providerId"] for option in options},
            {config.OLLAMA_PROVIDER.id},
        )

    def test_ollama_model_uses_local_openai_compatible_endpoint(self):
        option = {
            "id": "ollama::gemma4:12b",
            "label": "gemma4:12b",
            "model": "gemma4:12b",
            "provider": config.OLLAMA_PROVIDER.label,
            "providerId": config.OLLAMA_PROVIDER.id,
            "default": True,
            "discovered": True,
            "callable": True,
            "source": "live",
        }
        with (
            patch.object(config, "get_model_options", return_value=[option]),
            patch.dict(
                os.environ,
                {"OLLAMA_API_BASE": "http://localhost:11434/v1/"},
                clear=True,
            ),
        ):
            model = config.get_llm("ollama::gemma4:12b")

        self.assertEqual(model.model_name, "gemma4:12b")
        self.assertEqual(
            str(model.openai_api_base),
            "http://localhost:11434/v1/",
        )

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
