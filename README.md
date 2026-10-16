# Blender Render Status PWA

A deliberately small mobile dashboard for exactly two things:

- local Linux Blender bakes;
- one Windows Blender render over SSH.

It shows only active Blender work and frame progress. The app includes a 3D
loading animation and optional Web Push notifications when a monitored job
finishes.

## Security and configuration

Copy [`env.example`](env.example) to a **private** location outside this
repository (for example `~/.config/render-status-pwa.env`) and replace the
placeholders:

```sh
cp env.example ~/.config/render-status-pwa.env
chmod 600 ~/.config/render-status-pwa.env
```

The private file holds the Windows SSH destination and render directory. The
app generates its Web Push private key, browser subscriptions, and API token in
`state/`; that entire directory is ignored by Git.

## Run locally

```sh
python3 -m pip install --user -r requirements.txt
set -a
. ~/.config/render-status-pwa.env
set +a
python3 server.py
```

Open `http://127.0.0.1:8765/` locally, or place it behind your own HTTPS proxy
for PWA and push support.

## systemd user service

Install `render-status-pwa.service` as a user service, then reload and start
it:

```sh
mkdir -p ~/.config/systemd/user
cp render-status-pwa.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now render-status-pwa.service
```

The service reads `~/.config/render-status-pwa.env`; it never reads secrets
from the repository.

## Notifications

Open the dashboard over HTTPS, tap **Push**, and accept the browser prompt. A
green **Push an** switch means the browser subscription was stored successfully.
The server monitors job transitions and sends a notification only when an
observed Blender job actually finishes.
