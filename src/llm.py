"""LLM factory: create the right chat model from a model name string."""

from dataclasses import dataclass
from typing import Any, TypeVar, overload

from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import BaseMessage
from browser_use.llm.views import ChatInvokeCompletion
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4")


def is_openai_model(model_name: str) -> bool:
    """Check if a model name is an OpenAI model."""
    return model_name.startswith(OPENAI_PREFIXES)


def _extract_json(text: str) -> str:
    """Extract the first complete JSON object from text with trailing chars.

    GPT-5.2 sometimes appends extra text after valid JSON. This strips it.
    """
    # Try to find where the top-level JSON object ends
    depth = 0
    in_string = False
    escape_next = False

    for i, char in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[: i + 1]

    # If parsing failed, return original and let downstream handle the error
    return text


@dataclass
class CleanJsonOpenAI:
    """Wrapper around ChatOpenAI that cleans trailing chars from JSON output.

    GPT-5.2 sometimes outputs valid JSON followed by extra text, which
    breaks Pydantic's model_validate_json. This wrapper intercepts the
    raw response and strips trailing characters before parsing.
    """

    _inner: Any  # ChatOpenAI instance

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def _verified_api_keys(self) -> bool:
        return self._inner._verified_api_keys

    @_verified_api_keys.setter
    def _verified_api_keys(self, value: bool) -> None:
        self._inner._verified_api_keys = value

    @overload
    async def ainvoke(
        self, messages: list[BaseMessage], output_format: None = None, **kwargs: Any
    ) -> ChatInvokeCompletion[str]: ...

    @overload
    async def ainvoke(
        self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any
    ) -> ChatInvokeCompletion[T]: ...

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        """Call inner LLM, cleaning JSON output if structured format requested."""
        if output_format is None:
            return await self._inner.ainvoke(messages, output_format=None, **kwargs)

        # For structured output, intercept and clean JSON
        # First try the normal path
        try:
            return await self._inner.ainvoke(messages, output_format=output_format, **kwargs)
        except Exception as first_error:
            if "trailing characters" not in str(first_error):
                raise

        # Retry: call without output_format to get raw text, then parse ourselves
        raw_result = await self._inner.ainvoke(messages, output_format=None, **kwargs)
        raw_text = raw_result.completion
        cleaned = _extract_json(raw_text)

        parsed = output_format.model_validate_json(cleaned)
        return ChatInvokeCompletion(
            completion=parsed,
            usage=raw_result.usage,
            stop_reason=raw_result.stop_reason,
        )


def create_browser_llm(model_name: str, temperature: float = 0) -> BaseChatModel:
    """Create a browser-use compatible LLM from a model name.

    Supports both Anthropic (claude-*) and OpenAI (gpt-*, o1-*, o3-*, o4-*).
    Both return the same BaseChatModel interface — drop-in replacements.
    """
    if is_openai_model(model_name):
        from browser_use.llm.openai.chat import ChatOpenAI

        inner = ChatOpenAI(model=model_name, temperature=temperature)
        return CleanJsonOpenAI(_inner=inner)

    from browser_use.llm.anthropic.chat import ChatAnthropic

    return ChatAnthropic(model=model_name, temperature=temperature)
