class AlexError(Exception):
    """A problem Alex (or we) must fix. Always says what went wrong and which file."""

    def __init__(self, message: str, file=None):
        self.file = str(file) if file else None
        super().__init__(f"{message}" + (f"\n  File: {self.file}" if self.file else ""))


class NoSpeechError(AlexError):
    """Whisper found no words at all. Each mode says what that means in its own plain words."""
