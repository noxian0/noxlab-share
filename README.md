# NoxLab Share

NoxLab Share is a Windows desktop/local utility for sending files from a PC to devices on the same Wi-Fi or LAN, and receiving files from a phone back to the PC. It starts a temporary local web server, shows a LAN link, and generates a QR code.

No cloud upload. No account system. No tunneling service.

## Screenshots from v1.1.0

**Send from a PC.** A shared folder is ready to download as a ZIP using the link or QR code.

![Send tab sharing the Field recordings folder, with its QR code and LAN link](pics/shared.png)

**Receive on a PC.** The progress bar shows how much of the incoming file the PC has saved.

![Receive tab showing Soundcheck.wav at 49 percent, with 6 MB received](pics/main.png)

**Send from a phone.** Review the selected filenames and sizes before starting the upload.

<img src="pics/mobile.png" alt="Phone upload page with an audio file and a text file selected" width="320">

## Features

- Select one file or one folder
- Folder sharing creates a temporary ZIP automatically
- Local HTTP server binds to the LAN, not just localhost
- Automatic port conflict handling
- LAN URL such as `http://192.168.x.x:8765/download`
- QR code display, link copy, QR save, and QR copy on Windows
- Optional password protection
- Auto-stop timer: manual, 10, 30, or 60 minutes
- Mobile-friendly download page
- Mobile-safe download URLs with the real filename extension, so Android/iOS browsers keep `.zip` and other extensions
- Receive files from a phone through a LAN upload page
- Choose the PC folder where received files are saved
- Choose a receive upload limit: 512 MB, 2 GB, 5 GB, 10 GB, or no fixed limit
- Sender status shows the device's LAN IP and the time the PC finishes sending the file or final requested range
- Separate Send, Receive and Activity tabs, with incoming file progress on the PC
- Temporary ZIP cleanup when sharing stops or the app exits

## Stack choice

This MVP uses Python because it gives a small, inspectable Windows app without an Electron-sized runtime. Tkinter provides the desktop UI, Python's standard threaded HTTP server handles LAN downloads, `zipfile` packages folders, and `qrcode`/Pillow generates the QR image.

## Requirements

For normal users installing the release:

- Windows 10 or newer
- No Python required when using the portable release ZIP
- Devices must be on the same Wi-Fi/LAN
- Windows Firewall may ask you to allow NoxLab Share on Private networks

For running from source:

- Windows 10 or newer
- Python 3.11+ recommended, with `pip`
- Tkinter/Tcl-Tk support, included with the standard Python installer from python.org
- Python packages from `requirements.txt`: `qrcode[pil]` and `Pillow`
- PowerShell for the helper scripts

For building a Windows release:

- Everything needed to run from source
- PyInstaller, installed with `pip install pyinstaller`
- PowerShell and standard Windows .NET components for the setup builder

## Download from GitHub

Download the Windows ZIP from the [latest GitHub Release](https://github.com/noxian0/noxlab-share/releases/latest).

The ZIP includes:

- `NoxLab Share.exe` to run the app directly
- `Install.cmd` to install it to `%LOCALAPPDATA%\Programs\NoxLab Share`
- `README.md`, `LICENSE`, and the app icon

The setup EXE is not published by default because unsigned self-extracting installers are commonly blocked by browsers or antivirus tools. The portable ZIP is the recommended release format until the app has a signed installer.

## Run locally

From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m noxlab_share
```

## Create a Desktop shortcut

After dependencies are installed, run:

```powershell
.\scripts\create_desktop_shortcut.ps1
```

This creates `NoxLab Share.lnk` on your Desktop and `Open NoxLab Share.lnk` inside the project folder. If `dist\NoxLab Share.exe` exists, the shortcuts use the standalone app. Otherwise they fall back to the local `.venv` launcher.

## Send files from PC

1. Open **Send** and choose **Choose file** or **Choose folder**.
2. Optionally enable a password.
3. Choose an auto-stop timer or leave it on Manual.
4. Click **Start Sharing**.
5. Open the LAN URL on another device or scan the QR code.
6. **Last send** shows the device IP and time the PC finished sending the file or requested final range.
7. Click **Stop session** when finished.

## Receive files from phone

1. Open **Receive** and choose a folder or keep the default `Downloads\NoxLab Share Received`.
2. Optionally enable a password.
3. Choose an auto-stop timer or leave it on Manual.
4. Click **Start receiving**.
5. Scan the QR code with the phone.
6. Choose one or more files on the phone. Check their names and sizes, then tap **Upload to PC**.
7. Wait for **Saved on PC**, then click **Stop session**.

With the upload page open, the browser reads each selected file in 2 MiB pieces. The percentage advances when the PC confirms a piece. After a connection drop, **Retry upload** continues from the last confirmed piece while the same receive session and page are open. The PC discards incomplete transfers after 15 minutes of inactivity or when receiving stops. Wait for **Saved on PC** before closing the page.

If the browser reports that it cannot read the video, choose a locally stored copy through Android's **Files** picker. The PC activity log shows the start and completion of each file.

## LAN and firewall notes

The download link is local-network only. Devices must be on the same Wi-Fi or LAN, and the app must stay open while sharing.

If another device cannot open the link:

- Allow Python or the packaged NoxLab Share app through Windows Firewall on Private networks.
- Make sure both devices are on the same network.
- Disable VPN isolation while testing.
- Avoid guest Wi-Fi networks that block device-to-device access.
- Confirm the sender's PC did not switch networks after starting the share.

## Security notes

- NoxLab Share does not upload files to the cloud or internet.
- It does not create an internet tunnel.
- The local server listens on the LAN while sharing is active.
- Password protection is optional but recommended.
- Stop sharing when finished.
- Anyone on the same LAN who has the link, and the password if enabled, can download the shared item.

## Project structure

```text
noxlab_share/
  __main__.py       App entrypoint
  app.py            Desktop session controls
  desktop_ui.py     Send, Receive and Activity views
  mobile.py         Mobile transfer pages
  server.py         Local HTTP server and download page
  qr_tools.py       QR generation and clipboard helpers
  utils.py          LAN IP, ZIP, size, and cleanup utilities
noxlab_share_launcher.py
start_noxlab_share.cmd
assets/
  noxlab_share.ico
pics/
  main.png
  shared.png
scripts/
  create_desktop_shortcut.ps1
  generate_icon.py
  install_noxlab_share.cmd
  install_noxlab_share.ps1
  package_release.ps1
  setup_stub.cs
README.md
requirements.txt
LICENSE
.gitignore
```

## Create a Windows release

Install packaging tools:

```powershell
pip install pyinstaller
```

Build the standalone app and portable ZIP:

```powershell
.\scripts\package_release.ps1
```

Outputs:

- `dist\NoxLab Share.exe` standalone app
- `release\NoxLabShare-1.1.0-windows.zip` portable release

The portable ZIP includes `Install.cmd`, which copies the app to:

```text
%LOCALAPPDATA%\Programs\NoxLab Share
```

It also creates Desktop, Start Menu, and in-folder shortcuts.

To build the unsigned setup EXE for local testing, pass `-BuildSetup`:

```powershell
.\scripts\package_release.ps1 -BuildSetup
```

Do not publish the unsigned setup EXE unless you expect browser or antivirus warnings.

## Development notes

The main local validation path is:

1. Start the app with `python -m noxlab_share`.
2. Select a small test file.
3. Start sharing.
4. Open the displayed `/download` URL in a browser on the same PC.
5. Test sending from a phone or another machine on the same LAN.
6. Stop sharing and confirm the app log reports the server stopped.

Folder sharing is validated the same way, except the selected folder is served as a temporary ZIP.
Receive mode can be validated by opening **Receive**, clicking **Start receiving**, opening the `/upload` page on a phone, and sending a small test file.

You can also run the server smoke test without opening the desktop UI:

```powershell
python tests\smoke_server.py
```
