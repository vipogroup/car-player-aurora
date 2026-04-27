# LOCAL SERVER SETUP (Windows + Mobile)

1. Install Python 3.10+ from python.org (enable "Add Python to PATH").
2. Download and extract: local-server-unblocked.zip
3. Open CMD/PowerShell in extracted folder.
4. Run: pip install -r requirements.txt
5. Run: start-server-lan.bat
6. On your phone (same Wi-Fi), open: http://<PC-IP>:5600
7. Verify: http://<PC-IP>:5600/__player_check (must show OK_UNBLOCKED_PLAYER_V5)

Tips:
- If phone can't connect, allow Python/port 5600 in Windows Firewall.
- PC must remain on while using phone.
