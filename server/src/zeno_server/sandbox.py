"""Bit sandboxes: one storage pool per user, and a Docker container per working Bit.

Every user gets one workspace on the server, shared by all of their Bits and capped at
BIT_STORAGE_GB (10 GB by default) in total:

    data/workspaces/<user>/
        bit-01/   BIT-01's own folder: mounted read-write into BIT-01's container only
        bit-02/
        shared/   mounted read-only into every container; Bits publish into it through ZENO
        tools/    the toolbox: scripts Bits saved for each other (read-only; added via ZENO)
        .local/   Python packages the user's Bits installed with pip, shared by all of them

A Bit's container sees only its own folder plus those shared ones, so Bits can't touch each
other's work, and nothing in shared/ or tools/ changes without ZENO knowing which Bit wrote it.
Code in the container can reach ZENO's gateway (gateway.py) to use the user's apps.

The storage cap is enforced by ZENO, not the filesystem: file tools refuse writes that would go
over it, and a command that fills the pool while it runs is stopped.
"""

import asyncio
import hashlib
import os
import posixpath
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from zeno_server.config import settings

CONTAINER_ROOT = "/workspace"
SHARED = f"{CONTAINER_ROOT}/shared"
TOOLS = f"{CONTAINER_ROOT}/tools"
PACKAGES = f"{CONTAINER_ROOT}/.local"
LABEL = "zeno.bit"
MAX_OUTPUT_BYTES = 2_000_000

# ZENO's own sandbox image (built from sandbox_image/Dockerfile), and what Bits use until it's built.
IMAGE_DIR = Path(__file__).parent / "sandbox_image"
FALLBACK_IMAGE = "python:3.12-slim"
# The helper library code in a sandbox uses to reach ZENO (`import zeno`, or the zeno command).
HELPER_DIR = Path(__file__).parent / "sandbox_lib"
HELPER_MOUNT = "/opt/zeno"

_DOCKER_FALLBACKS = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
    "/usr/bin/docker",
)


class SandboxError(Exception):
    """Something a Bit asked for that its sandbox can't do; the message goes back to the Bit."""


# --- Workspaces --------------------------------------------------------------------------------


def slot_folder(slot: int) -> str:
    return f"bit-{slot:02d}"


def bit_home(slot: int) -> str:
    """A Bit's own folder, as its container sees it."""
    return f"{CONTAINER_ROOT}/{slot_folder(slot)}"


def user_root(user_id: str) -> Path:
    if not user_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"Unsafe user id: {user_id!r}")
    return settings.workspaces_dir / user_id


def ensure_workspace(user_id: str, slot: int | None = None) -> Path:
    root = user_root(user_id)
    for folder in ("shared", "tools", ".local"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    if slot is not None:
        (root / slot_folder(slot)).mkdir(parents=True, exist_ok=True)
    return root


def host_path(user_id: str, slot: int, path: str, *, write: bool) -> Path:
    """Map a path a Bit gave (absolute in its container, or relative to its folder) to the host.

    Bits may read their own folder, /workspace/shared and /workspace/tools, and write only their
    own folder.
    """
    home = bit_home(slot)
    path = (path or ".").strip()
    full = posixpath.normpath(path if path.startswith("/") else posixpath.join(home, path))
    root = ensure_workspace(user_id, slot)

    if full == home or full.startswith(home + "/"):
        base = root / slot_folder(slot)
        relative = full[len(home):].lstrip("/")
    elif not write and (full == SHARED or full.startswith(SHARED + "/")):
        base = root / "shared"
        relative = full[len(SHARED):].lstrip("/")
    elif not write and (full == TOOLS or full.startswith(TOOLS + "/")):
        base = root / "tools"
        relative = full[len(TOOLS):].lstrip("/")
    elif write:
        raise SandboxError(
            f"You can only write inside your own folder, {home}. To give other Bits a file, "
            "save it there and then use share_file."
        )
    else:
        raise SandboxError(f"You can only read {home}, {SHARED} and {TOOLS}.")

    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()):
        raise SandboxError("That path leads outside your workspace.")
    return target


def container_path(user_id: str, slot: int, host: Path) -> str:
    """The reverse of host_path, for messages back to the Bit."""
    root = user_root(user_id).resolve()
    relative = host.resolve().relative_to(root).as_posix()
    return f"{CONTAINER_ROOT}/{relative}"


# --- Storage pool ------------------------------------------------------------------------------


def _disk_usage(path: Path) -> int:
    total = 0
    for directory, folders, files in os.walk(path):
        for name in files + folders:
            try:
                stat = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            total += stat.st_blocks * 512
    return total


_usage_cache: dict[str, tuple[float, dict]] = {}


def storage(user_id: str, max_age: float = 2.0) -> dict:
    """How much of the user's pool each folder uses, cached briefly since walking it isn't free."""
    cached = _usage_cache.get(user_id)
    if cached and time.monotonic() - cached[0] < max_age:
        return cached[1]
    root = ensure_workspace(user_id)
    folders = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            folders.append({"name": child.name, "bytes": _disk_usage(child)})
    used = sum(folder["bytes"] for folder in folders)
    result = {"used": used, "quota": settings.bit_storage_bytes, "folders": folders}
    _usage_cache[user_id] = (time.monotonic(), result)
    return result


async def storage_async(user_id: str, max_age: float = 2.0) -> dict:
    return await asyncio.to_thread(storage, user_id, max_age)


def forget_usage(user_id: str):
    _usage_cache.pop(user_id, None)


def check_room(user_id: str, extra_bytes: int):
    pool = storage(user_id, max_age=0)
    if pool["used"] + extra_bytes > pool["quota"]:
        raise SandboxError(
            f"The storage pool is full ({_gb(pool['used'])} of {_gb(pool['quota'])} used). "
            "Delete files you no longer need first."
        )


def _gb(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} GB"
    return f"{size / 1024**2:.1f} MB"


# --- Docker ------------------------------------------------------------------------------------


def docker_bin() -> str | None:
    if settings.docker_bin:
        return settings.docker_bin
    return shutil.which("docker") or next((p for p in _DOCKER_FALLBACKS if os.path.exists(p)), None)


def _docker_env() -> dict:
    # The docker CLI looks up credential helpers next to itself; the server may run with a
    # minimal PATH (e.g. when started from an app), so make sure that folder is on it.
    env = dict(os.environ)
    binary = docker_bin()
    if binary:
        env["PATH"] = f"{os.path.dirname(binary)}:{env.get('PATH', '/usr/bin:/bin')}"
    return env


@dataclass
class CommandResult:
    exit_code: int
    output: str
    timed_out: bool = False


async def _docker(*args: str, timeout: float = 60) -> CommandResult:
    binary = docker_bin()
    if not binary:
        raise SandboxError("Docker isn't installed on the ZENO server.")
    process = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_docker_env(),
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return CommandResult(-1, "", timed_out=True)
    return CommandResult(process.returncode, out.decode(errors="replace").strip())


_status: tuple[float, dict] | None = None


async def docker_status(max_age: float = 15.0) -> dict:
    """Whether sandboxes can run right now, for the dashboard and the Bits themselves."""
    global _status
    if _status and time.monotonic() - _status[0] < max_age:
        return _status[1]
    if not docker_bin():
        status = {"available": False, "detail": "Docker isn't installed on the server"}
    else:
        try:
            result = await _docker("info", "--format", "{{.ServerVersion}}", timeout=8)
            if result.exit_code == 0:
                status = {"available": True, "detail": f"Docker {result.output}"}
            else:
                status = {"available": False, "detail": "Docker isn't running on the server"}
        except Exception as e:
            status = {"available": False, "detail": f"Docker error: {e}"}
    status["image"] = image()
    status["building"] = _building is not None and not _building.done()
    status["network"] = settings.sandbox_network != "none"
    status["cpus"] = settings.sandbox_cpus
    status["memory"] = settings.sandbox_memory
    _status = (time.monotonic(), status)
    return status


# --- The sandbox image ---------------------------------------------------------------------------

_built_image: str | None = None
_building: asyncio.Task | None = None


def _own_image_tag() -> str:
    digest = hashlib.sha256((IMAGE_DIR / "Dockerfile").read_bytes()).hexdigest()[:10]
    return f"zeno-sandbox:{digest}"


def image() -> str:
    """The image new sandboxes start from."""
    if settings.sandbox_image != "auto":
        return settings.sandbox_image
    return _built_image or FALLBACK_IMAGE


async def ensure_image():
    """With SANDBOX_IMAGE=auto, find ZENO's sandbox image, or start building it if it isn't there
    yet (in the background; it takes a few minutes the first time). Called at startup and again
    before each sandbox starts, so a slow Docker at startup doesn't leave Bits on the fallback."""
    global _built_image, _building
    if settings.sandbox_image != "auto" or _built_image or (_building and not _building.done()):
        return
    if not (await docker_status(max_age=0))["available"]:
        return
    tag = _own_image_tag()
    if (await _docker("image", "inspect", tag, timeout=20)).exit_code == 0:
        _built_image = tag
        logger.info(f"Bits use the sandbox image {tag}")
        return

    async def build():
        global _built_image
        logger.info(f"Building the Bit sandbox image {tag}; Bits use {FALLBACK_IMAGE} meanwhile")
        result = await _docker("build", "--tag", tag, str(IMAGE_DIR), timeout=3600)
        if result.exit_code == 0:
            _built_image = tag
            logger.info(f"Sandbox image {tag} is ready")
        else:
            logger.error(f"Couldn't build the sandbox image: {result.output[-1500:]}")

    _building = asyncio.create_task(build())


class Sandbox:
    """The Docker container one Bit works in, while it works on a task."""

    def __init__(self, user_id: str, slot: int, bit_id: str):
        self.user_id = user_id
        self.slot = slot
        self.bit_id = bit_id
        self.name = f"zeno-bit-{bit_id}"
        self.home = bit_home(slot)
        self.started = False

    async def start(self, gateway_url: str = "", gateway_token: str = ""):
        status = await docker_status(max_age=0)
        if not status["available"]:
            raise SandboxError(status["detail"])
        await ensure_image()
        root = ensure_workspace(self.user_id, self.slot)
        own = (root / slot_folder(self.slot)).resolve()
        shared = (root / "shared").resolve()
        tools = (root / "tools").resolve()
        packages = (root / ".local").resolve()
        path = (
            f"{PACKAGES}/bin:{HELPER_MOUNT}/bin:"
            "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
        )
        args = [
            "run", "--detach", "--init",
            "--name", self.name,
            "--label", f"{LABEL}={self.bit_id}",
            "--label", f"zeno.user={self.user_id}",
            # Run as the server's own user, so files on the host stay the server's to manage.
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--cpus", settings.sandbox_cpus,
            "--memory", settings.sandbox_memory,
            "--pids-limit", "256",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--network", settings.sandbox_network,
            # The image itself is read-only; scratch space is a small in-memory /tmp.
            "--read-only",
            "--tmpfs", "/tmp:rw,exec,size=512m",
            "--env", "HOME=/tmp",
            # pip installs into the user's package folder: packages count toward the pool, and
            # every Bit of the user (and every saved tool) can use them.
            "--env", "PIP_USER=1",
            "--env", f"PYTHONUSERBASE={PACKAGES}",
            "--env", "PIP_NO_CACHE_DIR=1",
            "--env", "PIP_DISABLE_PIP_VERSION_CHECK=1",
            "--env", f"PYTHONPATH={HELPER_MOUNT}",
            "--env", f"PATH={path}",
            "--env", f"ZENO_URL={gateway_url}",
            "--env", f"ZENO_TOKEN={gateway_token}",
            # Lets the container reach ZENO on the host (built in on Docker Desktop; needed on Linux).
            "--add-host", "host.docker.internal:host-gateway",
            "--volume", f"{own}:{self.home}",
            "--volume", f"{shared}:{SHARED}:ro",
            "--volume", f"{tools}:{TOOLS}:ro",
            "--volume", f"{packages}:{PACKAGES}",
            "--volume", f"{HELPER_DIR.resolve()}:{HELPER_MOUNT}:ro",
            "--workdir", self.home,
            image(),
            "sleep", "infinity",
        ]
        # The first run may pull the image, which takes a while.
        result = await _docker(*args, timeout=600)
        if result.exit_code != 0:
            raise SandboxError(f"Couldn't start the sandbox: {result.output[-500:]}")
        self.started = True

    async def run(self, command: str, timeout: int) -> CommandResult:
        """Run a shell command in the Bit's folder, stopping it if it overfills the pool."""
        check_room(self.user_id, 0)
        binary = docker_bin()
        process = await asyncio.create_subprocess_exec(
            binary,
            "exec", "--workdir", self.home, self.name,
            "timeout", "--signal=KILL", str(timeout), "bash", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_docker_env(),
        )
        watcher = asyncio.create_task(self._watch_storage(process))
        chunks: list[bytes] = []
        size = 0
        try:
            while chunk := await process.stdout.read(65536):
                # Keep the beginning and the (most useful) end of very long output.
                chunks.append(chunk)
                size += len(chunk)
                while size > MAX_OUTPUT_BYTES and len(chunks) > 2:
                    size -= len(chunks.pop(1))
            exit_code = await process.wait()
        finally:
            watcher.cancel()
        output = b"".join(chunks).decode(errors="replace")
        forget_usage(self.user_id)
        if watcher.done() and not watcher.cancelled() and watcher.result():
            output += "\n[ZENO stopped this command: the storage pool is full]"
        # `timeout --signal=KILL` exits with 137 when it fires.
        return CommandResult(exit_code, output, timed_out=exit_code == 137)

    async def _watch_storage(self, process: asyncio.subprocess.Process) -> bool:
        while process.returncode is None:
            await asyncio.sleep(2)
            pool = await storage_async(self.user_id, max_age=0)
            if pool["used"] > pool["quota"]:
                logger.warning(f"{self.name} filled the storage pool; stopping its command")
                # Kills every process of the sandbox user; PID 1 (the init) belongs to root.
                await _docker("exec", self.name, "kill", "-9", "-1", timeout=10)
                return True
        return False

    async def stop(self):
        if self.started:
            await _docker("rm", "--force", self.name, timeout=30)
            self.started = False


async def remove_stale_containers():
    """Remove sandboxes left over from before a server restart."""
    status = await docker_status(max_age=0)
    if not status["available"]:
        return
    result = await _docker("ps", "--all", "--quiet", "--filter", f"label={LABEL}", timeout=20)
    ids = result.output.split()
    if ids:
        await _docker("rm", "--force", *ids, timeout=60)
        logger.info(f"Removed {len(ids)} leftover Bit sandbox(es)")
