"""Start a long tool step as a Slurm job, a local background process or an inline process, then poll it.

Pattern: a tool calls ``submit`` (returns at once), the client polls ``status``. The job's own
entry point wraps its work in ``record_done``, which writes ``<jobdir>/done.json`` (COMPLETED or
FAILED) when it ends, so status is read the same way for every executor. A preempted Slurm job is
resubmitted with the fallback sbatch options.

Executors:
  slurm   sbatch; survives the server. For a persistent or one-shot server on a cluster login node.
  local   detached background process; needs the process that started it (the server, or the
          container around it) to stay alive, so use it with a persistent server, e.g. inside a
          Slurm allocation or a long-lived MCP session.
  inline  runs the job in the foreground and returns when it ends (``submit`` blocks). For one-shot
          callers in which each tool call is a fresh process or container, e.g. ``apptainer exec``
          per call or an agent sandbox; give the client a timeout longer than the job.

A local job whose process is gone without ``done.json`` is reported FAILED; the process is
identified by host, pid and start time, so a reused pid or another PID namespace is not mistaken
for the job.

Settings (environment):
  AISCI_EXECUTOR        slurm | local | inline             (default slurm)
  AISCI_SLURM           sbatch options, first attempt     (default "--cpus-per-task=4 --mem=16G --time=04:00:00")
  AISCI_SLURM_FALLBACK  sbatch options after a preemption (default: same as AISCI_SLURM)
  AISCI_JOB_PREFIX      prepended to the job command, e.g. "apptainer exec --bind /data image.sif"
"""

import contextlib
import fcntl
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

RETRY_STATES = ("PREEMPTED", "NODE_FAIL", "REQUEUED")
EXECUTORS = ("slurm", "local", "inline")


@dataclass(frozen=True)
class JobSettings:
    executor: str
    slurm: str
    slurm_fallback: str
    prefix: str

    @classmethod
    def from_env(cls, default_slurm="--cpus-per-task=4 --mem=16G --time=04:00:00"):
        e = os.environ.get
        slurm = e("AISCI_SLURM", default_slurm)
        executor = e("AISCI_EXECUTOR", "slurm")
        if executor not in EXECUTORS:
            raise ValueError(f"AISCI_EXECUTOR={executor!r}; expected one of {', '.join(EXECUTORS)}")
        return cls(executor, slurm, e("AISCI_SLURM_FALLBACK", "") or slurm, e("AISCI_JOB_PREFIX", ""))


def read_state(jobdir):
    p = managed_file(jobdir, "state.json")
    return json.loads(p.read_text()) if p.exists() else {}


def write_state(jobdir, state):
    write_json(jobdir, "state.json", state)


def managed_file(jobdir, name):
    """Runtime metadata/logs must be regular files, never links to other data."""
    root = Path(jobdir)
    p = root / name
    if Path(name).name != name or root.is_symlink() or p.is_symlink() or (p.exists() and not p.is_file()):
        raise ValueError("job metadata must be a regular file inside its workspace")
    return p


def write_json(jobdir, name, value):
    """Publish complete JSON atomically; concurrent writers never share a temp file."""
    target = managed_file(jobdir, name)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=jobdir, prefix=f".{name}.", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
        managed_file(jobdir, name)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def _status_lock(jobdir):
    """Serialize status changes, including the decision to resubmit a failed allocation."""
    with managed_file(jobdir, ".status.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _proc_start(pid):
    """Start time of ``pid`` in clock ticks since boot, or None if it is gone or a zombie."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if not Path("/proc").exists():
        # macOS has no procfs. kill(pid, 0) succeeds for zombies and cannot detect
        # PID reuse; ps provides both a live state and a stable start identity.
        try:
            proc = subprocess.run(
                ["ps", "-p", str(pid), "-o", "stat=", "-o", "lstart="],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            fields = proc.stdout.split()
            return "ps:" + " ".join(fields[1:]) if len(fields) > 1 and fields[0][0] not in "ZX" else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return None if fields[0] in ("Z", "X") else fields[19]


def _local_state(state):
    """RUNNING, FAILED (with a note) or UNKNOWN for a local job without done.json."""
    proc = state.get("proc", {})
    if proc.get("host") != socket.gethostname():
        return "UNKNOWN", f"job started on {proc.get('host', '?')}; check it there"
    if proc.get("start") is not None and _proc_start(proc.get("pid", 0)) == proc["start"]:
        return "RUNNING", ""
    return "FAILED", ("job process is gone without done.json (killed, or the server or container that "
                      "started it exited); for one-shot calls use AISCI_EXECUTOR=inline or slurm")  # fmt: skip


def _inline_state(state):
    """An interrupted inline caller cannot remain RUNNING after its allocation dies."""
    owner = state.get("proc", {})
    if owner.get("host") == socket.gethostname():
        return _local_state(state)
    allocation = state.get("allocation_job_id")
    if allocation:
        try:
            current = _slurm_state(allocation)
        except OSError:
            current = "UNKNOWN"
        if current in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED"}:
            return current, ""
        if current != "UNKNOWN":
            return "FAILED", f"inline allocation ended ({current}) without done.json; submit a new task"
    return "UNKNOWN", "inline caller liveness cannot be checked here; inspect its original host/allocation"


def submit(cfg, cmd, jobdir, name, slurm_opts=None, extra=None):
    """Start ``cmd`` (a list) for ``jobdir``. Returns the new state."""
    jobdir = Path(jobdir)
    jobdir.mkdir(parents=True, exist_ok=True)
    managed_file(jobdir, "done.json").unlink(missing_ok=True)
    full = shlex.split(cfg.prefix) + list(cmd)
    state = read_state(jobdir) | (extra or {}) | {"cmd": full, "executor": cfg.executor, "submitted": time.time()}
    if cfg.executor == "local":
        with managed_file(jobdir, "job.log").open("a") as log:
            proc = subprocess.Popen(full, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)  # fmt: skip
        state |= {"proc": {"host": socket.gethostname(), "pid": proc.pid, "start": _proc_start(proc.pid)},
                  "state": "RUNNING"}  # fmt: skip
    elif cfg.executor == "inline":
        state["proc"] = {"host": socket.gethostname(), "pid": os.getpid(), "start": _proc_start(os.getpid())}
        state.pop("allocation_job_id", None)
        if allocation := os.environ.get("SLURM_JOB_ID"):
            state["allocation_job_id"] = allocation
        # Publish only after the child exists. A caller that cancels the
        # process group on observing RUNNING must not race with posix_spawn
        # and leave a newly spawned worker holding the workspace lock.
        with (
            managed_file(jobdir, "job.log").open("a") as log,
            subprocess.Popen(full, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT) as proc,
        ):
            write_state(jobdir, state | {"state": "RUNNING"})
            rc = proc.wait()
        done = managed_file(jobdir, "done.json")
        state |= json.loads(done.read_text()) if done.exists() else {"state": "FAILED", "error": f"exit {rc}"}
    else:
        opts = slurm_opts if slurm_opts is not None else cfg.slurm
        script = managed_file(jobdir, "job.sbatch")
        managed_file(jobdir, "job.log")
        script.write_text(
            "#!/usr/bin/env bash\n"
            f"#SBATCH --job-name={name[:60]} {opts}\n"
            f"#SBATCH --output={jobdir}/job.log --open-mode=append\n"
            "set -eo pipefail\n"
            f"/usr/bin/time -v {shlex.join(full)}\n"
        )
        out = subprocess.run(["sbatch", "--parsable", str(script)], check=True, capture_output=True, text=True)
        jid = out.stdout.strip().split(";")[0]
        state |= {"job_id": jid, "slurm_opts": opts, "state": "PENDING"}
        state.setdefault("jobs", []).append({"job_id": jid, "slurm_opts": opts})
    write_state(jobdir, state)
    return state


def _slurm_state(jid):
    q = subprocess.run(["squeue", "-j", jid, "-h", "-o", "%T"], capture_output=True, text=True, check=False)
    if q.stdout.strip():
        return q.stdout.strip()
    acct = subprocess.run(["sacct", "-j", jid, "-X", "-n", "-o", "State"], capture_output=True, text=True,
                          check=False)  # fmt: skip
    return (acct.stdout.split() or ["UNKNOWN"])[0]


def status(cfg, jobdir):
    """Current state; resubmits a preempted Slurm job with the fallback options."""
    jobdir = Path(jobdir)
    with _status_lock(jobdir):
        return _status(cfg, jobdir)


def _status(cfg, jobdir):
    state = read_state(jobdir)
    done = managed_file(jobdir, "done.json")
    if done.exists():
        state |= json.loads(done.read_text())
    elif state.get("executor") == "local":
        state["state"], note = _local_state(state)
        if note:
            state["note"] = note
    elif state.get("executor") == "inline" and state.get("state") in {"RUNNING", "UNKNOWN"}:
        state["state"], note = _inline_state(state)
        if note:
            state["note"] = note
    elif "job_id" in state:
        s = _slurm_state(state["job_id"])
        state["state"] = s
        if s in RETRY_STATES:
            cmd = state["cmd"][len(shlex.split(cfg.prefix)) :]
            state = submit(cfg, cmd, jobdir, jobdir.name, slurm_opts=cfg.slurm_fallback)
            state["note"] = f"previous job {s.lower()}; resubmitted with: {cfg.slurm_fallback}"
    write_state(jobdir, state)
    return state


def record_done(jobdir, fn):
    """Run ``fn()`` inside a job and record COMPLETED or FAILED in done.json; exit 1 on failure."""
    t0, jobdir = time.time(), Path(jobdir)
    jobdir.mkdir(parents=True, exist_ok=True)
    try:
        fn()
        done = {"state": "COMPLETED"}
    except Exception as e:
        done = {"state": "FAILED", "error": f"{type(e).__name__}: {e}"}
        traceback.print_exc()
    done["elapsed_s"] = round(time.time() - t0, 1)
    write_json(jobdir, "done.json", done)
    if done["state"] != "COMPLETED":
        sys.exit(1)
