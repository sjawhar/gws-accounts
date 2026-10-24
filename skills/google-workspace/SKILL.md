---
name: google-workspace
description: Use when running gws; reading, searching, sending, or changing Gmail; reading, searching, uploading, downloading, sharing, or organizing Google Drive files; reading or editing Google Docs, Sheets, or Slides; reading Calendar; or calling a Google Workspace API gws lacks, such as the Admin SDK Directory. Covers choosing an account and credential tier (GWS_ACCOUNT, GWS_TIER, ~/.config/gws/accounts.json), gws auth login with no display, and minting a credential for a tier. Triggers on "gws", "gmail", "read Gmail", "search Gmail", "google drive", "gdrive", "google docs", "google sheets", "workspace", drive.google.com or docs.google.com URLs, Drive file IDs, and requests to find, list, export, share, or manage documents, spreadsheets, and slides.
---

# Google Workspace (gws)

`gws` ([googleworkspace/cli](https://github.com/googleworkspace/cli)) calls every Workspace API and prints JSON. This skill runs it through the gws-accounts wrapper, installed ahead of it on PATH: every call names an account, and the account's tier decides which credential the call runs on. Real gws ignores `GWS_ACCOUNT` and `GWS_TIER`, but honors `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` to select its store, so run the check below before any other call. gws has no MCP server mode; the CLI is the only path.

## Choose an account and a tier

`~/.config/gws/accounts.json` lists the accounts. Before the first call, check what calls will run on:

```bash
preflight_failed=0
for v in GWS_TIER GOOGLE_WORKSPACE_CLI_TOKEN GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE GOOGLE_WORKSPACE_CLI_CONFIG_DIR; do
  if [ "$(printenv "$v" | wc -c)" -gt 1 ]; then echo "$v is set to a value"; preflight_failed=1; fi
done
[ "$preflight_failed" -eq 0 ] && env -u GWS_ACCOUNT gws drive about get
```

- `<variable> is set to a value`: an exported `GWS_TIER` changes the tier that a later call omitting it would use; `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` selects gws's store; and `GOOGLE_WORKSPACE_CLI_TOKEN` or `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` makes the wrapper pass the call through. Stop and tell the user which variable is set and how it changes the call before clearing it. The check does not call gws while any of them is set to a value.
- `gws-accounts: no account selected`, followed by every account and tier with its description: the wrapper is in place. Pick an account from those descriptions. Never invent an account, and when the user's words fit more than one, ask.
- Any other `gws-accounts:` error: the wrapper is in place but its setup is not (a missing `accounts.json`, for one). Stop and tell the user what it says.
- Anything else, success included: `gws` on PATH is not the wrapper. A call selected with `GWS_ACCOUNT` would run on real gws's default store, because real gws ignores `GWS_ACCOUNT`; `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` still selects the store it names. Stop, run `type -a gws`, and tell the user the gws-accounts wrapper is not first on PATH.

```bash
GWS_ACCOUNT=personal gws drive files list --params '{"pageSize": 10}'
GWS_ACCOUNT=personal GWS_TIER=send gws gmail +send --to a@example.com --subject Hi --body Hello
```

- `GWS_ACCOUNT=<account>` is shorthand for `GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$HOME/.config/gws/<account>"`. Either works. Write `$HOME`, never a quoted `~`, which does not expand.
- `GWS_TIER` picks the credential. Unset means `read`. The usual tiers are `read` (Drive, Docs, Sheets, Slides, Calendar, and Gmail read-only), `modify` (Gmail labels, archive, drafts, `+reply`, `+forward`) and `send` (`+send` alone; a `gmail.send` credential cannot read mail, so `+reply` fails under it with `insufficient authentication scopes`). The account's descriptions in the error say what each of its tiers grants.
- A tier's description may say that releasing its credential needs a human: a hardware-key touch, a biometric prompt. Tell the human which account and tier you are about to use before the call, run such calls one at a time, and front-load them before any long unattended work.
- An account without tiers runs on the login `gws auth login` stored in its directory. `GWS_TIER` on it is an error. Until that login exists, every call on it except `gws auth` fails with `holds no stored login`; the login is `GWS_ACCOUNT=<account> gws auth login`, which needs the user to consent in a browser (gws auth, below).
- Never set a `GWS_*_OAUTH` variable yourself. The wrapper runs the tier's command, which injects it, and, except for `gws auth`, refuses a call on an account that carries one for a tier or account it did not select.
- A tier command may inject either an `authorized_user` JSON credential or a bare OAuth2 access token. A value whose first non-whitespace character is `{` is JSON; a bare token must be non-empty and have no whitespace anywhere. The wrapper gives gws a JSON credential through a temporary credentials file, or a bare token through `GOOGLE_WORKSPACE_CLI_TOKEN` in an empty private store with no credentials file, Application Default Credentials path, client ID, or client secret. Do not expose either value in a command line, file, or transcript.

`GOOGLE_WORKSPACE_CLI_CONFIG_DIR` pointing anywhere outside `~/.config/gws/<account>` runs gws on that store with no credential injected. That is how minting works (below). `GOOGLE_WORKSPACE_CLI_TOKEN` or `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` set by the caller bypass account selection entirely.

A `403 Caller does not have required permission to use project <project>` means gws sent `<project>` as the call's quota project and the account may not use that project. For an `authorized_user` JSON credential, gws takes the quota project from the first of: `GOOGLE_WORKSPACE_PROJECT_ID`; `installed.project_id` in `client_secret.json` in the store gws runs on (`~/.config/gws/<account>` for the JSON read tier; `gws auth setup` and the console's client download both write it, so an OAuth client shared from someone else's project names their project); the `quota_project_id` of Application Default Credentials. A bare-token tier runs in an empty private store and sets the credential variables empty, including `GOOGLE_APPLICATION_CREDENTIALS`, so only `GOOGLE_WORKSPACE_PROJECT_ID` can supply a quota project, and only from the caller's environment: a `.env` cannot, because the wrapper refuses a call whose `.env` sets a `GOOGLE_WORKSPACE_*` key (below). Clear the source that names `<project>`: unset `GOOGLE_WORKSPACE_PROJECT_ID`, or for the JSON read tier set `installed.project_id` to `""` (keep the key: gws ignores a `client_secret.json` without it, and `gws auth login` then finds no OAuth client). If no later source supplies a quota project, Drive and Gmail calls run without one. If a JSON retry names the Application Default Credentials' project, or that is the source, have the project's owner grant the account `roles/serviceusage.serviceUsageConsumer` on it rather than changing a setting other tools share.

gws loads the nearest `.env` in its working directory or any parent directory, and takes any variable from it that the environment leaves unset. A call fails with `gws-accounts: <file> sets <keys>` when that file would choose something for gws: any `GOOGLE_WORKSPACE_*` setting or `GOOGLE_APPLICATION_CREDENTIALS`; a proxy (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, the lowercase forms, or `REQUEST_METHOD`); `SSL_CERT_FILE` or `SSL_CERT_DIR`; `SSLKEYLOGFILE`; or `HOME`, `XDG_CONFIG_HOME` or `TMPDIR`. A `gws auth` call starts `gcloud` and also refuses what that child reads: `CLOUDSDK_*`, `PYTHON*`, `LD_*`, `DYLD_*`, `OPENSSL_*`, `GOOGLE_CLOUD_*`, `GOOGLE_API_*`, `GCLOUD_*`, `GCE_METADATA_*`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `BASH_ENV`, `SHELLOPTS`, `PS4` and `NO_GCE_CHECK`. Those keys would route, re-trust, log or run code for a call that carries a credential. Tell the user which file and keys the error names; do not edit their `.env`, and do not set the keys yourself to get past it. Running gws from a directory outside that `.env` also works. An ordinary `.env` is not refused, and a tier call still runs under one that names `GOOGLE_APPLICATION_CREDENTIALS`, since the tier launch sets it.

## stdout is pure JSON; never merge stderr into it

Advisories (`Using keyring backend: keyring`, `Tip: ...`) go to stderr. With stderr kept apart, stdout always parses as JSON.

```bash
gws … | jq .                      # correct
gws … 2>/dev/null | jq .          # correct, advisories dropped
gws … >out.json 2>err.txt         # correct, both kept and separate
gws … 2>&1 | jq .                 # WRONG: the advisories break the parse
```

A habitual `2>&1` is the easiest way to break a gws pipeline, and the error looks like a gws bug.

## Discover API methods

```bash
gws schema drive.files.list                      # params and types; needs no account
gws schema docs.documents.batchUpdate --resolve-refs
GWS_ACCOUNT=<account> gws drive --help           # resources and methods
```

Build `--params` and `--json` from `gws schema` output.

## Docs and Sheets formatting

Formatting mistakes in documents people read are recurring and costly. Follow these without exception:

- **Native structure, not lookalike characters.** Make bullet lists through the API (`createParagraphBullets`), never with literal `•` or `◦` in the text.
- **Hyperlinked display text, not bare URLs.** Write "transcript" or "link" with a hyperlink, not the whole Drive URL.
- **Match adjacent content.** Before inserting rows, cells or sections, read the formatting of their neighbors and copy it: text style, hyperlink pattern, column conventions. Table cells use normal text, never a heading style (Heading 2 in a table cell renders huge).
- **Never overwrite concurrent human edits.** People edit shared documents while you work. Re-read the target range right before writing, and merge around their changes.

## gws auth

`gws auth status` reports the selected account's store. `gws auth login` refuses a tiered account, because a login stored in its directory would be used whenever the wrapper is bypassed; mint into a throwaway store instead (below). For an account without tiers, `GWS_ACCOUNT=<account> gws auth login` stores its login.

`gws auth login` starts a localhost callback server and prints the consent URL. Without a display:

- With `forward` on PATH and no display variable set (`DISPLAY`, `WAYLAND_DISPLAY`, `MIR_SOCKET`), the wrapper opens the URL in the laptop's browser through it, and the callback comes back the same way. If forward fails, the wrapper prints forward's error and then `gws-accounts: forward open failed (exit N); the login cannot complete through forward`, and the login waits for a callback that will not come. Stop it, tell the user what forward said, and follow [references/auth-login.md](references/auth-login.md) instead.
- Otherwise, follow [references/auth-login.md](references/auth-login.md): run the login in the background with a time limit, give the user the URL, and poll. A foreground login dies with your command timeout, and the URL then leads nowhere.

To undo a login into the wrong store, delete that store's files. Do not revoke the grant at Google: Google may revoke every token issued under the same grant (RFC 7009 permits it), taking the stored credentials for that client and account with it.

## Mint a JSON credential for a tier

A tier command may instead release a bare, short-lived OAuth2 access token; the wrapper passes it directly to gws and does not need a client ID, client secret, refresh token, or temporary credentials file. The steps below mint an `authorized_user` JSON credential (`client_id`, `client_secret`, `refresh_token`) when the tier command needs a renewable credential. Mint each tier as its own consent, with exactly that tier's scopes, on a Desktop-app OAuth client from a Google Cloud project the account's organization trusts (`gws auth setup` creates one with gcloud):

```bash
store="$(mktemp -d)"
GOOGLE_WORKSPACE_CLI_CLIENT_ID=<client id> GOOGLE_WORKSPACE_CLI_CLIENT_SECRET=<client secret> \
GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$store" \
  gws auth login --scopes 'https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/gmail.readonly'
GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$store" gws auth export --unmasked | jq -c . | <store it where the tier's command reads it>
rm -rf "$store"
```

- Pass the client through the environment. gws also reads a `client_secret.json` in the console's download format, but ignores one missing a required field (`installed.client_id`, `client_secret`, `project_id`, `auth_uri`, or `token_uri`).
- The consent page names the OAuth client's app, not the account. Tell the human which Google account to choose before they open it.
- `gws auth login` always adds `openid` and the `userinfo` scopes. If a consumer needs a credential with exactly one scope, mint it with a tool that requests only the scopes given.
- Pipe the value into the store. Never put it in argv, a file you leave behind, or the transcript.
- A credential that cannot read (`gmail.send` alone) proves its identity by sending one message to the account's own address; find it in Sent with the read tier.

## Call an API gws lacks

gws 0.22.5 has no Admin SDK Directory service (`gws admin:directory_v1 …` fails with `Unknown service 'admin'`), and other APIs may be missing too. Run the tier's command from `accounts.json` around a script that uses a bare access token directly, or trades an `authorized_user` JSON credential's refresh token for one, then calls the REST API. Never print the token or the credential.

```bash
cmd=()
while IFS= read -r arg; do cmd+=("$arg"); done < <(jq -r --arg a <account> --arg t <tier> '.[$a].tiers[$t].command[]' ~/.config/gws/accounts.json)
"${cmd[@]}" python3 - <<'EOF'
import json, os, urllib.parse, urllib.request
value = os.environ["GWS_<ACCOUNT>_<TIER>_OAUTH"]
if value.lstrip().startswith("{"):
    cred = json.loads(value)
    form = {k: cred[k] for k in ("client_id", "client_secret", "refresh_token")} | {"grant_type": "refresh_token"}
    token = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", urllib.parse.urlencode(form).encode()))["access_token"]
else:
    if not value or any(character.isspace() for character in value):
        raise SystemExit("tier command returned an invalid credential")
    token = value
req = urllib.request.Request(
    "https://admin.googleapis.com/admin/directory/v1/users?customer=my_customer&maxResults=10&fields=users(primaryEmail,suspended)",
    headers={"Authorization": f"Bearer {token}"},
)
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
EOF
```

The variable name is `GWS_<ACCOUNT>_<TIER>_OAUTH`, with the account and tier upper-cased and every character outside `A-Z0-9` turned into `_` (`alice@example.com`, `admin` → `GWS_ALICE_EXAMPLE_COM_ADMIN_OAUTH`).

Admin SDK Directory behavior worth knowing:

- `DELETE /groups/<group>/members/<member>` returns 204 with an empty body, and a member list read right after can still show the member for about 20 seconds. The 204 is the truth; re-read after a pause.
- `POST /users/<key>/signOut` (204) ends every session, but neither it nor a password reset revokes an OAuth grant whose scopes include no Gmail scope.
- A user in an OU that enforces 2-Step Verification who has not enrolled cannot sign in at all. Changing who is enforced (group or OU membership) takes minutes to propagate, in both directions: re-read `isEnforcedIn2Sv` after a pause before concluding.
- Admin calls need a credential consented by an administrator. A credential minted by a regular user of the same domain has no admin authority.

## Gotchas

- **`fields` is required** by some methods (`about.get`, `comments.*`). An empty response usually means it is missing; check `gws schema`.
- **A name you guessed that is not there proves nothing.** gws mirrors Google's APIs, so methods and request types carry Google's names. `gws schema <service.resource.method> --resolve-refs` lists every request type and field, and `gws --help` lists the services. Enumerate, then conclude.
- **Google Docs tabs are supported under names you would not guess.** Create one with a `docs.documents.batchUpdate` request keyed `addDocumentTab` (not `createTab`), whose `tabProperties` carry `title`, `iconEmoji` and `parentTabId`; there are also `deleteTab` and `updateDocumentTabProperties`. `docs.documents.get` leaves tab content out unless you pass `includeTabsContent: true`, and requests target a tab through `TabsCriteria` / `tabId`.
- **Sheets ranges contain `!`**, which bash treats as history expansion. Single-quote them: `'Sheet1!A1:C10'`.
- **Unverified OAuth apps are limited to about 25 scopes.** Request the scopes a tier needs, not all of them.
- **Getting a file's content depends on how it was stored.** Native Docs and Sheets are exported: `files.export` with a target MIME type (`application/pdf`, `text/csv`). Office files uploaded to Drive (`.docx`, `.xlsx`; `rtpof=true` in the browser URL) are the reverse: `files.export` returns `403 fileNotExportable`, and `files.get` with `alt: media` returns the bytes.
- **A `404 File not found` for a file you can open in the browser usually means a missing `supportsAllDrives: true`.** Shared-drive files are invisible without it. Pass it on every Drive call: get, list, copy, delete.
- **A file with `canDownload: false` / `canCopy: false` can still be converted.** `files.copy` with `mimeType: application/vnd.google-apps.document` produces a new file you own. Check that the user is entitled to a copy before making one.
- **gws is pre-1.0.** Flags change between releases.

## Full API reference

The upstream skills cover every service:

- [gws-drive](https://github.com/googleworkspace/cli/blob/main/skills/gws-drive/SKILL.md): every Drive resource and method
- [gws-shared](https://github.com/googleworkspace/cli/blob/main/skills/gws-shared/SKILL.md): auth, global flags, security rules
- [Skills index](https://github.com/googleworkspace/cli/blob/main/docs/skills.md): every service, helper and recipe
