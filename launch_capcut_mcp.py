import os
from pathlib import Path

ffprobe_bin = Path(os.environ["CAPCUT_FFPROBE_BIN"])
if not (ffprobe_bin / "ffprobe.exe").is_file():
    raise FileNotFoundError(f"ffprobe.exe não encontrado em {ffprobe_bin}")
os.environ["PATH"] = str(ffprobe_bin) + os.pathsep + os.environ.get("PATH", "")

from entry import main

if __name__ == "__main__":
    main()
