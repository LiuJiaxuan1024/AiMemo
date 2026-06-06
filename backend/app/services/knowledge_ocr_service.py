from __future__ import annotations

import importlib.util
from pathlib import Path
import platform
import shutil
import subprocess

from app.core.config import settings
from app.local_operator.background_command import pool as background_pool
from app.local_operator.policy import LocalOperatorPolicy


INSTALL_TIMEOUT_SECONDS = 600
MANAGED_TESSDATA_DIR = Path("data") / "tessdata"


def get_knowledge_ocr_status() -> dict:
    mode = settings.knowledge_image_text_extraction_mode.strip().lower()
    required_languages = _parse_languages(settings.knowledge_image_ocr_languages)
    if mode in {"qwen_vl_ocr", "qwen-vl-ocr", "dashscope_qwen_vl_ocr", "auto"}:
        ready = bool(settings.dashscope_api_key)
        return {
            "mode": mode,
            "ready": ready,
            "status": "ready" if ready else "provider_not_configured",
            "tesseract_available": False,
            "tesseract_path": None,
            "tesseract_version": None,
            "tessdata_path": None,
            "available_languages": [],
            "required_languages": [],
            "missing_languages": [],
            "install_running": False,
            "install_processes": [],
            "install_task_ids": [],
            "python_packages": {
                "Pillow": importlib.util.find_spec("PIL") is not None,
                "pytesseract": importlib.util.find_spec("pytesseract") is not None,
            },
            "message": (
                f"qwen-vl-ocr 图片转文本可用：{settings.knowledge_image_text_extraction_model}。"
                if ready
                else "qwen-vl-ocr 图片转文本未就绪：缺少 DASHSCOPE_API_KEY。"
            ),
        }
    tesseract_path = resolve_tesseract_path()
    install_task_ids = _running_ocr_install_task_ids()
    install_processes = _detect_install_processes(install_task_ids)
    install_running = bool(install_task_ids or install_processes)
    python_packages = {
        "Pillow": importlib.util.find_spec("PIL") is not None,
        "pytesseract": importlib.util.find_spec("pytesseract") is not None,
    }

    if mode in {"off", "none", "disabled"}:
        return {
            "mode": mode,
            "ready": False,
            "status": "disabled",
            "tesseract_available": bool(tesseract_path),
            "tesseract_path": tesseract_path,
            "tesseract_version": None,
            "tessdata_path": None,
            "available_languages": [],
            "required_languages": required_languages,
            "missing_languages": required_languages,
            "install_running": install_running,
            "install_processes": install_processes,
            "install_task_ids": install_task_ids,
            "python_packages": python_packages,
            "message": "知识库图片 OCR 已关闭。",
        }

    if not tesseract_path:
        return {
            "mode": mode,
            "ready": False,
            "status": "missing_tesseract",
            "tesseract_available": False,
            "tesseract_path": None,
            "tesseract_version": None,
            "tessdata_path": None,
            "available_languages": [],
            "required_languages": required_languages,
            "missing_languages": required_languages,
            "install_running": install_running,
            "install_processes": install_processes,
            "install_task_ids": install_task_ids,
            "python_packages": python_packages,
            "message": "OCR 安装后台任务正在运行，完成后会重新检测。" if install_running else "未检测到 tesseract 命令，知识库图片无法进行本地 OCR。",
        }

    version = _run_tesseract_version(tesseract_path)
    default_languages = _run_tesseract_languages(tesseract_path)
    managed_tessdata_dir = _managed_tessdata_dir()
    managed_languages = _run_tesseract_languages(tesseract_path, tessdata_dir=managed_tessdata_dir)
    available_languages, tessdata_path = _select_effective_languages(
        default_languages=default_languages,
        managed_languages=managed_languages,
        managed_tessdata_dir=managed_tessdata_dir,
        required_languages=required_languages,
    )
    missing_languages = [language for language in required_languages if language not in set(available_languages)]
    ready = not missing_languages
    return {
        "mode": mode,
        "ready": ready,
        "status": "ready" if ready else "missing_languages",
        "tesseract_available": True,
        "tesseract_path": tesseract_path,
        "tesseract_version": version,
        "tessdata_path": tessdata_path,
        "available_languages": available_languages,
        "required_languages": required_languages,
        "missing_languages": missing_languages,
        "install_running": install_running,
        "install_processes": install_processes,
        "install_task_ids": install_task_ids,
        "python_packages": python_packages,
        "message": "本地 OCR 可用。" if ready else f"本地 OCR 缺少语言包：{', '.join(missing_languages)}。",
    }


def get_knowledge_ocr_install_plan() -> dict:
    mode = settings.knowledge_image_text_extraction_mode.strip().lower()
    if mode in {"qwen_vl_ocr", "qwen-vl-ocr", "dashscope_qwen_vl_ocr", "auto"}:
        has_api_key = bool(settings.dashscope_api_key)
        return {
            "platform": platform.system().lower() or "unknown",
            "supported": False,
            "commands": [],
            "message": (
                "当前使用 qwen-vl-ocr 图片转文本，无需安装本地 OCR。"
                if has_api_key
                else "当前使用 qwen-vl-ocr 图片转文本，无需安装本地 OCR；请配置 DASHSCOPE_API_KEY。"
            ),
        }
    commands = _build_install_commands()
    return {
        "platform": platform.system().lower() or "unknown",
        "supported": bool(commands),
        "commands": [_display_command(command) for command in commands],
        "message": "可一键安装 Tesseract OCR。" if commands else "当前平台未找到可用的一键安装方式。",
    }


def resolve_tesseract_path() -> str | None:
    command = shutil.which("tesseract")
    if command:
        return command
    for candidate in _common_tesseract_paths():
        if candidate.exists():
            return str(candidate)
    return None


def resolve_tesseract_runtime(required_languages: list[str] | None = None) -> tuple[str | None, str | None]:
    command = resolve_tesseract_path()
    if not command:
        return None, None
    required = required_languages or []
    if not required:
        return command, None
    managed_tessdata_dir = _managed_tessdata_dir()
    managed_languages = _run_tesseract_languages(command, tessdata_dir=managed_tessdata_dir)
    if set(required).issubset(set(managed_languages)):
        return command, managed_tessdata_dir
    return command, None


def install_knowledge_ocr(*, confirm_install: bool) -> dict:
    if not confirm_install:
        raise ValueError("confirm_install must be true before installing OCR components.")

    before_status = get_knowledge_ocr_status()
    plan = get_knowledge_ocr_install_plan()
    if before_status.get("ready"):
        mode = str(before_status.get("mode") or "").lower()
        message = (
            "qwen-vl-ocr 图片转文本已可用，无需安装本地 OCR。"
            if mode in {"qwen_vl_ocr", "qwen-vl-ocr", "dashscope_qwen_vl_ocr", "auto"}
            else "本地 OCR 已可用，无需重复安装。"
        )
        return {
            "supported": True,
            "installed": True,
            "command_results": [],
            "install_task_id": None,
            "before_status": before_status,
            "after_status": before_status,
            "message": message,
        }
    if before_status.get("status") == "provider_not_configured":
        return {
            "supported": False,
            "installed": False,
            "command_results": [],
            "install_task_id": None,
            "before_status": before_status,
            "after_status": before_status,
            "message": before_status.get("message", "图片转文本 provider 未配置。"),
        }
    if before_status.get("install_running"):
        install_task_ids = before_status.get("install_task_ids") or []
        return {
            "supported": True,
            "installed": False,
            "command_results": [],
            "install_task_id": install_task_ids[0] if install_task_ids else None,
            "before_status": before_status,
            "after_status": before_status,
            "message": "检测到 OCR 安装后台任务正在运行，请在后台任务面板查看进度。",
        }
    if before_status.get("status") == "missing_languages" and platform.system().lower() == "windows":
        command = _build_windows_language_install_command(before_status.get("required_languages") or [])
        if command:
            command_results = [_start_install_background_task(command)]
            after_status = get_knowledge_ocr_status()
            return {
                "supported": True,
                "installed": False,
                "command_results": command_results,
                "install_task_id": command_results[0].get("task_id"),
                "before_status": before_status,
                "after_status": after_status,
                "message": "OCR 语言包安装后台任务已启动，请在后台任务面板查看进度。",
            }
        return {
            "supported": False,
            "installed": False,
            "command_results": [],
            "install_task_id": None,
            "before_status": before_status,
            "after_status": before_status,
            "message": (
                "已检测到 Tesseract OCR，但缺少语言包："
                f"{', '.join(before_status.get('missing_languages') or [])}。"
                "当前平台暂不支持一键安装语言包；请补充语言包后刷新状态。"
            ),
        }
    commands = _build_install_commands()
    if not commands:
        return {
            "supported": False,
            "installed": False,
            "command_results": [],
            "install_task_id": None,
            "before_status": before_status,
            "after_status": before_status,
            "message": plan["message"],
        }

    if platform.system().lower() == "windows":
        command_results = [_start_install_background_task(commands[0])]
        after_status = get_knowledge_ocr_status()
        return {
            "supported": True,
            "installed": False,
            "command_results": command_results,
            "install_task_id": command_results[0].get("task_id"),
            "before_status": before_status,
            "after_status": after_status,
            "message": "OCR 安装后台任务已启动，请在后台任务面板查看进度。",
        }

    command_results = []
    for command in commands:
        result = _run_install_command(command)
        command_results.append(result)
        if result["exit_code"] != 0:
            after_status = get_knowledge_ocr_status()
            return {
                "supported": True,
                "installed": False,
                "command_results": command_results,
                "install_task_id": None,
                "before_status": before_status,
                "after_status": after_status,
                "message": result["message"] or "OCR 安装命令执行失败。",
            }

    after_status = get_knowledge_ocr_status()
    return {
        "supported": True,
        "installed": bool(after_status.get("ready")),
        "command_results": command_results,
        "install_task_id": None,
        "before_status": before_status,
        "after_status": after_status,
        "message": "OCR 安装完成。" if after_status.get("ready") else after_status.get("message", "OCR 安装后仍未就绪。"),
    }


def _parse_languages(value: str) -> list[str]:
    languages = [item.strip() for item in value.replace(",", "+").split("+") if item.strip()]
    return languages or ["eng"]


def _run_tesseract_version(command: str) -> str | None:
    result = _run_tesseract_command([command, "--version"])
    if result is None or result.returncode != 0:
        return None
    first_line = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
    return first_line.strip() or None


def _run_tesseract_languages(command: str, *, tessdata_dir: str | None = None) -> list[str]:
    args = [command, "--list-langs"]
    if tessdata_dir:
        args.extend(["--tessdata-dir", tessdata_dir])
    result = _run_tesseract_command(args)
    if result is None or result.returncode != 0:
        return []
    languages = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if not value or value.lower().startswith("list of available languages"):
            continue
        languages.append(value)
    return sorted(set(languages))


def _run_tesseract_command(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return None


def _detect_install_processes(install_task_ids: list[str] | None = None) -> list[str]:
    found = [f"background_task:{task_id}" for task_id in (install_task_ids or [])]
    system = platform.system().lower()
    if system == "windows":
        found.extend(_detect_windows_install_processes())
    return sorted(set(found))


def _detect_windows_install_processes() -> list[str]:
    result = _run_tesseract_command(["tasklist", "/FO", "CSV", "/NH"])
    if result is None or result.returncode != 0:
        return []
    found = []
    for line in result.stdout.splitlines():
        lower = line.lower()
        if lower.startswith('"winget.exe"') or lower.startswith('"msiexec.exe"'):
            name = line.split(",", 1)[0].strip().strip('"')
            if name:
                found.append(name)
    return sorted(set(found))


def _build_install_commands() -> list[list[str]]:
    system = platform.system().lower()
    if system == "windows":
        if resolve_tesseract_path():
            return []
        winget = shutil.which("winget")
        if not winget:
            return []
        return [
            [
                winget,
                "install",
                "--id",
                "UB-Mannheim.TesseractOCR",
                "--exact",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ]
        ]
    if system == "darwin":
        brew = shutil.which("brew")
        return [[brew, "install", "tesseract"]] if brew else []
    if system == "linux":
        if shutil.which("apt-get"):
            apt_get = shutil.which("apt-get") or "apt-get"
            sudo = shutil.which("sudo")
            prefix = [sudo] if sudo else []
            return [
                [*prefix, apt_get, "update"],
                [*prefix, apt_get, "install", "-y", "tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-chi-sim"],
            ]
        if shutil.which("dnf"):
            dnf = shutil.which("dnf") or "dnf"
            sudo = shutil.which("sudo")
            prefix = [sudo] if sudo else []
            return [
                [*prefix, dnf, "install", "-y", "tesseract", "tesseract-langpack-eng", "tesseract-langpack-chi_sim"]
            ]
        if shutil.which("pacman"):
            pacman = shutil.which("pacman") or "pacman"
            sudo = shutil.which("sudo")
            prefix = [sudo] if sudo else []
            return [[*prefix, pacman, "-S", "--noconfirm", "tesseract", "tesseract-data-eng", "tesseract-data-chi_sim"]]
    return []


def _common_tesseract_paths() -> list[Path]:
    if platform.system().lower() != "windows":
        return []
    return [
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ]


def _managed_tessdata_dir() -> str:
    return str(MANAGED_TESSDATA_DIR.resolve())


def _select_effective_languages(
    *,
    default_languages: list[str],
    managed_languages: list[str],
    managed_tessdata_dir: str,
    required_languages: list[str],
) -> tuple[list[str], str | None]:
    default_missing = [language for language in required_languages if language not in set(default_languages)]
    managed_missing = [language for language in required_languages if language not in set(managed_languages)]
    if len(managed_missing) < len(default_missing):
        return sorted(set(managed_languages)), managed_tessdata_dir
    return sorted(set(default_languages)), None


def _build_windows_language_install_command(required_languages: list[str]) -> list[str] | None:
    languages = sorted({language for language in required_languages if language and language != "osd"})
    if not languages:
        return None
    tessdata_dir = _managed_tessdata_dir()
    language_literals = ", ".join(f"'{_escape_powershell_single_quoted(language)}'" for language in languages)
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$tessdataDir='{_escape_powershell_single_quoted(tessdata_dir)}'; "
        "[System.IO.Directory]::CreateDirectory($tessdataDir) | Out-Null; "
        f"$languages=@({language_literals}); "
        "foreach ($lang in $languages) { "
        "$out=[System.IO.Path]::Combine($tessdataDir, $lang + '.traineddata'); "
        "if (Test-Path -LiteralPath $out) { Write-Output ('Already exists: ' + $out); continue; } "
        "$url='https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/' + $lang + '.traineddata'; "
        "Write-Output ('Downloading ' + $lang + ' to ' + $out); "
        "Invoke-WebRequest -Uri $url -OutFile $out; "
        "} "
        "Write-Output ('Tessdata directory: ' + $tessdataDir);"
    )
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def _escape_powershell_single_quoted(value: str) -> str:
    return value.replace("'", "''")


def _run_install_command(command: list[str]) -> dict:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": _display_command(command),
            "exit_code": -1,
            "stdout": _tail_text(exc.stdout),
            "stderr": _tail_text(exc.stderr),
            "message": "OCR 安装命令执行超时。",
        }
    except Exception as exc:
        return {
            "command": _display_command(command),
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "message": str(exc),
        }
    return {
        "command": _display_command(command),
        "exit_code": result.returncode,
        "stdout": _tail_text(result.stdout),
        "stderr": _tail_text(result.stderr),
        "message": "" if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip())[:1000],
    }


def _start_install_background_task(command: list[str]) -> dict:
    command_text = _display_command(command)
    cwd = Path.cwd().resolve()
    result = background_pool.spawn(
        policy=LocalOperatorPolicy.from_roots([str(cwd)]),
        command=command_text,
        cwd=str(cwd),
        conversation_id=None,
    )
    if not result.ok:
        return {
            "task_id": None,
            "command": command_text,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "message": result.message or result.error_code or "OCR 安装后台任务启动失败。",
        }
    data = result.data if isinstance(result.data, dict) else {}
    return {
        "task_id": data.get("task_id"),
        "command": command_text,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "message": "后台安装任务已启动。",
    }


def _running_ocr_install_task_ids() -> list[str]:
    task_ids: list[str] = []
    try:
        tasks = background_pool.list_tasks()
        task_ids.extend(
            task.task_id
            for task in tasks
            if task.status == "running" and _is_ocr_install_command(task.command)
        )
        records = background_pool.list_persisted()
        task_ids.extend(
            record.task_id
            for record in records
            if record.status == "running" and _is_ocr_install_command(record.command)
        )
    except Exception:
        return []
    return sorted(set(task_ids))


def _is_ocr_install_command(command: str) -> bool:
    normalized = command.lower()
    markers = (
        "ub-mannheim.tesseractocr",
        "tessdata_fast",
        "traineddata",
        "tesseract-ocr",
        "tesseract-langpack",
        "tesseract-data",
        " install tesseract",
    )
    return any(marker in normalized for marker in markers)


def _display_command(command: list[str]) -> str:
    display_parts = ["winget" if part.lower().endswith("winget.exe") else part for part in command]
    return " ".join(_quote_command_part(part) for part in display_parts)


def _quote_command_part(value: str) -> str:
    if not value or any(char.isspace() for char in value):
        return f'"{value}"'
    return value


def _tail_text(value: str | bytes | None, *, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value
    return text[-limit:]
