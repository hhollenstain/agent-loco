from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HardwareProfile:
    os_name: str
    arch: str
    is_apple_silicon: bool
    has_nvidia: bool
    gpu_name: str | None
    recommended_backend: str
    recommended_model: str
    notes: list[str] = field(default_factory=list)


def detect_hardware() -> HardwareProfile:
    os_name = platform.system().lower()
    arch = platform.machine().lower()
    is_apple_silicon = os_name == "darwin" and arch in {"arm64", "aarch64"}
    gpu_name, has_nvidia = _detect_nvidia()
    notes: list[str] = []

    if is_apple_silicon:
        backend = "ollama-host"
        model = "qwen2.5-coder:14b"
        notes.append(
            "Docker on macOS cannot use Metal. Run Ollama on the host and point the agent at it."
        )
    elif has_nvidia:
        backend = "ollama-cuda"
        model = "qwen2.5-coder:32b"
        notes.append(
            "Use docker compose with the NVIDIA overlay. "
            "Host driver should be 570+ for RTX 50-series."
        )
    else:
        backend = "ollama-cpu"
        model = "qwen2.5-coder:7b"
        notes.append("No GPU detected. A small CPU model will work, but coding quality will drop.")

    return HardwareProfile(
        os_name=os_name,
        arch=arch,
        is_apple_silicon=is_apple_silicon,
        has_nvidia=has_nvidia,
        gpu_name=gpu_name,
        recommended_backend=backend,
        recommended_model=model,
        notes=notes,
    )


def _detect_nvidia() -> tuple[str | None, bool]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return None, False
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    if result.returncode != 0:
        return None, False
    name = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else "NVIDIA GPU"
    return name, True


def command_available(name: str) -> bool:
    return shutil.which(name) is not None
