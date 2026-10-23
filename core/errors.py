from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnostic:
    code: str
    path: str
    message: str
    line: int | None = None


class VCError(ValueError):
    def __init__(self, code: str, path: str, message: str, line: int | None = None):
        self.diagnostic = Diagnostic(code, path, message, line)
        super().__init__(f"{code} {path}" + (f" (line {line})" if line else "") + f": {message}")


def require(condition, code, path, message):
    if not condition:
        raise VCError(code, path, message)
