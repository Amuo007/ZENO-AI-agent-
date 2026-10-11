"""How a Bit talks to its model: one adapter per API, behind the same small interface.

Each adapter keeps the conversation in its provider's own format. The agent loop in bits.py only
sees Steps (the reply's text, its tool calls, token usage) and hands tool results back.

- OpenAIChat: any OpenAI-compatible API (Lemonade, Ollama, OpenAI, Gemini's compatible endpoint).
- ClaudeChat: Claude through Anthropic's SDK, with streaming, prompt caching, adaptive thinking,
  web search and server-side refusal fallback on the models that support them.

Tool results can carry attachments (images, PDFs) a Bit opened with view_file: Claude gets them
inside the tool result; OpenAI-style APIs get images in a user message right after it.
"""

import json
import re
from dataclasses import dataclass, field

import openai
from loguru import logger

from zeno_server import llm
from zeno_server.llm import ModelConfig
from zeno_server.media import Attachment

# Older tool outputs are cut down to this in OpenAI-style histories, so long tasks don't overflow
# small local contexts. (Claude's history stays append-only: it has a 1M context, and editing
# earlier turns would break its prompt cache and thinking blocks.)
OLD_TOOL_RESULT_CHARS = 600
KEEP_FULL_TOOL_RESULTS = 6
# Images stay in an OpenAI-style history for this many later images, then become a placeholder.
KEEP_IMAGES = 3
CLAUDE_MAX_TOKENS = 64000
NO_VISION = "[The image was left out: this model can't see images.]"


class ModelRefused(Exception):
    """The model declined the task."""


class ModelError(Exception):
    """The model's reply can't be used (e.g. it was cut off mid tool call)."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None  # None when the model sent arguments that aren't a JSON object


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class Step:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    thinking: str = ""
    # Tools the provider ran itself (e.g. Claude's web search), as (name, input), for the log.
    server_calls: list[tuple[str, dict]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


def _strip_thinking(text: str | None) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _parse_arguments(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class OpenAIChat:
    reads_pdfs = False
    searches_web = False

    def __init__(self, cfg: ModelConfig, system: str, task: str, tools: list[dict]):
        self.cfg = cfg
        self.client = llm.openai_client(cfg)
        self.set_tools(tools)
        self.messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        # Whether the model takes images; found out the first time one is sent.
        self.vision: bool | None = None

    def set_tools(self, tools: list[dict]):
        self.tools = [{"type": "function", "function": tool} for tool in tools]

    async def step(self, tools_on: bool = True) -> Step:
        kwargs: dict = {"model": self.cfg.model, "messages": self.messages}
        if tools_on and self.tools:
            kwargs["tools"] = self.tools
        if self.cfg.extra:
            kwargs["extra_body"] = self.cfg.extra
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except openai.BadRequestError:
            # Text-only models reject images; leave them out from now on and try again.
            if self.vision is not None or not self._has_images():
                raise
            logger.info(f"{self.cfg.model} doesn't take images; leaving them out")
            self.vision = False
            self._drop_images()
            response = await self.client.chat.completions.create(**kwargs)
        if self._has_images():
            self.vision = True
        message = response.choices[0].message

        # Keep the message as the provider sent it (minus empty fields): some providers attach
        # data they need back on the next turn, like Gemini's thought signatures on tool calls.
        entry = message.model_dump(exclude_none=True)
        entry["content"] = _strip_thinking(message.content)
        self.messages.append(entry)

        usage = response.usage
        if usage and usage.prompt_tokens is not None:
            prompt_tokens, completion_tokens = usage.prompt_tokens, usage.completion_tokens or 0
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0
        else:
            # Some servers don't report usage; estimate about four characters per token.
            prompt_tokens = len(json.dumps(self.messages, default=str)) // 4
            completion_tokens = len(message.content or "") // 4
            cached = 0
        return Step(
            text=entry["content"],
            calls=[
                ToolCall(call.id, call.function.name, _parse_arguments(call.function.arguments))
                for call in message.tool_calls or []
            ],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached,
        )

    def add_tool_results(self, results: list[ToolResult]):
        images = []
        for result in results:
            self.messages.append(
                {"role": "tool", "tool_call_id": result.call_id, "content": result.content}
            )
            images += [a for a in result.attachments if a.kind == "image"]
        if images:
            # Tool messages can only hold text, so the images follow in a user message.
            parts: list[dict] = [{"type": "text", "text": "The images you opened with view_file:"}]
            for image in images:
                if self.vision is False:
                    parts.append({"type": "text", "text": f"{image.name}: {NO_VISION}"})
                else:
                    parts.append({"type": "text", "text": image.name})
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{image.media_type};base64,{image.data}"},
                        }
                    )
            self.messages.append({"role": "user", "content": parts})
        self._trim()

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def _trim(self):
        tool_messages = [m for m in self.messages if m["role"] == "tool"]
        for old in tool_messages[:-KEEP_FULL_TOOL_RESULTS]:
            if len(old["content"]) > OLD_TOOL_RESULT_CHARS:
                old["content"] = old["content"][:OLD_TOOL_RESULT_CHARS] + "… [older output trimmed]"
        image_parts = [
            part
            for message in self.messages
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if part.get("type") == "image_url"
        ]
        for part in image_parts[:-KEEP_IMAGES]:
            part.clear()
            part.update({"type": "text", "text": "[An image you looked at earlier]"})

    def _has_images(self) -> bool:
        return any(
            part.get("type") == "image_url"
            for message in self.messages
            if isinstance(message.get("content"), list)
            for part in message["content"]
        )

    def _drop_images(self):
        for message in self.messages:
            if isinstance(message.get("content"), list):
                for part in message["content"]:
                    if part.get("type") == "image_url":
                        part.clear()
                        part.update({"type": "text", "text": NO_VISION})


class ClaudeChat:
    reads_pdfs = True

    def __init__(self, cfg: ModelConfig, system: str, task: str, tools: list[dict]):
        self.cfg = cfg
        self.client = llm.anthropic_client(cfg)
        self.system = system
        # Claude's own web search and fetch (run by Anthropic) on the models that have them.
        self.searches_web = llm.claude_supports_adaptive(cfg.model)
        self.set_tools(tools)
        self.messages: list[dict] = [{"role": "user", "content": task}]
        # Content for the next user turn: tool results, then any documents and notes.
        self._pending: list[dict] = []

    def set_tools(self, tools: list[dict]):
        # Streaming with client tools: inputs stream as they're generated, and arguments are
        # checked (as JSON objects) before any tool runs, in bits.py.
        self.tools = [
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["parameters"],
                "eager_input_streaming": True,
            }
            for tool in tools
        ]
        if self.searches_web:
            self.tools += [
                {"type": "web_search_20260209", "name": "web_search"},
                {"type": "web_fetch_20260209", "name": "web_fetch"},
            ]

    async def step(self, tools_on: bool = True) -> Step:
        if self._pending:
            self.messages.append({"role": "user", "content": self._pending})
            self._pending = []

        kwargs: dict = {
            "model": self.cfg.model,
            "max_tokens": CLAUDE_MAX_TOKENS,
            "system": self.system,
            "messages": self.messages,
            "tools": self.tools,
            # Caches everything up to the newest block, so each step re-reads the
            # conversation so far from the cache.
            "cache_control": {"type": "ephemeral"},
        }
        if not tools_on:
            kwargs["tool_choice"] = {"type": "none"}
        if llm.claude_supports_adaptive(self.cfg.model):
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["output_config"] = {"effort": self.cfg.effort}
        betas = []
        if llm.claude_supports_fallbacks(self.cfg.model):
            # If a safety classifier declines, Anthropic re-runs the request on the model it
            # recommends for that case instead of failing the task.
            betas.append("server-side-fallback-2026-07-01")
            kwargs["fallbacks"] = "default"

        prompt_tokens = completion_tokens = cached_tokens = 0
        server_calls: list[tuple[str, dict]] = []
        for _ in range(4):
            response = await self._stream(kwargs, betas)
            server_calls += [
                (b.name, b.input if isinstance(b.input, dict) else {})
                for b in response.content
                if b.type == "server_tool_use"
            ]
            usage = response.usage
            cached_tokens += usage.cache_read_input_tokens or 0
            prompt_tokens += (
                usage.input_tokens
                + (usage.cache_creation_input_tokens or 0)
                + (usage.cache_read_input_tokens or 0)
            )
            completion_tokens += usage.output_tokens
            # Append the content unchanged (thinking blocks included): history is append-only.
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "pause_turn":
                break

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            raise ModelRefused(f"Claude declined this task{f' ({category})' if category else ''}.")
        calls = [block for block in response.content if block.type == "tool_use"]
        if response.stop_reason == "max_tokens" and calls:
            raise ModelError("Its reply was cut off in the middle of a tool call.")

        return Step(
            text="\n".join(b.text for b in response.content if b.type == "text").strip(),
            calls=[ToolCall(b.id, b.name, _parse_arguments(b.input)) for b in calls],
            server_calls=server_calls,
            thinking="\n".join(
                b.thinking for b in response.content if b.type == "thinking" and b.thinking
            ).strip(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    async def _stream(self, kwargs: dict, betas: list[str]):
        for attempt in range(3):
            try:
                if betas:
                    manager = self.client.beta.messages.stream(betas=betas, **kwargs)
                else:
                    manager = self.client.messages.stream(**kwargs)
                async with manager as stream:
                    return await stream.get_final_message()
            except ValueError:
                # A streamed tool input the SDK couldn't parse at all. It never completed, so
                # there's no tool call to answer; ask again (a few times at most).
                if attempt == 2:
                    raise ModelError("Claude kept sending tool arguments that weren't valid JSON.")
        raise AssertionError("unreachable")

    def add_tool_results(self, results: list[ToolResult]):
        documents = []
        for result in results:
            content: str | list[dict] = result.content
            images = [a for a in result.attachments if a.kind == "image"]
            if images:
                content = [{"type": "text", "text": result.content}] + [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": a.media_type, "data": a.data},
                    }
                    for a in images
                ]
            block = {"type": "tool_result", "tool_use_id": result.call_id, "content": content}
            if result.is_error:
                block["is_error"] = True
            self._pending.append(block)
            documents += [a for a in result.attachments if a.kind == "pdf"]
        # Tool results come first in a user turn; the PDFs follow them.
        for document in documents:
            self._pending.append(
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": document.data},
                    "title": document.name,
                }
            )

    def add_user(self, text: str):
        self._pending.append({"type": "text", "text": text})


def open_chat(cfg: ModelConfig, system: str, task: str, tools: list[dict]):
    if cfg.kind == "anthropic":
        return ClaudeChat(cfg, system, task, tools)
    return OpenAIChat(cfg, system, task, tools)
