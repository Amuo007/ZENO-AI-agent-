"""ZENO's long-term memory of each user.

Memories come from three places:
- learned: after every voice conversation, the LLM reads the transcript and adds, updates or
  removes durable facts (who the user is, preferences, people, what they're working on);
- told: the user asks ZENO to remember something (the `remember` tool);
- added: the user writes one on the dashboard.

Every memory goes into ZENO's system prompt (and the Bits'), and the user can see, edit and
delete all of them on the dashboard, or turn learning off.
"""

import json
import re
import time
from datetime import datetime

from loguru import logger

from zeno_server import db, llm

KIND_TITLES = {
    "profile": "About them",
    "preference": "Preferences",
    "work": "What they're working on",
    "people": "People in their life",
    "other": "Other",
}
# Keeps the voice prompt (and first-reply latency) in check.
MAX_PROMPT_MEMORIES = 80
MAX_TRANSCRIPT_CHARS = 16000


def prompt_section(user_id: str, *, limit: int = MAX_PROMPT_MEMORIES) -> str:
    """The user's memories, grouped by kind, for a system prompt. Empty when there are none."""
    memories = db.list_memories(user_id)[:limit]
    if not memories:
        return ""
    lines = []
    for kind, title in KIND_TITLES.items():
        group = [m["text"] for m in memories if m["kind"] == kind]
        if group:
            lines.append(f"{title}:")
            lines.extend(f"- {text}" for text in group)
    return "\n".join(lines)


def _similar(a: str, b: str) -> bool:
    words_a, words_b = set(db.words(a)), set(db.words(b))
    if not words_a or not words_b:
        return a.strip().lower() == b.strip().lower()
    return len(words_a & words_b) / min(len(words_a), len(words_b)) >= 0.8


def remember(user_id: str, text: str, kind: str = "other", source: str = "told") -> dict:
    """Save a memory, or refresh a near-identical one instead of storing it twice."""
    text = text.strip()
    for memory in db.list_memories(user_id):
        if _similar(memory["text"], text):
            return db.update_memory(user_id, memory["id"], text=text, kind=kind)
    return db.add_memory(user_id, kind=kind, text=text, source=source)


def forget(user_id: str, about: str) -> list[dict]:
    """Delete the memories that best match a description; returns what was deleted."""
    matches = db.search_memories(user_id, about, limit=3)
    if not matches:
        return []
    best = len(set(db.words(about)) & set(db.words(matches[0]["text"])))
    removed = [
        m for m in matches
        if len(set(db.words(about)) & set(db.words(m["text"]))) == best
    ]
    for memory in removed:
        db.delete_memory(user_id, memory["id"])
    return removed


# --- Learning from conversations ---------------------------------------------------------------

LEARN_PROMPT = """You maintain the long-term memory that ZENO, a personal voice assistant, keeps \
about one user, {name}. Read the conversation below and update the memory.

Worth remembering: durable facts that will make future conversations better: who the user is \
(work, studies, where they live, family), their preferences (how they like things done, likes \
and dislikes), people they mention (names, roles, relationships), what they're working on \
(projects, goals, deadlines, tools they use) and routines. Write each memory as one short \
sentence about the user, e.g. "Works as a data engineer at Geometris." or "Prefers short answers."

Not worth remembering: one-off requests, small talk, anything ZENO said about itself, \
temporary states ("is tired today"), facts only true for a moment, and secrets such as \
passwords, codes or card numbers. Never invent anything the user didn't say.

Current memories (id: text):
{memories}

Conversation:
{transcript}

Reply with JSON only, in this shape:
{{"title": "a 3-6 word title for the conversation",
  "summary": "one or two sentences on what was discussed or done",
  "add": [{{"kind": "profile|preference|work|people|other", "text": "..."}}],
  "update": [{{"id": "...", "text": "..."}}],
  "remove": ["id"]}}
Use "update" when the conversation changes an existing memory and "remove" when the user says \
one is no longer true or asks ZENO to forget it. Leave lists empty when nothing changes."""


def transcript_text(messages: list[dict], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    lines = [f"{'User' if m['role'] == 'user' else 'ZENO'}: {m['text']}" for m in messages]
    text = "\n".join(lines)
    # Keep the end of very long conversations; that's where they land.
    return text if len(text) <= limit else "…" + text[-limit:]


def _parse_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in the reply")
    return json.loads(text[start : end + 1])


async def learn_from_conversation(user: dict, conversation_id: str, messages: list[dict]):
    """Summarize a finished conversation and fold what it taught into the user's memory."""
    user_turns = [m for m in messages if m["role"] == "user"]
    if not user_turns:
        return
    user_id = user["id"]
    existing = db.list_memories(user_id)
    prompt = LEARN_PROMPT.format(
        name=user.get("name") or user["email"],
        memories="\n".join(f"{m['id']}: {m['text']}" for m in existing) or "(none yet)",
        transcript=transcript_text(messages),
    )
    started = time.monotonic()
    data = _parse_json(await llm.complete(llm.config("zeno"), prompt))

    db.set_conversation_summary(
        conversation_id,
        title=(data.get("title") or "").strip()[:80] or None,
        summary=(data.get("summary") or "").strip()[:600] or None,
    )
    if not user.get("memory_enabled", 1):
        return

    known = {m["id"] for m in existing}
    added = updated = removed = 0
    for item in data.get("add") or []:
        if isinstance(item, dict) and (item.get("text") or "").strip():
            remember(user_id, item["text"][:300], item.get("kind") or "other", source="learned")
            added += 1
    for item in data.get("update") or []:
        if isinstance(item, dict) and item.get("id") in known and (item.get("text") or "").strip():
            db.update_memory(user_id, item["id"], text=item["text"][:300])
            updated += 1
    for memory_id in data.get("remove") or []:
        if memory_id in known:
            db.delete_memory(user_id, memory_id)
            removed += 1
    logger.info(
        f"Memory: learned from conversation {conversation_id} in "
        f"{time.monotonic() - started:.1f}s (+{added} ~{updated} -{removed})"
    )


def recent_work_section(user_id: str) -> str:
    """Recent conversations, so ZENO can pick up where the user left off."""
    lines = []
    for conversation in db.recent_conversation_summaries(user_id, limit=5):
        when = datetime.fromtimestamp(conversation["started_at"]).strftime("%b %-d")
        lines.append(f"- {when}: {conversation['summary']}")
    return "\n".join(lines)
