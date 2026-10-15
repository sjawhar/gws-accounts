# gws auth login without a display or forward

`gws auth login` prints a consent URL, starts a callback server on `127.0.0.1`, and waits for the browser to redirect back to it. An agent's command timeout (often about 60 seconds) kills a foreground login together with its callback server, and the URL the user already has then leads nowhere. Run the login in the background instead.

1. Start it, with its output in a log:

   ```bash
   log="$(mktemp)"
   GWS_ACCOUNT=<account> nohup gws auth login --scopes '<scopes>' >"$log" 2>&1 &
   ```

   For a throwaway store (minting a tier's credential), set `GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$store"` and the client variables instead of `GWS_ACCOUNT`, as the skill's minting steps show.

2. Read the URL from the log once it appears:

   ```bash
   sleep 2; grep -oE 'https://accounts\.google\.com/o/oauth2/[^[:space:]]+' "$log" | head -n 1
   ```

3. Give the user that URL exactly as printed, and say which Google account to choose: the consent page names the OAuth client's app, not the account.

4. The redirect goes to `127.0.0.1` on the machine running gws. When the browser runs on another machine, that port has to reach it (an `ssh -L` tunnel to the port in the URL's `redirect_uri`); `forward` does this automatically. Without it, the browser shows a connection error after consent, and the login does not complete.

5. Poll until the login finishes, then check it:

   ```bash
   GWS_ACCOUNT=<account> gws auth status    # or GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$store" gws auth status
   tail -n 5 "$log"
   ```

   `gws auth status` shows a stored credential once the callback has arrived. If the background process has exited without one, read the log, fix the cause, and start again from step 1 with a fresh URL.
