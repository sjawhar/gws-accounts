#!/usr/bin/env python3
"""The .env rule for bin/gws: what the .env that gws would load may set, on every route.

The stubs and the Harness base class are in support.py.
"""

import os
import subprocess
import unittest

from support import BARE_TOKEN_CALL, Harness, WRAPPER, credential, fields, write_stub


class Dotenv(Harness):
    # A .env that would redirect gws

    def test_a_proxy_dotenv_is_neutralized_on_every_route(self):
        # Emptying a proxy variable turns it off, so the call runs and gws cannot read the
        # .env's value: an ordinary project .env with a proxy setting is not refused.
        proxy = "http://dotenv-proxy.invalid:3128"
        work = self.dotenv_tree(f"APP_ENV=dev\nHTTPS_PROXY={proxy}\nNO_PROXY=localhost\n")
        tier_routes = [
            ("json via tier command",
             {"STUB_VALUE_GWS_PERSONAL_READ_OAUTH": credential("personal-read")}, None),
            ("bare token via tier command",
             {"STUB_VALUE_GWS_PERSONAL_READ_OAUTH": "fixture-bearer-value"}, None),
            ("json already set", {"GWS_PERSONAL_READ_OAUTH": credential("personal-read")}, None),
            ("bare token already set", {"GWS_PERSONAL_READ_OAUTH": "fixture-bearer-value"}, None),
        ]
        tier_routes = [(name, {"GWS_ACCOUNT": "personal", **env}, args) for name, env, args in tier_routes]
        for name, env, args in tier_routes + list(self.non_tier_routes()):
            with self.subTest(name):
                result = self.run_gws(env, args=args, cwd=work)
                self.assertEqual(result.returncode, 0, result.stderr)
                neutral = fields(result.stdout)["NEUTRAL"].split()
                self.assertIn("HTTPS_PROXY", neutral)
                self.assertIn("NO_PROXY", neutral)
                self.assertNotIn(proxy, result.stdout)

    def test_a_proxy_the_caller_set_survives_in_either_case(self):
        # hyper-util reads the first name of a pair that is set, even to empty, so an empty
        # twin would hide the caller's value. The launch copies it into the twin instead.
        work = self.dotenv_tree("HTTPS_PROXY=http://dotenv-proxy.invalid:3128\nhttp_proxy=http://x.invalid\n")
        for upper, lower in (("HTTP_PROXY", "http_proxy"), ("HTTPS_PROXY", "https_proxy"),
                             ("ALL_PROXY", "all_proxy"), ("NO_PROXY", "no_proxy")):
            for caller_name in (upper, lower):
                with self.subTest(caller=caller_name):
                    value = "http://corp-proxy.example:3128"
                    result = self.run_gws({**BARE_TOKEN_CALL, caller_name: value}, cwd=work)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    got = fields(result.stdout)["SET"].split()
                    self.assertIn(f"{upper}={value}", got)
                    self.assertIn(f"{lower}={value}", got)
                    self.assertNotIn("invalid", result.stdout)

    def test_a_proxy_pair_the_caller_set_both_names_of_is_left_alone(self):
        work = self.dotenv_tree("APP_ENV=dev\n")
        result = self.run_gws({**BARE_TOKEN_CALL, "HTTPS_PROXY": "http://upper.example:3128",
                               "https_proxy": "http://lower.example:3128"}, cwd=work)
        self.assertEqual(result.returncode, 0, result.stderr)
        got = fields(result.stdout)["SET"].split()
        self.assertIn("HTTPS_PROXY=http://upper.example:3128", got)
        self.assertIn("https_proxy=http://lower.example:3128", got)

    def test_dotenv_forms_dotenvy_loads_are_refused(self):
        cases = {
            "export prefix": ("export SSL_CERT_FILE=/srv/evil/ca.pem\n", "SSL_CERT_FILE"),
            "spaces around the key": ("  SSL_CERT_DIR  =  /srv/evil/certs\n", "SSL_CERT_DIR"),
            "tab after export": ("export\tREQUEST_METHOD=GET\n", "REQUEST_METHOD"),
            "byte order mark": ("\ufeffGOOGLE_WORKSPACE_CLI_LOG=gws=trace\n", "GOOGLE_WORKSPACE_CLI_LOG"),
            "unicode whitespace": ("\u00a0SSL_CERT_FILE\u00a0=/srv/evil/ca.pem\n", "SSL_CERT_FILE"),
            "empty value": ("SSL_CERT_DIR=\n", "SSL_CERT_DIR"),
            "log file": ("GOOGLE_WORKSPACE_CLI_LOG_FILE=/srv/evil/logs\n", "GOOGLE_WORKSPACE_CLI_LOG_FILE"),
            "no final newline": ("APP=1\nSSL_CERT_FILE=/srv/evil/ca.pem", "SSL_CERT_FILE"),
        }
        for name, (text, key) in cases.items():
            with self.subTest(name):
                work = self.dotenv_dir(name.replace(" ", "-"), text)
                result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
                self.assert_rejected_before_gws_or_secrets(result, 1, os.path.realpath(work / ".env"), key)
                for value in ("/srv/evil/ca.pem", "x.invalid", "trace", "/srv/evil/logs"):
                    self.assertNotIn(value, result.stderr)

    def test_dotenv_key_already_in_the_environment_runs(self):
        # dotenvy sets a key only when std::env::var fails for it (iter.rs:34), so a caller
        # value, empty or not, keeps the file from supplying a refused key. Without one, the
        # same file is refused.
        work = self.dotenv_tree("SSL_CERT_FILE=/srv/evil/ca.pem\n")
        for value in ("/caller/ca.pem", ""):
            with self.subTest(value=value):
                result = self.run_gws({**BARE_TOKEN_CALL, "SSL_CERT_FILE": value}, cwd=work)
                self.assert_ran_on_the_bare_token(result)
        with self.subTest(value="absent"):
            result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
            self.assert_rejected_before_gws_or_secrets(result, 1, self.tree_dotenv(), "SSL_CERT_FILE")
            self.assertNotIn("/srv/evil", result.stderr)

    def test_ordinary_or_commented_dotenv_runs(self):
        cases = {
            "commented lines": "# HTTPS_PROXY=http://x.invalid\n   # export SSL_CERT_FILE=/srv/evil/ca.pem\n",
            "other keys only": "APP_ENV=dev\nMY_HTTPS_PROXY=http://x.invalid\nHTTPS_PROXY_URL=http://x.invalid\n"
                               "RUST_LOG=trace\n",
            "dotted key": "HTTPS_PROXY.backup=http://x.invalid\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                work = self.dotenv_dir(name.replace(" ", "-"), text)
                result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
                self.assert_ran_on_the_bare_token(result)

    def test_only_the_nearest_dotenv_counts(self):
        # dotenvy loads the first .env it finds walking up (find.rs:33-57), so a nearer
        # ordinary file shadows a farther one. The farther one sets a refused key, so this
        # fails if the search ever reaches it.
        work = self.dotenv_tree("SSL_CERT_FILE=/srv/evil/ca.pem\n")
        (work / ".env").write_text("APP_ENV=dev\n", encoding="utf-8")
        result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
        self.assert_ran_on_the_bare_token(result)

    def test_dotenv_setting_any_gws_setting_is_refused(self):
        # A project .env never supplies a gws setting: the wrapper chooses gws's credentials and
        # configuration. GOOGLE_WORKSPACE_CLI_SANITIZE_TEMPLATE alone makes gws 0.22.5 POST each
        # response body, with the bearer token, to a host the value picks.
        for key in ("GOOGLE_WORKSPACE_CLI_SANITIZE_TEMPLATE", "GOOGLE_WORKSPACE_CLI_SANITIZE_MODE",
                    "GOOGLE_WORKSPACE_PROJECT_ID", "GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND",
                    "GOOGLE_WORKSPACE_CLI_SOMETHING_NEW"):
            with self.subTest(key=key):
                work = self.dotenv_dir(key.lower(), f"{key}=projects/evil/locations/x/templates/y\n")
                result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
                self.assert_rejected_before_gws_or_secrets(result, 1, os.path.realpath(work / ".env"), key)
                self.assertNotIn("projects/evil", result.stderr)

    def test_a_deleted_working_directory_runs_instead_of_hanging(self):
        # A failed pwd must end the .env search: gws cannot load a .env from a directory that
        # no longer exists. run_gws's 30-second timeout fails the test if the wrapper spins.
        gone = self.root / "gone"
        gone.mkdir()
        script = f'cd "{gone}" && rmdir "{gone}" && exec "{WRAPPER}" drive files list'
        proc = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, timeout=30,
                              env={**self.env, **BARE_TOKEN_CALL})
        self.assert_ran_on_the_bare_token(proc)

    def test_a_working_directory_whose_name_ends_in_a_newline_is_not_a_way_past(self):
        # Command substitution strips trailing newlines, so a wrapper that read the working
        # directory that way would search the parent instead and miss this .env.
        work = self.dotenv_dir("newline-project\n", "SSL_CERT_FILE=/srv/evil/ca.pem\n")
        result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
        self.assert_rejected_before_gws_or_secrets(result, 1, "SSL_CERT_FILE")
        self.assertIn(os.path.realpath(work / ".env"), result.stderr)

    def test_a_tier_command_cannot_reach_a_refused_dotenv_by_changing_directory(self):
        # The pre-command check saw the caller's directory; this one is the check inside the
        # launch, which sees the directory the tier command moved to.
        hostile = self.dotenv_dir("hostile", "SSL_CERT_FILE=/srv/evil/ca.pem\n")
        self.write_accounts({"personal": {"tiers": {"read": {
            "command": ["sh", "-c", f'cd "{hostile}" && export GWS_PERSONAL_READ_OAUTH=fixture-bearer-value && exec "$@"', "sh"]}}}})
        result = self.run_gws({"GWS_ACCOUNT": "personal"})
        self.assert_rejected_before_gws_or_secrets(result, 1, os.path.realpath(hostile / ".env"), "SSL_CERT_FILE")

    def test_tier_launch_variables_in_dotenv_do_not_block_a_tier_call(self):
        # The tier launch sets these six for gws itself, so gws never reads them from .env.
        work = self.dotenv_tree("GOOGLE_APPLICATION_CREDENTIALS=/srv/project/sa.json\n"
                                "GOOGLE_WORKSPACE_CLI_CONFIG_DIR=/srv/project/gws\n")
        for value in ("fixture-bearer-value", credential("personal-read")):
            with self.subTest(json=value.startswith("{")):
                result = self.run_gws({"GWS_ACCOUNT": "personal", "STUB_VALUE_GWS_PERSONAL_READ_OAUTH": value},
                                      cwd=work)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotEqual(fields(result.stdout)["ADC"], "/srv/project/sa.json")

    # The same .env rule on every route to real gws

    def non_tier_routes(self):
        """Each call that reaches real gws without a tier command, as (name, env, args)."""
        self.store_login("plain@example.com")
        return (
            ("stored login", {"GWS_ACCOUNT": "plain@example.com"}, ["drive", "files", "list"]),
            ("auth status on a tiered account", {"GWS_ACCOUNT": "personal"}, ["auth", "status"]),
            ("auth login on an account without tiers", {"GWS_ACCOUNT": "plain@example.com", "DISPLAY": ":0"},
             ["auth", "login"]),
            ("pass-through store", {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/tmp/mint-store"}, ["drive", "files", "list"]),
            ("pass-through auth login", {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/tmp/mint-store", "DISPLAY": ":0"},
             ["auth", "login"]),
            ("caller token", {"GOOGLE_WORKSPACE_CLI_TOKEN": "caller-bearer-value"}, ["drive", "files", "list"]),
            ("caller credentials file", {"GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": "/tmp/caller.json"},
             ["drive", "files", "list"]),
            ("schema", {}, ["schema", "drive.files.list"]),
            ("no subcommand", {}, ["--version"]),
        )

    def test_every_route_to_gws_is_refused_under_a_cert_dotenv(self):
        work = self.dotenv_tree("APP_ENV=dev\nSSL_CERT_FILE=/srv/evil/ca.pem\n")
        dotenv = self.tree_dotenv()
        for name, env, args in self.non_tier_routes():
            with self.subTest(name):
                result = self.run_gws(env, args=args, cwd=work)
                self.assert_rejected_before_gws_or_secrets(result, 1, dotenv, "SSL_CERT_FILE")
                self.assertNotIn("/srv/evil", result.stderr)
                self.assertNotIn("caller-bearer-value", result.stderr)

    def test_every_route_to_gws_runs_under_an_ordinary_dotenv(self):
        # No PYTHONPATH here: an auth call refuses it, which its own test covers.
        work = self.dotenv_tree("APP_ENV=dev\nDATABASE_URL=postgres://localhost/app\n")
        for name, env, args in self.non_tier_routes():
            with self.subTest(name):
                result = self.run_gws(env, args=args, cwd=work)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"ARG={args[0]}", result.stdout)

    def test_stored_login_is_refused_under_a_dotenv_naming_application_credentials(self):
        # Off the tier route nothing assigns GOOGLE_APPLICATION_CREDENTIALS, and an empty value
        # breaks gws's own ADC fallback, so a .env naming it is refused here.
        self.store_login("plain@example.com")
        work = self.dotenv_tree("GOOGLE_APPLICATION_CREDENTIALS=/srv/project/sa.json\n")
        result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"}, cwd=work)
        self.assert_rejected_before_gws_or_secrets(result, 1, self.tree_dotenv(), "GOOGLE_APPLICATION_CREDENTIALS")
        self.assertNotIn("/srv/project", result.stderr)

    def test_pass_through_leaves_application_credentials_unset(self):
        # gws reads an empty value as an explicit path and skips the well-known ADC file, so the
        # launch must not set it when the caller did not.
        result = self.run_gws({"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": "/tmp/mint-store"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", fields(result.stdout)["EMPTY"].split())
        self.assertEqual(fields(result.stdout)["ADC"], "")

    def test_auth_call_is_refused_under_a_dotenv_gcloud_would_read(self):
        # gws auth starts gcloud, which inherits what gws loaded from .env.
        for text, key in (("CLOUDSDK_PYTHON=/srv/evil/python\n", "CLOUDSDK_PYTHON"),
                          ("CLOUDSDK_CORE_LOG_HTTP=true\n", "CLOUDSDK_CORE_LOG_HTTP"),
                          ("PYTHONSTARTUP=/srv/evil/x.py\n", "PYTHONSTARTUP"),
                          ("LD_PRELOAD=/srv/evil/x.so\n", "LD_PRELOAD"),
                          ("BASH_ENV=/srv/evil/x.sh\n", "BASH_ENV"),
                          ("REQUESTS_CA_BUNDLE=/srv/evil/ca.pem\n", "REQUESTS_CA_BUNDLE"),
                          ("GCE_METADATA_HOST=evil.invalid\n", "GCE_METADATA_HOST"),
                          ("TMPDIR=/srv/evil/tmp\n", "TMPDIR")):
            for account in ("personal", "plain@example.com"):
                with self.subTest(key=key, account=account):
                    work = self.dotenv_dir(f"{key.lower()}-{account}", text)
                    result = self.run_gws({"GWS_ACCOUNT": account}, args=["auth", "status"], cwd=work)
                    self.assert_rejected_before_gws_or_secrets(result, 1, os.path.realpath(work / ".env"), key)
                    self.assertNotIn("/srv/evil", result.stderr)

    def test_auth_call_is_refused_under_a_dotenv_naming_a_browser(self):
        # gcloud's login flow hands BROWSER to Python's webbrowser, which runs a value
        # holding %s as a command line, so a project .env could choose the program. Named
        # here rather than left to the list-driven test, which cannot see a missing key.
        work = self.root / "browser"
        work.mkdir()
        payload = work / "browser-payload"
        (work / ".env").write_text(f'BROWSER="{payload} %s"\nDISPLAY=:99\n', encoding="utf-8")
        for args in (["auth", "status"], ["auth", "login"]):
            with self.subTest(args[1]):
                result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"}, args=args, cwd=work)
                self.assert_rejected_before_gws_or_secrets(
                    result, 1, os.path.realpath(work / ".env"), "BROWSER")
                self.assertNotIn(str(payload), result.stderr)
        with self.subTest("the caller's own browser still runs the call"):
            result = self.run_gws({"GWS_ACCOUNT": "plain@example.com", "BROWSER": "/usr/bin/true",
                                   "DISPLAY": ":0"}, args=["auth", "status"], cwd=work)
            self.assertEqual(result.returncode, 0, result.stderr)

    # One test per browser input, each naming its key here, so dropping any one of them from
    # bin/gws fails its own test; the list-driven test below cannot see a key that is gone.

    def assert_only_an_auth_call_refuses(self, key, value):
        work = self.dotenv_dir(f"only-{key.lower()}", f"{key}={value}\n")
        self.store_login("plain@example.com")
        result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"}, args=["auth", "status"], cwd=work)
        self.assert_rejected_before_gws_or_secrets(result, 1, os.path.realpath(work / ".env"), f"sets {key}.")
        self.assertNotIn(value, result.stderr)
        with self.subTest("a call that starts no gcloud runs"):
            result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"}, args=["drive", "files", "list"], cwd=work)
            self.assertEqual(result.returncode, 0, result.stderr)
        with self.subTest("the caller's own value runs the call"):
            result = self.run_gws({"GWS_ACCOUNT": "plain@example.com", key: "caller-value"},
                                  args=["auth", "status"], cwd=work)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_auth_call_is_refused_under_a_dotenv_setting_display(self):
        self.assert_only_an_auth_call_refuses("DISPLAY", ":99")

    def test_auth_call_is_refused_under_a_dotenv_setting_wayland_display(self):
        self.assert_only_an_auth_call_refuses("WAYLAND_DISPLAY", "wayland-99")

    def test_auth_call_is_refused_under_a_dotenv_setting_mir_socket(self):
        self.assert_only_an_auth_call_refuses("MIR_SOCKET", "/srv/evil/mir")

    def test_auth_call_is_refused_under_a_dotenv_setting_gnome_desktop_session_id(self):
        self.assert_only_an_auth_call_refuses("GNOME_DESKTOP_SESSION_ID", "this-is-deprecated")

    def test_auth_call_is_refused_under_a_dotenv_setting_kde_full_session(self):
        self.assert_only_an_auth_call_refuses("KDE_FULL_SESSION", "true")

    def test_auth_call_is_refused_under_a_dotenv_setting_term(self):
        self.assert_only_an_auth_call_refuses("TERM", "xterm-evil")

    def test_an_invalid_utf8_caller_value_does_not_exempt_a_key(self):
        # dotenvy keeps a caller value only when std::env::var reads it, and that fails on
        # invalid UTF-8 (iter.rs:34), so the file would supply the key after all. The byte
        # goes through the environment mapping, never through a command line.
        work = self.dotenv_tree("SSL_CERT_FILE=/srv/evil/ca.pem\n")
        dotenv = self.tree_dotenv()
        with self.subTest("invalid UTF-8"):
            # "\udcff" is how os.fsencode writes the single byte 0xff.
            result = self.run_gws({**BARE_TOKEN_CALL, "SSL_CERT_FILE": "\udcff"}, cwd=work)
            self.assert_rejected_before_gws_or_secrets(result, 1, dotenv, "SSL_CERT_FILE")
        with self.subTest("valid UTF-8 control"):
            result = self.run_gws({**BARE_TOKEN_CALL, "SSL_CERT_FILE": "\u00ff"}, cwd=work)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_invalid_utf8_proxy_is_not_treated_as_the_callers(self):
        # Same rule for the proxy pairs: a value dotenvy would replace is not the caller's,
        # so the launch empties the pair instead of copying it into the twin.
        work = self.dotenv_tree("HTTPS_PROXY=http://dotenv-proxy.invalid:3128\n")
        with self.subTest("invalid UTF-8"):
            result = self.run_gws({**BARE_TOKEN_CALL, "HTTPS_PROXY": "\udcff"}, cwd=work)
            self.assertEqual(result.returncode, 0, result.stderr)
            neutral = fields(result.stdout)["NEUTRAL"].split()
            self.assertIn("HTTPS_PROXY", neutral)
            self.assertIn("https_proxy", neutral)
        with self.subTest("valid UTF-8 control"):
            result = self.run_gws({**BARE_TOKEN_CALL, "HTTPS_PROXY": "http://caller.example:3128"}, cwd=work)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("https_proxy=http://caller.example:3128", fields(result.stdout)["SET"])

    def wrapper_launching_through(self, shell):
        """A copy of bin/gws whose launch runs through SHELL instead of /bin/sh."""
        copy = self.root / f"launch-through-{os.path.basename(shell)}" / "gws"
        copy.parent.mkdir()
        copy.write_text(WRAPPER.read_text(encoding="utf-8").replace("/bin/sh", shell), encoding="utf-8")
        copy.chmod(0o755)
        return copy

    def test_an_inherited_dotted_name_exempts_its_key_only_when_gws_receives_it(self):
        # dotenvy accepts a dot in a key and keeps an inherited value for it, but gws receives
        # what the launch's /bin/sh passes on: bash keeps a name that is not a shell
        # identifier, dash drops it. bash's own compgen -e lists such a name on 3.2 and not on
        # 5.2, so it cannot say which. Each case runs a copy of the wrapper whose launch shell
        # is the one named, so both outcomes are tested on every host. The stub reports
        # from the raw environment whether the caller's value arrived.
        key = "GOOGLE_WORKSPACE_.PROBE"
        write_stub(self.stub_dir, "gws",
                   f'echo "SEEN=$(/usr/bin/env | grep -cxF "{key}=caller-value" || true)"')
        work = self.dotenv_tree(f"{key}=project-value\n")
        env = {**BARE_TOKEN_CALL, key: "caller-value"}
        for shell, passes_the_name in (("/bin/bash", True), ("/bin/dash", False)):
            with self.subTest(shell=shell):
                if not os.access(shell, os.X_OK):
                    self.skipTest(f"{shell} is not on this host")
                copy = self.wrapper_launching_through(shell)
                result = self.run_gws(env, cwd=work, wrapper=copy)
                if passes_the_name:
                    # gws receives the caller's value, so dotenvy leaves it and the file sets nothing.
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("SEEN=1", result.stdout)
                else:
                    # gws never receives it, so dotenvy sets the key from the file: refused.
                    self.assert_rejected_before_gws_or_secrets(result, 1, self.tree_dotenv(), key)

    def test_a_dotted_name_does_not_exempt_a_key_on_the_login_route_through_script(self):
        # util-linux script hands the command to the caller's $SHELL, which may drop a name
        # that is not a shell identifier, and the wrapper cannot test which. So on that route
        # a dotted name never counts as reaching gws; an ordinary name still does. The copy
        # launches through bash, which keeps the name, so only that rule can refuse it. macOS's
        # script runs the command itself.
        if os.uname().sysname == "Darwin":
            self.skipTest("macOS's script does not go through the caller's shell")
        write_stub(self.stub_dir, "forward", ":")
        copy = self.wrapper_launching_through("/bin/bash")
        for key, expect in (("GOOGLE_WORKSPACE_.PROBE", "refused"), ("GOOGLE_WORKSPACE_PROBE", "runs")):
            with self.subTest(key=key):
                work = self.dotenv_dir(f"script-{key.lower()}", f"{key}=project-value\n")
                result = self.run_gws({"GWS_ACCOUNT": "plain@example.com", key: "caller-value"},
                                      args=["auth", "login"], cwd=work, wrapper=copy)
                if expect == "refused":
                    self.assert_rejected_before_gws_or_secrets(result, 1, os.path.realpath(work / ".env"), key)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_what_dotenvy_would_set_is_refused(self):
        # Each outcome below is what the compiled dotenvy 0.15.7 oracle did with that file,
        # in a scratch build from the crate source. dotenvy reads logical lines, not physical
        # ones, and stops at the first line, or the first value, it cannot parse, and at the
        # first line that is not valid UTF-8. Fixtures are bytes, so a byte UTF-8 forbids can
        # be one.
        cert = b"SSL_CERT_FILE=/srv/evil/ca.pem\n"
        cases = {
            # oracle: result=ok, SSL_CERT_FILE=unset, APP_CONFIG=set
            "a quoted value that spans lines":
                (b'APP_CONFIG="ordinary\n' + cert + b'"\n', "runs"),
            # oracle: result=ok, SSL_CERT_FILE=unset, APP_CONFIG=set
            "a single-quoted value that spans lines":
                (b"APP_CONFIG='ordinary\n" + cert + b"'\n", "runs"),
            # oracle: result=error at 'this is not an assignment', SSL_CERT_FILE=unset
            "a key after a line dotenvy cannot parse":
                (b"this is not an assignment\n" + cert, "runs"),
            # oracle: result=error, SSL_CERT_FILE=unset
            "an unclosed quote":
                (b'SSL_CERT_FILE="/srv/evil/ca.pem\n', "runs"),
            # oracle: result=error LineParse("hello world", 6), SSL_CERT_FILE=unset
            "a key after a value dotenvy cannot parse":
                (b"APP_CONFIG=hello world\n" + cert, "runs"),
            # oracle: result=error LineParse at the \q, SSL_CERT_FILE=unset
            "a key after an escape dotenvy rejects":
                (b'APP_CONFIG="a\\qb"\n' + cert, "runs"),
            # oracle: result=error LineParse("${HOME", 5), SSL_CERT_FILE=unset
            "a key after a substitution left open":
                (b"APP_CONFIG=${HOME\n" + cert, "runs"),
            # oracle: result=error LineParse at the closing quote, SSL_CERT_FILE=unset
            "a key after a quote a substitution swallowed":
                (b'APP_CONFIG="$HOME"\n' + cert, "runs"),
            # oracle: result=error LineParse, SSL_CERT_FILE=unset: the key's own value fails
            "a key whose own value dotenvy cannot parse":
                (b"SSL_CERT_FILE=/srv/evil/ca.pem extra\n", "runs"),
            # oracle: result=error InvalidData "stream did not contain valid UTF-8",
            # SSL_CERT_FILE=unset
            "a key after a byte that is not UTF-8":
                (b"APP_CONFIG=\xff\n" + cert, "runs"),
            # oracle: result=error InvalidData, SSL_CERT_FILE=unset: a UTF-16 surrogate
            "a key after a surrogate encoded as UTF-8":
                (b"APP_CONFIG=\xed\xa0\x80\n" + cert, "runs"),
            # oracle: result=ok, SSL_CERT_FILE=set, APP_CONFIG=set, APP_PATH=set
            "a key after values that parse":
                (b'APP_CONFIG="${HOME}" #note\nAPP_PATH=a\\ b\n' + cert, "refused"),
            # oracle: result=ok, SSL_CERT_FILE=set, APP_CONFIG=set
            "a key after a quoted value that spans lines":
                (b'APP_CONFIG="ordinary\nvalue\n"\n' + cert, "refused"),
            # oracle: result=error, SSL_CERT_FILE=set: dotenvy sets it before it stops
            "a key before a line dotenvy cannot parse":
                (cert + b"this is not an assignment\n", "refused"),
            # oracle: result=error InvalidData, SSL_CERT_FILE=set: dotenvy sets it before it stops
            "a key before a byte that is not UTF-8":
                (cert + b"APP_CONFIG=\xff\n", "refused"),
        }
        for name, (text, expect) in cases.items():
            with self.subTest(name):
                work = self.dotenv_dir(name.replace(" ", "-"), text)
                result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
                if expect == "refused":
                    self.assert_rejected_before_gws_or_secrets(
                        result, 1, os.path.realpath(work / ".env"), "SSL_CERT_FILE")
                else:
                    self.assert_ran_on_the_bare_token(result)
                self.assertNotIn("/srv/evil", result.stdout + result.stderr)

    def test_a_character_split_across_a_jq_read_block_is_read_whole(self):
        # jq before 1.8.2, which Ubuntu 24.04 and macOS 15 ship, reads each line of raw input
        # (-R) in blocks of at most 4095 bytes, and turns a character whose bytes fall in two
        # blocks into U+FFFD (jqlang/jq#3421). Here that character is U+3000, whitespace to
        # dotenvy, so dotenvy ends the first line at the # after it and sets the next two
        # keys; read as U+FFFD, the " after the # would open a quote that swallows them.
        # Oracle (compiled dotenvy 0.15.7), both files: SET APP,
        # GOOGLE_WORKSPACE_CLI_SANITIZE_TEMPLATE, SSL_CERT_FILE, then LineParse at the last ".
        rest = ('#"\nSSL_CERT_FILE=/srv/evil/ca.pem\n'
                'GOOGLE_WORKSPACE_CLI_SANITIZE_TEMPLATE=evil.invalid\n"\n')
        for split, pad in (("two bytes, then one", 4089), ("one byte, then two", 4090)):
            with self.subTest(split):
                text = ("APP=" + "a" * pad + "\u3000" + rest).encode()
                self.assertEqual(text.index("\u3000".encode()), 4 + pad)
                work = self.dotenv_dir(f"pad-{pad}", text)
                result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
                self.assert_rejected_before_gws_or_secrets(
                    result, 1, os.path.realpath(work / ".env"),
                    "SSL_CERT_FILE", "GOOGLE_WORKSPACE_CLI_SANITIZE_TEMPLATE")
                self.assertNotIn("/srv/evil", result.stdout + result.stderr)

    def test_a_metadata_error_ends_the_search_where_dotenvy_ends_it(self):
        # dotenvy's finder stops on any metadata error but NotFound (find.rs:36-46), wherever
        # in the candidate's path the error arises. Each outcome is the compiled dotenvy 0.15.7
        # oracle's, run from the child directory under a parent .env that sets SSL_CERT_FILE.

        def locked(candidate):
            (candidate.parent / "locked").mkdir(mode=0o000)
            self.addCleanup((candidate.parent / "locked").chmod, 0o700)
            candidate.symlink_to("locked/.env")

        cases = (
            # oracle: result=error FilesystemLoop (errno 40), SSL_CERT_FILE=unset
            ("symlink loop", lambda p: p.symlink_to(".env"), "runs"),
            # oracle: result=error FilesystemLoop (errno 40), SSL_CERT_FILE=unset
            ("loop in a path component",
             lambda p: ((p.parent / "loop").symlink_to("loop"), p.symlink_to("loop/env")), "runs"),
            # oracle: result=error NotADirectory (errno 20), SSL_CERT_FILE=unset
            ("component that is not a directory",
             lambda p: ((p.parent / "ordinary-file").write_text("", encoding="utf-8"),
                        p.symlink_to("ordinary-file/env")), "runs"),
            # oracle: result=error PermissionDenied (errno 13), SSL_CERT_FILE=unset
            ("permission denied", locked, "runs"),
            # oracle: result=ok, SSL_CERT_FILE=set
            ("dangling symlink", lambda p: p.symlink_to("missing-target"), "refused"),
            # oracle: result=ok, SSL_CERT_FILE=set
            ("directory", lambda p: p.mkdir(), "refused"),
        )
        for name, make, expect in cases:
            with self.subTest(name):
                if name == "permission denied" and os.geteuid() == 0:
                    self.skipTest("root searches a mode-000 directory, so nothing is denied")
                parent = self.root / name.replace(" ", "-")
                work = parent / "child"
                work.mkdir(parents=True)
                (parent / ".env").write_text("SSL_CERT_FILE=/srv/evil/ca.pem\n", encoding="utf-8")
                make(work / ".env")
                result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
                if expect == "runs":
                    # The nearest candidate errors, so dotenvy never reaches the parent file.
                    self.assert_ran_on_the_bare_token(result)
                else:
                    self.assert_rejected_before_gws_or_secrets(
                        result, 1, os.path.realpath(parent / ".env"), "SSL_CERT_FILE")

    def test_every_listed_key_is_handled_as_its_list_says(self):
        # Read the lists from the wrapper itself, so a key added there is tested here without a
        # second copy: refused keys and prefixes stop the call, neutral keys arrive empty, and
        # PATH, which is always set, leaves the call running.
        wrapper_lines = WRAPPER.read_text(encoding="utf-8").splitlines()

        def wrapper_list(name):
            for line in wrapper_lines:
                if line.startswith(f'readonly {name}="'):
                    return line.split('"')[1].split()
            self.fail(f"bin/gws defines no {name}")
        refused = wrapper_list("DOTENV_REFUSE_KEYS") + [p + "PROBE" for p in wrapper_list("DOTENV_REFUSE_PREFIXES")]
        auth_refused = wrapper_list("DOTENV_AUTH_KEYS") + [p + "PROBE" for p in wrapper_list("DOTENV_AUTH_PREFIXES")]
        neutral = wrapper_list("DOTENV_NEUTRAL_KEYS") + [
            name for pair in wrapper_list("DOTENV_PROXY_PAIRS") for name in pair.split(":")]
        # The stored-login route assigns nothing itself, so no key is exempt there.
        self.store_login("plain@example.com")
        cases = ([(key, ["drive", "files", "list"], "refused") for key in refused]
                 + [(key, ["auth", "status"], "refused") for key in auth_refused]
                 + [(key, ["drive", "files", "list"], "neutral") for key in neutral]
                 # PATH is always set, so gws never takes it from .env and the call runs.
                 + [("PATH", ["drive", "files", "list"], "runs")])
        for index, (key, args, expect) in enumerate(cases):
            with self.subTest(key=key, args=args[0]):
                # Numbered, not named by key: macOS's filesystem folds HTTP_PROXY and http_proxy.
                work = self.dotenv_dir(f"list-{index}", f"{key}=/srv/evil\n")
                result = self.run_gws({"GWS_ACCOUNT": "plain@example.com"}, args=args, cwd=work)
                if expect == "refused":
                    self.assert_rejected_before_gws_or_secrets(result, 1, os.path.realpath(work / ".env"), key)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if expect == "neutral":
                        self.assertIn(key, fields(result.stdout)["NEUTRAL"].split())
                self.assertNotIn("/srv/evil", result.stdout + result.stderr)

    def test_gcloud_keys_do_not_block_a_call_that_starts_no_gcloud(self):
        work = self.dotenv_tree("CLOUDSDK_PYTHON=/usr/bin/python3\nPYTHONPATH=src\nLD_LIBRARY_PATH=/opt/lib\n")
        result = self.run_gws(BARE_TOKEN_CALL, cwd=work)
        self.assert_ran_on_the_bare_token(result)


if __name__ == "__main__":
    unittest.main()
