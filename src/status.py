import os
import time
from enum import Enum

from discord import Interaction
from discord.abc import Messageable

from src.pubsub import MessageEvent

MAX_MESSAGE_LENGTH = 2000
FORMATTING_CHARS_LENGTH = 12
SPINNER_STATES = 4

MAGIC_RUNES_CMD = "[0;2m[0;36m$[0m"
MAGIC_RUNES_INFO_START = "[0;2m[0;30m"
MAGIC_RUNES_INFO_STOP = "[0m"
MAGIC_RUNES_ERROR = "[[2;31m![0m]"


class Format(Enum):
    STANDARD = 0
    COMMAND = 1
    COMMENT = 2
    ERROR = 3


class StatusMessage:
    def __init__(self, target: Messageable | Interaction):
        super().__init__()
        self.target = target
        self.buffered_text = ""
        self.next_message = True
        self.message = None

        self._last_flush = 0
        self._flush_time = 0.5

    async def receive_message(self, event: MessageEvent):
        func = self.write_line
        match event.title:
            case "standard":
                func = self.write_line
            case "command":
                func = self.write_command
            case "comment":
                func = self.write_comment
            case "error":
                func = self.write_error
        await func(event.message)

    async def write(self, message: str) -> None:
        message = message.replace(os.environ.get("GITHUB_TOKEN"), "<REDACTED>")
        remaining_length = MAX_MESSAGE_LENGTH - FORMATTING_CHARS_LENGTH - len(self.buffered_text)
        if remaining_length - len(message) <= 0:
            self.next_message = True
            self.buffered_text = ""

        while len(message) + FORMATTING_CHARS_LENGTH > MAX_MESSAGE_LENGTH:
            message_slice = message[:MAX_MESSAGE_LENGTH - FORMATTING_CHARS_LENGTH]
            message = message[MAX_MESSAGE_LENGTH - FORMATTING_CHARS_LENGTH:]
            self.buffered_text = message_slice
            await self.flush()
            self.next_message = True
            self.buffered_text = ""

        self.buffered_text += message
    
    async def _periodic_flush(self):
        if self._last_flush + self._flush_time < time.time():
            await self.flush()

    async def write_line(self, message: str, format: Format = Format.STANDARD):
        match format:
            case Format.COMMAND:
                message = f"{MAGIC_RUNES_CMD} {message}"
            case Format.COMMENT:
                message = f"{MAGIC_RUNES_INFO_START}// {message}{MAGIC_RUNES_INFO_STOP}"
            case Format.ERROR:
                message = f"{MAGIC_RUNES_ERROR} {message}"
        message += "\n"
        await self.write(message)
        await self._periodic_flush()

    async def write_command(self, message: str):
        await self.write_line(message, Format.COMMAND)

    async def write_comment(self, message: str):
        await self.write_line(message, Format.COMMENT)

    async def write_error(self, message: str):
        await self.write_line(message, Format.ERROR)

    async def rewrite_line(self, message: str) -> None:
        self.buffered_text = self.buffered_text.rsplit("\n", 2)[0] + "\n"
        await self.write_line(message)

    async def flush(self) -> None:
        content = f"```ansi\n{self.buffered_text}```"
        if self.next_message:
            if isinstance(self.target, Interaction):
                self.message = await self.target.channel.send(content)
            else:
                self.message = await self.target.send(content)
            self.next_message = False
        else:
            await self.message.edit(content=content)

        self._last_flush = time.time()


class Spinner:
    def __init__(self, status: StatusMessage, text: str):
        self.status = status
        self.text = text
        self.step = 1
        self.has_written = False

    async def _write(self, text: str) -> None:
        method = self.has_written and self.status.rewrite_line or self.status.write_line
        self.has_written = True
        await method(text)

    async def spin(self):
        char = ""
        match self.step:
            case 0:
                char = "✓"
            case 1:
                char = "/"
            case 2:
                char = "-"
            case 3:
                char = "\\"
            case 4:
                char = "|"
        self.step = (self.step % SPINNER_STATES) + 1
        await self._write(f"[{char}] {self.text}")
        await self.status.flush()

    async def complete(self):
        self.step = 0
        await self.spin()
