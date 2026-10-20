"""Start the embedded test postgres and exit immediately (no waiting)."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._pg_embedded import PGDATA, _bin  # noqa: E402

log = PGDATA / "server.log"
proc = subprocess.Popen(
    [_bin("pg_ctl"), "-D", str(PGDATA), "-l", str(log), "-w", "-t", "60", "start"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
out, _ = proc.communicate(timeout=90)
sys.stdout.write(out.decode("utf-8", "replace"))
sys.stdout.write(f"rc={proc.returncode}\n")
