# gws-accounts

Named accounts and least-privilege credential tiers for [gws](https://github.com/googleworkspace/cli), the Google Workspace CLI, plus a `google-workspace` skill that teaches coding agents (Claude Code, omp and similar) to use them.

```bash
GWS_ACCOUNT=work gws drive files list --params '{"pageSize": 5}'          # runs on work's read credential
GWS_ACCOUNT=work GWS_TIER=send gws gmail +send --to a@example.com …       # runs on work's send credential
```

`bin/gws` is a wrapper that sits ahead of the real `gws` on PATH. Every call names an account. An account can have **tiers**: one credential per scope set (`read`, `send`, …), each released by a command you choose. A call runs on the `read` tier unless it asks for another, so an agent reading Drive never holds a credential that can send mail.

## Requirements

- Linux or macOS, with bash (3.2 or later) and [jq](https://jqlang.org)
- gws itself: `npm install -g @googleworkspace/cli`

## Install

Give this to your coding agent (Claude Code, omp, or another):

```text
Install and set up gws-accounts by following https://raw.githubusercontent.com/sjawhar/gws-accounts/main/INSTALL.md
```

The agent asks which Google accounts to set up, installs gws, the wrapper and the skill, and gives you each account's Google consent page to click through. To install by hand instead:

Put the wrapper **before** the real gws on PATH. It finds the real gws by skipping every copy of itself on PATH, and every gws that hands the call straight back to it, as mise's gws shim does when mise has no gws version for the directory; with nothing left, it exits 127 instead of looping.

```bash
git clone --branch v0.1.6 https://github.com/sjawhar/gws-accounts ~/.local/share/gws-accounts
export PATH="$HOME/.local/share/gws-accounts/bin:$PATH"    # in your shell profile
type -a gws                                                 # the wrapper must be listed first
```

An omp plugin install (below) also holds the wrapper, at `<omp plugins dir>/node_modules/gws-accounts/bin/gws`. Link that onto PATH instead of cloning, and one pin covers both.

Do not install the wrapper as a mise tool next to gws. mise's shims mode puts a single `gws` shim on PATH, which resolves to whichever of the two tools is declared first, so the wrapper could not reach the real gws through it.

The skill installs per agent:

- **Claude Code**: `/plugin marketplace add sjawhar/gws-accounts`, then `/plugin install gws-accounts@gws-accounts`.
- **omp**: `omp plugin install github:sjawhar/gws-accounts#v0.1.6`. This installs the skill (`package.json` declares it under `omp.skills`; omp loads no part of a package that lacks both an `omp` field and the legacy `pi` field) but not the wrapper: omp does not add a plugin's `bin/` to PATH, so link the wrapper as described above.

## Accounts

`~/.config/gws/accounts.json` lists the accounts, keyed by name. Each account's gws store is `~/.config/gws/<name>/`.

```json
{
  "work": {
    "description": "alice@example.com, Example Corp Workspace",
    "tiers": {
      "read": {
        "command": ["secrets", "GWS_WORK_READ_OAUTH", "--"],
        "description": "Drive, Docs, Sheets, Calendar, gmail.readonly"
      },
      "send": {
        "command": ["op", "run", "--env-file", "/Users/alice/.config/gws/work-send.env", "--"],
        "description": "gmail.send only; 1Password approval"
      }
    }
  },
  "alice@gmail.com": {
    "description": "Personal; uses the login gws auth login stored for it"
  }
}
```

- An account's `description`, and each tier's, appear in the wrapper's errors. An agent that calls gws without an account gets the list and picks from it, so say who the account is and what each tier can do.
- An account **without** `tiers` runs on the login `gws auth login` stored in its directory. Until that login exists, the wrapper refuses every call on it except `gws auth` and names the `GWS_ACCOUNT=<name> gws auth login` that creates it, because real gws, finding no login, would fall back to Application Default Credentials and run the call as whatever identity they hold.
- An account **with** `tiers` gets its credentials only from them. The wrapper refuses to run `gws auth login` for it, and refuses every call except `gws auth` while its directory holds a stored login, because real gws would use that login whenever the wrapper is bypassed.

### Selecting an account

A call selects an account the way gws selects a store: `GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$HOME/.config/gws/<name>"`. `GWS_ACCOUNT=<name>` is shorthand for the same thing. With neither, the call fails and lists the accounts; there is no default account.

- `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` pointing at any other directory runs gws on that store with no credential injected, even when `GWS_ACCOUNT` is also set. It and `GWS_ACCOUNT` naming two different accounts is an error.
- `GOOGLE_WORKSPACE_CLI_TOKEN` or `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` set by the caller, `gws schema`, and calls with no subcommand (`gws --help`) run gws directly.

## Tiers

`GWS_TIER=<tier>` selects a tier; unset means `read`. An account with no `read` tier needs `GWS_TIER` on every call except `gws auth`, which takes none.

A tier's `command` is an argv array. The wrapper appends its resolved absolute path and the original arguments and runs it, so `["secrets", "GWS_WORK_READ_OAUTH", "--"]` becomes `secrets GWS_WORK_READ_OAUTH -- /absolute/path/to/gws drive files list`. The command must start that program with `GWS_<ACCOUNT>_<TIER>_OAUTH` set to an `authorized_user` JSON credential: the account and tier names upper-cased, with every character outside `A-Z0-9` turned into `_` (`alice@gmail.com`, `read` → `GWS_ALICE_GMAIL_COM_READ_OAUTH`). The command is not run through a shell, so `~` and `$HOME` do not expand in it.

Any secret runner works: [secretsd](https://github.com/sjawhar/forward)'s `secrets KEY --`, 1Password's `op run --env-file FILE --`, `sops exec-env`, or a script of your own. A plain file, for example:

```json
"command": ["sh", "-c", "export GWS_WORK_READ_OAUTH=\"$(cat \"$HOME/.config/gws-credentials/work-read.json\")\"; exec \"$@\"", "sh"]
```

**A tier protects exactly as much as the command that releases its credential.** A command that needs a human to approve each release (a hardware-key touch, a biometric prompt) keeps an agent from sending mail on its own. A command that reads a file only keeps an agent from doing it by accident.

The wrapper writes the credential to a 0600 file in a private temporary directory, points gws at it, and removes it when gws exits. No `GWS_*_OAUTH` variable reaches gws's environment. The `read` tier runs against the account's directory, so gws keeps its token cache there; every other tier runs against a private store removed with the credential, because two tiers sharing an OAuth client would otherwise share cached tokens. Except for `gws auth`, the wrapper refuses a call on an account when the call already carries a `GWS_*_OAUTH` variable for an account or tier it did not select.

The [skill](skills/google-workspace/SKILL.md) shows how to mint a credential for each tier, and how to call an API gws lacks (the Admin SDK Directory, for one) on a tier's credential.

## Logging in with no display

`gws auth login` prints a consent URL and waits for a redirect to `127.0.0.1`. On a machine with no display, when [forward](https://github.com/sjawhar/forward) is on PATH, the wrapper hands the URL to `forward open`, which opens it in the laptop's browser and carries the redirect back. When `forward open` fails, the wrapper prints forward's error and then `gws-accounts: forward open failed (exit N); the login cannot complete through forward` to stderr, and the login waits for a redirect that will not come: stop it, fix forward, or log in without it. Without forward, run the login in the background and give the user the URL: [the skill's reference](skills/google-workspace/references/auth-login.md) has the steps.

## With gws-multi-account

[indentcorp/gws-multi-account](https://github.com/indentcorp/gws-multi-account) guards agents with harness hooks that block any `gws` call lacking a `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` prefix. gws-accounts uses the same `~/.config/gws/accounts.json` and the same per-account directories, so the two work together: its accounts are accounts without tiers here, and a call written for its hook selects the same account through the wrapper.

## Design notes

- **A wrapper, not a hook.** A hook can only allow or block a command. Tiers need a process around gws that fetches a credential and hands it over as a file, and a wrapper on PATH also covers callers no agent hook sees: scripts, MCP servers, `bash -c`.
- **Not upstream.** gws removed its own multi-account support in v0.7.0 ([googleworkspace/cli#253](https://github.com/googleworkspace/cli/pull/253)).
- **Fail loud.** A missing, ambiguous or mismatched credential is an error, never a fall-back to another account.

## Development

```bash
python3 -m unittest discover -s tests -v
shellcheck -s bash -e SC2016 bin/gws
```

The tests run the wrapper against a stub gws and stub credential commands. CI runs them on Ubuntu and on macOS, where the wrapper runs under the system bash 3.2.

Releases are `v<version>` tags on a commit whose CI passed, with `version` in `package.json` matching.

## License

MIT or Apache-2.0, at your option.
