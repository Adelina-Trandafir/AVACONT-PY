# SSH key setup for pushing to the K-BOT server

This document explains how `push-update.ps1` authenticates to the server,
how to restore the key after a reformat, and what _not_ to do.

**This repository contains no key material.** The private key lives:

1. In the team password manager (primary).
2. On the Linux server at `/root/.ssh/backup/` (secondary, root-only).

---

## Background

`push-update.ps1` uses `sftp.exe` in batch mode (`sftp -b`). Batch mode
**cannot prompt for a password**, so it must authenticate with an SSH key.
That means every machine that runs the script needs:

1. A **private key** at `%USERPROFILE%\.ssh\id_ed25519`
2. Its matching **public key** appended to `/root/.ssh/authorized_keys`
   on the server

The server holds the public half. The PC holds the private half.
Nothing secret is stored in the repo.

---

## Where the key is stored

| Location         | Path                                   | Who can read it      |
| ---------------- | -------------------------------------- | -------------------- |
| Windows PC       | `%USERPROFILE%\.ssh\id_ed25519`        | you                  |
| Password manager | attachment `id_ed25519`                | you                  |
| Server (backup)  | `/root/.ssh/backup/id_ed25519.KBOT-PC` | root only (mode 600) |

The server backup is labelled with the machine name it came from. When a
second PC is added, it will be `id_ed25519.<MACHINENAME>`.

**Never commit the private key to Git** - not zipped, not encrypted, not
"temporarily". Git history is permanent.

---

## Restore the key on a fresh Windows PC

Assumes OpenSSH Client is installed (_Settings → System → Optional features_).

### 1. Create the `.ssh` folder if needed

    New-Item -ItemType Directory -Force -Path $env:USERPROFILE\.ssh

### 2. Pull the key from the server

    scp root@89.33.25.34:/root/.ssh/backup/id_ed25519.KBOT-PC "$env:USERPROFILE\.ssh\id_ed25519"

It will ask for the server password **once** (the new PC has no key yet).
If the server backup is missing, get `id_ed25519` from the password manager
instead and copy it to the same path.

### 3. Lock down permissions (optional but tidy)

    icacls $env:USERPROFILE\.ssh\id_ed25519 /inheritance:r
    icacls $env:USERPROFILE\.ssh\id_ed25519 /grant:r "$env:USERNAME:(R)"

### 4. Recreate `~/.ssh/config`

Write this to `%USERPROFILE%\.ssh\config` (UTF-8 without BOM):

    Host 89.33.25.34
      HostName 89.33.25.34
      User root
      Port 27015

### 5. Test

    ssh root@89.33.25.34

Should log in **without asking for a password**. Then:

    .\push-update.ps1 -Notes "test"

---

## If both backups are lost

Regenerate the key and install it.

### 1. New key

    ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_ed25519

Press Enter **twice** (empty passphrase - batch mode cannot type a
passphrase either).

### 2. Install the public key on the server

**Use `scp`, not `type | ssh`.** Piping on Windows can add a UTF-8 BOM and
CRLF line endings; sshd then silently ignores the line and the key stops
working. We hit exactly this bug the first time.

    scp $env:USERPROFILE\.ssh\id_ed25519.pub root@89.33.25.34:/tmp/newkey.pub
    ssh root@89.33.25.34 "cat /tmp/newkey.pub >> ~/.ssh/authorized_keys && rm /tmp/newkey.pub"

### 3. Verify and re-back-up

    ssh root@89.33.25.34

Then:

1. Update the password manager copy of `id_ed25519`.
2. Update the server backup:

   scp "$env:USERPROFILE\.ssh\id_ed25519" root@89.33.25.34:/root/.ssh/backup/id_ed25519.KBOT-PC

---

## Troubleshooting

### `Permission denied (publickey,password)` from `sftp -b`

The key is not being accepted. Check in this order.

**1. Is the key present locally?**

    dir $env:USERPROFILE\.ssh

Should list `id_ed25519` and `id_ed25519.pub`.

**2. Does plain `ssh` log in without a password?**

    ssh -v root@89.33.25.34

Look for `Offering public key: ... id_ed25519`. If it is followed by
`Authentications that can continue: publickey,password` and a password
prompt, the server rejected the key. Continue to step 3.

**3. Is the `authorized_keys` line valid on the server?**

    awk '/\r/ { print "line " NR ": has CR" }' ~/.ssh/authorized_keys
    awk 'NR>1 && /^\xef\xbb\xbf/ { print "line " NR ": has BOM" }' ~/.ssh/authorized_keys
    awk '/^[^#]/ && $1 !~ /^(ssh-rsa|ssh-ed25519|ecdsa-sha2-|ssh-dss)$/ { print "line " NR ": INVALID prefix" }' ~/.ssh/authorized_keys

All three should print nothing. If one prints, repair with:

    sed -i 's/\xEF\xBB\xBF//g; s/\r$//' ~/.ssh/authorized_keys

**4. Wrong port?** `push_settings.json` and `~/.ssh/config` must agree
(currently `27015`).

### `sftp: Connection closed` mid-upload

Uploads are atomic: the package lands as `*.part` and is renamed into
place, `latest.json` is written last. A failed transfer leaves the
previous version advertised; no client sees a partial file. Re-run the
script.

---

## Server-side backup: how it was made

For future-you, and for any teammate who needs to understand it.

    # on the Windows PC
    scp "$env:USERPROFILE\.ssh\id_ed25519" root@89.33.25.34:/root/.ssh/backup/id_ed25519.KBOT-PC

    # on the server
    mkdir -p /root/.ssh/backup
    chmod 700 /root/.ssh/backup
    chmod 600 /root/.ssh/backup/id_ed25519.KBOT-PC
    ls -la /root/.ssh/backup   # should show -rw------- root root

The key is at `/root/.ssh/backup/id_ed25519.KBOT-PC` and is readable only
by root. It is **not** inside `/root/AVACONT/`, so a deploy never touches
it.

---

## Things never to do

- Do **not** commit the private key to Git, even zipped, even encrypted.
- Do **not** pipe a public key into `authorized_keys` from PowerShell
  (`type ... | ssh ...`). Use `scp` and append remotely.
- Do **not** reuse this key for other servers. One key per host/role.
- Do **not** disable password authentication on the server until at least
  two independent machines have working keys.
- Do **not** share `id_ed25519` over chat, email, or screenshots. Only via
  the password manager or an `scp` between machines you control.
