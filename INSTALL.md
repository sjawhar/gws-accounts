# Install gws-accounts (instructions for an agent)

You are a coding agent setting up gws-accounts for the person you work for. Run the steps in order. Each step ends with a check; move on only when it passes.

The goal: for every Google account the person names, `GWS_ACCOUNT=<name> gws drive about get --params '{"fields":"user"}'` prints that account's email. The person only answers the questions in step 1, supplies their organization's OAuth client file (without one, runs `gws auth setup` in step 6), and clicks through Google's consent page.

Never print a credential: no client secret, refresh token or access token, and never the contents of `client_secret.json` or `credentials.*`.

The [README](README.md) describes how the wrapper behaves. Once step 3 has cloned the repository, the same files are on disk under `~/.local/share/gws-accounts/`, and later steps refer to them there.

## 1. Ask once

Ask the person these questions in one message, then work without asking again until step 7 needs their browser:

1. Which Google accounts to set up. For each one: a short name, such as `work` or `personal`, which becomes `GWS_ACCOUNT=<name>` and the directory `~/.config/gws/<name>/`, and its email address.
2. Does their organization provide a shared OAuth client file (`client_secret.json`, for a Desktop-app client)? If it does, where do you get it: a path on this machine, or where to download it?
3. What may agents do on these accounts?
   - **Read only** (recommended): read-only Drive, Sheets, Docs, Slides, Calendar, Tasks and Gmail.
   - **Read and write**: Drive, Sheets, Docs, Slides, Calendar and Tasks, plus `gmail.modify`, which can also send mail.

Do not ask anything you can check yourself: the operating system, the shell, installed tools, agent harnesses, or whether there is a display.

## 2. Prerequisites

```bash
bash --version | head -n 1      # 3.2 or later
jq --version
git --version
curl --version | head -n 1
```

If jq is missing, install it with the system package manager (`sudo apt-get install -y jq`, `brew install jq`). If that needs a password, ask the person to run it.

Then look for gws:

```bash
type -a gws && gws --version
```

If gws is missing, or older than 0.22.5, put the release binary in `~/.local/bin`:

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
  base=https://github.com/googleworkspace/cli/releases/latest/download
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

`npm install -g @googleworkspace/cli` also works when npm's global directory is on PATH and writable without sudo.

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

Your own shell started before this change. Run `export PATH="$HOME/.local/share/gws-accounts/bin:$HOME/.local/bin:$PATH"` in it, or begin each later command with it, and confirm `type -a gws` lists the wrapper first. Agent sessions that were already running need a restart to pick up the new PATH and the skill.

## 4. The skill

Install the google-workspace skill into every agent harness on this machine (`command -v claude omp`), and into the one you are running in.

**Claude Code:**

```bash
claude plugin marketplace add sjawhar/gws-accounts
claude plugin install gws-accounts@gws-accounts
claude plugin details gws-accounts@gws-accounts
```

**Check:** the details list `Skills (1)  google-workspace`.

**omp:** install the same tag as the wrapper. omp installs plugins with bun, so bun has to be on PATH.

```bash
omp plugin install "github:sjawhar/gws-accounts#$(git -C ~/.local/share/gws-accounts describe --tags)"
omp skill list | cut -f 1
```

**Check:** `google-workspace` is in the list. Without bun, link the skill into `~/.omp/agent/skills` as below instead.

**Any other harness** that reads skills from a directory of `<name>/SKILL.md` folders: link the clone's skill into that directory, so it follows the clone when the clone is updated. Inside the quotes, write `$HOME`, not `~`, which does not expand there.

```bash
mkdir -p "<skills dir>"
ln -sfn ~/.local/share/gws-accounts/skills/google-workspace "<skills dir>/google-workspace"
```

**Check:** `head -n 3 "<skills dir>/google-workspace/SKILL.md"` shows `name: google-workspace`.

## 5. Accounts

Add each account to `~/.config/gws/accounts.json`, with no `tiers` and a description that says who the account is, and create its directory. Agents choose an account by reading these descriptions. An existing file is merged into, never replaced: an account name it already has keeps its entry. If a name you were going to add is already taken, tell the person.

```bash
mkdir -p -m 700 ~/.config/gws
f=~/.config/gws/accounts.json
[ -e "$f" ] || printf '{}\n' >"$f"
jq -r 'keys[]' "$f"                      # the names already taken
# once per account:
jq --arg name '<name>' --arg desc '<email>, <who it is, e.g. "Example Corp Workspace" or "personal Gmail">' \
  'if has($name) then . else . + {($name): {description: $desc}} end' "$f" >"$f.new" &&
  cat "$f.new" >"$f" && rm "$f.new"
mkdir -p -m 700 ~/.config/gws/<name>
```

`cat "$f.new" >"$f"` writes through a symlinked `accounts.json` instead of replacing the link.

**Check:**

```bash
env -u GWS_ACCOUNT gws drive about get
```

It fails with `gws-accounts: no account selected` and lists every account, each new one marked `no tiers`. Any other `gws-accounts:` error names what is wrong with the file.

## 6. The OAuth client

gws reads the OAuth client from `client_secret.json` in the store it runs on, so each account's directory needs its own copy.

**With the organization's file.** Check that it is a Desktop-app client, without printing it:

```bash
jq -e '.installed | has("client_id") and has("client_secret") and has("project_id") and has("auth_uri") and has("token_uri")' "<path>" >/dev/null && echo "Desktop client"
install -m 600 "<path>" ~/.config/gws/<name>/client_secret.json      # once per account
```

A file without an `installed` object is some other kind of client, which gws cannot use: ask for the Desktop-app client. Use the file as it is. Do not create another client in the organization's project or add a secret to its client: a client holds at most two secrets.

**Without one,** the person creates a client with `gws auth setup`, which needs [gcloud](https://cloud.google.com/sdk/docs/install) and a terminal: it signs in to gcloud, picks or creates a Google Cloud project, enables the Workspace APIs, and then asks for the ID and secret of a Desktop-app client it tells them how to create in the Cloud Console. You cannot run it for them, since gcloud's sign-in and those prompts read from the terminal. Ask them to run this in a new terminal, once per account:

```bash
GWS_ACCOUNT=<name> gws auth setup
```

The wrapper hands `gws auth setup` to gws with the account's directory as its store, so gws writes the client there.

**Check:** `ls -l ~/.config/gws/<name>/client_secret.json` shows the file, with mode `-rw-------`, for every account.

## 7. Log in

Log in each account with the scopes the person chose in step 1:

- **Read only:** `GWS_ACCOUNT=<name> gws auth login --readonly`
- **Read and write:** `GWS_ACCOUNT=<name> gws auth login --scopes 'https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/presentations,https://www.googleapis.com/auth/tasks'`

Always pass one of the two. With neither, gws opens an interactive scope picker whenever it has a terminal, and it has one when the wrapper runs the login through forward. gws adds `openid` and the `userinfo` scopes to either.

Run the login the way `~/.local/share/gws-accounts/skills/google-workspace/references/auth-login.md` describes: in the background with its output in a log, giving the person the URL from the log and telling them which Google account to choose on the consent page, then polling `gws auth status`. After consent, Google sends the browser to `http://localhost:<port>` on the machine running gws:

- **The browser is on this machine:** nothing more is needed.
- **No display, and `forward` is on PATH:** the wrapper opens the URL in the person's browser and carries the redirect back. The README's "Logging in with no display" covers its failure.
- **No display and no forward:** use the `ssh -L` tunnel that `auth-login.md` describes, opened before the person clicks through. If they cannot open a tunnel, the browser ends on a page that fails to load at `http://localhost:<port>/?code=…`. Ask them to copy that whole address from the address bar and send it to you, then fetch it on this machine while the login is still waiting:

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

## 9. Tell the person

Say, in one line, how they use it: agents now reach Google Workspace with `GWS_ACCOUNT=<name> gws …` (for example `GWS_ACCOUNT=<name> gws drive files list --params '{"pageSize": 5}'`), and the google-workspace skill teaches them the rest once their sessions restart. Then add that if they want agents to be unable to send mail, or able to only with their approval, credential tiers are the next step: the README's Tiers section.
