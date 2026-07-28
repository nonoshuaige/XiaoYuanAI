"""Model configuration and discovery for XiaoYuan AI."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from openai import OpenAI


load_dotenv()


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    api_key_env: str
    base_url_env: str
    default_base_url: str


@dataclass(frozen=True)
class ModelCatalog:
    """A provider catalog plus how confidently its models were observed."""

    models: tuple[str, ...]
    discovered_models: frozenset[str]
    source: str


CODER_PROVIDER = ProviderSpec(
    id="qwen-coder",
    label="Coding Plan",
    api_key_env="DASHSCOPE_API_KEY",
    base_url_env="OPENAI_API_BASE",
    default_base_url="https://coding.dashscope.aliyuncs.com/v1/",
)
QWEN3D_PROVIDER = ProviderSpec(
    id="qwen3d",
    label="Qwen3D",
    api_key_env="QWEN3D6_API_KEY",
    base_url_env="QWEN3D6_API_BASE",
    default_base_url="http://40.19.92.194:9080/aip-qwen3d6-27b-fp8/v1",
)

DEFAULT_CODER_MODEL = os.getenv("MODEL_NAME", "qwen3-coder-plus")
QWEN3D_MODEL = os.getenv("QWEN3D6_MODEL_NAME", "qwen3d6-27b")
_configured_default = os.getenv("DEFAULT_MODEL_ID", DEFAULT_CODER_MODEL)
DEFAULT_MODEL_ID = (
    DEFAULT_CODER_MODEL
    if _configured_default == CODER_PROVIDER.id
    else _configured_default
)

# Keep the last successfully observed catalog as a resilient fallback. Discovery
# still refreshes from the provider, so newly published models can appear without
# a code change.
CODER_MODEL_FALLBACKS = (
    "MiniMax-M2.5",
    "glm-4.7",
    "glm-5",
    "kimi-k2.5",
    "qwen3-coder-next",
    "qwen3-coder-plus",
    "qwen3-max-2026-01-23",
    "qwen3.5-plus",
    "qwen3.6-plus",
    "qwen3.7-plus",
)
MODEL_CATALOG_TTL_SECONDS = 300
_catalog_lock = threading.Lock()
_coder_catalog_cache: ModelCatalog | None = None
_coder_model_cache_time = 0.0


def get_model_options(
    *,
    refresh: bool = False,
) -> list[dict[str, str | bool]]:
    """Return only models whose provider is currently configured.

    ``discovered`` means the model was observed from the provider's catalog.
    ``callable`` means the server has enough provider configuration to accept a
    chat request for it. ``source`` explains whether the catalog is live,
    cached, a static provider model, or a built-in outage fallback.
    """
    options: list[dict[str, str | bool]] = []
    if is_provider_configured(CODER_PROVIDER):
        catalog = _discover_coder_catalog(refresh=refresh)
        options.extend(
            {
                "id": model_name,
                "label": model_name,
                "model": model_name,
                "provider": CODER_PROVIDER.label,
                "providerId": CODER_PROVIDER.id,
                "default": False,
                "discovered": model_name in catalog.discovered_models,
                "callable": True,
                "source": catalog.source,
            }
            for model_name in catalog.models
        )

    if is_provider_configured(QWEN3D_PROVIDER):
        options.append(
            {
                "id": QWEN3D_MODEL,
                "label": "Qwen3D6 27B",
                "model": QWEN3D_MODEL,
                "provider": QWEN3D_PROVIDER.label,
                "providerId": QWEN3D_PROVIDER.id,
                "default": False,
                "discovered": False,
                "callable": True,
                "source": "configured",
            }
        )

    if options:
        default_id = (
            DEFAULT_MODEL_ID
            if any(option["id"] == DEFAULT_MODEL_ID for option in options)
            else str(options[0]["id"])
        )
        for option in options:
            option["default"] = option["id"] == default_id
    return options


def discover_coder_models(*, refresh: bool = False) -> tuple[str, ...]:
    """Discover callable model IDs from the Coding Plan OpenAI API."""
    if not is_provider_configured(CODER_PROVIDER):
        return ()
    return _discover_coder_catalog(refresh=refresh).models


def _discover_coder_catalog(*, refresh: bool = False) -> ModelCatalog:
    global _coder_catalog_cache, _coder_model_cache_time
    now = time.monotonic()
    with _catalog_lock:
        if (
            not refresh
            and _coder_catalog_cache is not None
            and now - _coder_model_cache_time < MODEL_CATALOG_TTL_SECONDS
        ):
            return _coder_catalog_cache

        previous = _coder_catalog_cache
        try:
            client = OpenAI(
                api_key=_provider_api_key(CODER_PROVIDER),
                base_url=_provider_base_url(CODER_PROVIDER),
                timeout=10,
                max_retries=0,
            )
            discovered = tuple(
                sorted(
                    {
                        model.id.strip()
                        for model in client.models.list().data
                        if model.id and model.id.strip()
                    }
                )
            )
            if discovered:
                _coder_catalog_cache = ModelCatalog(
                    models=discovered,
                    discovered_models=frozenset(discovered),
                    source="live",
                )
            elif previous is not None and previous.discovered_models:
                _coder_catalog_cache = ModelCatalog(
                    models=previous.models,
                    discovered_models=previous.discovered_models,
                    source="cached",
                )
            else:
                _coder_catalog_cache = ModelCatalog(
                    models=CODER_MODEL_FALLBACKS,
                    discovered_models=frozenset(),
                    source="fallback",
                )
        except Exception:
            # A catalog outage must not make the chat page unusable. Calls to a
            # selected model still surface their own provider error normally.
            if previous is not None and previous.discovered_models:
                _coder_catalog_cache = ModelCatalog(
                    models=previous.models,
                    discovered_models=previous.discovered_models,
                    source="cached",
                )
            else:
                _coder_catalog_cache = ModelCatalog(
                    models=CODER_MODEL_FALLBACKS,
                    discovered_models=frozenset(),
                    source="fallback",
                )
        _coder_model_cache_time = now
        return _coder_catalog_cache


def get_default_model_id() -> str:
    """Resolve the configured default, falling back to the first callable model."""
    options = get_model_options()
    for option in options:
        if option["default"]:
            return str(option["id"])
    raise RuntimeError(
        "没有可调用模型，请至少配置一个 Provider 的 API Key"
    )


def get_llm(model_id: str = DEFAULT_MODEL_ID) -> ChatOpenAI:
    """Build a chat model for one allowed model selection."""
    selected_model = (
        DEFAULT_CODER_MODEL if model_id == CODER_PROVIDER.id else model_id
    )
    options = {
        str(option["id"]): option
        for option in get_model_options()
        if option["callable"]
    }
    option = options.get(selected_model)
    if option is None:
        raise ValueError(f"模型未配置或不可调用：{selected_model}")

    if option["providerId"] == QWEN3D_PROVIDER.id:
        provider = QWEN3D_PROVIDER
    elif option["providerId"] == CODER_PROVIDER.id:
        provider = CODER_PROVIDER
    else:
        raise ValueError(f"模型 Provider 不受支持：{option['providerId']}")

    return ChatOpenAI(
        model=selected_model,
        api_key=_provider_api_key(provider),
        base_url=_provider_base_url(provider),
        temperature=0.7,
    )


def get_llms() -> dict[str, ChatOpenAI]:
    """Compatibility helper that eagerly builds every callable model."""
    models = {
        str(option["id"]): get_llm(str(option["id"]))
        for option in get_model_options()
    }
    default_model_id = get_default_model_id()
    if default_model_id not in models:
        raise RuntimeError(f"默认模型不可用：{default_model_id}")
    return models


def is_provider_configured(provider: ProviderSpec) -> bool:
    """Return whether a provider has a non-empty API key."""
    return bool(_provider_api_key(provider, required=False))


def _provider_api_key(
    provider: ProviderSpec,
    *,
    required: bool = True,
) -> str:
    api_key = os.getenv(provider.api_key_env)
    if not api_key and provider == CODER_PROVIDER:
        api_key = os.getenv("OPENAI_API_KEY")
    api_key = api_key.strip() if api_key else ""
    if not api_key and required:
        raise RuntimeError(
            f"缺少模型密钥，请先在 .env 中配置 {provider.api_key_env}"
        )
    return api_key


def _provider_base_url(provider: ProviderSpec) -> str:
    return os.getenv(provider.base_url_env, provider.default_base_url)
