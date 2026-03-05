from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str
    command: str | None = None


def _run(
    args: list[str], *, text: str | None = None, shell: bool = False
) -> ActionResult:
    try:
        completed = subprocess.run(
            args if not shell else " ".join(args),
            input=text,
            text=True,
            capture_output=True,
            check=False,
            shell=shell,
        )
    except Exception as exc:  # noqa: BLE001
        return ActionResult(ok=False, message=str(exc), command=" ".join(args))

    if completed.returncode != 0:
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"exit={completed.returncode}"
        )
        return ActionResult(ok=False, message=detail, command=" ".join(args))
    return ActionResult(ok=True, message="ok", command=" ".join(args))


def copy_text_to_clipboard(text: str) -> ActionResult:
    payload = text or ""
    if not payload:
        return ActionResult(ok=False, message="No text provided for clipboard copy.")

    if sys.platform == "darwin":
        if shutil.which("pbcopy"):
            result = _run(["pbcopy"], text=payload)
            return ActionResult(
                ok=result.ok,
                message="Copied to clipboard." if result.ok else result.message,
                command=result.command,
            )
        return ActionResult(ok=False, message="pbcopy is not available on PATH.")

    if sys.platform.startswith("win"):
        result = _run(["cmd", "/c", "clip"], text=payload)
        return ActionResult(
            ok=result.ok,
            message="Copied to clipboard." if result.ok else result.message,
            command=result.command,
        )

    # Linux/Unix fallback
    if shutil.which("wl-copy"):
        result = _run(["wl-copy"], text=payload)
        return ActionResult(
            ok=result.ok,
            message="Copied to clipboard." if result.ok else result.message,
            command=result.command,
        )
    if shutil.which("xclip"):
        result = _run(["xclip", "-selection", "clipboard"], text=payload)
        return ActionResult(
            ok=result.ok,
            message="Copied to clipboard." if result.ok else result.message,
            command=result.command,
        )
    return ActionResult(
        ok=False, message="No clipboard utility found (expected wl-copy or xclip)."
    )


def open_local_path(path: Path) -> ActionResult:
    target = path.expanduser().resolve()
    if not target.exists():
        return ActionResult(ok=False, message=f"Path does not exist: {target}")

    if sys.platform == "darwin":
        result = _run(["open", str(target)])
        return ActionResult(
            ok=result.ok,
            message="Opened path." if result.ok else result.message,
            command=result.command,
        )

    if sys.platform.startswith("win"):
        # `start` is a cmd builtin.
        result = _run(["cmd", "/c", "start", "", str(target)], shell=False)
        return ActionResult(
            ok=result.ok,
            message="Opened path." if result.ok else result.message,
            command=result.command,
        )

    result = _run(["xdg-open", str(target)])
    return ActionResult(
        ok=result.ok,
        message="Opened path." if result.ok else result.message,
        command=result.command,
    )
