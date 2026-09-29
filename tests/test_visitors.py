"""deploy/visitors.sh's summarizer, fed synthetic Caddy access-log lines.

The summarizer is a Python program embedded in the shell script (so the box needs nothing but
python3). These tests lift it out and run it on stdin, with no box and no docker.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "visitors.sh"


def _summarize(lines, days=7):
    program = re.search(r"<<'PY'\n(.*?)\nPY\n", SCRIPT.read_text(), re.S).group(1)
    result = subprocess.run(
        [sys.executable, "-c", program, str(days)],
        input="\n".join(lines).encode(), capture_output=True, check=True,
    )
    return result.stdout.decode()


def _line(uri, ip="203.0.113.7", ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)",
          host="orbital.vibcreates.com"):
    return json.dumps({"ts": time.time(), "request": {
        "host": host, "uri": uri, "client_ip": ip, "headers": {"User-Agent": [ua]},
    }})


def test_scanner_paths_count_as_bots_whatever_the_user_agent():
    """A scanner sends a browser's user agent and asks for files no visitor ever wants; before
    this, each one counted as a human visitor and its probes topped the reading list."""
    probes = ["/.env", "/.git/config", "/wp-login.php", "/wp-admin/setup-config.php",
              "/x/.aws/credentials", "/cgi-bin/luci", "/phpmyadmin/", "/%2eenv", "/cgi-bin"]
    lines = [_line(p, ip="198.51.100.9") for p in probes]
    lines += [_line("/satellites/25544"), _line("/operators/st.engineering")]
    out = _summarize(lines)
    assert "11 requests, 2 human-ish, 9 bot, 1 unique visitors" in out
    assert "198.51.100.9" not in out
    assert "/.env" not in out and "/wp-login.php" not in out
    assert "/operators/st.engineering" in out


def test_a_bot_user_agent_still_counts_as_a_bot():
    out = _summarize([_line("/", ua="Mozilla/5.0 (compatible; Googlebot/2.1)"), _line("/")])
    assert "2 requests, 1 human-ish, 1 bot, 1 unique visitors" in out


def test_no_comment_in_the_program_has_an_apostrophe():
    """The program rides inside $(cat <<'PY' ...), and bash 3.2 (macOS) scans that heredoc for
    quotes. The code's quotes come in pairs, but one apostrophe in a comment ("a browser's")
    leaves a quote open and breaks the whole script there. Checked statically, because the
    bash on a CI runner is newer and parses it anyway."""
    program = re.search(r"<<'PY'\n(.*?)\nPY\n", SCRIPT.read_text(), re.S).group(1)
    comments = [line.split("#", 1)[1] for line in program.splitlines() if "#" in line]
    assert not [c for c in comments if "'" in c]


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
