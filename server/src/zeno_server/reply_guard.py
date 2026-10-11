"""Keeps cut-off replies out of ZENO's voice and conversation.

Small local models sometimes end a reply mid-sentence ("Sure, what", "Go", "I"). Spoken, and kept
in the context, those fragments make the model imitate them: after a few, every reply is "I".
This processor sits between the LLM and the TTS and holds a reply's text until a sentence ends:

- complete sentences flow on to the voice as usual (the TTS waits for sentence ends anyway, so
  this adds no delay);
- an unfinished tail after complete sentences is dropped;
- a reply with no complete sentence at all is dropped and generated again, up to MAX_RETRIES
  times, then replaced with a short "say that again" that isn't added to the context.
"""

import re

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

MAX_RETRIES = 2
# A sentence is over at . ! ? or …, optionally followed by closing quotes or brackets.
SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*\s*$")
GIVE_UP = "Sorry, I lost my train of thought there. Could you say that again?"


class ReplyGuard(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._held: list[Frame] = []
        self._held_text = ""
        self._in_reply = False
        self._released = False
        self._retries = 0

    def _clear(self):
        self._held = []
        self._held_text = ""
        self._released = False

    async def _release(self):
        for frame in self._held:
            await self.push_frame(frame)
        self._held = []
        self._held_text = ""
        self._released = True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            # The user spoke: whatever was held is moot, and a fresh turn begins.
            self._clear()
            self._in_reply = False
            self._retries = 0
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._clear()
            self._in_reply = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame) and self._in_reply:
            self._held.append(frame)
            self._held_text += frame.text
            if SENTENCE_END.search(self._held_text):
                await self._release()
            return

        if isinstance(frame, LLMFullResponseEndFrame) and self._in_reply:
            self._in_reply = False
            tail = self._held_text.strip()
            released = self._released
            self._clear()
            await self.push_frame(frame, direction)

            if not tail:
                if released:
                    self._retries = 0
                return
            if released:
                logger.warning(f"Reply guard: dropped an unfinished tail: {tail!r}")
                self._retries = 0
            elif self._retries < MAX_RETRIES:
                self._retries += 1
                logger.warning(f"Reply guard: cut-off reply {tail!r}; generating it again")
                await self.push_frame(LLMRunFrame(), direction)
            else:
                logger.warning(f"Reply guard: still cut off after {MAX_RETRIES} retries ({tail!r})")
                self._retries = 0
                await self.push_frame(TTSSpeakFrame(GIVE_UP, append_to_context=False), direction)
            return

        await self.push_frame(frame, direction)
