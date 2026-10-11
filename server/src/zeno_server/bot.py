"""ZENO voice pipeline: SmallWebRTC -> Silero VAD -> Whisper -> LLM -> Kokoro or Pocket TTS -> SmallWebRTC."""

import asyncio
import os
import random
import time
from datetime import datetime

from loguru import logger

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMContextFrame,
    LLMMessagesAppendFrame,
    TTSSpeakFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIObserverParams
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.processors.frameworks.rtvi.observer import RTVIFunctionCallReportLevel
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from zeno_server import bits, connectors, db, memory, speech, voices
from zeno_server import llm as models
from zeno_server.config import settings
from zeno_server.reply_guard import ReplyGuard
from zeno_server.tools import TOOLS, session_tools

SYSTEM_PROMPT = """You are ZENO, a voice-first personal AI assistant. Everything you say is
spoken aloud by a text-to-speech voice, so talk like a person on a phone call.

How to speak:
- Keep replies short and natural: usually one or two sentences.
- Never use markdown, numbered or bulleted lists, bold text, headings, emojis, links, or code.
- Never read out IDs, URLs, or raw data.
- When there are several items, sum them up in one sentence and name only the two or three
  that matter most, for example: "You have three new emails: Pipedream wants you to confirm
  your address, Google sent a security alert, and Luis wrote about a PTO error." Then offer
  more detail.
- Say dates and times the way people do, like "yesterday at 4 PM".
- Don't comment on yourself, your tools, or whether things are working; just answer.
- The user's words come from speech recognition, which can mishear. "Bit" often arrives as
  "bid", "bit's", "bet" or "beat", and "ZENO" as "Zeno" or "Gino"; read them as meant. If a
  message is only filler like "um" or "so", say a few words and let the user go on.

How to act:
- You have no built-in knowledge of live information such as emails or calendars. For anything
  like that, call the matching tool first and answer from its result; never guess.
- When the user asks you to do something with a tool, such as sending an email or creating an
  event, do it right away without asking for confirmation, unless a required detail is
  missing.
- When you call a tool, call it straight away without saying anything first; a short "let me
  check" is spoken for you.
- Lookups in the user's apps run in the background, so the conversation never has to stop.
  While one is still running (its result shows as IN_PROGRESS), don't say again that you're
  checking: briefly ask if there's anything else meanwhile, and keep helping with whatever the
  user says next. As soon as a result comes in, give the answer.
- For the exact current time, call get_current_time.

You and your Bits:
- You're the boss of up to {max_bits} Bits (BIT-01, BIT-02, ...): worker AIs that do jobs in the
  background while you keep talking. A Bit has full freedom: it writes and runs code, uses the
  user's apps and their APIs, searches the web, downloads and reads files, attachments, images
  and PDFs, builds its own tools, and keeps going for hours or days: timers, reminders, and
  watching for something to happen.
- Do it yourself only when it's quick: answering from what you know, the time, one lookup in an
  app (like "any new email?" or "what's on today?"), or one simple action the user asked for
  (send this email, add this event).
- Hand everything else to a Bit with start_bit, without asking first: anything with
  attachments, files, images or documents; research; anything that takes several steps; and
  anything about later, like "remind me", "let me know when", "keep an eye on" or "every
  morning". Never tell the user something can't be done because it needs code, waiting or
  watching: that's what Bits are for.
- Write the task so the Bit can do it without hearing this conversation: exactly who and what
  (names, email addresses, dates), what done looks like, and how to report (tell the user the
  moment something happens, keep watching, or report once at the end).
- After handing off, say in one short sentence which Bit is on it, then carry on with the user.
- Follow-ups go to the same Bit: when the user wants more on something a Bit did (like "open
  that attachment"), send that Bit the request with message_bit; it remembers its work. Use
  message_bit too for new details or a change of plan for a Bit that's working.
- Lines in square brackets aren't from the user:
  "[Bit question]": a Bit is stuck until the user answers; the question is read out to the
  user for you. When they answer, call message_bit with that Bit's name and their answer.
  Never say you passed something on without calling message_bit.
  "[Bit update]": a Bit's news, already read out to the user; answer any follow-up questions.
  "[Bit finished]": a Bit is done. Tell the user the outcome briefly.
  "[Reminder]": a Bit's question is still open. Answer the user first, then ask it again.
  "[Note]": something to keep in mind; don't reply to it.
- Use bit_status when the user asks how the Bits are doing, and stop_bit to stop one.

Your memory:
- You remember the user across conversations; what you know is listed below. Use it naturally,
  without announcing that you remember things.
- When the user asks you to remember something, or tells you a lasting fact or preference,
  call remember. When they ask you to forget something, call forget. To look up something
  older, like what you talked about before, call recall."""

# Messages added to the conversation that aren't the user's words (Bit news, reminders).
NOTE_PREFIXES = ("[Bit ", "[Reminder]", "[Note]")
APP_TOOL_TIMEOUT = 120
# A session whose app never finishes connecting (it closed, or the network blocked it) ends
# after this long instead of waiting forever.
CONNECT_TIMEOUT = 30
# Tools that answer instantly, so no "one moment" is spoken for them.
QUICK_TOOLS = {
    "get_current_time", "start_bit", "message_bit", "bit_status", "stop_bit",
    "remember", "forget", "recall",
}
# Bit results older than this aren't brought up in a new conversation.
UNREPORTED_WINDOW = 24 * 3600


def _local_time_zone() -> str:
    """The server's IANA time zone name, e.g. America/Chicago."""
    try:
        return os.readlink("/etc/localtime").split("zoneinfo/")[-1]
    except OSError:
        return datetime.now().astimezone().tzname() or "UTC"


def build_system_prompt(
    user: dict,
    connected_apps: list[str],
    unconnected_apps: list[str],
    unreported: list[dict],
    updates: list[dict] | None = None,
    questions: list[dict] | None = None,
) -> str:
    now = datetime.now().astimezone()
    apps = ", ".join(connected_apps) if connected_apps else "none yet"
    name = user.get("name") or user["email"]
    # Only the date (not the time) goes in: an identical prompt prefix lets the LLM server reuse
    # its cache across sessions instead of re-reading the whole prompt and tool list.
    prompt = (
        f"{SYSTEM_PROMPT.format(max_bits=settings.max_bits)}\n\n"
        f"You're talking with {name} ({user['email']}).\n"
        f"Today is {now:%A, %B %-d, %Y} (time zone {_local_time_zone()}, UTC offset {now:%z}).\n"
        f"Connected apps: {apps}."
    )
    if unconnected_apps:
        prompt += (
            f"\nAdded but not connected yet: {', '.join(unconnected_apps)}. You can't use these; "
            "if the user asks for them, tell them to connect the account in the MCP Store."
        )

    known = memory.prompt_section(user["id"])
    prompt += f"\n\nWhat you know about {name}:\n{known or 'Nothing yet.'}"
    recent = memory.recent_work_section(user["id"])
    if recent:
        prompt += f"\n\nYour recent conversations with {name}:\n{recent}"

    working = [bits.status_line(bit) for bit in db.active_bits(user["id"])]
    if working:
        prompt += "\n\nBits working right now:\n" + "\n".join(f"- {line}" for line in working)
    if questions:
        prompt += (
            "\n\nBits waiting for the user's answer (ask them, then send the answers with "
            "message_bit):\n"
            + "\n".join(f"- {q['bit_name']} (on \"{q['bit_title']}\") asks: {q['text']}" for q in questions)
        )
    if unreported:
        prompt += (
            "\n\nWhile you were away, these Bits finished. Mention them briefly early on:\n"
            + "\n".join(f"- {bits.outcome_text(bit)}" for bit in unreported)
        )
    if updates:
        prompt += (
            "\n\nWhile you were away, Bits sent these updates. Mention them briefly early on:\n"
            + "\n".join(f"- {u['bit_name']}: {u['text']}" for u in updates)
        )
    return prompt


def _greeting(user: dict, questions: list[dict]) -> str:
    first_name = (user.get("name") or "").split(" ")[0]
    hello = f"Hi {first_name}" if first_name else "Hi"
    if questions:
        # Lead with what's blocking a Bit; the other questions come up as the talk goes on.
        first = questions[0]
        waiting = "A couple of Bits are waiting on you." if len(questions) > 1 else ""
        return f"{hello}. {waiting} {first['bit_name']} has a question: {first['text']}".replace("  ", " ")
    if first_name:
        return random.choice(
            [f"Hi {first_name}, ZENO here. What can I do for you?", f"Hey {first_name}! What's up?"]
        )
    return "Hi, I'm ZENO. How can I help?"


# Spoken the moment a tool call starts, so the user isn't left in silence while it runs.
FILLERS = {
    "mail_read": ["Let me check your inbox.", "Checking your email, one sec.", "Let me take a look."],
    "mail_send": ["Sending that now.", "On it, sending it."],
    "calendar_read": ["Let me look at your calendar.", "Checking your calendar, one sec."],
    "calendar_write": ["Updating your calendar.", "On it."],
    "other": ["One moment.", "Let me check.", "Give me a second."],
}


def _filler(function_names: list[str]) -> str | None:
    if not function_names or all(name in QUICK_TOOLS for name in function_names):
        return None
    names = " ".join(function_names).lower()
    mail = any(word in names for word in ("mail", "outlook", "gmail"))
    calendar = "calendar" in names or "event" in names
    write = any(word in names for word in ("send", "create", "update", "delete", "draft", "reply"))
    if calendar:
        kind = "calendar_write" if write else "calendar_read"
    elif mail:
        kind = "mail_send" if write else "mail_read"
    else:
        kind = "other"
    return random.choice(FILLERS[kind])


class ToolEventsObserver(BaseObserver):
    """Announces each tool call's start and finish as a plain RTVI server message.

    The web client reads RTVI's function-call events, but the Android SDK only understands the
    older event the server no longer sends, so clients get this simpler message too.
    """

    def __init__(self):
        super().__init__()
        self.worker: PipelineWorker | None = None
        self._seen: set[int] = set()

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, FunctionCallInProgressFrame):
            status = "running"
        elif isinstance(frame, FunctionCallResultFrame):
            status = "done"
        else:
            return
        # Observers see a frame once per hop through the pipeline; announce it once.
        if frame.id in self._seen or not self.worker:
            return
        self._seen.add(frame.id)
        message = {
            "type": "tool-call",
            "id": frame.tool_call_id,
            "name": frame.function_name,
            "status": status,
        }
        await self.worker.queue_frames([RTVIServerMessageFrame(data=message)])


def _question_note(bit_name: str, title: str, question: str) -> str:
    return (
        f"[Bit question] {bit_name} needs the user's answer to keep going on \"{title}\". Ask the "
        f"user now, in your own words: {question} When they answer, call message_bit with bit "
        f"\"{bit_name}\" and their answer."
    )


class QuestionReminder(FrameProcessor):
    """ZENO's checklist of Bit questions the user hasn't answered.

    Sits between the user's turns and the LLM. Right after a question is put to the user, their
    next turn gets a note to pass the answer on with message_bit. If the user moves on without
    answering, a "[Reminder]" follows every few turns (a few times at most) so ZENO asks again.
    Answered questions drop off by themselves.
    """

    REMIND_AFTER_TURNS = 3
    MAX_REMINDERS = 3

    def __init__(self, user_id: str, **kwargs):
        super().__init__(**kwargs)
        self._user_id = user_id
        self._turns: dict[int, int] = {}
        self._reminders: dict[int, int] = {}

    def raised(self, question_id: int):
        self._turns[question_id] = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMContextFrame):
            self._check(frame.context)
        await self.push_frame(frame, direction)

    def _check(self, context: LLMContext):
        messages = context.get_messages()
        last = messages[-1] if messages else None
        if not isinstance(last, dict) or last.get("role") != "user":
            return
        content = last.get("content")
        if not isinstance(content, str) or content.startswith(NOTE_PREFIXES):
            return
        open_questions = db.open_questions(self._user_id)
        still_open = {q["id"] for q in open_questions}
        for question_id in list(self._turns):
            if question_id not in still_open:
                del self._turns[question_id]
        hints, reminders = [], []
        for question in open_questions:
            if question["id"] not in self._turns:
                continue  # not put to the user yet in this conversation
            turns = self._turns[question["id"]] + 1
            self._turns[question["id"]] = turns
            if turns == 1:
                hints.append(question)
            elif turns >= self.REMIND_AFTER_TURNS:
                sent = self._reminders.get(question["id"], 0)
                if sent < self.MAX_REMINDERS:
                    reminders.append(question)
                    self._reminders[question["id"]] = sent + 1
                    self._turns[question["id"]] = 0
        for question in hints:
            context.add_message(
                {
                    "role": "user",
                    "content": f"[Note] {question['bit_name']} is waiting for the user's answer to: "
                    f"{question['text']} If the user's last message answers it, call message_bit "
                    f"with bit \"{question['bit_name']}\" and the answer, then confirm in a few "
                    "words. Otherwise just reply to the user.",
                }
            )
        if reminders:
            lines = " ".join(
                f"{q['bit_name']} is still waiting for the user's answer to: {q['text']}"
                for q in reminders
            )
            context.add_message(
                {
                    "role": "user",
                    "content": f"[Reminder] {lines} Reply to the user first, then ask it again; "
                    "send the answer with message_bit.",
                }
            )


def warm_up_models():
    """Load Whisper and the voices once at startup so the first session doesn't wait on them."""
    speech.warm_up()
    voices.warm_up()
    logger.info("Speech models ready")


def _spoken_messages(context: LLMContext) -> list[dict]:
    """What the user and ZENO said, without tool calls, tool results or Bit news."""
    spoken = []
    for message in context.get_messages():
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        text = (content or "").strip()
        if text and not text.startswith(NOTE_PREFIXES):
            spoken.append({"role": message["role"], "text": text})
    return spoken


async def _learn(user: dict, conversation_id: str, messages: list[dict]):
    try:
        await memory.learn_from_conversation(user, conversation_id, messages)
    except Exception as e:
        logger.error(f"Memory: couldn't learn from conversation {conversation_id}: {e}")


# Background memory work outlives the session that started it.
_background: set[asyncio.Task] = set()


async def run_bot(webrtc_connection: SmallWebRTCConnection, user: dict):
    user = db.get_user(user["id"]) or user
    mcp_tools, connected_apps, unconnected_apps = await connectors.load_tools(user["id"])
    for tool in mcp_tools:
        # App lookups can take seconds. Run them in the background: ZENO keeps talking and
        # answers once the result arrives (Pipecat holds it until nobody is speaking).
        tool_options(cancel_on_interruption=False, timeout_secs=APP_TOOL_TIMEOUT)(tool.handler)
    since = time.time() - UNREPORTED_WINDOW
    unreported = db.unreported_bits(user["id"], since=since)
    db.mark_bits_reported([bit["id"] for bit in unreported])
    updates = db.undelivered_updates(user["id"], since=since)
    db.mark_messages_delivered([update["id"] for update in updates])
    questions = db.open_questions(user["id"])
    db.mark_messages_delivered([question["id"] for question in questions])
    conversation_id = db.start_conversation(user["id"])

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    stt = speech.create_stt()

    llm = models.voice_llm(
        build_system_prompt(user, connected_apps, unconnected_apps, unreported, updates, questions)
    )

    tts = voices.create_tts(user.get("voice"))

    context = LLMContext(tools=[*TOOLS, *session_tools(user), *mcp_tools])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    reminder = QuestionReminder(user["id"])
    for question in questions:
        reminder.raised(question["id"])

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            reminder,
            llm,
            ReplyGuard(),
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    tool_events = ToolEventsObserver()
    worker = PipelineWorker(
        pipeline,
        observers=[tool_events],
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        processor_unusable_policy=ProcessorUnusablePolicy.END,
        # Send tool names, arguments and results to the dashboard (hidden by default).
        rtvi_observer_params=RTVIObserverParams(
            function_call_report_level={"*": RTVIFunctionCallReportLevel.FULL}
        ),
    )

    tool_events.worker = worker

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    async def on_bit_event(event: dict) -> bool:
        """News from a Bit: tell the user now, and let the apps know.

        Questions and short updates are spoken word for word (fast, and nothing gets lost in
        the retelling), with a note in the conversation so ZENO knows what was said. A finished
        task's report goes through the LLM, which sums it up.
        """
        kind, bit = event["type"], event["bit"]
        message = event.get("message")
        notice = {
            "type": "bit",
            "event": kind,
            "id": bit["id"],
            "name": bit["name"],
            "title": bit["title"],
            "status": bit["status"],
        }
        frames: list = [RTVIServerMessageFrame(data=notice)]
        if kind == "finished":
            db.mark_bits_reported([bit["id"]])
            text = f"[Bit finished] {bits.outcome_text(bit)}"
            frames.append(
                LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}], run_llm=True)
            )
        elif kind == "question":
            notice.update(text=message["text"], question_id=message["id"])
            note = _question_note(bit["name"], bit["title"], message["text"])
            frames += [
                LLMMessagesAppendFrame(messages=[{"role": "user", "content": note}], run_llm=False),
                TTSSpeakFrame(f"{bit['name']} has a question. {message['text']}"),
            ]
            reminder.raised(message["id"])
        elif kind == "update":
            notice["text"] = message["text"]
            note = f"[Bit update] {bit['name']} (on \"{bit['title']}\") says: {message['text']}"
            frames += [
                LLMMessagesAppendFrame(messages=[{"role": "user", "content": note}], run_llm=False),
                TTSSpeakFrame(f"{bit['name']} says: {message['text']}"),
            ]
        elif kind == "answered":
            notice.update(text=message["answer"], question_id=message["id"])
            if event.get("via") != "zeno":
                # Answered on the dashboard: ZENO shouldn't ask it again.
                note = (
                    f"[Note] The user answered {bit['name']}'s question on the dashboard: "
                    f"{message['answer']}"
                )
                frames.append(
                    LLMMessagesAppendFrame(messages=[{"role": "user", "content": note}], run_llm=False)
                )
        else:
            return False
        await worker.queue_frames(frames)
        return True

    unsubscribe = bits.manager.subscribe(user["id"], on_bit_event)

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        filler = _filler([call.function_name for call in function_calls])
        if filler:
            await tts.queue_frame(TTSSpeakFrame(filler, append_to_context=False))

    connected = asyncio.Event()

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        connected.set()
        await worker.queue_frames([TTSSpeakFrame(_greeting(user, questions))])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await runner.cancel()

    async def end_if_never_connected():
        try:
            await asyncio.wait_for(connected.wait(), CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"The app didn't connect within {CONNECT_TIMEOUT}s; ending the session")
            await runner.cancel()

    watchdog = asyncio.create_task(end_if_never_connected())
    try:
        await runner.run()
    finally:
        watchdog.cancel()
        unsubscribe()
        messages = _spoken_messages(context)
        db.end_conversation(conversation_id, messages)
        db.prune_empty_conversations(user["id"])
        if any(m["role"] == "user" for m in messages):
            task = asyncio.create_task(_learn(user, conversation_id, messages))
            _background.add(task)
            task.add_done_callback(_background.discard)
