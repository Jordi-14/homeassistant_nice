"""One asynchronous execution and outcome boundary for user commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import time
from typing import Any

from .errors import NiceBidiAuthError, NiceResponseError, nice_error_code
from .models.commands import (
    CommandAcknowledgement,
    CommandReceipt,
    NiceCommand,
    NiceCommandResult,
)


class CommandExecutor:
    """Run each supplied operation once, retaining failures without replaying it."""

    def __init__(
        self, run: Callable[[Callable], Awaitable[Any]], *, clock=time.monotonic
    ) -> None:
        self._run = run
        self._clock = clock
        self.last_result: NiceCommandResult | None = None

    def failed_result(
        self, command: NiceCommand, started: float, error: Exception
    ) -> NiceCommandResult:
        explicit = (
            isinstance(error, (NiceResponseError, NiceBidiAuthError, ValueError))
            or nice_error_code(error) is not None
        )
        uncertain = (
            getattr(error, "delivery_unknown", False) or nice_error_code(error) == "14"
        )
        return NiceCommandResult(
            command,
            CommandAcknowledgement.REJECTED
            if explicit and not uncertain
            else CommandAcknowledgement.UNKNOWN,
            max(0, round((self._clock() - started) * 1000)),
            error_code=error.code
            if isinstance(error, NiceResponseError)
            else nice_error_code(error),
            error_layer=str(error.layer)
            if getattr(error, "layer", None) is not None
            else None,
            details=(("delivery", getattr(error, "delivery", "unknown")),),
        )

    async def execute(
        self, command: NiceCommand, operation: Callable
    ) -> NiceCommandResult:
        started = self._clock()
        try:
            receipt = await self._run(operation)
        except Exception as err:
            self.last_result = self.failed_result(command, started, err)
            raise
        if not isinstance(receipt, CommandReceipt):
            receipt = CommandReceipt()
        self.last_result = NiceCommandResult(
            command,
            receipt.acknowledgement,
            max(0, round((self._clock() - started) * 1000)),
            details=receipt.details,
            verification=receipt.verification,
        )
        return self.last_result
