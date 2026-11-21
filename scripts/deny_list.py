"""Fail if a public file names Rendobar internals.

Every public Rendobar repo runs this from its CI, against its own checkout:
  python3 -I .openapi/scripts/deny_list.py
Public repos only ever read the public spec, so internals have no way in
except by hand. This is the check for that hand.

It scans the files git tracks (what a push publishes), not the working tree.
"""
import pathlib
import re
import subprocess
import sys

# Letters only around a name, so SHOTCUT_URL, MLT_FILTER and libmltqt6 are caught
# (\b would treat the underscore and digits as part of the word).
WORD = r"(?<![a-z]){}(?![a-z])"
DENY = [
    # Monorepo paths. Not after "/" or ".", so URLs such as files.pythonhosted.org/packages/db/ pass.
    r"(?<![\w/.])apps/[\w-]+/", r"(?<![\w/.])packages/(shared|db|brand|email|runner-[\w-]+)/",
    r"\.claude\b",  # the private repo's agent rules
    # Infrastructure vendors. Modal only as the vendor: "a modal" is also a UI dialog in the API.
    WORD.format("trigger\\.dev"), r"\bmodal\.(com|run)\b", r"\bmodal (deploy|app|gpu|volume|lab|cpu)\b", WORD.format("infisical"),
    WORD.format("shotcut"), WORD.format("melt"), WORD.format("mlt"), r"libmlt",  # the compose engine is not public
    r"api-staging\.rendobar\.com", r"\.workers\.dev\b",  # internal hosts
]
SKIP_SUFFIX = {".whl", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}


def tracked() -> list:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=False)
    if out.returncode == 0:
        return [pathlib.Path(p) for p in out.stdout.decode().split("\0") if p]
    return [p for p in pathlib.Path(".").rglob("*") if p.is_file() and ".git" not in p.parts]


hits = []
for path in tracked():
    if path.name == "deny_list.py" or path.suffix in SKIP_SUFFIX or not path.is_file():
        continue
    text = path.read_text(errors="ignore")
    for pattern in DENY:
        for m in re.finditer(pattern, text, flags=re.I):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{path}:{line}: {m.group(0)}")
print("\n".join(hits) if hits else "deny-list: clean")
sys.exit(1 if hits else 0)
