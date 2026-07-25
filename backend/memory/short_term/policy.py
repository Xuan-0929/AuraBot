from dataclasses import dataclass

from .models import BufferStats


@dataclass(frozen=True)
class ShortTermPolicy:
    max_breakpoints: int = 4
    max_chars: int = 3000

    def __post_init__(self) -> None:
        if type(self.max_breakpoints) is not int or self.max_breakpoints <= 0:
            raise ValueError("max_breakpoints must be a positive integer")
        if type(self.max_chars) is not int or self.max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")

    def should_prepare(self, stats: BufferStats) -> bool:
        return (
            stats.breakpoint_count >= self.max_breakpoints
            or stats.char_count >= self.max_chars
        )
