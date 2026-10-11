"""Tools ZENO's voice agent can call: the time, its Bits, and its memory of the user.

The user's connected apps (Gmail, Calendar, ... via MCP) are added alongside these in bot.py.
"""

from datetime import datetime

from pipecat.services.llm_service import FunctionCallParams

from zeno_server import bits, db, memory


async def get_current_time(params: FunctionCallParams):
    """Get the current local date and time on the ZENO server."""
    now = datetime.now().astimezone()
    await params.result_callback(
        {"iso": now.isoformat(timespec="seconds"), "spoken": now.strftime("%A, %B %-d, %-I:%M %p")}
    )


def session_tools(user: dict) -> list:
    """The tools for one user's voice session; they act on that user's Bits and memory."""
    user_id = user["id"]

    async def start_bit(
        params: FunctionCallParams, task: str, title: str, wait_for: list[str] | None = None
    ):
        """Hand a job to a Bit: a worker AI that does it in the background while you keep talking.

        A Bit has full freedom: it writes and runs code, uses the user's apps and their APIs,
        searches the web, downloads and reads files, attachments, images and PDFs, builds its
        own tools, and keeps going for hours or days (timers, reminders, watching for something
        to happen). Use it for anything beyond a quick lookup: attachments and files, research,
        multi-step work, code, reports, and anything about later, like "remind me", "let me
        know when", "keep an eye on" or "every morning".

        Args:
            task: Complete instructions. The Bit can't hear this conversation, so include every
                detail it needs (names, email addresses, dates, what to look for), what done
                looks like, and how to report: tell the user right away when something happens,
                keep watching, or report once at the end.
            title: A short name for the job, two to five words.
            wait_for: Bits (like "BIT-01") that must finish first; their reports are passed on.
        """
        try:
            bit = await bits.manager.start(
                user, task=task, title=title, wait_for=wait_for or [], source="zeno"
            )
        except bits.BitError as e:
            await params.result_callback({"error": str(e)})
            return
        waiting = f" It starts when {', '.join(wait_for)} finish." if wait_for else ""
        await params.result_callback(
            {"bit": bit["name"], "status": f"{bit['name']} is on it.{waiting}"}
        )

    async def message_bit(params: FunctionCallParams, bit: str, message: str):
        """Send one of your Bits a message: the user's answer to its question, new details or a
        change of plan while it works, or a follow-up job for a Bit that finished (it continues
        with its memory and files from the earlier work, e.g. "now open that attachment").

        Args:
            bit: The Bit's name, like "BIT-01".
            message: What to tell it, complete and self-contained.
        """
        found = bits.manager.resolve(user_id, bit)
        if not found:
            await params.result_callback({"error": f"There's no Bit called {bit}."})
            return
        try:
            outcome = await bits.manager.message(user, found, message, via="zeno")
        except bits.BitError as e:
            await params.result_callback({"error": str(e)})
            return
        await params.result_callback({"bit": found["name"], "result": outcome["text"]})

    async def bit_status(params: FunctionCallParams):
        """Check on the user's Bits: what each is doing, what they're waiting on, and how recent
        jobs ended."""
        recent = db.list_bits(user_id, limit=5)
        lines = [bits.status_line(bit) for bit in recent]
        await params.result_callback({"bits": lines or ["No Bits have worked on anything yet."]})

    async def stop_bit(params: FunctionCallParams, bit: str):
        """Stop a Bit that's working on a job (or waiting, or keeping watch).

        Args:
            bit: The Bit's name, like "BIT-02".
        """
        found = bits.manager.resolve(user_id, bit)
        if not found or found["status"] not in db.ACTIVE_BIT_STATUSES:
            await params.result_callback({"error": f"{bit} isn't working on anything."})
            return
        await bits.manager.stop(user_id, found["id"])
        await params.result_callback({"stopped": found["name"]})

    async def remember(params: FunctionCallParams, fact: str, kind: str = "other"):
        """Save something about the user to your long-term memory, for future conversations.

        Use it when the user asks you to remember something, or shares a lasting fact or
        preference worth keeping. Don't store secrets like passwords.

        Args:
            fact: One short sentence about the user, e.g. "Prefers meetings after 10 AM."
            kind: One of profile, preference, work, people or other.
        """
        saved = memory.remember(user_id, fact, kind, source="told")
        await params.result_callback({"saved": saved["text"]})

    async def forget(params: FunctionCallParams, about: str):
        """Delete what you remember about something, when the user asks you to forget it.

        Args:
            about: What to forget, in a few words, e.g. "my old job".
        """
        removed = memory.forget(user_id, about)
        await params.result_callback(
            {"forgot": [m["text"] for m in removed]} if removed else {"error": "Nothing matched."}
        )

    async def recall(params: FunctionCallParams, query: str):
        """Search your memory of the user and your past conversations with them.

        Args:
            query: What to look for, in a few words.
        """
        memories = [m["text"] for m in db.search_memories(user_id, query)]
        words = set(db.words(query))
        conversations = [
            f"{datetime.fromtimestamp(c['started_at']):%b %-d}: {c['summary']}"
            for c in db.list_conversations(user_id, limit=100)
            if c["summary"] and words & set(db.words(f"{c['title']} {c['summary']}"))
        ][:5]
        await params.result_callback(
            {"memories": memories, "conversations": conversations}
            if memories or conversations
            else {"result": "Nothing found."}
        )

    return [start_bit, message_bit, bit_status, stop_bit, remember, forget, recall]


TOOLS = [get_current_time]
