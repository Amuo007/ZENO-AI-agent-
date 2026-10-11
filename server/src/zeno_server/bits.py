"""Bits: the worker agents ZENO hands tasks to.

ZENO stays in the conversation while Bits work in the background. Each Bit is an LLM agent loop
with its own Docker sandbox (see sandbox.py) and free rein over how it does its task: it runs
commands and Python, reads and writes files in its own folder, publishes files to the user's
shared folder, uses the user's connected apps (their tools, and their APIs from code through the
gateway), looks at images and PDFs, and builds tools that it saves to a toolbox all of the
user's Bits share. A user can have up to MAX_BITS working at once, named by slot: BIT-01, ...

Bits are long-lived when the task needs it: a Bit can wait (timers, reminders) or keep watch
(re-running a check until its output changes) for days, without spending tokens meanwhile.

Bits and the user talk both ways, through ZENO or the dashboard:
- a Bit can ask the user a question (ask_user) and waits for the answer, or tell them something
  right away (notify_user);
- the user can send a working Bit a message (new details, a change of plan), and give a finished
  Bit a follow-up: it picks up where it left off, with its memory of the work.

A Bit can wait for others to finish first (wait_for); their reports are passed on to it, which
lets ZENO chain work: one Bit gathers data into /workspace/shared, the next analyzes it.

Every LLM call's token usage and every step is recorded, so the dashboard can show what each Bit
is doing and what it costs, and stop it.
"""

import asyncio
import json
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from types import SimpleNamespace

from loguru import logger

from zeno_server import bit_models, connectors, db, gateway, llm, media, memory, sandbox
from zeno_server.bit_models import ModelError, ModelRefused, ToolResult
from zeno_server.config import settings
from zeno_server.sandbox import SandboxError

MAX_TOOL_RESULT_CHARS = 12000
MAX_READ_CHARS = 20000
# How long a Bit may wait, keep watch, or wait for an answer in one go.
MAX_WAIT_SECONDS = 7 * 24 * 3600
DEFAULT_ANSWER_HOURS = 24
MIN_WATCH_INTERVAL = 5
# A Bit that rested at least this long gets fresh step and token limits for its next stretch.
REST_RESETS_LIMITS = 60


class BitError(Exception):
    """A request ZENO or the dashboard made that can't be done, e.g. every Bit is busy."""


class BitFailed(Exception):
    """Ends a Bit's task as failed, with a reason for the user."""


# Called with every event the user should hear about: {"type": "finished" | "question" |
# "update" | "answered", "bit": ..., "message": ...}. Returns True when it reached the user.
Listener = Callable[[dict], Awaitable[bool | None]]


# --- Tools a Bit can use -----------------------------------------------------------------------


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    """A tool definition in the neutral shape the model adapters translate."""
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


BUILTIN_TOOLS = [
    _function(
        "run_command",
        "Run a bash command in your Linux sandbox, in your own folder. Returns the exit code and "
        "the combined output.",
        {
            "command": {"type": "string"},
            "timeout_seconds": {
                "type": "integer",
                "description": f"Default 120, at most {settings.bit_command_timeout}.",
            },
        },
        ["command"],
    ),
    _function(
        "write_file",
        "Create or overwrite a text file in your own folder.",
        {
            "path": {"type": "string", "description": "Relative to your folder, or absolute."},
            "content": {"type": "string", "description": "The file's full contents."},
        },
        ["path", "content"],
    ),
    _function(
        "read_file",
        "Read a text file from your folder, /workspace/shared or /workspace/tools.",
        {
            "path": {"type": "string"},
            "max_chars": {"type": "integer", "description": f"Default {MAX_READ_CHARS}."},
        },
        ["path"],
    ),
    _function(
        "view_file",
        "Look at an image (PNG, JPEG, GIF, WebP, ...) or a PDF, to see what's in it.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _function(
        "list_files",
        "List the files in a folder: your own by default, or /workspace/shared or /workspace/tools.",
        {"path": {"type": "string", "description": "Folder to list; default your own."}},
        [],
    ),
    _function(
        "delete_file",
        "Delete a file or folder in your own folder, to free up storage.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _function(
        "share_file",
        "Publish a finished file from your folder to /workspace/shared, where the user and other "
        "Bits can get it. You can't replace a file another Bit shared.",
        {
            "path": {"type": "string", "description": "The file in your folder."},
            "name": {
                "type": "string",
                "description": "File name in /workspace/shared; defaults to the same name.",
            },
        },
        ["path"],
    ),
    _function(
        "save_tool",
        "Save a script (or a folder with a script and its files) you wrote and tested to the "
        "toolbox, /workspace/tools/NAME/, so every Bit can use it. Saving under an existing name "
        "replaces that tool with your version.",
        {
            "name": {"type": "string", "description": "Short id, like pdf_text or gmail_attachments."},
            "path": {"type": "string", "description": "The script or folder in your own folder."},
            "description": {"type": "string", "description": "What it does, in one sentence."},
            "usage": {
                "type": "string",
                "description": "How to run it, e.g. python3 /workspace/tools/pdf_text/pdf_text.py FILE.pdf",
            },
        },
        ["name", "path", "description", "usage"],
    ),
    _function(
        "wait",
        "Pause until a time or for a while, without using tokens: for timers, reminders, or "
        "spacing out checks. You wake early if the user sends you a message.",
        {
            "seconds": {"type": "integer", "description": "How long to wait."},
            "until": {
                "type": "string",
                "description": "Or a local time to wake at, like 17:30 or 2026-10-12 09:00.",
            },
            "reason": {"type": "string", "description": "What you're waiting for, in a few words."},
        },
        [],
    ),
    _function(
        "watch",
        "Keep watch: run a command every few seconds or minutes, and wake when its output "
        "changes from the first run (or when the user messages you). Make the command print "
        "only the state you care about (e.g. the newest matching email's id), not times or "
        "counters, so it changes only when something happened. Uses no tokens while watching.",
        {
            "command": {"type": "string", "description": "A bash command run in your folder."},
            "every_seconds": {
                "type": "integer",
                "description": f"Time between checks; default 60, at least {MIN_WATCH_INTERVAL}.",
            },
            "hours": {"type": "number", "description": "Stop watching after this long; default 24."},
        },
        ["command"],
    ),
    _function(
        "ask_user",
        "Ask the user a question through ZENO, and wait for the answer. Only for what only the "
        "user can give: a choice, a missing detail, or approval to send, buy or delete "
        "something. One short question, easy to answer out loud.",
        {
            "question": {"type": "string"},
            "hours": {
                "type": "number",
                "description": f"How long to wait for an answer; default {DEFAULT_ANSWER_HOURS}.",
            },
        },
        ["question"],
    ),
    _function(
        "notify_user",
        "Tell the user something right away, through ZENO (spoken if they're talking to ZENO, "
        "otherwise shown later): an alert, a reminder, an important finding. Keeps you working.",
        {"message": {"type": "string", "description": "One or two short, spoken-style sentences."}},
        ["message"],
    ),
]
BUILTIN_NAMES = {tool["name"] for tool in BUILTIN_TOOLS}

BIT_PROMPT = """You are {name}, one of {user}'s Bits: an autonomous AI worker. ZENO, {user}'s \
voice assistant, is your boss and handed you the task below. How you get it done is up to you: \
write and run code, install packages, use {user}'s apps and their APIs, look things up online, \
build tools, and wait or keep watch as long as the task needs. If something you need doesn't \
exist yet, make it.

Your workspace (a Linux sandbox; commands run in your own folder):
- {home}: your own folder. What you leave here stays for your next task.
- /workspace/shared: files for {user} and the other Bits. It's read-only for you: publish \
finished files into it with share_file. {user} may drop files here for you too.
- /workspace/tools: the toolbox all of {user}'s Bits share (read-only; add to it with save_tool).
- All of {user}'s Bits share one {quota} storage pool ({used} in use).
{sandbox_note}
{web_note}
{apps_note}
{toolbox}

How to work:
- Plan, then go step by step and check each result; fix problems yourself.
- Use the toolbox when a tool fits. When you write something reusable (a downloader, a \
converter, an app helper), test it, then save it with save_tool so other Bits can use it.
- To see an image or a PDF, open it with view_file.
- Timers and reminders: use wait. To keep an eye on something (new email, a price, a web page), \
write a small script that prints only the current state, then call watch with it; it wakes you \
when the output changes. If the task is to keep watching, notify the user and watch again.
- notify_user tells {user} something now: an alert, a reminder, an important finding. Not \
routine progress.
- ask_user only for what only {user} can give: a choice, a missing detail, or approval to send, \
buy or delete something. Never ask permission to write code or build tools.
- {user} may message you while you work, through ZENO; follow what they say.
- Use {user}'s apps only as far as the task needs. Don't send messages, create events or \
delete anything unless the task (or {user}) says to.
- Write everything in the language the task is written in.
- When you're done, reply without calling a tool: a short report that ZENO will read out to \
{user}, under 120 words, in plain sentences without markdown: the outcome first, then what you \
did, and the names of files you shared.

Today is {today}.{memories}"""

FULL_IMAGE_NOTE = (
    "Python 3 is installed with requests, httpx, beautifulsoup4, pandas, numpy, matplotlib, "
    "pypdf, pdfplumber, pillow, pytesseract, python-docx and openpyxl, plus curl, wget, git, "
    "jq, ffmpeg, poppler (pdftotext, pdftoppm) and tesseract. pip install anything else; "
    "packages you install stay for every Bit."
)
BASIC_IMAGE_NOTE = (
    "Python 3 and pip are installed (no curl: use Python's urllib). pip install what you need; "
    "packages you install stay for every Bit."
)


def _short(value: str, limit: int = 160) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= limit else f"{value[:limit]}…"


def _clip(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4
    return f"{text[:head]}\n… [{len(text) - limit} characters cut] …\n{text[-(limit - head):]}"


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


def _human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} minutes"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.1f} hours".replace(".0 ", " ")
    return f"{seconds / 86400:.1f} days".replace(".0 ", " ")


def _clock(when: datetime) -> str:
    now = datetime.now().astimezone()
    if when.date() == now.date():
        return when.strftime("%-I:%M %p")
    return when.strftime("%a %b %-d, %-I:%M %p")


def _parse_until(value: str) -> datetime:
    """A local time to wake at: "17:30", "5:30 PM", or a date and time."""
    value = value.strip()
    now = datetime.now().astimezone()
    for pattern in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            clock = datetime.strptime(value.upper(), pattern)
        except ValueError:
            continue
        when = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
        return when if when > now else when + timedelta(days=1)
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo else when.astimezone()


def _tool_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", value.strip().lower()).strip("_-")[:40]


class BitWorker:
    """One Bit working on a task: the agent loop and its tools. It's kept after the task ends,
    so a follow-up can continue the same conversation."""

    def __init__(self, manager: "BitManager", user: dict, bit: dict):
        self.manager = manager
        self.user = user
        self.user_id = user["id"]
        self.bit = bit
        self.bit_id = bit["id"]
        self.slot = bit["slot"]
        self.name = bit["name"]
        self.sandbox = sandbox.Sandbox(self.user_id, self.slot, self.bit_id)
        self.has_sandbox = False
        self.cfg = llm.config("bits")
        self.app_tools: dict = {}
        self.app_names: list[str] = []
        self.app_apis: list[dict] = []
        self.chat = None
        self.token: str | None = None
        # Messages from the user (or ZENO) waiting for the Bit: answers, instructions.
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        # Set once it waits, keeps watch or asks: such a task resumes after a server restart.
        self.long_running = False
        # What it was last waiting for (e.g. "Waiting until 5:00 PM: ..."), to resume it right.
        self.last_rest: str | None = None
        self.steps = 0
        self.tokens = 0

    # -- bookkeeping --

    def event(self, kind: str, text: str):
        db.add_bit_event(self.bit_id, kind, text)

    def activity(self, text: str | None):
        db.update_bit(self.bit_id, activity=text)

    def _set_status(self, status: str, activity: str | None = None):
        db.update_bit(self.bit_id, status=status, activity=activity)

    # -- setup --

    async def _prepare(self):
        status = await sandbox.docker_status(max_age=0)
        self.has_sandbox = False
        if status["available"]:
            self.activity("Starting its sandbox")
            await sandbox.ensure_image()
            self.event("status", f"Starting a sandbox ({sandbox.image()})")
            self.token = gateway.issue(self)
            await self.sandbox.start(gateway.url(), self.token)
            self.has_sandbox = True
        else:
            sandbox.ensure_workspace(self.user_id, self.slot)
            self.event("error", f"No sandbox: {status['detail']}. Working without commands.")

        try:
            tools, self.app_names, _ = await connectors.load_tools(self.user_id)
        except Exception as e:
            logger.error(f"{self.name}: couldn't load the user's app tools: {e}")
            tools = []
        self.app_tools = {t.name: t for t in tools if t.name not in BUILTIN_NAMES}
        self.app_apis = []
        if settings.pipedream_enabled and self.has_sandbox:
            try:
                self.app_apis = await connectors.api_apps(self.user_id)
            except Exception as e:
                logger.warning(f"{self.name}: couldn't list the user's app APIs: {e}")

    def _tools(self) -> list[dict]:
        return BUILTIN_TOOLS + [t.to_default_dict() for t in self.app_tools.values()]

    async def _close(self):
        gateway.revoke(self.token)
        self.token = None
        await self.sandbox.stop()
        cleanups = {
            id(t.handler): t.handler
            for t in self.app_tools.values()
            if getattr(t.handler, "_pipecat_cleanup", None)
        }
        for handler in cleanups.values():
            try:
                await handler._pipecat_cleanup()
            except Exception as e:
                logger.warning(f"{self.name}: error closing an app connection: {e}")

    def _chat_class(self):
        return bit_models.ClaudeChat if self.cfg.kind == "anthropic" else bit_models.OpenAIChat

    def _system_prompt(self) -> str:
        pool = sandbox.storage(self.user_id)
        name = self.user.get("name") or self.user["email"]
        online = settings.sandbox_network != "none"
        if self.has_sandbox:
            full = sandbox.image().startswith("zeno-sandbox")
            sandbox_note = (
                f"- run_command runs bash in your sandbox ({settings.sandbox_cpus} CPU, "
                f"{settings.sandbox_memory} memory, {'online' if online else 'offline'}). "
                f"{FULL_IMAGE_NOTE if full else BASIC_IMAGE_NOTE}"
            )
        else:
            sandbox_note = (
                "- The sandbox is unavailable right now, so you can't run commands or keep "
                "watch; work with files and apps."
            )
        if self.cfg.kind == "anthropic" and llm.claude_supports_adaptive(self.cfg.model):
            web_note = "- web_search and web_fetch look things up online."
        elif self.has_sandbox and online:
            web_note = (
                "- To search the web, fetch https://html.duckduckgo.com/html/?q=YOUR+QUERY "
                "(with curl or Python) and read the result links; then fetch the pages you need."
            )
        else:
            web_note = ""

        apps_note = ""
        if self.app_names or self.app_apis:
            lines = [f"{name}'s connected apps: {', '.join(self.app_names) or 'none with tools'}."]
            if self.app_tools:
                lines.append(
                    "- Their tools are in your tool list. They run on the apps' own servers: a "
                    "file a tool says it saved (say, in /tmp) is not in your sandbox."
                )
            if self.app_tools and self.has_sandbox:
                lines.append(
                    "- From code, call those tools with `zeno tool NAME '{\"arg\": \"value\"}'`, "
                    "or in Python `import zeno; zeno.tool(NAME, arg=value)`; `zeno apps` lists them."
                )
            if self.app_apis:
                apis = ", ".join(f"{a['app']} ({a['api_base']})" for a in self.app_apis)
                lines.append(
                    f"- For what the tools can't do (like downloading email attachments), call an "
                    f"app's own API, signed in as {name}: `zeno api APP METHOD URL "
                    f"[--data JSON] [--out FILE]`, or in Python `zeno.api(APP, METHOD, URL, "
                    f"json=...)`, which returns .status, .json(), .text and .content. "
                    f"APIs: {apis}."
                )
                lines.append(
                    "- Scripts can tell the user things too: `zeno notify \"...\"` or "
                    "zeno.notify(...)."
                )
            apps_note = "\n".join(lines)

        tools = db.list_tools(self.user_id)
        if tools:
            toolbox = "The toolbox (made by Bits before you):\n" + "\n".join(
                f"- {t['name']} ({t['made_by']}): {t['description']} Run: {t['usage']}" for t in tools
            )
        else:
            toolbox = "The toolbox is empty so far."

        known = memory.prompt_section(self.user_id, limit=40)
        return BIT_PROMPT.format(
            name=self.name,
            user=name,
            home=sandbox.bit_home(self.slot),
            quota=_human_size(pool["quota"]),
            used=_human_size(pool["used"]),
            sandbox_note=sandbox_note,
            web_note=web_note,
            apps_note=apps_note,
            toolbox=toolbox,
            today=datetime.now().astimezone().strftime("%A, %B %-d, %Y"),
            memories=f"\n\nWhat ZENO knows about {name}:\n{known}" if known else "",
        )

    def _task_message(self, note: str | None) -> str:
        message = f"Task: {self.bit['title']}\n\n{self.bit['task']}"
        inputs = []
        for dependency_id in self.bit["wait_for"]:
            dependency = db.get_bit(dependency_id)
            if dependency:
                outcome = dependency["result"] or dependency["error"] or dependency["status"]
                inputs.append(f"- {dependency['name']} ({dependency['title']}): {outcome}")
        if inputs:
            message += "\n\nReports from the Bits that worked before you:\n" + "\n".join(inputs)
        if note:
            message += f"\n\n{note}"
        return message

    def _check_key(self):
        if llm.PROVIDERS[self.cfg.provider]["needs_key"] and not self.cfg.api_key:
            raise BitFailed(f"Add an API key for {self.cfg.label} in Settings → AI models.")

    # -- running --

    async def start(self, note: str | None = None) -> str:
        """Work on the task from the beginning."""
        self._check_key()
        db.update_bit(self.bit_id, model=f"{self.cfg.label} · {self.cfg.model}")
        await self._prepare()
        self.chat = bit_models.open_chat(
            self.cfg, self._system_prompt(), self._task_message(note), self._tools()
        )
        self.event("status", f"Working on the task with {self.cfg.model}")
        return await self._work()

    async def follow_up(self, message: str) -> str:
        """Continue the same conversation with a new request, after the task ended."""
        self._check_key()
        await self._prepare()
        self.chat.set_tools(self._tools())
        self.chat.add_user(f"A follow-up from {self._user_name()} (through ZENO): {message}")
        self.event("status", "Picking up the follow-up")
        return await self._work()

    def _user_name(self) -> str:
        return self.user.get("name") or "the user"

    def _deliver_inbox(self):
        while not self.inbox.empty():
            text = self.inbox.get_nowait()
            self.chat.add_user(f"A message from {self._user_name()} (through ZENO): {text}")

    async def _work(self) -> str:
        self._set_status("running", "Thinking")
        self.steps = self.tokens = 0
        nudged = False
        while True:
            self._deliver_inbox()
            if self.steps >= settings.bit_max_steps:
                return await self._wrap_up()
            self.activity("Thinking")
            step = await self._step()
            if not step.calls:
                if not self.inbox.empty():
                    continue
                if step.text:
                    return step.text
                if nudged:
                    raise BitFailed("It stopped without writing a report.")
                nudged = True
                self.chat.add_user("Write your short final report for ZENO now.")
                continue

            if step.text:
                self.event("think", step.text)
            results = []
            for call in step.calls:
                results.append(await self._call_tool(call.id, call.name, call.arguments))
            self.chat.add_tool_results(results)

    async def _wrap_up(self) -> str:
        self.event("status", f"Reached its {settings.bit_max_steps}-step limit; wrapping up")
        self.chat.add_user(
            "You've run out of steps. Don't call any more tools. Write your final report for "
            "ZENO now: what you finished, what's left, and the files you shared."
        )
        step = await self._step(tools_on=False)
        return step.text or "It ran out of steps before finishing."

    async def _step(self, tools_on: bool = True) -> bit_models.Step:
        try:
            step = await self.chat.step(tools_on)
        except (ModelRefused, ModelError) as e:
            raise BitFailed(str(e)) from e
        except Exception as e:
            raise BitFailed(f"Its language model failed: {e}") from e
        self.steps += 1
        db.add_bit_usage(
            self.bit_id,
            prompt_tokens=step.prompt_tokens,
            completion_tokens=step.completion_tokens,
            cached_tokens=step.cached_tokens,
            llm_calls=1,
        )
        if step.thinking:
            self.event("think", step.thinking)
        for name, arguments in step.server_calls:
            self.event("tool", f"{name} {_short(json.dumps(arguments), 200)}")
        self.tokens += step.prompt_tokens - step.cached_tokens + step.completion_tokens
        if self.tokens > settings.bit_max_tokens:
            raise BitFailed(
                f"It used up its token budget ({settings.bit_max_tokens:,} tokens) before finishing."
            )
        return step

    def _rested(self, seconds: float):
        """After a long enough rest, the next stretch of work gets fresh limits."""
        self._set_status("running", "Thinking")
        if seconds >= REST_RESETS_LIMITS:
            self.steps = self.tokens = 0

    # -- tools --

    async def _call_tool(self, call_id: str, name: str, arguments: dict | None) -> ToolResult:
        db.add_bit_usage(self.bit_id, tool_calls=1)
        if arguments is None:
            self.event("error", f"{name}: the arguments weren't a valid JSON object")
            return ToolResult(
                call_id, "Error: the arguments weren't a valid JSON object. Call the tool again.", True
            )

        attachments: list[media.Attachment] = []
        try:
            if name == "run_command":
                result = await self._run_command(arguments)
            elif name == "write_file":
                result = await self._write_file(arguments)
            elif name == "read_file":
                result = self._read_file(arguments)
            elif name == "view_file":
                result, attachments = await self._view_file(arguments)
            elif name == "list_files":
                result = self._list_files(arguments)
            elif name == "delete_file":
                result = self._delete_file(arguments)
            elif name == "share_file":
                result = self._share_file(arguments)
            elif name == "save_tool":
                result = await self._save_tool(arguments)
            elif name == "wait":
                result = await self._wait(arguments)
            elif name == "watch":
                result = await self._watch(arguments)
            elif name == "ask_user":
                result = await self._ask_user(arguments)
            elif name == "notify_user":
                result = await self._notify_user(arguments)
            elif name in self.app_tools:
                result = await self.call_app_tool(name, arguments)
            else:
                result = f"Error: there's no tool called {name}."
        except SandboxError as e:
            result = f"Error: {e}"
        except Exception as e:
            logger.exception(f"{self.name}: tool {name} failed")
            result = f"Error: {name} failed: {e}"

        is_error = result.startswith("Error")
        if name not in ("ask_user", "notify_user"):
            self.event("error" if is_error else "output", result)
        return ToolResult(call_id, _clip(result), is_error, attachments)

    def _require_sandbox(self):
        if not self.has_sandbox:
            raise SandboxError("The sandbox is unavailable, so commands can't run right now.")

    async def _run_command(self, arguments: dict) -> str:
        self._require_sandbox()
        command = str(arguments.get("command") or "").strip()
        if not command:
            return "Error: no command given."
        timeout = int(arguments.get("timeout_seconds") or 120)
        timeout = max(1, min(timeout, settings.bit_command_timeout))
        self.activity(f"Running {_short(command, 80)}")
        self.event("tool", f"$ {command}")
        used = set(re.findall(r"/workspace/tools/([\w-]+)", command))
        if used:
            db.count_tool_use(self.user_id, sorted(used))
        result = await self.sandbox.run(command, timeout)
        status = "timed out" if result.timed_out else f"exit code {result.exit_code}"
        return f"[{status}]\n{result.output or '(no output)'}"

    async def _write_file(self, arguments: dict) -> str:
        path = str(arguments.get("path") or "")
        content = str(arguments.get("content") or "")
        target = sandbox.host_path(self.user_id, self.slot, path, write=True)
        if target.is_dir():
            return f"Error: {path} is a folder."
        data = content.encode()
        existing = target.stat().st_size if target.exists() else 0
        sandbox.check_room(self.user_id, max(0, len(data) - existing))
        shown = sandbox.container_path(self.user_id, self.slot, target)
        self.activity(f"Writing {target.name}")
        self.event("tool", f"write_file {shown} ({_human_size(len(data))})")
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)
        sandbox.forget_usage(self.user_id)
        return f"Wrote {_human_size(len(data))} to {shown}."

    def _read_file(self, arguments: dict) -> str:
        path = str(arguments.get("path") or "")
        limit = int(arguments.get("max_chars") or MAX_READ_CHARS)
        target = sandbox.host_path(self.user_id, self.slot, path, write=False)
        shown = sandbox.container_path(self.user_id, self.slot, target)
        self.activity(f"Reading {target.name}")
        self.event("tool", f"read_file {shown}")
        if not target.is_file():
            return f"Error: {shown} doesn't exist or isn't a file."
        data = target.read_bytes()
        if b"\0" in data[:8192]:
            hint = " Open it with view_file." if media.is_image(target) or media.is_pdf(target) else ""
            return (
                f"{shown} is a binary file ({_human_size(len(data))}).{hint} "
                "Inspect or convert it with run_command."
            )
        text = data.decode(errors="replace")
        if len(text) > limit:
            return f"{text[:limit]}\n… [{len(text) - limit} more characters]"
        return text or "(empty file)"

    async def _view_file(self, arguments: dict) -> tuple[str, list[media.Attachment]]:
        path = str(arguments.get("path") or "")
        target = sandbox.host_path(self.user_id, self.slot, path, write=False)
        shown = sandbox.container_path(self.user_id, self.slot, target)
        if not target.is_file():
            return f"Error: {shown} doesn't exist or isn't a file.", []
        self.activity(f"Looking at {target.name}")
        self.event("tool", f"view_file {shown}")
        if media.is_image(target):
            try:
                attachment, size = await asyncio.to_thread(media.image_attachment, target)
            except Exception as e:
                return f"Error: couldn't open {shown} as an image: {e}", []
            return f"{shown}, {size}:", [attachment]
        if media.is_pdf(target):
            if self.chat.reads_pdfs and target.stat().st_size <= media.MAX_PDF_BYTES:
                return f"{shown}:", [await asyncio.to_thread(media.pdf_attachment, target)]
            try:
                text, pages = await asyncio.to_thread(media.pdf_text, target)
            except Exception as e:
                return f"Error: couldn't read {shown} as a PDF: {e}", []
            return (
                f"{shown} has {pages} pages. Its text is below; to see a page's pictures or "
                f"layout, render it to an image (pdftoppm -png -r 100 -f N -l N FILE page) and "
                f"view that.\n\n{text}",
                [],
            )
        return self._read_file({"path": path}), []

    def _list_files(self, arguments: dict) -> str:
        path = str(arguments.get("path") or ".")
        target = sandbox.host_path(self.user_id, self.slot, path, write=False)
        shown = sandbox.container_path(self.user_id, self.slot, target)
        self.event("tool", f"list_files {shown}")
        if not target.is_dir():
            return f"Error: {shown} isn't a folder."
        entries = []
        for child in sorted(target.iterdir())[:300]:
            if child.name == ".local":
                continue
            kind = "folder" if child.is_dir() else _human_size(child.stat().st_size)
            entries.append(f"{child.name}{'/' if child.is_dir() else ''}  ({kind})")
        return f"{shown}:\n" + ("\n".join(entries) or "(empty)")

    def _delete_file(self, arguments: dict) -> str:
        path = str(arguments.get("path") or "")
        target = sandbox.host_path(self.user_id, self.slot, path, write=True)
        home = sandbox.host_path(self.user_id, self.slot, ".", write=True)
        if target == home:
            return "Error: you can't delete your whole folder."
        shown = sandbox.container_path(self.user_id, self.slot, target)
        self.event("tool", f"delete_file {shown}")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        else:
            return f"Error: {shown} doesn't exist."
        sandbox.forget_usage(self.user_id)
        return f"Deleted {shown}."

    def _share_file(self, arguments: dict) -> str:
        source = sandbox.host_path(self.user_id, self.slot, str(arguments.get("path") or ""), write=True)
        if not source.is_file():
            return "Error: share_file needs a file in your own folder."
        name = re.sub(r"[^\w.\- ]+", "_", str(arguments.get("name") or source.name)).strip(" .")
        if not name:
            return "Error: give the shared file a name."
        owner = db.get_shared_file(self.user_id, name)
        if owner and owner["bit_name"] not in (self.name, "You"):
            return (
                f"Error: {owner['bit_name']} already shared a file called {name}. "
                "Pick a different name."
            )
        if owner and owner["bit_name"] == "You":
            return f"Error: {self._user_name()} put a file called {name} there. Pick a different name."
        size = source.stat().st_size
        target = sandbox.ensure_workspace(self.user_id) / "shared" / name
        existing = target.stat().st_size if target.exists() else 0
        sandbox.check_room(self.user_id, max(0, size - existing))
        self.activity(f"Sharing {name}")
        self.event("tool", f"share_file {name} ({_human_size(size)})")
        shutil.copyfile(source, target)
        db.put_shared_file(self.user_id, name=name, bit_name=self.name, bit_id=self.bit_id, size=size)
        sandbox.forget_usage(self.user_id)
        return f"Shared as /workspace/shared/{name}."

    async def _save_tool(self, arguments: dict) -> str:
        name = _tool_name(str(arguments.get("name") or ""))
        description = " ".join(str(arguments.get("description") or "").split())
        usage = " ".join(str(arguments.get("usage") or "").split())
        if len(name) < 2:
            return "Error: give the tool a short name, like pdf_text."
        if not description or not usage:
            return "Error: save_tool needs a description and usage."
        source = sandbox.host_path(self.user_id, self.slot, str(arguments.get("path") or ""), write=True)
        home = sandbox.host_path(self.user_id, self.slot, ".", write=True)
        if source == home or not source.exists():
            return "Error: save_tool needs a script or a folder inside your own folder."
        size = sandbox._disk_usage(source) if source.is_dir() else source.stat().st_size
        sandbox.check_room(self.user_id, size)
        target = sandbox.ensure_workspace(self.user_id) / "tools" / name
        existing = db.get_tool(self.user_id, name)

        def copy() -> list[str]:
            if target.exists():
                shutil.rmtree(target)
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            else:
                target.mkdir(parents=True)
                shutil.copy2(source, target / source.name)
            return sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())

        files = await asyncio.to_thread(copy)
        db.put_tool(
            self.user_id, name=name, description=description, usage=usage, files=files,
            made_by=self.name, bit_id=self.bit_id,
        )
        sandbox.forget_usage(self.user_id)
        replaced = f" (replacing {existing['made_by']}'s version)" if existing else ""
        self.event("tool", f"save_tool {name}{replaced}: {description}")
        return (
            f"Saved to the toolbox as /workspace/tools/{name}/{replaced}: {', '.join(files)}. "
            f"Every Bit can now use it: {usage}"
        )

    # -- time, watching, and the user --

    async def _rest(self, seconds: float, status: str, activity: str) -> str | None:
        """Sleep for up to `seconds`; a message from the user ends it early and is returned."""
        self.long_running = True
        self.last_rest = activity
        self._set_status(status, activity)
        try:
            return await asyncio.wait_for(self.inbox.get(), timeout=max(0, seconds))
        except asyncio.TimeoutError:
            return None

    async def _wait(self, arguments: dict) -> str:
        reason = _short(str(arguments.get("reason") or ""), 80)
        now = datetime.now().astimezone()
        if arguments.get("until"):
            try:
                wake = _parse_until(str(arguments["until"]))
            except ValueError:
                return "Error: couldn't read that time; use 17:30 or 2026-10-12 09:00."
            seconds = (wake - now).total_seconds()
        else:
            seconds = float(arguments.get("seconds") or 0)
            wake = now + timedelta(seconds=seconds)
        if seconds <= 0:
            return f"That time has already passed. It's now {_clock(now)}."
        if seconds > MAX_WAIT_SECONDS:
            return "Error: you can wait at most 7 days at a time; wait again afterwards."
        label = f"Waiting until {_clock(wake)}" + (f": {reason}" if reason else "")
        self.event("status", label)
        started = time.monotonic()
        message = await self._rest(seconds, "sleeping", label)
        self._rested(time.monotonic() - started)
        now = datetime.now().astimezone()
        if message is not None:
            return f"Woken early, at {_clock(now)}, by a message from {self._user_name()}: {message}"
        return f"Done waiting. It's now {_clock(now)}."

    async def _watch(self, arguments: dict) -> str:
        self._require_sandbox()
        command = str(arguments.get("command") or "").strip()
        if not command:
            return "Error: no command given."
        every = max(MIN_WATCH_INTERVAL, int(arguments.get("every_seconds") or 60))
        hours = float(arguments.get("hours") or 24)
        if hours * 3600 > MAX_WAIT_SECONDS:
            return "Error: you can watch for at most 7 days at a time; watch again afterwards."
        timeout = min(settings.bit_command_timeout, max(30, every * 2))

        self.event("tool", f"watch every {_human_duration(every)}: $ {command}")
        first = await self.sandbox.run(command, timeout)
        if first.exit_code != 0:
            return (
                f"Error: the command failed on its first run (exit code {first.exit_code}), so "
                f"there's nothing to watch yet. Fix it first. Its output:\n{first.output}"
            )
        baseline = first.output.strip()
        started = time.monotonic()
        deadline = started + hours * 3600
        checks, failures = 1, 0
        label = f"Keeping watch: {_short(command, 60)}"
        try:
            while time.monotonic() < deadline:
                message = await self._rest(
                    min(every, deadline - time.monotonic()),
                    "sleeping",
                    f"{label} (check {checks}, last at {_clock(datetime.now().astimezone())})",
                )
                if message is not None:
                    return (
                        f"The watch stopped after {checks} checks over "
                        f"{_human_duration(time.monotonic() - started)}: {self._user_name()} sent "
                        f"you a message: {message}\nThe output was still:\n{baseline}"
                    )
                result = await self.sandbox.run(command, timeout)
                checks += 1
                output = result.output.strip()
                if result.exit_code != 0:
                    failures += 1
                    if failures >= 3:
                        return (
                            f"The watch stopped: the command failed 3 times in a row (exit code "
                            f"{result.exit_code}) after {checks} checks. Its output:\n{output}"
                        )
                    continue
                failures = 0
                if output != baseline:
                    self.event("status", f"Something changed (check {checks})")
                    return (
                        f"The output changed at check {checks}, after "
                        f"{_human_duration(time.monotonic() - started)} "
                        f"(it's now {_clock(datetime.now().astimezone())}).\n"
                        f"Before:\n{baseline}\n\nNow:\n{output}"
                    )
            return (
                f"Nothing changed in {_human_duration(hours * 3600)} ({checks} checks). "
                f"The output stayed:\n{baseline}"
            )
        finally:
            self._rested(time.monotonic() - started)

    async def _ask_user(self, arguments: dict) -> str:
        question = " ".join(str(arguments.get("question") or "").split())
        if not question:
            return "Error: no question given."
        hours = min(float(arguments.get("hours") or DEFAULT_ANSWER_HOURS), MAX_WAIT_SECONDS / 3600)
        self.long_running = True
        self.last_rest = f"Waiting for the answer to your question: {question}"
        asked = db.add_bit_message(db.get_bit(self.bit_id), "question", question)
        self.event("question", question)
        self._set_status("asking", "Waiting for your answer")
        await self.manager.emit(self.user_id, {"type": "question", "message": asked})
        started = time.monotonic()
        try:
            answer = await asyncio.wait_for(self.inbox.get(), timeout=hours * 3600)
        except asyncio.TimeoutError:
            db.answer_question(asked["id"], None)
            self.event("status", f"No answer after {_human_duration(hours * 3600)}")
            return (
                f"No answer after {_human_duration(hours * 3600)}. Use your best judgment, and "
                "say what you decided in your report."
            )
        finally:
            self._rested(time.monotonic() - started)
        return f"{self._user_name()} answered: {answer}"

    async def _notify_user(self, arguments: dict) -> str:
        text = " ".join(str(arguments.get("message") or "").split())
        if not text:
            return "Error: no message given."
        await self.notify(text)
        return "Sent. ZENO tells the user now (or as soon as they're back)."

    async def notify(self, text: str, from_code: bool = False):
        """Tell the user something now, through ZENO (also used by scripts, via the gateway)."""
        update = db.add_bit_message(db.get_bit(self.bit_id), "update", text)
        self.event("update", f"{'[from code] ' if from_code else ''}{text}")
        await self.manager.emit(self.user_id, {"type": "update", "message": update})

    async def call_app_tool(self, name: str, arguments: dict, log: bool = True) -> str:
        if log:
            self.activity(f"Using {name}")
            self.event("tool", f"{name} {_short(json.dumps(arguments), 200)}")
        results: list = []

        async def result_callback(result, *args, **kwargs):
            results.append(result)

        params = SimpleNamespace(
            function_name=name,
            tool_call_id=f"{self.bit_id}-{time.monotonic_ns()}",
            arguments=arguments,
            result_callback=result_callback,
        )
        await self.app_tools[name].handler(params)
        result = results[0] if results else ""
        return result if isinstance(result, str) else json.dumps(result)


# --- The manager -------------------------------------------------------------------------------


class BitManager:
    """Starts, tracks, messages and stops every user's Bits, and tells ZENO what they say."""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._workers: dict[str, BitWorker] = {}
        # The last worker in each (user, slot), kept after its task ends for follow-ups.
        self._idle: dict[tuple[str, int], BitWorker] = {}
        self._finished: dict[str, asyncio.Event] = {}
        self._stopped_by_user: set[str] = set()
        self._listeners: dict[str, set[Listener]] = {}
        self._shutting_down = False

    def subscribe(self, user_id: str, listener: Listener) -> Callable[[], None]:
        """Call listener(event) for each thing the user should hear about (see Listener)."""
        self._listeners.setdefault(user_id, set()).add(listener)
        return lambda: self._listeners.get(user_id, set()).discard(listener)

    async def emit(self, user_id: str, event: dict):
        message = event.get("message")
        if message:
            event.setdefault("bit", db.get_bit(message["bit_id"]))
        delivered = False
        for listener in list(self._listeners.get(user_id, ())):
            try:
                delivered = bool(await listener(event)) or delivered
            except Exception as e:
                logger.error(f"Bit listener failed: {e}")
        if delivered and message and event["type"] in ("question", "update"):
            db.mark_messages_delivered([message["id"]])

    def resolve(self, user_id: str, name_or_id: str) -> dict | None:
        """A Bit by id, or by name ("BIT-02", "bit 2", "2"): the newest task in that slot."""
        bit = db.get_bit(name_or_id, user_id)
        if bit:
            return bit
        match = re.search(r"(\d+)", name_or_id)
        return db.latest_bit_in_slot(user_id, int(match.group(1))) if match else None

    def _free_slots(self, user_id: str) -> list[int]:
        busy = {bit["slot"] for bit in db.active_bits(user_id)}
        return [slot for slot in range(1, settings.max_bits + 1) if slot not in busy]

    async def start(
        self,
        user: dict,
        *,
        task: str,
        title: str,
        wait_for: list[str] | None = None,
        source: str = "zeno",
        slot: int | None = None,
    ) -> dict:
        user_id = user["id"]
        task, title = task.strip(), title.strip()[:80] or "Task"
        if not task:
            raise BitError("The task is empty.")
        free = self._free_slots(user_id)
        if not free:
            raise BitError(
                f"All {settings.max_bits} Bits are busy. Wait for one to finish or stop one."
            )
        dependencies = []
        for name in wait_for or []:
            dependency = self.resolve(user_id, name)
            if not dependency:
                raise BitError(f"There's no Bit called {name}.")
            dependencies.append(dependency["id"])

        bit = db.create_bit(
            user_id,
            slot=slot if slot in free else free[0],
            title=title,
            task=task,
            wait_for=dependencies,
            source=source,
        )
        db.add_bit_event(bit["id"], "status", f"Task received: {title}")
        self._idle.pop((user_id, bit["slot"]), None)
        self._launch(user, bit, BitWorker(self, user, bit))
        logger.info(f"{bit['name']} started for {user['email']}: {title}")
        return bit

    def _launch(self, user: dict, bit: dict, worker: BitWorker, **kwargs):
        self._finished[bit["id"]] = asyncio.Event()
        self._workers[bit["id"]] = worker
        self._tasks[bit["id"]] = asyncio.create_task(
            self._run(user, bit, worker, **kwargs), name=bit["name"]
        )

    async def message(self, user: dict, bit: dict, text: str, via: str = "zeno") -> dict:
        """Send a Bit a message from the user (via "zeno" or the "dashboard"): the answer to its
        open question, news for it while it works, or a follow-up task once it's done. Returns
        what happened."""
        text = text.strip()
        if not text:
            raise BitError("The message is empty.")
        bit_id = bit["id"]
        worker = self._workers.get(bit_id)
        if worker and bit["status"] in db.ACTIVE_BIT_STATUSES:
            question = db.open_question_for(bit_id)
            if question:
                db.answer_question(question["id"], text)
                worker.event("answer", text)
                worker.inbox.put_nowait(text)
                answered = db.get_bit_message(question["id"])
                await self.emit(user["id"], {"type": "answered", "message": answered, "via": via})
                return {"result": "answered", "text": f"{bit['name']} has the answer and is back to work."}
            db.add_bit_message(bit, "message", text)
            worker.event("message", text)
            worker.inbox.put_nowait(text)
            woke = " It was waiting, so it's on it now." if bit["status"] == "sleeping" else ""
            return {"result": "delivered", "text": f"{bit['name']} got the message.{woke}"}
        if bit["status"] in db.ACTIVE_BIT_STATUSES:
            raise BitError(f"{bit['name']} is just starting; try again in a moment.")

        # A finished task: follow up on it.
        user_id = user["id"]
        idle = self._idle.get((user_id, bit["slot"]))
        if idle and idle.bit_id == bit_id and bit["slot"] in self._free_slots(user_id):
            del self._idle[(user_id, bit["slot"])]
            db.update_bit(
                bit_id, status="starting", result=None, error=None, finished_at=None,
                reported=0, activity="Picking up a follow-up", started_at=time.time(),
            )
            db.add_bit_message(bit, "message", text)
            db.add_bit_event(bit_id, "message", f"Follow-up: {text}")
            self._launch(user, db.get_bit(bit_id), idle, follow_up=text)
            return {"result": "resumed", "text": f"{bit['name']} is on it, picking up where it left off."}
        outcome = bit["result"] or bit["error"] or bit["status"]
        task = (
            f"This is a follow-up to an earlier task by {bit['name']}, \"{bit['title']}\".\n\n"
            f"The earlier task:\n{bit['task']}\n\nHow it ended: {outcome}\n\n"
            f"Files from that work may still be in {sandbox.bit_home(bit['slot'])} or "
            f"/workspace/shared.\n\nThe new request:\n{text}"
        )
        new = await self.start(user, task=task, title=bit["title"], slot=bit["slot"])
        return {"result": "started", "text": f"{new['name']} is on it.", "bit": new}

    async def stop(self, user_id: str, bit_id: str) -> dict | None:
        bit = db.get_bit(bit_id, user_id)
        if not bit:
            return None
        task = self._tasks.get(bit_id)
        if task and not task.done():
            self._stopped_by_user.add(bit_id)
            task.cancel()
            await asyncio.wait([task], timeout=60)
        return db.get_bit(bit_id)

    async def shutdown(self):
        self._shutting_down = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=60)

    async def resume_interrupted(self):
        """At startup: Bits that were waiting, keeping watch or waiting on the user when the
        server stopped start over on their task; others that were cut off mid-work fail."""
        for bit in db.interrupted_bits():
            db.drop_open_questions(bit["id"])
            user = db.get_user(bit["user_id"])
            if not user or bit["status"] not in db.RESUMABLE_BIT_STATUSES:
                db.fail_bit(bit["id"], "The ZENO server restarted while this Bit was working.")
                logger.warning(f"{bit['name']} was interrupted by the restart: {bit['title']}")
                continue
            was = (bit["activity"] or "").removeprefix("Paused while ZENO restarts.").strip()
            note = (
                "Note: ZENO's server restarted while you were on this task"
                + (f". Right before, you were: {was}" if was else "")
                + ". Files in your folder are still there. Check what's already done (don't "
                "repeat anything already sent or created), then carry on: if you were waiting "
                "until a time, wait until that same time; if you were keeping watch, watch again; "
                "if you had asked a question, ask it again."
            )
            db.update_bit(
                bit["id"], status="starting", activity="Resuming after a restart", error=None,
                started_at=time.time(),
            )
            db.add_bit_event(bit["id"], "status", "Resuming after a server restart")
            fresh = db.get_bit(bit["id"])
            self._launch(user, fresh, BitWorker(self, user, fresh), note=note)
            logger.info(f"{bit['name']} resumed after the restart: {bit['title']}")

    async def _wait_for_dependencies(self, bit: dict):
        db.update_bit(bit["id"], status="waiting", activity="Waiting for other Bits")
        for dependency_id in bit["wait_for"]:
            event = self._finished.get(dependency_id)
            if event:
                await event.wait()
            dependency = db.get_bit(dependency_id)
            if dependency and dependency["status"] != "done":
                raise BitFailed(f"{dependency['name']}, which it was waiting for, didn't finish.")
        db.update_bit(bit["id"], status="starting")

    async def _run(
        self, user: dict, bit: dict, worker: BitWorker, follow_up: str | None = None,
        note: str | None = None,
    ):
        bit_id = bit["id"]
        paused = False
        try:
            if follow_up is not None:
                result = await worker.follow_up(follow_up)
            else:
                if bit["wait_for"] and not note:
                    await self._wait_for_dependencies(bit)
                db.update_bit(bit_id, started_at=time.time())
                result = await worker.start(note)
            db.update_bit(bit_id, status="done", result=result)
            db.add_bit_event(bit_id, "result", result)
        except asyncio.CancelledError:
            if bit_id in self._stopped_by_user:
                db.update_bit(bit_id, status="stopped", error="Stopped by you.")
                db.add_bit_event(bit_id, "status", "Stopped by you.")
            elif self._shutting_down and worker.long_running:
                # It picks this task up again when the server is back (resume_interrupted).
                paused = True
                was = worker.last_rest or "Resting"
                db.update_bit(bit_id, status="paused", activity=f"Paused while ZENO restarts. {was}")
                db.add_bit_event(bit_id, "status", f"Paused while ZENO restarts ({was})")
            else:
                db.update_bit(bit_id, status="stopped", error="The server shut down.")
                db.add_bit_event(bit_id, "status", "The server shut down.")
        except (BitFailed, SandboxError) as e:
            db.update_bit(bit_id, status="failed", error=str(e))
            db.add_bit_event(bit_id, "error", str(e))
        except Exception as e:
            logger.exception(f"{bit['name']} crashed")
            db.update_bit(bit_id, status="failed", error=f"Unexpected error: {e}")
            db.add_bit_event(bit_id, "error", f"Unexpected error: {e}")
        finally:
            try:
                await worker._close()
            except Exception as e:
                logger.error(f"{bit['name']}: cleanup failed: {e}")
            db.drop_open_questions(bit_id)
            self._stopped_by_user.discard(bit_id)
            self._tasks.pop(bit_id, None)
            self._workers.pop(bit_id, None)
            self._finished.pop(bit_id, asyncio.Event()).set()
            if not paused:
                db.update_bit(bit_id, activity=None, finished_at=time.time())
                if worker.chat is not None:
                    self._idle[(user["id"], bit["slot"])] = worker
                finished = db.get_bit(bit_id)
                logger.info(f"{finished['name']} {finished['status']}: {finished['title']}")
                await self.emit(user["id"], {"type": "finished", "bit": finished})


manager = BitManager()


# --- Words for ZENO ----------------------------------------------------------------------------


def outcome_text(bit: dict) -> str:
    """How a finished Bit's task went, in a line ZENO can relay."""
    if bit["status"] == "done":
        return f"{bit['name']} finished \"{bit['title']}\". Its report: {bit['result']}"
    if bit["status"] == "stopped":
        return f"{bit['name']} was stopped while working on \"{bit['title']}\"."
    return f"{bit['name']} couldn't finish \"{bit['title']}\": {bit['error']}"


def status_line(bit: dict) -> str:
    if bit["status"] not in db.ACTIVE_BIT_STATUSES:
        return outcome_text(bit)
    if bit["status"] == "asking":
        question = db.open_question_for(bit["id"])
        if question:
            return (
                f"{bit['name']} (on \"{bit['title']}\") is waiting for the user's answer to: "
                f"{question['text']}"
            )
    elapsed = int(time.time() - (bit["started_at"] or bit["created_at"]))
    doing = bit["activity"] or bit["status"]
    return (
        f"{bit['name']} is working on \"{bit['title']}\" ({doing}; "
        f"{_human_duration(elapsed)} so far)."
    )
