# Install gws-accounts (instructions for an agent)

You are a coding agent setting up gws-accounts for the person you work for. Run the steps in order. Each step ends with a check; move on only when it passes.

The goal: for every Google account the person names, `GWS_ACCOUNT=<name> gws drive about get --params '{"fields":"user"}'` prints that account's email. The person only answers the questions in step 1, supplies their organization's OAuth client file (without one, runs `gws auth setup` in step 6), and clicks through Google's consent page.

Never print a credential: no client secret, refresh token or access token, and never the contents of `client_secret.json` or `credentials.*`.

The [README](README.md) describes how the wrapper behaves. Once step 3 has cloned the repository, the README, skill and reference this guide links to are on disk under `~/.local/share/gws-accounts/`, and later steps refer to them there.

## 1. Ask once

First look at what an earlier gws left, without printing any file's contents:

```bash
ls -la ~/.config/gws 2>/dev/null
jq -r 'keys[]' ~/.config/gws/accounts.json 2>/dev/null
env | grep -oE '^GOOGLE_WORKSPACE_CLI_[A-Z_]+'
```

- Every name in `accounts.json`, and every directory in `~/.config/gws` (gws keeps its own `cache` there), is taken: step 5 leaves those alone.
- `client_secret.json` in `~/.config/gws` is a Desktop client gws already uses: offer it as the answer to question 2.
- `credentials.enc` or `credentials.json` in `~/.config/gws` is an earlier login. The wrapper never uses it, but gws does whenever the wrapper is bypassed, with whatever scopes it was granted: ask question 4.
- `GOOGLE_WORKSPACE_CLI_CLIENT_ID` or `GOOGLE_WORKSPACE_CLI_CLIENT_SECRET` makes gws log every account in on that client instead of the account's own file: unset both in step 7's commands. `GOOGLE_WORKSPACE_CLI_TOKEN`, `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`, or a `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` that is not an account's directory makes the wrapper hand calls straight to gws: tell the person where it is set and ask them to remove it. Leave other `GOOGLE_WORKSPACE_CLI_` variables alone.

Then ask the person these questions in one message, and work without asking again until step 7 needs their browser:

1. Which Google accounts to set up. For each one: a short name that is not taken, such as `work` or `personal`, which becomes `GWS_ACCOUNT=<name>` and the directory `~/.config/gws/<name>/`; its email address; and a few words on what it is (such as "Example Corp Workspace" or "personal Gmail"), which step 5 writes into its description.
2. Does their organization provide a shared OAuth client file (`client_secret.json`, for a Desktop-app client)? If it does, where do you get it: a path on this machine, or where to download it?
3. What may agents do on these accounts?
   - **Read only** (recommended): read-only Drive, Sheets, Docs, Slides, Calendar, Tasks and Gmail.
   - **Read and write**: Drive, Sheets, Docs, Slides, Calendar and Tasks, plus `gmail.modify`, which can also send mail.
4. Only if `~/.config/gws` holds an earlier login: remove it after step 8? Removing it means `GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$HOME/.config/gws" gws auth logout`, which deletes that login and its token cache and nothing else.

Do not ask anything you can check yourself: the operating system, the shell, installed tools, agent harnesses, or whether there is a display.

## 2. Prerequisites

```bash
bash --version | head -n 1      # 3.2 or later
jq --version
git --version
curl --version | head -n 1
```

If jq is missing: on macOS, `brew install jq`; on Linux, ask the person before running `sudo apt-get install -y jq` (or their distribution's equivalent).

Then look for gws:

```bash
type -a gws && gws --version
```

If gws is missing, or older than 0.22.5, put the 0.22.5 release binary in `~/.local/bin`. The wrapper and the skill document 0.22.5, and gws changes its flags between releases.

```bash
(
  set -e
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)  target=x86_64-unknown-linux-musl ;;
    Linux-aarch64) target=aarch64-unknown-linux-musl ;;
    Darwin-arm64)  target=aarch64-apple-darwin ;;
    Darwin-x86_64) target=x86_64-apple-darwin ;;
    *) echo "no gws release for $(uname -sm)"; exit 1 ;;
  esac
  asset="google-workspace-cli-$target.tar.gz"
  base=https://github.com/googleworkspace/cli/releases/download/v0.22.5
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  cd "$tmp"
  curl -fsSLO "$base/$asset"
  curl -fsSLO "$base/$asset.sha256"
  if command -v sha256sum >/dev/null; then sha256sum -c "$asset.sha256"; else shasum -a 256 -c "$asset.sha256"; fi
  mkdir -p "$HOME/.local/bin"
  tar -xzf "$asset" -C "$HOME/.local/bin" ./gws
)
~/.local/bin/gws --version
```

`npm install -g @googleworkspace/cli@0.22.5` also works when npm's global directory is on PATH and writable without sudo.

**Check:** `gws --version` (or `~/.local/bin/gws --version`) prints `gws 0.22.5` or later.

## 3. The wrapper

Clone the latest release tag:

```bash
tag="$(git ls-remote --tags --refs --sort=-v:refname https://github.com/sjawhar/gws-accounts 'v*' | head -n 1 | sed 's|.*refs/tags/||')"
dir="$HOME/.local/share/gws-accounts"
if [ -d "$dir/.git" ]; then
  git -C "$dir" fetch --quiet --tags origin && git -C "$dir" -c advice.detachedHead=false checkout --quiet "$tag"
else
  git -c advice.detachedHead=false clone --quiet --branch "$tag" https://github.com/sjawhar/gws-accounts "$dir"
fi
git -C "$dir" describe --tags
```

Put its `bin/` first on PATH, ahead of the real gws; the same line puts `~/.local/bin`, where step 2 installs gws, on PATH behind it. The line goes at the end of both the login and the interactive startup file, because Ubuntu's `~/.profile` reads `~/.bashrc` and then puts `~/.local/bin` first:

```bash
line='export PATH="$HOME/.local/share/gws-accounts/bin:$HOME/.local/bin:$PATH"  # gws-accounts wrapper first'
add_line() { grep -qF '.local/share/gws-accounts/bin' "$1" 2>/dev/null || printf '\n%s\n' "$line" >>"$1"; }
case "$(basename "$SHELL")" in
  zsh)  add_line "$HOME/.zprofile"; add_line "$HOME/.zshrc" ;;
  bash) add_line "$HOME/.bashrc"
        if [ -f "$HOME/.bash_profile" ]; then add_line "$HOME/.bash_profile"; else add_line "$HOME/.profile"; fi ;;
  *)    echo "add this line to the startup file of $SHELL: $line" ;;
esac
```

Do not install the wrapper as a mise tool: the README's Install section explains why.

**Check**, in fresh shells that inherit nothing from yours:

```bash
for flags in -lc -lic; do
  first="$(env -i HOME="$HOME" USER="$USER" TERM=dumb "$SHELL" "$flags" 'command -v gws' 2>/dev/null | tail -n 1)"
  if head -c 512 "$first" | grep -q gws-accounts-wrapper; then echo "$flags: wrapper first ($first)"; else echo "$flags: NOT the wrapper: $first"; fi
done
```

Both lines must say `wrapper first`. If one does not, run `type -a gws` in that kind of shell: a line in the startup files that runs after this one puts another directory first (a version manager's activation, for one). Move the gws-accounts line below it.

Your own shell started before this change, and harnesses such as Claude Code keep no exported variables between commands. Start every later command that runs gws with `export PATH="$HOME/.local/share/gws-accounts/bin:$HOME/.local/bin:$PATH";`, and confirm `type -a gws` lists the wrapper first. Agent sessions that were already running need a restart to pick up the new PATH and the skill.

## 4. The skill

Install the google-workspace skill into every agent harness on this machine (`command -v claude omp`), and into the one you are running in. Each installs the same tag as the wrapper.

**Claude Code:** adding the marketplace fails while it is already added from another source (another tag, or none), so the first line removes it. Removing it uninstalls the plugin, and the next lines put it back at the tag.

```bash
if claude plugin marketplace list | grep -q 'gws-accounts'; then claude plugin marketplace remove gws-accounts; fi
claude plugin marketplace add "sjawhar/gws-accounts#$(git -C ~/.local/share/gws-accounts describe --tags)"
claude plugin install gws-accounts@gws-accounts
claude plugin details gws-accounts@gws-accounts
```

**Check:** the details list `Skills (1)  google-workspace`, and `claude plugin list` shows gws-accounts at a `Version:` that begins with the output of `git -C ~/.local/share/gws-accounts rev-parse --short=12 HEAD`.

**omp:** omp installs plugins with bun, so bun has to be on PATH.

```bash
omp plugin install "github:sjawhar/gws-accounts#$(git -C ~/.local/share/gws-accounts describe --tags)"
omp skill list | cut -f 1
```

**Check:** `google-workspace` is in the list. Without bun, link the skill into `~/.omp/agent/skills` as below instead.

**Any other harness** that reads skills from a directory of `<name>/SKILL.md` folders: link the clone's skill into that directory, so it follows the clone when the clone is updated. Inside the quotes, write `$HOME`, not `~`, which does not expand there.

```bash
mkdir -p "<skills dir>"
[ -e "<skills dir>/google-workspace" ] || [ -L "<skills dir>/google-workspace" ] ||
  ln -s ~/.local/share/gws-accounts/skills/google-workspace "<skills dir>/google-workspace"
```

**Check:** `readlink "<skills dir>/google-workspace"` prints the clone's skill path. Anything else is another skill with that name: leave it, and tell the person.

## 5. Accounts

Add each account to `~/.config/gws/accounts.json`, with no `tiers` and a description that says who the account is, and create its directory. Agents choose an account by reading these descriptions. An existing file is merged into, never replaced. A name that step 1 found taken, in the file or as a directory, stays as it is: leave that account and its directory alone, skip it in steps 6–8, and ask the person for another name.

```bash
mkdir -p -m 700 ~/.config/gws
f=~/.config/gws/accounts.json
[ -e "$f" ] || printf '{}\n' >"$f"
# once per account:
name='<name>'
if jq -e --arg name "$name" 'has($name)' "$f" >/dev/null || [ -e ~/.config/gws/"$name" ]; then
  echo "$name is taken"
else
  jq --arg name "$name" --arg desc '<email>, <who it is, e.g. "Example Corp Workspace" or "personal Gmail">' \
    '. + {($name): {description: $desc}}' "$f" >"$f.new" && cat "$f.new" >"$f" && rm "$f.new" &&
    mkdir -m 700 ~/.config/gws/"$name"
fi
```

`cat "$f.new" >"$f"` writes through a symlinked `accounts.json` instead of replacing the link. Running the block again for an account it already added prints `taken`; that account is yours, not taken.

**Check:**

```bash
env -u GWS_ACCOUNT gws drive about get
```

It fails with `gws-accounts: no account selected` and lists every account, each new one marked `no tiers`. Any other `gws-accounts:` error names what is wrong with the file.

## 6. The OAuth client

gws reads the OAuth client from `client_secret.json` in the store it runs on, so each account's directory needs its own copy.

**With the organization's file.** If it has to be downloaded, save it straight to `~/.config/gws/<name>/client_secret.json` of one account you added in step 5 that has no `client_secret.json` yet, never an account that was already there, with `umask 077` set for the download, and use that file as `<path>` for the other accounts. Leave no other copy of it, in Downloads or anywhere else. Check that it is a Desktop-app client, without printing it, then copy it to each account that has no client yet:

```bash
jq -e '.installed | has("client_id") and has("client_secret") and has("project_id") and has("auth_uri") and has("token_uri")' "<path>" >/dev/null && echo "Desktop client"
[ -e ~/.config/gws/<name>/client_secret.json ] || install -m 600 "<path>" ~/.config/gws/<name>/client_secret.json      # once per account
```

A file without an `installed` object is some other kind of client, which gws cannot use: delete a copy you downloaded, and ask for the Desktop-app client. Use the file as it is. Do not create another client in the organization's project or add a secret to its client: a client holds at most two secrets.

**Without one,** the person creates a client with `gws auth setup`, which needs [gcloud](https://cloud.google.com/sdk/docs/install) and a terminal: it signs in to gcloud, picks or creates a Google Cloud project, enables the Workspace APIs, and then asks for the ID and secret of a Desktop-app client it tells them how to create in the Cloud Console. You cannot run it for them, since gcloud's sign-in and those prompts read from the terminal. Ask them to run it once, in a new terminal on this machine (over ssh if they sit elsewhere):

```bash
GWS_ACCOUNT=<first name> gws auth setup
```

Tell them, before they start:

- On the consent screen, choose **Internal** when every account belongs to the project's Workspace organization. Otherwise choose External and then publish the app (In production): Google gives an External app left in Testing logins that expire after 7 days.
- When setup asks `Run gws auth login now? [Y/n]`, answer `n`. Step 7 logs in with the scopes chosen in step 1.

The wrapper hands `gws auth setup` to gws with the account's directory as its store, so gws writes the client there. Copy it to each other account the consent screen admits, with the guarded `install` above and `<path>` = `~/.config/gws/<first name>/client_secret.json`.

**Check:** `ls -l ~/.config/gws/<name>/client_secret.json` shows the file, with mode `-rw-------`, for every account.

## 7. Log in

Log in each account with the scopes the person chose in step 1, following `~/.local/share/gws-accounts/skills/google-workspace/references/auth-login.md`: in the background with a time limit and its output in a log, giving the person the URL from the log and telling them which Google account to choose on the consent page, then polling. Its `<scope flags>` are:

- **Read only:** `--readonly`
- **Read and write:** `--scopes 'https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/presentations,https://www.googleapis.com/auth/tasks'`

If step 1 found `GOOGLE_WORKSPACE_CLI_CLIENT_ID` or `GOOGLE_WORKSPACE_CLI_CLIENT_SECRET`, start each login with `unset GOOGLE_WORKSPACE_CLI_CLIENT_ID GOOGLE_WORKSPACE_CLI_CLIENT_SECRET;`.

After consent, Google sends the browser to `http://localhost:<port>` on the machine running gws. The person's browser is on this machine when `SSH_CONNECTION` is unset and either the system is macOS or `DISPLAY` or `WAYLAND_DISPLAY` is set; otherwise it is elsewhere. If the person says otherwise, believe them.

- **The browser is on this machine:** nothing more is needed.
- **The browser is elsewhere, `forward` is on PATH, and no display variable is set (`DISPLAY`, `WAYLAND_DISPLAY`, `MIR_SOCKET`):** the wrapper opens the URL in the person's browser and carries the redirect back. The README's "Logging in with no display" covers its failure. With a display variable set, as under `ssh -X`, the wrapper does not use forward: follow the next bullet.
- **The browser is elsewhere, and the wrapper does not use forward:** use the `ssh -L` tunnel that `auth-login.md` describes, opened before the person clicks through. If they cannot open a tunnel, the browser ends on a page that fails to load at `http://localhost:<port>/?code=…`. Ask them to copy that whole address from the address bar and send it to you, then fetch it on this machine while the login is still waiting:

  ```bash
  curl -sS '<the address they sent>'
  ```

  gws's callback server answers `Success`, exchanges the code, and the login finishes. A code works once, for the login that printed the URL: if that login has exited, start a new one and send the new URL. Do not repeat the address in your replies.

**Check:** `GWS_ACCOUNT=<name> gws auth status | jq .encrypted_credentials_exists` prints `true`, and the background login has exited with no error in its log.

## 8. Verify

```bash
GWS_ACCOUNT=<name> gws drive about get --params '{"fields":"user"}' | jq -r .user.emailAddress
```

**Check:** it prints the email the person gave for that account, for every account. A different address means they chose another Google account on the consent page: run `GWS_ACCOUNT=<name> gws auth logout`, which deletes the login stored in that account's directory and keeps its `client_secret.json`, and repeat step 7. A `403 Caller does not have required permission to use project` is a quota-project problem: follow the paragraph about it in `~/.local/share/gws-accounts/skills/google-workspace/SKILL.md`.

Then confirm that a call naming no account is refused:

```bash
env -u GWS_ACCOUNT gws drive about get
```

**Check:** it fails with `gws-accounts: no account selected` and lists the accounts.

If the person answered yes to question 4, remove the earlier login now:

```bash
GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$HOME/.config/gws" gws auth logout
```

**Check:** `ls ~/.config/gws` shows no `credentials.enc` or `credentials.json`, and every account directory is still there.

## 9. Tell the person

Say, in one line, how they use it: agents now reach Google Workspace with `GWS_ACCOUNT=<name> gws …` (for example `GWS_ACCOUNT=<name> gws drive files list --params '{"pageSize": 5}'`), and the google-workspace skill teaches them the rest once their sessions restart. Then add that if they want agents to be unable to send mail, or able to only with their approval, credential tiers are the next step: the README's Tiers section.
