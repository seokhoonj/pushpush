---
name: send
description: Send a result, alert, or file from the conversation to Telegram, Slack, or Discord. Holds no logic of its own -- it calls the pushpush package's send() -- and always shows the destination (route), content, and attachment for approval before sending. Trigger phrases: send to telegram, send to slack, send to discord, push this, send a notification, 텔레그램으로 보내줘, 슬랙으로 보내, 디스코드로 보내, 알림 보내줘, 푸시 보내줘.
---

# pushpush send -- send a conversation result to a messenger

Sending a message is an **irreversible external action**. A wrong message cannot be
recalled, and a file sent to the wrong channel stays there. So half of this skill is
not the send itself -- it is the confirmation before it.

## What this skill does not do

It holds no logic. Framing the HTTP request, capability checks (file support, size,
markup), and route resolution are **all the pushpush package's job.** Do not
reimplement them here or copy a provider's limits into this file -- a rule that lives
in two places will drift. This skill only assembles one `send(...)` call and runs it.

## Confirm the package is ready

This skill calls the **`pushpush` command** the package installs. Once per session,
check it is there:

```sh
pushpush --version
```

If a version prints, you are ready -- every example below uses this command. If
`command not found` appears, do not invent a path; tell the user how to install it:

```sh
pip install pushpush        # once it is on PyPI
pipx install pushpush       # for a global command, kept isolated
```

If the user installed it into a specific virtual environment, confirm they run it with
that environment active.

## Procedure

### 1. Settle what to send, and where

If anything is missing, **ask -- do not guess.**

- `to` -- which route (omit for `default_route`)
- `text` -- the message body
- `media` -- path to a file to send (optional)
- `caption` -- a line shown with the media (only when `media` is given)
- `markup` -- `"plain"` (default), `"markdown"`, or `"html"`. html is Telegram only.
- `silent` -- deliver without a notification sound

If you do not know which routes exist, read them first:

```sh
pushpush routes
```

### 2. Get approval before sending -- never skip this

Lay the route out so the user can see which service it goes to and where:

```
Please confirm what to send:

  route   alerts (telegram, chat 123456789)
  body    chip supply crash -- take a look
  file    chart.png (240 KB)
  markup  plain

Send it?
```

If there is no attachment, say `file    (none)` explicitly.

Do not send without approval. Even if the user already said "send it", show the route,
content, and attachment in their settled form once -- what the user approved was the
*act* of sending, not yet this specific content.

**Never send a test message to a channel that is not the user's own** (a team channel,
someone else's bot). If the goal is to confirm delivery, send only to the user's route.

### 3. Send

The body may carry newlines, quotes, and non-ASCII, so do not pass it as a shell
argument -- **write it to a file and pipe it in on stdin**:

```sh
# after writing the body to <scratchpad>/body.txt:
pushpush send --to alerts --media /path/to/chart.png --caption "today" \
    < <scratchpad>/body.txt
```

- Drop `--media` and `--caption` when there is no file.
- Markup is `--markup markdown|html`; deliver without a sound with `--silent`.
- On success one line, `provider message-id`, is printed to stdout.

### 4. Report the result as-is

If `send` returned without an exception, delivery is done. Report `provider` and the
message id. A failure arrives as an exception, never rounded off into a quiet success.

## When an exception is raised

A package exception is already a sentence a user can read. **Pass `str(err)` through
as-is.** The table below lists only the action to add per exception.

| Exception | Action to add |
|---|---|
| `MissingSecretError` | **Do not have the user paste a token or webhook URL into the chat.** Point them at the `getpass` command to enter it in their own terminal (README step 3). |
| `InsecureCredentialsError` | None -- the exception already carries the `chmod 600` command. |
| `MediaUnsupportedError` | The route cannot carry a file (a Slack webhook -- a bot-token route can). Ask whether to send it via Telegram or Discord, or put a link in the text. |
| `MediaTooLargeError` | Ask whether to shrink the file or send it on a different route. |
| `MarkupUnsupportedError` | The service does not render that markup. Ask whether to resend with `markup="plain"`. |
| `InvalidPushError` | Nothing to send, a caption without media, or a missing destination. Get it from the user and reassemble. |
| `SendFailedError` | The service refused (a revoked token, a wrong chat id). The exception carries the service's own reason. |
| `UnknownRouteError` / `UnknownProviderError` | A route or provider not in the config. Go to "First setup" below. |
| `urllib.error.URLError` | The network itself failed. Ask whether to retry shortly. |

## First setup

A `ConfigError` means the config file is missing. Its format is in the repo's
`README.md`, steps 2 and 3.

**Do not hardcode paths.** The package decides them from `PUSHPUSH_CONFIG`,
`PUSHPUSH_CREDENTIALS`, and `XDG_CONFIG_HOME`, so ask the package:

```sh
python3 -c "
from pushpush import default_config_path, default_credentials_path
print('config:     ', default_config_path())
print('credentials:', default_credentials_path())
"
```

**Do not create the config or credentials inside the repo** -- put them where the
command above prints (both outside the repo). **Never write a token or webhook URL in
plain text in the chat or a script.** Point the user at the `getpass` command and have
them run it in their own terminal.
