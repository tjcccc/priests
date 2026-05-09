from __future__ import annotations

import asyncio
import threading
from functools import partial
from typing import AsyncGenerator

import anyio
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from priest.errors import ProviderError, ProviderTimeoutError
from priest.providers.base import AdapterResult, ProviderAdapter
from priest.schema.request import OutputSpec, PriestConfig
from priests.providers.github_copilot_auth import GITHUB_COPILOT_HEADERS


class GitHubCopilotProvider(ProviderAdapter):
    """OpenAI-compatible GitHub Copilot adapter with required IDE auth headers."""

    def __init__(self, base_url: str, api_key: str = "", proxy: str | None = None) -> None:
        self._name = "github_copilot"
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._proxy = proxy
        self._headers = dict(GITHUB_COPILOT_HEADERS)

    @property
    def provider_name(self) -> str:
        return self._name

    async def complete(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
    ) -> AdapterResult:
        kwargs = _completion_kwargs(messages, config, output_spec)

        call = partial(
            _call_sync,
            api_key=self._api_key or "dummy",
            base_url=self._base_url,
            timeout=config.timeout_seconds or 60.0,
            proxy=self._proxy,
            headers=self._headers,
            kwargs=kwargs,
        )
        try:
            response = await anyio.to_thread.run_sync(call)
        except APITimeoutError:
            raise ProviderTimeoutError(self._name, config.timeout_seconds or 60.0)
        except APIStatusError as exc:
            raise ProviderError(self._name, f"HTTP {exc.status_code}: {exc.message}")
        except APIConnectionError as exc:
            raise ProviderError(self._name, str(exc))

        choices = response.choices
        text = choices[0].message.content if choices else None
        finish_reason = _map_finish_reason(choices[0].finish_reason if choices else None)

        usage = response.usage
        return AdapterResult(
            text=text,
            raw=response.model_dump(),
            finish_reason=finish_reason,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )

    async def stream(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
    ) -> AsyncGenerator[str, None]:
        kwargs = _completion_kwargs(messages, config, output_spec)
        kwargs["stream"] = True

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str | Exception | None] = asyncio.Queue()

        def _run() -> None:
            try:
                import httpx as _httpx

                http_client = _httpx.Client(proxy=self._proxy) if self._proxy else None
                client = OpenAI(
                    api_key=self._api_key or "dummy",
                    base_url=self._base_url,
                    timeout=config.timeout_seconds or 60.0,
                    max_retries=0,
                    http_client=http_client,
                )
                response = client.chat.completions.create(
                    **kwargs,
                    extra_headers=self._headers,
                )
                for chunk in response:
                    choices = chunk.choices
                    if choices and choices[0].delta.content:
                        loop.call_soon_threadsafe(q.put_nowait, choices[0].delta.content)
            except Exception as exc:
                loop.call_soon_threadsafe(q.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, APITimeoutError):
                    raise ProviderTimeoutError(self._name, config.timeout_seconds or 60.0)
                if isinstance(item, APIStatusError):
                    raise ProviderError(self._name, f"HTTP {item.status_code}: {item.message}")
                if isinstance(item, APIConnectionError):
                    raise ProviderError(self._name, str(item))
                if isinstance(item, Exception):
                    raise ProviderError(self._name, str(item))
                yield item
        finally:
            thread.join(timeout=5)


def _completion_kwargs(
    messages: list[dict],
    config: PriestConfig,
    output_spec: OutputSpec,
) -> dict:
    kwargs: dict = {
        "model": config.model,
        "messages": messages,
    }

    if config.max_output_tokens is not None:
        kwargs["max_tokens"] = config.max_output_tokens

    if output_spec.json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": output_spec.json_schema_name,
                "schema": output_spec.json_schema,
                "strict": output_spec.json_schema_strict,
            },
        }
    elif output_spec.provider_format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    if config.provider_options:
        kwargs["extra_body"] = config.provider_options

    return kwargs


def _call_sync(
    *,
    api_key: str,
    base_url: str,
    timeout: float,
    proxy: str | None,
    headers: dict[str, str],
    kwargs: dict,
):
    import httpx

    http_client = httpx.Client(proxy=proxy) if proxy else None
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
        http_client=http_client,
    )
    return client.chat.completions.create(
        **kwargs,
        extra_headers=headers,
    )


def _map_finish_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return {
        "stop": "stop",
        "length": "length",
        "content_filter": "content_filter",
    }.get(reason, "unknown")
