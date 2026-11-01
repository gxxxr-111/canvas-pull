# Security policy

## How canvas-pull handles your data

- Your Canvas access token is stored in the operating system keychain (macOS Keychain,
  Windows Credential Manager, Secret Service on Linux). It is written to the config file only
  when no keychain is available; on macOS and Linux the file is then readable only by you
  (mode 600).
- canvas-pull talks only to the Canvas site you configure. File downloads may be redirected to
  your school's file storage, and the token is **not** sent along on those redirects.
- There is no telemetry, analytics or third-party service. Everything runs on your machine.
- Downloaded files and the `.canvas-pull/` state folder stay in your save folder.

## Good practice

- Give your Canvas token an expiry date, and delete it in Canvas when you stop using the tool.
- Never paste your token into an issue, a log or a chat.

## Reporting a vulnerability

Please **do not** open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/gxxxr-111/canvas-pull/security/advisories/new)
instead. You will get a reply as soon as possible.
