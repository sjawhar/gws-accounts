"""Shared harness for the bin/gws tests: stubs, fixtures and the Harness base class.

Each test gets a scratch HOME holding ~/.config/gws/accounts.json and a stub
directory first on PATH. The stub `gws` stands in for real gws and prints the
environment it was given; `fake-secrets KEY -- cmd` injects KEY from
STUB_VALUE_<KEY>, the way a secret runner does. The rest of PATH is /usr/bin
and /bin only, so on macOS the wrapper runs under the system bash 3.2.
"""

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent / "bin" / "gws"

STUB_GWS = r"""echo "TOKEN=${GOOGLE_WORKSPACE_CLI_TOKEN:-}"
echo "FILE=${GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE:-}"
echo "ADC=${GOOGLE_APPLICATION_CREDENTIALS:-}"
echo "DIR=${GOOGLE_WORKSPACE_CLI_CONFIG_DIR:-}"
echo "HOME=${HOME:-}"
echo "CLIENT_ID=${GOOGLE_WORKSPACE_CLI_CLIENT_ID:-}"
echo "CLIENT_SECRET=${GOOGLE_WORKSPACE_CLI_CLIENT_SECRET:-}"
# gws loads a .env for each variable left unset, so the wrapper blanks a source it must
# keep from gws instead of unsetting it; EMPTY lists the credential variables set empty.
empty=""
neutral=""
set_nonempty=""
for v in GOOGLE_WORKSPACE_CLI_TOKEN GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE GOOGLE_APPLICATION_CREDENTIALS GOOGLE_WORKSPACE_CLI_CLIENT_ID GOOGLE_WORKSPACE_CLI_CLIENT_SECRET; do
  if [ -n "${!v+set}" ] && [ -z "${!v}" ]; then empty="$empty $v"; fi
done
# NEUTRAL lists every exported variable gws receives set to empty; SET lists the proxy
# variables that arrive with a value. Neither holds a copy of the wrapper's key lists.
for v in $(compgen -e); do
  if [ -z "${!v}" ]; then neutral="$neutral $v"; fi
done
for v in $(compgen -e | grep -i '_proxy$'); do
  if [ -n "${!v}" ]; then set_nonempty="$set_nonempty $v=${!v}"; fi
done
echo "NEUTRAL=${neutral# }"
echo "SET=${set_nonempty# }"
if [ -n "${STUB_TOKEN_SCAN_DIR:-}" ]; then
  PYTHON -c 'import os, pathlib, sys
token = os.environ["GOOGLE_WORKSPACE_CLI_TOKEN"].encode()
for path in pathlib.Path(sys.argv[1]).rglob("*"):
    if path.is_file() and token in path.read_bytes():
        raise SystemExit("access token was materialized on disk")' "$STUB_TOKEN_SCAN_DIR" || exit $?
fi
echo "EMPTY=${empty# }"
printf 'ARG=%s\n' "$@"
for v in $(compgen -v); do
  case "$v" in
    GWS_*_OAUTH|GWS_ACCOUNTS_REENTRY) if [ -n "${!v:-}" ]; then echo "LEAKED=$v"; fi ;;
  esac
done
if [ -n "${GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE:-}" ]; then
  echo "MODE=$(PYTHON -c 'import os, sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE")"
  cat "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"
  echo
fi
exit "${STUB_EXIT:-0}"
""".replace("PYTHON", sys.executable)

FAKE_SECRETS = r"""key="$1"; shift
[ "$1" = "--" ] && shift
echo "FETCHED=$key" >&2
value_var="STUB_VALUE_$key"
if [ -z "${!value_var:-}" ]; then echo "fake-secrets: no value for $key" >&2; exit 1; fi
export "$key=${!value_var}"
for name in GOOGLE_APPLICATION_CREDENTIALS GOOGLE_WORKSPACE_CLI_TOKEN GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE GOOGLE_WORKSPACE_CLI_CLIENT_ID GOOGLE_WORKSPACE_CLI_CLIENT_SECRET; do
  inherited="STUB_INHERITED_$name"
  [ -z "${!inherited:-}" ] || export "$name=${!inherited}"
done
exec "$@"
"""

FAKE_ENV = r"""printf '%s\n' "$@" >>"$STUB_ENV_ARG_LOG"
exec /usr/bin/env "$@"
"""

# What mise's shim does when mise has no gws version for the directory (a relocated HOME or
# XDG_CONFIG_HOME hides mise's global config): exec the first gws on PATH outside its own
# directory, which is this wrapper when it is installed ahead of mise's shims.
HAND_BACK = r"""own="${BASH_SOURCE[0]%/*}"
IFS=':'
for d in $PATH; do
    [[ "$d" -ef "$own" ]] && continue
    [[ -x "$d/gws" ]] && exec "$d/gws" "$@"
done
echo "hand-back: no gws on PATH" >&2
exit 127"""


def secrets_cmd(key: str) -> list[str]:
    return ["fake-secrets", key, "--"]


ACCOUNTS = {
    "personal": {
        "description": "Personal Gmail",
        "tiers": {
            "read": {"command": secrets_cmd("GWS_PERSONAL_READ_OAUTH"), "description": "Drive, Docs, gmail.readonly"},
            "send": {"command": secrets_cmd("GWS_PERSONAL_SEND_OAUTH"), "description": "gmail.send only"},
        },
    },
    "work@example.com": {
        "description": "Work",
        "tiers": {"read": {"command": secrets_cmd("GWS_WORK_EXAMPLE_COM_READ_OAUTH")}},
    },
    "sendonly": {"tiers": {"send": {"command": secrets_cmd("GWS_SENDONLY_SEND_OAUTH")}}},
    "plain@example.com": {"description": "An account gws auth login manages"},
}


def credential(tag: str) -> str:
    return json.dumps(
        {"type": "authorized_user", "id": tag, "client_id": f"{tag}-client-id", "client_secret": f"{tag}-client-secret"}
    )


def write_stub(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def fields(stdout: str) -> dict[str, str]:
    out = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.isupper() and key not in out:
            out[key] = value
    return out


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.root = root
        self.home = root / "home"
        self.gws_root = self.home / ".config" / "gws"
        self.gws_root.mkdir(parents=True)
        self.stub_dir = root / "stubs"
        self.stub_dir.mkdir()
        write_stub(self.stub_dir, "gws", STUB_GWS)
        write_stub(self.stub_dir, "fake-secrets", FAKE_SECRETS)
        # jq joins the stubs from a system location, so PATH stays minimal
        # whether the host keeps jq in /usr/bin (Linux, macOS 15) or Homebrew.
        jq = shutil.which("jq", path="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin")
        self.assertIsNotNone(jq, "the test host needs jq")
        (self.stub_dir / "jq").symlink_to(jq)
        self.write_accounts(ACCOUNTS)
        self.env = {"PATH": f"{self.stub_dir}:/usr/bin:/bin", "HOME": str(self.home)}

    def write_accounts(self, accounts) -> None:
        text = accounts if isinstance(accounts, str) else json.dumps(accounts)
        (self.gws_root / "accounts.json").write_text(text, encoding="utf-8")

    def run_gws(self, env=None, args=None, cwd=None, unset=()) -> subprocess.CompletedProcess:
        # A session of its own, so a wrapper that loops fails the test at the timeout and leaves
        # no process behind, even where the loop runs in a child of the wrapper. The scratch
        # root is the default working directory, so no .env above the checkout reaches a test.
        proc = subprocess.Popen(
            [str(WRAPPER), *(args if args is not None else ["drive", "files", "list"])],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={k: v for k, v in {**self.env, **(env or {})}.items() if k not in unset},
            cwd=cwd or self.root,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    def hand_back_path(self, *after) -> str:
        """PATH with a copy of this wrapper first, then a gws that hands every call back to it."""
        copy_dir = self.stub_dir.parent / "copy"
        copy_dir.mkdir()
        shutil.copy(WRAPPER, copy_dir / "gws")
        self.hand_back_dir = self.stub_dir.parent / "hand-back"
        self.hand_back_dir.mkdir()
        write_stub(self.hand_back_dir, "gws", HAND_BACK)
        return ":".join(map(str, [copy_dir, self.hand_back_dir, *after]))

    def assert_fails(self, result, *needles: str) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout)
        for needle in needles:
            self.assertIn(needle, result.stderr)
        self.assertNotIn("ARG=", result.stdout)

    def assert_rejected_before_gws_or_secrets(self, result, exit_status: int, *needles: str) -> None:
        self.assertEqual(result.returncode, exit_status, result.stderr)
        self.assertIn("gws-accounts:", result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stderr)
        self.assertNotIn("ARG=", result.stdout)
        self.assertNotIn("FETCHED=", result.stderr)

    def store_login(self, account: str, stored: str = "credentials.enc") -> Path:
        store = self.gws_root / account
        store.mkdir(exist_ok=True)
        path = store / stored
        path.write_text("x", encoding="utf-8")
        return path

    def dotenv_tree(self, text: str) -> Path:
        """A project directory holding .env with TEXT, and the subdirectory a call runs from."""
        project = self.root / "project"
        work = project / "sub" / "work"
        work.mkdir(parents=True)
        (project / ".env").write_text(text, encoding="utf-8")
        return work
