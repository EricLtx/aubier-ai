class Refusal(Exception):
    """One line, the kernel's voice: what was wrong, then the remedy."""

    def __init__(self, what: str, remedy: str):
        super().__init__(f"{what}; remedy: {remedy}")
        self.what, self.remedy = what, remedy
