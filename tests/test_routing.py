#!/usr/bin/env python3
"""Routing tests for bin/gws: which credential reaches real gws, and every error.

The stubs and the Harness base class are in support.py.
"""

import json
import shutil
import unittest
from pathlib import Path

from support import Harness, FAKE_ENV, STUB_GWS, WRAPPER, credential, fields, write_stub


class Routing(Harness):
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

    def test_missing_perl_is_rejected_before_gws(self):
        # The .env search needs perl's stat, which reads the errno bash's file tests hide.
        for name in ("bash", "head", "tr"):
            target = shutil.which(name, path="/usr/bin:/bin")
            self.assertIsNotNone(target, f"the test host needs {name}")
            (self.stub_dir / name).symlink_to(target)
        result = self.run_gws({"PATH": str(self.stub_dir)}, args=["--version"])
        self.assert_rejected_before_gws_or_secrets(result, 127, "perl is not on PATH")

    def test_a_locale_the_host_lacks_adds_no_perl_warning(self):
        # macOS forwards LC_CTYPE=UTF-8 over ssh by default and most Linux hosts have no
        # such locale, so perl warned four lines per helper run, 31 stderr lines a call.
        # PERL_BADLANG=0 silences that; the helper reads and writes bytes either way.
        work = self.dotenv_tree("APP_ENV=dev\n")
        env = {"GWS_ACCOUNT": "personal", "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": "fixture-bearer-value",
               "LC_CTYPE": "gws-accounts-no-such.locale"}
        result = self.run_gws(env, cwd=work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fields(result.stdout)["TOKEN"], "fixture-bearer-value")
        self.assertNotIn("Setting locale failed", result.stderr)
        self.assertEqual([l for l in result.stderr.splitlines() if l.startswith("perl:")], [])

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
                self.assertEqual(got["TOKEN"], "")
                self.assertIn('"id": "personal-read"', result.stdout)
                self.assertEqual(got["MODE"], "600")
                self.assertEqual(got["DIR"], str(self.gws_root / "personal"))
                self.assertEqual(got["ADC"], got["FILE"])
                self.assertEqual(got["CLIENT_ID"], "personal-read-client-id")
                self.assertEqual(got["CLIENT_SECRET"], "personal-read-client-secret")
                self.assertNotIn("LEAKED=", result.stdout)
                self.assertFalse(Path(got["FILE"]).exists())

    def test_json_credential_clears_an_inherited_access_token(self):
        result = self.run_gws({
            "GWS_ACCOUNT": "personal",
            "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": credential("personal-read"),
            "STUB_INHERITED_GOOGLE_WORKSPACE_CLI_TOKEN": "inherited-bearer-value",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        got = fields(result.stdout)
        self.assertEqual(got["TOKEN"], "")
        # Set empty, not unset, so a .env in gws's working directory cannot supply a token.
        self.assertEqual(got["EMPTY"], "GOOGLE_WORKSPACE_CLI_TOKEN")
        self.assertIn('"id": "personal-read"', result.stdout)

    def test_json_credential_after_leading_whitespace_takes_the_json_route(self):
        # A pretty-printed login a secret runner emits with a leading newline is still JSON:
        # classification goes by the first non-whitespace character.
        result = self.run_gws({
            "GWS_ACCOUNT": "personal",
            "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": "\n  \t" + json.dumps(
                {"type": "authorized_user", "id": "pretty", "client_id": "pretty-client-id",
                 "client_secret": "pretty-client-secret"}, indent=2),
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        got = fields(result.stdout)
        self.assertEqual(got["TOKEN"], "")
        self.assertEqual(got["MODE"], "600")
        self.assertIn('"id": "pretty"', result.stdout)
        self.assertEqual(got["CLIENT_ID"], "pretty-client-id")
        self.assertEqual(got["CLIENT_SECRET"], "pretty-client-secret")
        self.assertEqual(got["DIR"], str(self.gws_root / "personal"))

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

    def test_bare_access_token_reaches_gws_without_credential_variables(self):
        env_log = self.stub_dir / "env-arguments"
        token_scan_dir = self.stub_dir.parent / "token-scan"
        token_scan_dir.mkdir()
        write_stub(self.stub_dir, "env", FAKE_ENV)
        result = self.run_gws({
            "GWS_ACCOUNT": "personal",
            "TMPDIR": str(token_scan_dir),
            "STUB_TOKEN_SCAN_DIR": str(token_scan_dir),
            "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": "fixture-bearer-value",
            "STUB_INHERITED_GOOGLE_APPLICATION_CREDENTIALS": "/inherited/adc.json",
            "STUB_INHERITED_GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": "/inherited/credentials.json",
            "STUB_INHERITED_GOOGLE_WORKSPACE_CLI_CLIENT_ID": "inherited-client-id",
            "STUB_INHERITED_GOOGLE_WORKSPACE_CLI_CLIENT_SECRET": "inherited-client-secret",
            "STUB_INHERITED_GOOGLE_WORKSPACE_CLI_TOKEN": "inherited-bearer-value",
            "STUB_ENV_ARG_LOG": str(env_log),
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        got = fields(result.stdout)
        self.assertEqual(got["TOKEN"], "fixture-bearer-value")
        self.assertEqual(got["FILE"], "")
        self.assertEqual(got["ADC"], "")
        self.assertEqual(got["CLIENT_ID"], "")
        self.assertEqual(got["CLIENT_SECRET"], "")
        # Set empty, not unset, so a .env in gws's working directory cannot supply any of them.
        self.assertEqual(
            got["EMPTY"].split(),
            [
                "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE",
                "GOOGLE_APPLICATION_CREDENTIALS",
                "GOOGLE_WORKSPACE_CLI_CLIENT_ID",
                "GOOGLE_WORKSPACE_CLI_CLIENT_SECRET",
            ],
        )
        store = Path(got["DIR"])
        self.assertFalse(store.is_relative_to(self.gws_root))
        # HOME stays the caller's: gws's npm launcher runs node through mise's shim, which
        # needs the caller's mise config. The empty ADC variable keeps gws off ~/.config/gcloud.
        self.assertEqual(got["HOME"], str(self.home))
        self.assertFalse(store.exists())
        self.assertNotIn("LEAKED=", result.stdout)
        self.assertNotIn(
            "GOOGLE_WORKSPACE_CLI_TOKEN=fixture-bearer-value",
            env_log.read_text(encoding="utf-8"),
        )

    def test_bare_access_token_runs_the_selected_named_tier(self):
        result = self.run_gws({
            "GWS_ACCOUNT": "personal",
            "GWS_TIER": "send",
            "STUB_VALUE_GWS_PERSONAL_SEND_OAUTH": "fixture-bearer-value",
        }, args=["gmail", "+send", "--to", "a@example.com"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FETCHED=GWS_PERSONAL_SEND_OAUTH", result.stderr)
        self.assertNotIn("FETCHED=GWS_PERSONAL_READ_OAUTH", result.stderr)
        got = fields(result.stdout)
        self.assertEqual(got["TOKEN"], "fixture-bearer-value")
        self.assertEqual(got["FILE"], "")
        store = Path(got["DIR"])
        self.assertFalse(store.is_relative_to(self.gws_root))
        self.assertFalse(store.exists())

    def test_bare_access_token_cannot_bypass_tier_selection(self):
        value = "fixture-bearer-value"
        result = self.run_gws({
            "GWS_ACCOUNT": "personal",
            "GWS_PERSONAL_SEND_OAUTH": value,
        })
        self.assert_rejected_before_gws_or_secrets(
            result, 1, "GWS_PERSONAL_SEND_OAUTH", "GWS_PERSONAL_READ_OAUTH"
        )
        self.assertNotIn(value, result.stdout)
        self.assertNotIn(value, result.stderr)

    def test_invalid_tier_values_are_refused_without_echoing_them(self):
        for value in ("fixture bearer value", "fixture\nbearer\nvalue", "{not-json",
                      " fixture-bearer-value", "fixture-bearer-value\n", "  \t ", "\n  {not-json"):
            with self.subTest(value=value):
                result = self.run_gws({
                    "GWS_ACCOUNT": "personal",
                    "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": value,
                })
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("gws-accounts:", result.stderr)
                self.assertNotIn("ARG=", result.stdout)
                self.assertNotIn(value, result.stdout)
                self.assertNotIn(value, result.stderr)

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
