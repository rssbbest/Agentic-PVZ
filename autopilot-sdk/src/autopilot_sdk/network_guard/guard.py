"""Runtime socket-level guard (doc §10.3).

Installed INSIDE the per-turn worker subprocess before participant
code is imported. Not installed in the SDK main process (which needs
unrestricted backend access).

Denylist model: only known LLM providers (+ local-LLM ports) are
blocked; open internet otherwise stays usable.
"""
import _socket
import ipaddress
import os
import re
import socket
import sys
import threading
from typing import Any, Callable, Iterable

from .domains import ALLOWED_HOSTS, BLOCKED_HOST_SUFFIXES, LOCAL_LLM_PORTS


class BlockedNetworkCallError(RuntimeError):
    pass


_original_create_connection = socket.create_connection
_original_getaddrinfo = socket.getaddrinfo
_guard_installed = False


def _host_matches(host: str, table: set[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in table)


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _log_violation_lazy(event: str, **fields: Any) -> None:
    try:
        from autopilot_sdk.telemetry.logger import log_violation

        log_violation(event, **fields)
    except Exception:
        pass


def _check_host(host: str, port: int) -> None:
    if _host_matches(host, ALLOWED_HOSTS):
        return
    relay = os.environ.get("AUTOPILOT_SDK_RELAY_PORT")
    if relay and _is_loopback(host) and str(port) == relay:
        return  # `autopilot play`'s own relay to the organiser's LLM gateway
    if _is_loopback(host) and port in LOCAL_LLM_PORTS:
        _log_violation_lazy("blocked_local_llm_server", host=host, port=port)
        raise BlockedNetworkCallError(
            f"Blocked connection to local LLM server at {host}:{port}. "
            "Only the organisers' LLM gateway may be used for LLM access."
        )
    if _host_matches(host, BLOCKED_HOST_SUFFIXES):
        _log_violation_lazy("blocked_llm_provider", host=host, port=port)
        raise BlockedNetworkCallError(
            f"Blocked outbound call to '{host}'. Only the organisers' LLM gateway may "
            "be used for LLM access — see the Agent Contract doc."
        )
    # Anything else is allowed.


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        host, port = address[0], address[1]
    except Exception:
        return _original_create_connection(address, *args, **kwargs)
    _check_host(str(host), int(port) if isinstance(port, int) else 0)
    return _original_create_connection(address, *args, **kwargs)


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    if host is not None:
        port = args[0] if args else 0
        try:
            port_int = int(port) if isinstance(port, int) else 0
        except Exception:
            port_int = 0
        _check_host(str(host), port_int)
    return _original_getaddrinfo(host, *args, **kwargs)


def install_network_guard() -> None:
    """Idempotent: patch socket choke points for this process only."""
    global _guard_installed
    socket.create_connection = _guarded_create_connection  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[method-assign]
    _guard_installed = True


def uninstall_network_guard() -> None:
    """Restore originals (mainly for unit tests)."""
    global _guard_installed
    socket.create_connection = _original_create_connection  # type: ignore[method-assign]
    socket.getaddrinfo = _original_getaddrinfo  # type: ignore[method-assign]
    _guard_installed = False


def is_guard_installed() -> bool:
    return _guard_installed


class BlockedOperationError(BlockedNetworkCallError):
    """A sandboxed agent tried something other than an allowed network call."""


_PROCESS_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty", "os.startfile",
})
# ctypes may be imported, but not used to call native code.
_NATIVE_EVENTS = frozenset({"ctypes.dlsym", "ctypes.dlsym/handle", "ctypes.call_function"})
# These reach every live object or frame, including the sandbox's own state.
_INTROSPECTION_EVENTS = frozenset({
    "gc.get_objects", "gc.get_referrers", "gc.get_referents", "sys._current_frames",
})


def _readable_roots(agent_path: str | None) -> tuple[tuple[str, ...], set[str]]:
    """Folders and files a frozen agent may read: Python's own installation,
    the SDK package and the SDK program, and the agent's frozen copy. Nothing
    the participant can edit while a run plays."""
    import site

    import autopilot_sdk

    roots = {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix,
             getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(autopilot_sdk.__file__))}
    try:
        roots.update(site.getsitepackages())
    except AttributeError:  # pragma: no cover - old virtualenv
        pass
    files = {os.path.realpath(sys.executable)}
    if agent_path:
        import importlib.util

        files.add(os.path.realpath(agent_path))
        # Where the import system looks for the agent's compiled copy (never written: see below).
        files.add(os.path.realpath(importlib.util.cache_from_source(agent_path)))
    norm = tuple(sorted({os.path.normcase(os.path.realpath(r)) for r in roots if r}))
    return norm, {os.path.normcase(f) for f in files}


def install_agent_sandbox(
    allowed_hosts: Iterable[str] = (),
    on_violation: Callable[[str], None] | None = None,
    loopback_ports: Iterable[int] = (),
    frozen_agent: str | None = None,
) -> None:
    """Strict guard for live reinforcement agents, built on a CPython audit hook.

    Unlike install_network_guard, an audit hook cannot be removed and also
    sees C-level calls (``_socket``), so restoring ``socket`` functions or
    calling the guard's uninstall does not help. Inside the agent process:

    - Network: only the LLM gateway: ALLOWED_HOSTS and ``allowed_hosts``, and
      on loopback only the SDK's LLM relay port (AUTOPILOT_SDK_RELAY_PORT) and
      ``loopback_ports`` (a gateway on this machine). A raw IP is allowed only
      if one of those hostnames resolved to it. No local (AF_UNIX) sockets.
      So agent code cannot ask another program on the machine what to play.
    - ``frozen_agent`` (a published run's frozen agent.py): files may be opened
      and folders listed only inside Python's installation, the SDK and that
      file, so nothing the participant edits during the run (another file, a
      module beside agent.py) can change what the agent plays.
    - No subprocesses, os.system, exec or fork.
    - No native calls through ctypes; no gc object walks (they would reach
      this hook's state).
    - No imports of banned LLM provider SDKs, however the import is written.

    ``on_violation`` runs before the error is raised, so a violation is
    reported even if agent code catches the exception. The state lives only
    in this closure; nothing in a module global points at it. This raises the
    bar for agent code; it is not an OS sandbox.
    """
    from autopilot_sdk.verifier.denylist import BANNED_IMPORT_ROOTS, WARN_ONLY_IMPORT_ROOTS

    allowed = {h.lower().rstrip(".") for h in (*ALLOWED_HOSTS, *allowed_hosts) if h}
    banned_imports = BANNED_IMPORT_ROOTS - WARN_ONLY_IMPORT_ROOTS
    # As path components, e.g. ("google", "generativeai").
    banned_paths = [tuple(name.split(".")) for name in banned_imports]
    approved_ips: set[str] = set()
    resolving = threading.local()
    resolve = _socket.getaddrinfo
    reachable = ", ".join(sorted(allowed - {"localhost"})) or "none"

    def block(message: str) -> None:
        if on_violation is not None:
            on_violation(message)
        raise BlockedOperationError(message)

    relay = os.environ.get("AUTOPILOT_SDK_RELAY_PORT", "")
    local_ports = {int(relay)} if relay.isdigit() else set()
    local_ports.update(int(p) for p in loopback_ports if p)

    def check_loopback(host: str, port: int) -> None:
        if port in local_ports or port == 0:
            return  # the relay / a gateway on this machine; port 0: a name lookup, no connection yet
        if port in LOCAL_LLM_PORTS:
            block(f"Blocked connection to local LLM server at {host}:{port}. "
                  "Only the organiser's LLM gateway may be used for LLM access.")
        block(f"Blocked connection to {host}:{port}. Agent code may only reach the LLM gateway, "
              "not other programs on this machine.")

    def check_ip(host: str, port: int) -> None:
        addr = ipaddress.ip_address(host.split("%", 1)[0])
        if getattr(addr, "ipv4_mapped", None):
            addr = addr.ipv4_mapped
        if addr.is_loopback or addr.is_unspecified:
            check_loopback(host, port)
        elif str(addr) not in approved_ips:
            block(f"Blocked connection to {host}:{port}. Agent code may only reach "
                  f"loopback and the LLM gateway ({reachable}).")

    def check_name(host: str, port: int, remember: bool) -> None:
        name = host.lower().rstrip(".")
        if name == "localhost":
            check_loopback(host, port)
            return
        if _host_matches(name, BLOCKED_HOST_SUFFIXES):
            block(f"Blocked outbound call to '{host}'. Only the organiser's LLM "
                  "gateway may be used for LLM access.")
        if not _host_matches(name, allowed):
            block(f"Blocked connection to '{host}'. Agent code may only reach "
                  f"loopback and the LLM gateway ({reachable}).")
        if remember:
            resolving.active = True
            try:
                approved_ips.update(info[4][0] for info in resolve(name, None))
            except OSError:
                pass
            finally:
                resolving.active = False

    def check_host(host: object, port: object, remember: bool = False) -> None:
        if host is None:
            return
        host = host.decode() if isinstance(host, (bytes, bytearray)) else str(host)
        port = int(port) if isinstance(port, int) or str(port).isdigit() else 0
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            check_name(host, port, remember)
        else:
            check_ip(host, port)

    def check_address(address: object) -> None:
        if isinstance(address, (str, bytes)):
            block("Blocked a local (AF_UNIX) socket: agent code may only reach the LLM gateway.")
        if not isinstance(address, tuple) or len(address) < 2:
            block(f"Blocked connection to unrecognised address {address!r}")
        check_host(address[0], address[1])

    def block_import(module: str) -> None:
        block(f"Blocked import of LLM provider SDK '{module}'. Only the "
              "organiser's LLM gateway may be used for LLM access.")

    def check_import(module: str) -> None:
        if any(module == b or module.startswith(b + ".") for b in banned_imports):
            block_import(module)

    def check_module_code(code: object) -> None:
        # importlib.import_module raises no "import" event, but every module's
        # code runs through exec once it is in sys.modules.
        for name in banned_imports:
            if name in sys.modules:
                block_import(name)
        parts = tuple(re.split(r"[\\/]", str(getattr(code, "co_filename", ""))))
        parts = parts[:-1] + (parts[-1].removesuffix(".py"),) if parts else parts
        for banned in banned_paths:
            n = len(banned)
            if any(parts[i:i + n] == banned for i in range(len(parts) - n + 1)):
                block_import(".".join(banned))

    roots, files = _readable_roots(frozen_agent) if frozen_agent else ((), set())

    def check_path(event: str, path: object) -> None:
        if isinstance(path, int):
            return  # an already open file descriptor (the SDK's own pipe)
        try:
            real = os.path.normcase(os.path.realpath(os.fsdecode(path) if path is not None else "."))
        except (TypeError, ValueError):
            real = ""
        if real in files or real == os.devnull or any(real == r or real.startswith(r + os.sep) for r in roots):
            return
        block(f"Blocked {event} of {path!r}: in a published run the agent reads nothing but its frozen "
              "agent.py, so nothing changed during the run can change what it plays.")

    def hook(event: str, args: tuple) -> None:
        if getattr(resolving, "active", False):
            return
        if roots and event in ("open", "os.listdir", "os.scandir", "glob.glob"):
            check_path(event, args[0] if args else None)
            return
        if event in ("socket.connect", "socket.sendto"):
            try:
                check_address(args[1])
            except BlockedOperationError:
                raise
            except Exception:  # fail closed on anything unparseable
                block(f"Blocked connection to unrecognised address {args[1]!r}")
        elif event == "socket.getaddrinfo":
            check_host(args[0], args[1], remember=True)
        elif event in ("socket.gethostbyname", "socket.gethostbyname_ex"):
            check_host(args[0], 0)
        elif event == "import":
            check_import(str(args[0]))
        elif event == "exec":
            check_module_code(args[0])
        elif event in _PROCESS_EVENTS:
            block(f"Blocked {event}: agent code may not start other programs or processes.")
        elif event in _NATIVE_EVENTS:
            block(f"Blocked {event}: agent code may not call native code through ctypes.")
        elif event in _INTROSPECTION_EVENTS:
            block(f"Blocked {event}: agent code may not walk the interpreter's objects.")

    # On Windows, importing ctypes resolves kernel32 functions (ctypes.dlsym
    # events). Load it before the hook so only the agent's own native calls count.
    import ctypes  # noqa: F401
    if frozen_agent:
        sys.dont_write_bytecode = True  # imports must not write .pyc files either
    sys.addaudithook(hook)
