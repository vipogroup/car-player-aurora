# Local server (Windows + mobile on same Wi‑Fi)

1. Install Python 3.10+ from [python.org](https://www.python.org) (enable **Add Python to PATH**).
2. Download and extract `local-server-unblocked.zip` from the GitHub repo.
3. In the folder, run once: `setup-windows.bat`  
   (alternative: `powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1`)
4. Run **`start-server-lan.bat`**.
5. On this PC, open the player in the browser. Click **"מובייל · סריקת QR"** at the top and scan the code from your phone (same Wi‑Fi).  
   Or on the phone open: `http://[PC-IP]:5600/` (see terminal line `lan_url=...` or `http://192.168.x.x:5600/`).
6. Check: `http://[PC-IP]:5600/__player_check` should include `OK_UNBLOCKED_PLAYER_V5` and `unblocked_local_version=...`.

**Firewall:** if the phone cannot connect, run `add-firewall-unblocked.ps1` **as Administrator** once (allows TCP 5600).

**Updates:** when a newer `unblockedLocalServer` is published in `local-server-version.json`, the server prints a download hint on startup. Re-download the zip or replace `unblocked_player.py`.

- PC must stay on while using the phone with the local server.
- The QR encodes the LAN URL; it does not work from mobile data only (different network).

## Outside home Wi‑Fi (optional): Tailscale

After the server is running, open the player in the browser on the PC and click **"מרחוק · Tailscale"** for short setup steps and download links. You then open `http://<Tailscale-IP-of-PC>:<port>/` on the phone from anywhere (encrypted). This does **not** replace installing Tailscale once on both devices.
