"""Sequential ORCA job queue driven by QProcess (the plugin is the launcher)."""

import logging
import os
import shutil
import signal
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from . import config, proc

logger = logging.getLogger(__name__)

# Files never copied back from the scratch folder (the .out IS a result);
# ORCA temp files can carry ".tmp" anywhere in the name, not just as suffix
def _is_transient(name, input_name):
    return name == input_name or ".tmp" in name.lower()
# The success banner is checked on the file tail; it can straddle read chunks
TAIL_BYTES = 65536
NORMAL_TERMINATION = "ORCA TERMINATED NORMALLY"


class QueueRunner(QObject):
    """Run a list of .inp files one at a time inside <workspace>/temp."""

    job_started = Signal(str, int, int)   # name, index (1-based), total
    job_output = Signal(str)              # raw ORCA output chunk
    job_finished = Signal(str, bool, str)  # name, ok, message
    queue_finished = Signal(list)         # [{path, name, ok, error}, ...]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.binary = ""
        self._paths = []
        self._index = 0
        self._results = []
        self._process = None
        self._out_file = None
        self._out_path = None
        self._job_start = None
        self._stop_after_current = False
        self._stopping = False
        self._temp_root = None

    # ------------------------------------------------------------- state
    def is_running(self):
        return self._index < len(self._paths)

    def current_input(self):
        if 0 <= self._index < len(self._paths):
            return self._paths[self._index]
        return None

    def stop_after_current(self):
        """Graceful stop: the running file finishes, the next ones are not started."""
        self._stop_after_current = True

    # -------------------------------------------------------------- start
    def start(self, binary, workspace, input_paths):
        """Launch the queue; ownership of the list stays with the caller."""
        if self.is_running():
            logger.warning("start() called while a queue is already running")
            return False
        if not config.validate_binary(binary)[0]:
            logger.warning("invalid ORCA binary: %s", binary)
            return False

        self.binary = str(Path(binary).resolve())
        self._paths = [str(Path(p).resolve()) for p in input_paths]
        self._index = 0
        self._results = []
        self._stop_after_current = False
        self._stopping = False
        self._temp_root = Path(workspace) / config.TEMP_DIRNAME
        self._temp_root.mkdir(parents=True, exist_ok=True)
        self._start_job()
        return True

    def _start_job(self):
        if self._index >= len(self._paths):
            self._end_queue()
            return
        # A requested stop (or graceful stop after a finished file) ends here
        if self._stopping or (self._stop_after_current and self._index > 0):
            self._end_queue()
            return

        input_path = Path(self._paths[self._index])
        name = input_path.name
        scratch = self._temp_root / input_path.stem
        if scratch.exists():
            shutil.rmtree(scratch)
        scratch.mkdir(parents=True)
        shutil.copy2(input_path, scratch / name)

        self._out_path = scratch / (input_path.stem + ".out")
        self._out_file = open(self._out_path, "wb")
        self._job_start = datetime.now()

        env = QProcessEnvironment.systemEnvironment()
        for key, value in config.extra_environment(self.binary).items():
            env.insert(key, value)

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.setProcessEnvironment(env)
        self._process.setWorkingDirectory(str(scratch))
        self._process.readyRead.connect(self._read_output)
        self._process.finished.connect(self._job_done)
        self._process.errorOccurred.connect(self._process_error)
        self._process.start(self.binary, [name])

        self.job_started.emit(name, self._index + 1, len(self._paths))

    def _end_queue(self):
        """Reset the queue state BEFORE emitting: slots may query is_running()
        and start a new queue right away from the queue_finished handler."""
        results = self._results
        self._paths = []
        self._index = 0
        self._results = []
        self._stop_after_current = False
        self._stopping = False
        self.queue_finished.emit(results)

    # ------------------------------------------------------------- output
    def _read_output(self):
        if self._process is None or self._out_file is None:
            return
        try:
            chunk = bytes(self._process.readAll())
        except RuntimeError:  # process destroyed mid-signal
            return
        self._out_file.write(chunk)  # tee: the .out file is the full record
        self.job_output.emit(chunk.decode("utf-8", errors="replace"))

    # ------------------------------------------------------------ finish
    def _job_done(self, exit_code, _status):
        process, self._process = self._process, None
        out_file, self._out_file = self._out_file, None
        # Drain any output queued between the last readyRead and finished
        if process is not None:
            try:
                chunk = bytes(process.readAll())
            except RuntimeError:
                chunk = b""
            if chunk and out_file is not None:
                out_file.write(chunk)
                self.job_output.emit(chunk.decode("utf-8", errors="replace"))
        if out_file is not None:
            out_file.close()
        if process is not None:
            process.deleteLater()

        stopped = self._stopping
        ok = (not stopped) and exit_code == 0 and self._terminated_normally()
        input_path = Path(self._paths[self._index])
        message = self._collect_results(input_path, ok, stopped)

        self._results.append({
            "path": str(input_path),
            "name": input_path.name,
            "ok": ok,
            "error": "" if ok else message,
        })
        self.job_finished.emit(input_path.name, ok, message)
        self._index += 1
        self._start_job()

    def _process_error(self, error):
        """Only FailedToStart needs handling here; Crashed and Timedout are
        always followed by finished(), which advances the queue itself."""
        if error != QProcess.FailedToStart:
            return
        text = "ORCA failed to start (bad executable path?)"
        logger.warning("%s", text)
        if self._out_file is not None:
            self._out_file.close()
            self._out_file = None
        self._process = None
        input_path = Path(self._paths[self._index])
        self._results.append({
            "path": str(input_path),
            "name": input_path.name,
            "ok": False,
            "error": text,
        })
        self.job_finished.emit(input_path.name, False, text)
        self._index += 1
        self._stopping = True  # a broken ORCA will fail for every file: stop
        self._start_job()

    # ------------------------------------------------------------- helpers
    def _terminated_normally(self):
        """Check the success banner on the .out tail (it may straddle chunks)."""
        try:
            with open(self._out_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - TAIL_BYTES))
                return NORMAL_TERMINATION.encode() in f.read()
        except OSError:
            return False

    def _collect_results(self, input_path, ok, stopped):
        """Collect everything ORCA produced into the run folder (input
        included); keep the scratch folder only when the job failed."""
        results_dir = input_path.parent / input_path.stem
        scratch = self._temp_root / input_path.stem
        message = ""
        try:
            results_dir.mkdir(parents=True, exist_ok=True)
            # On success the run owns its input: MOVE it in, leaving no loose
            # duplicates behind. On error (or user stop) it stays loose so
            # Retry failed can re-run the original path.
            if ok and input_path != (results_dir / input_path.name):
                shutil.move(str(input_path), str(results_dir / input_path.name))
            elif not ok:
                shutil.copy2(input_path, results_dir / input_path.name)
            for item in sorted(scratch.iterdir()):
                if not item.is_file():
                    continue
                if _is_transient(item.name, input_path.name):
                    continue
                shutil.copy2(item, results_dir / item.name)
            if ok or stopped:
                shutil.rmtree(scratch, ignore_errors=True)
                message = "" if ok else "stopped by user"
            else:
                message = f"error (raw files kept in {scratch})"
        except OSError as e:
            message = f"error collecting results: {e}"
            logger.warning("%s: %s", input_path.name, message)
        return message

    # --------------------------------------------------------------- stop
    def stop(self):
        """Stop now: kill the tree; the queue ends when QProcess reports back."""
        if self._process is None:
            self._stopping = True
            self._start_job()
            return
        self._stopping = True
        if proc.IS_LINUX:
            pids = proc.tree_of(self._process.processId())
            proc.signal_tree(pids, signal.SIGTERM)

            # SIGKILL the survivors after the grace period (non-blocking)
            def finish_off():
                proc.signal_tree([p for p in pids if proc.is_alive(p)],
                                 signal.SIGKILL)

            QTimer.singleShot(proc.GRACE_SECONDS * 1000, finish_off)
        else:
            # Best effort off Linux: signal the direct child only; helper
            # children are not guaranteed to die (see README)
            self._process.terminate()
            QTimer.singleShot(proc.GRACE_SECONDS * 1000, self._process.kill)

    def stop_sync(self, grace_msec=8000):
        """Blocking stop for dialog close: kill the tree and wait for Qt."""
        if self._process is not None:
            pid = self._process.processId()
            if pid > 0:
                proc.terminate_tree_sync(pid)
            self._process.waitForFinished(grace_msec)
        self._stopping = True
