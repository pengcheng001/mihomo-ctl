"""Shared helpers: console output, process check, sudo wrapper."""
from __future__ import annotations

import shlex
import subprocess
from typing import Optional

from rich.console import Console

console = Console()


def say(msg: str, ok: bool = True) -> None:
    icon = "✓" if ok else "✗"
    style = "green" if ok else "red"
    console.print(f"[{style}]{icon}[/] {msg}")


def step(msg: str) -> None:
    console.print(f"[cyan]→[/] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]![/] {msg}")


def mihomo_pid() -> Optional[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-x", "mihomo"], capture_output=True, text=True, check=False
        )
        if out.returncode == 0:
            return int(out.stdout.split()[0])
    except (FileNotFoundError, ValueError):
        pass
    return None


def run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)


def sudo(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run a command via sudo. Lets sudo prompt the user for password normally."""
    return run(["sudo", *cmd], check=check, capture=capture)


def shell_escape(s: str) -> str:
    return shlex.quote(s)
