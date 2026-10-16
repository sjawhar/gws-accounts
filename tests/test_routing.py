#!/usr/bin/env python3
"""Routing tests for bin/gws: which credential reaches real gws, and every error.

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
echo "CLIENT_ID=${GOOGLE_WORKSPACE_CLI_CLIENT_ID:-}"
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
exec "$@"
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


class Routing(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
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

    def run_gws(self, env=None, args=None) -> subprocess.CompletedProcess:
        # A session of its own, so a wrapper that loops fails the test at the timeout and leaves
        # no process behind, even where the loop runs in a child of the wrapper.
        proc = subprocess.Popen(
            [str(WRAPPER), *(args if args is not None else ["drive", "files", "list"])],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**self.env, **(env or {})},
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

    # Selecting an account

    def test_no_account_lists_every_account_and_tier(self):
        result = self.run_gws()
        self.assert_fails(result, "no account selected", "personal: Personal Gmail", "GWS_TIER=send: gmail.send only",
                          "work@example.com", "sendonly", "plain@example.com")
        self.assertNotIn("FETCHED=", result.stderr)

    def test_invalid_gws_account_name_is_rejected_before_gws_or_secrets(self):
        for account in ("a/b", ".", ".."):
            with self.subTest(account=account):
                result = self.run_gws({"GWS_ACCOUNT": account})
                self.assert_rejected_before_gws_or_secrets(
                    result, 1, "GWS_ACCOUNT must be an account name", f"got '{account}'"
                )

    def test_shorthand_and_config_dir_naming_different_accounts_is_loud(self):
        result = self.run_gws({"GWS_ACCOUNT": "personal", "GOOGLE_WORKSPACE_CLI_CONFIG_DIR": str(self.gws_root / "sendonly")})
        self.assert_fails(result, "name different accounts")

    def test_config_dir_outside_the_accounts_wins_over_shorthand(self):
        # An exported GWS_ACCOUNT default must not block minting into a throwaway store.
        result = self.run_gws({"GWS_ACCOUNT": "personal", "GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/tmp/mint-store"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fields(result.stdout)["DIR"], "/tmp/mint-store")
        self.assertEqual(fields(result.stdout)["FILE"], "")
        self.assertNotIn("FETCHED=", result.stderr)

    def test_missing_accounts_file_is_loud(self):
        (self.gws_root / "accounts.json").unlink()
        for env in ({}, {"GWS_ACCOUNT": "personal"}):
            with self.subTest(env=env):
                self.assert_fails(self.run_gws(env), "accounts.json not found")

    def test_unknown_account_lists_accounts(self):
        self.assert_fails(self.run_gws({"GWS_ACCOUNT": "work"}), "account 'work' is not in", "personal")

    def test_unlisted_gws_account_directory_is_rejected_before_gws_or_secrets(self):
        (self.gws_root / "unlisted").mkdir()
        result = self.run_gws({"GWS_ACCOUNT": "unlisted"})
        self.assert_rejected_before_gws_or_secrets(result, 1, "account 'unlisted' is not in")

    def test_shorthand_and_config_dir_select_the_same_account(self):
        selectors = (
            {"GWS_ACCOUNT": "personal"},
            {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": str(self.gws_root / "personal")},
            {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": f"{self.gws_root / 'personal'}/"},
            {"GWS_ACCOUNT": "personal", "GOOGLE_WORKSPACE_CLI_CONFIG_DIR": str(self.gws_root / "personal")},
        )
        for selector in selectors:
            with self.subTest(selector=selector):
                result = self.run_gws({**selector, "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": credential("personal")})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("FETCHED=GWS_PERSONAL_READ_OAUTH", result.stderr)
                self.assertEqual(fields(result.stdout)["DIR"], str(self.gws_root / "personal"))

    def test_config_dir_outside_the_accounts_passes_through(self):
        for directory in ("/tmp/some-store", str(self.gws_root / "unlisted")):
            with self.subTest(directory=directory):
                result = self.run_gws({"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": directory})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fields(result.stdout)["DIR"], directory)
                self.assertEqual(fields(result.stdout)["FILE"], "")
                self.assertNotIn("FETCHED=", result.stderr)

    def test_tier_with_a_pass_through_dir_is_loud(self):
        result = self.run_gws({"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/tmp/some-store", "GWS_TIER": "send"})
        self.assert_fails(result, "GWS_TIER=send needs an account")

    def test_literal_tilde_is_loud(self):
        self.assert_fails(self.run_gws({"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "~/.config/gws/personal"}), "literal ~")

    def test_email_account_maps_to_its_credential_variable(self):
        result = self.run_gws({"GWS_ACCOUNT": "work@example.com", "STUB_VALUE_GWS_WORK_EXAMPLE_COM_READ_OAUTH": credential("work")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FETCHED=GWS_WORK_EXAMPLE_COM_READ_OAUTH", result.stderr)

    def test_invalid_accounts_file_is_rejected_before_gws_or_secrets(self):
        cases = {
            "not json": ("{", "not valid JSON"),
            "top level array": ([], "must be a JSON object keyed by account name"),
            "command is a string": ({"a": {"tiers": {"read": {"command": "secrets X --"}}}}, "command must be a non-empty array"),
            "unknown tier field": ({"a": {"tiers": {"read": {"command": ["x"], "scopes": []}}}}, 'unknown field "scopes"'),
            "empty tiers": ({"a": {"tiers": {}}}, "tiers must be a non-empty object"),
            "slash in name": ({"a/b": {}}, "is not a directory name"),
            "non-string account description": ({"a": {"description": ["not", "a", "string"]}}, "description must be a string"),
            "invalid tier name": ({"a": {"tiers": {"read!": {"command": ["x"]}}}}, 'tier name "read!" must match'),
            "same account token": ({"a.b": {"tiers": {"read": {"command": ["x"]}}}, "a_b": {"tiers": {"send": {"command": ["x"]}}}},
                                   'accounts "a.b" and "a_b" both map to A_B'),
            "same credential variable": ({"a_b": {"tiers": {"c": {"command": ["x"]}}}, "a": {"tiers": {"b_c": {"command": ["x"]}}}},
                                         "both use the credential variable GWS_A_B_C_OAUTH"),
        }
        for name, (accounts, needle) in cases.items():
            with self.subTest(name):
                self.write_accounts(accounts)
                self.assert_rejected_before_gws_or_secrets(self.run_gws({"GWS_ACCOUNT": "a"}), 1, needle)

    def test_missing_jq_is_rejected_before_gws_or_secrets(self):
        (self.stub_dir / "jq").unlink()
        for name in ("bash", "head", "tr"):
            target = shutil.which(name, path="/usr/bin:/bin")
            self.assertIsNotNone(target, f"the test host needs {name}")
            (self.stub_dir / name).symlink_to(target)
        result = self.run_gws({"PATH": str(self.stub_dir), "GWS_ACCOUNT": "personal"})
        self.assert_rejected_before_gws_or_secrets(result, 127, "jq is not on PATH")

    # Tiers

    def test_read_tier_runs_by_default_on_the_account_store(self):
        for exit_code in (0, 42):
            with self.subTest(exit_code=exit_code):
                result = self.run_gws({
                    "GWS_ACCOUNT": "personal",
                    "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": credential("personal-read"),
                    "STUB_EXIT": str(exit_code),
                })
                self.assertEqual(result.returncode, exit_code, result.stderr)
                got = fields(result.stdout)
                self.assertIn('"id": "personal-read"', result.stdout)
                self.assertEqual(got["MODE"], "600")
                self.assertEqual(got["DIR"], str(self.gws_root / "personal"))
                self.assertEqual(got["ADC"], got["FILE"])
                self.assertEqual(got["CLIENT_ID"], "personal-read-client-id")
                self.assertNotIn("LEAKED=", result.stdout)
                self.assertFalse(Path(got["FILE"]).exists())

    def test_named_tier_runs_its_command_on_a_private_store(self):
        for exit_code in (0, 42):
            with self.subTest(exit_code=exit_code):
                result = self.run_gws({
                    "GWS_ACCOUNT": "personal",
                    "GWS_TIER": "send",
                    "STUB_VALUE_GWS_PERSONAL_SEND_OAUTH": credential("personal-send"),
                    "STUB_EXIT": str(exit_code),
                }, args=["gmail", "+send", "--to", "a@example.com"])
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertIn("FETCHED=GWS_PERSONAL_SEND_OAUTH", result.stderr)
                self.assertNotIn("FETCHED=GWS_PERSONAL_READ_OAUTH", result.stderr)
                got = fields(result.stdout)
                store = Path(got["DIR"])
                self.assertFalse(store.is_relative_to(self.gws_root))
                self.assertFalse(store.exists())
                self.assertFalse(Path(got["FILE"]).exists())
                self.assertNotIn("LEAKED=", result.stdout)

    def test_unknown_tier_lists_the_account_tiers(self):
        result = self.run_gws({"GWS_ACCOUNT": "personal", "GWS_TIER": "admin"})
        self.assert_fails(result, "no tier 'admin'", "GWS_TIER=read", "GWS_TIER=send")
        self.assertNotIn("FETCHED=", result.stderr)

    def test_account_without_a_read_tier_needs_a_tier(self):
        self.assert_fails(self.run_gws({"GWS_ACCOUNT": "sendonly"}), "no read tier", "GWS_TIER=send")
        result = self.run_gws({"GWS_ACCOUNT": "sendonly", "GWS_TIER": "send",
                               "STUB_VALUE_GWS_SENDONLY_SEND_OAUTH": credential("sendonly")})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_account_without_tiers_runs_on_its_own_store(self):
        for stored in ("credentials.enc", "credentials.json"):
            with self.subTest(stored=stored):
                path = self.store_login("plain@example.com", stored)
                result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"})
                path.unlink()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fields(result.stdout)["DIR"], str(self.gws_root / "plain@example.com"))
                self.assertEqual(fields(result.stdout)["FILE"], "")
                self.assertNotIn("FETCHED=", result.stderr)

    def test_account_without_tiers_and_no_stored_login_is_loud(self):
        # Real gws falls back to Application Default Credentials on a store
        # with no login, which would run the call as another identity.
        adc = self.stub_dir / "adc.json"
        adc.write_text(credential("adc"), encoding="utf-8")
        store = self.gws_root / "plain@example.com"
        for name, prepare in (("no store", lambda: None), ("empty store", lambda: store.mkdir(exist_ok=True))):
            with self.subTest(name):
                prepare()
                result = self.run_gws({"GWS_ACCOUNT": "plain@example.com", "GOOGLE_APPLICATION_CREDENTIALS": str(adc)})
                self.assert_fails(result, str(store), "no stored login", "GWS_ACCOUNT=plain@example.com gws auth login")

    def test_tier_on_an_account_without_tiers_is_loud(self):
        self.assert_fails(self.run_gws({"GWS_ACCOUNT": "plain@example.com", "GWS_TIER": "read"}), "has no tiers")

    def test_credential_on_an_account_without_tiers_is_loud(self):
        for key in ("GWS_PERSONAL_SEND_OAUTH", "GWS_PLAIN_EXAMPLE_COM_READ_OAUTH"):
            with self.subTest(key=key):
                result = self.run_gws({"GWS_ACCOUNT": "plain@example.com", key: credential("other")})
                self.assert_fails(result, "gws-accounts: ", key, "has no tiers", str(self.gws_root / "plain@example.com"))

    def test_stored_login_in_a_tiered_account_is_loud(self):
        for stored in ("credentials.enc", "credentials.json"):
            with self.subTest(stored=stored):
                path = self.store_login("personal", stored)
                result = self.run_gws({"GWS_ACCOUNT": "personal", "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": credential("p")})
                path.unlink()
                # A deleted credential cannot be restored, so the fix offered first keeps it.
                self.assert_fails(result, stored, f"mv '{path}' '{path}.pre-gws-accounts'")
                self.assertNotIn("FETCHED=", result.stderr)

    def test_credential_for_an_unselected_tier_or_account_is_loud(self):
        for key in ("GWS_PERSONAL_SEND_OAUTH", "GWS_SENDONLY_SEND_OAUTH", "GWS_OTHER_ADMIN_OAUTH"):
            with self.subTest(key=key):
                result = self.run_gws({"GWS_ACCOUNT": "personal", key: credential("other")})
                self.assert_fails(result, key, "GWS_PERSONAL_READ_OAUTH")
                self.assertNotIn("FETCHED=", result.stderr)

    def test_selected_credential_already_set_is_used_without_the_command(self):
        result = self.run_gws({"GWS_ACCOUNT": "personal", "GWS_TIER": "send",
                               "GWS_PERSONAL_SEND_OAUTH": credential("caller-injected")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"id": "caller-injected"', result.stdout)
        self.assertNotIn("FETCHED=", result.stderr)

    def test_tier_command_that_never_runs_gws_is_loud(self):
        self.write_accounts({"a": {"tiers": {"read": {"command": ["true"]}}}})
        self.assert_fails(self.run_gws({"GWS_ACCOUNT": "a"}), "exited 0 without running gws")

    def test_tier_command_that_does_not_set_the_credential_is_loud(self):
        self.write_accounts({"a": {"tiers": {"read": {"command": ["env"]}}}})
        self.assert_fails(self.run_gws({"GWS_ACCOUNT": "a"}), "ran gws without setting GWS_A_READ_OAUTH")

    def test_failing_tier_command_keeps_its_exit_status(self):
        self.write_accounts({"a": {"tiers": {"read": {"command": ["sh", "-c", "exit 7"]}}}})
        result = self.run_gws({"GWS_ACCOUNT": "a"})
        self.assertEqual(result.returncode, 7)
        self.assertIn("failed (exit 7) before running gws", result.stderr)

    def test_credential_without_client_id_is_loud(self):
        bad = json.dumps({"type": "authorized_user", "client_secret": "s", "refresh_token": "r"})
        self.assert_fails(self.run_gws({"GWS_ACCOUNT": "personal", "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": bad}), "client_id")

    def test_credential_without_client_secret_is_rejected_before_gws_or_secrets(self):
        bad = json.dumps({"type": "authorized_user", "client_id": "id", "refresh_token": "r"})
        result = self.run_gws({"GWS_ACCOUNT": "personal", "GWS_PERSONAL_READ_OAUTH": bad})
        self.assert_rejected_before_gws_or_secrets(result, 1, "GWS_PERSONAL_READ_OAUTH is missing a non-empty client_secret")

    def test_argument_with_spaces_survives_the_tier_command(self):
        result = self.run_gws(
            {"GWS_ACCOUNT": "personal", "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": credential("p")},
            args=["drive", "files", "list", "--params", '{"q": "name contains hello there"}'],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ARG={"q": "name contains hello there"}\n', result.stdout)
        self.assertEqual(result.stdout.count("ARG="), 5)

    # Calls that need no account

    def test_schema_and_bare_flags_need_no_account(self):
        for args in (["schema", "drive.files.list"], ["--help"], []):
            with self.subTest(args=args):
                result = self.run_gws(args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fields(result.stdout)["FILE"], "")

    def test_caller_credentials_pass_through_scrubbed(self):
        creds = self.stub_dir / "caller.json"
        creds.write_text(credential("caller"), encoding="utf-8")
        for env, field, value in (
            ({"GOOGLE_WORKSPACE_CLI_TOKEN": "preset-token"}, "TOKEN", "preset-token"),
            ({"GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(creds)}, "FILE", str(creds)),
        ):
            with self.subTest(field=field):
                result = self.run_gws({**env, "GWS_PERSONAL_READ_OAUTH": credential("x")})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fields(result.stdout)[field], value)
                self.assertNotIn("LEAKED=", result.stdout)

    def test_every_copy_of_the_wrapper_on_path_is_skipped(self):
        copy_dir = self.stub_dir.parent / "copy"
        copy_dir.mkdir()
        shutil.copy(WRAPPER, copy_dir / "gws")
        env = {"PATH": f"{copy_dir}:{self.stub_dir}:/usr/bin:/bin", "GOOGLE_WORKSPACE_CLI_TOKEN": "t"}
        result = self.run_gws(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fields(result.stdout)["TOKEN"], "t")

    def test_missing_real_gws_is_loud(self):
        (self.stub_dir / "gws").unlink()
        result = self.run_gws({"GOOGLE_WORKSPACE_CLI_TOKEN": "t"})
        self.assertEqual(result.returncode, 127)
        self.assertIn("real gws is not on PATH", result.stderr)

    def test_a_gws_that_hands_the_call_back_is_skipped_for_the_gws_past_it(self):
        path = self.hand_back_path(self.stub_dir, "/usr/bin", "/bin")
        result = self.run_gws({"PATH": path, "GOOGLE_WORKSPACE_CLI_TOKEN": "t"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fields(result.stdout)["TOKEN"], "t")

    def test_a_tier_call_through_a_gws_that_hands_it_back_reaches_the_gws_past_it(self):
        # A tier call runs real gws in a child of the wrapper, not in the wrapper's own pid.
        path = self.hand_back_path(self.stub_dir, "/usr/bin", "/bin")
        result = self.run_gws({"PATH": path, "GWS_ACCOUNT": "personal",
                               "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": credential("personal-read")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"id": "personal-read"', result.stdout)
        self.assertNotIn("LEAKED=", result.stdout)

    def test_when_every_gws_hands_the_call_back_the_wrapper_fails_instead_of_looping(self):
        path = self.hand_back_path("/usr/bin", "/bin")
        result = self.run_gws({"PATH": path, "GOOGLE_WORKSPACE_CLI_TOKEN": "t"})
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn(f"{self.hand_back_dir / 'gws'} handed the call back", result.stderr)

    # gws auth

    def test_auth_status_uses_the_account_store_without_a_credential(self):
        for account in ("personal", "plain@example.com"):
            with self.subTest(account=account):
                result = self.run_gws({"GWS_ACCOUNT": account}, args=["auth", "status"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fields(result.stdout)["DIR"], str(self.gws_root / account))
                self.assertEqual(fields(result.stdout)["FILE"], "")
                self.assertNotIn("FETCHED=", result.stderr)

    def test_auth_login_on_a_tiered_account_is_loud(self):
        self.assert_fails(self.run_gws({"GWS_ACCOUNT": "personal"}, args=["auth", "login"]), "throwaway store")

    def test_auth_with_a_tier_is_loud(self):
        self.assert_fails(self.run_gws({"GWS_ACCOUNT": "personal", "GWS_TIER": "send"}, args=["auth", "status"]),
                          "GWS_TIER does not apply")

    # Headless gws auth login through forward

    def test_headless_login_hands_the_oauth_url_to_forward(self):
        url = "https://accounts.google.com/o/oauth2/auth?x=1&redirect_uri=http://localhost:4321"
        selectors = ({"GWS_ACCOUNT": "plain@example.com"}, {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/tmp/mint-store"})
        for selector in selectors:
            for exit_code in (0, 3):
                with self.subTest(selector=selector, exit_code=exit_code):
                    received = self.stub_dir / f"forward-{len(selector)}-{exit_code}"
                    received.unlink(missing_ok=True)
                    write_stub(self.stub_dir, "forward", f'printf %s "$2" > "{received}"')
                    # Like gws auth login waiting for its callback, the stub
                    # keeps running until forward has the URL, or 5 seconds.
                    write_stub(self.stub_dir, "gws", f'''echo "Open this URL: {url}"
for ((i = 0; i < 50; i++)); do [ -e "{received}" ] && break; sleep 0.1; done
[ -e "{received}" ] && echo "FORWARDED=while-gws-ran" || echo "FORWARDED=not-while-gws-ran"
exit {exit_code}''')
                    result = self.run_gws(selector, args=["auth", "login"])
                    self.assertEqual(result.returncode, exit_code, result.stderr)
                    self.assertIn("FORWARDED=while-gws-ran", result.stdout)
                    self.assertEqual(received.read_text(), url)

    def test_headless_login_reports_a_failed_forward(self):
        url = "https://accounts.google.com/o/oauth2/auth?x=1&redirect_uri=http://localhost:4321"
        # forward prints the URL it could not deliver to stdout, and why to stderr.
        write_stub(self.stub_dir, "forward", 'echo "$2"; echo "forward: the opener channel is down" >&2; exit 5')
        write_stub(self.stub_dir, "gws", f'echo "Open this URL: {url}"')
        result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"}, args=["auth", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gws-accounts: forward open failed (exit 5); the login cannot complete through forward\n", result.stderr)
        self.assertIn("forward: the opener channel is down", result.stderr)
        self.assertEqual(result.stdout.count(url), 1, result.stdout)

    def test_login_with_a_display_or_without_forward_runs_plain(self):
        url = "https://accounts.google.com/o/oauth2/auth?x=1&redirect_uri=http://localhost:4321"
        ran = self.stub_dir / "forward-ran"
        write_stub(self.stub_dir, "forward", f': > "{ran}"')
        write_stub(self.stub_dir, "gws", f'echo "Open this URL: {url}"\n{STUB_GWS}')
        # Control: these stubs reach forward when nothing stops them.
        result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"}, args=["auth", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(ran.exists(), "the stub forward never ran without a display")
        ran.unlink()
        for env, args in (
            ({"DISPLAY": ":0"}, ["auth", "login"]),
            ({"WAYLAND_DISPLAY": "wayland-0"}, ["auth", "login"]),
            ({"MIR_SOCKET": "/run/mir_socket"}, ["auth", "login"]),
            ({}, ["auth", "status"]),
        ):
            with self.subTest(env=env, args=args):
                result = self.run_gws({"GWS_ACCOUNT": "plain@example.com", **env}, args=args)
                forwarded = ran.exists()
                ran.unlink(missing_ok=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"ARG={args[1]}", result.stdout)
                self.assertFalse(forwarded, f"forward ran with {env or 'no display'} for {args}")
        (self.stub_dir / "forward").unlink()
        result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"}, args=["auth", "login"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ARG=login", result.stdout)


if __name__ == "__main__":
    unittest.main()
