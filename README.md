# SMC – Sistem Monitor CCTV

A web dashboard for monitoring the status of CCTV cameras connected to multiple NVRs across one or more local networks.
The app polls each NVR for camera and HDD status, captures snapshots, then keeps a status record for each day the data was taken, and automatically opens or closes incident tickets based on that data.

Tested with Uniview (UNV) and Hikvision NVRs.

## Key features

- **Dashboard** – live status of every NVR and camera, grouped by site. A manual scan
  ends with a result popup: unreachable NVRs, cameras that are online but return no
  snapshot, and HDD states the system does not recognise yet.
- **Camera & HDD scanning** – parallel polling of NVR APIs with Digest auth,
  auto-discovery of new channels, per-camera snapshots, per-slot HDD status with
  capacity, free space and disk brand.
- **Weekly reports** – Generate camera and HDD status history, where manual status revision is allowed with photo upload.
- **Ticketing** – tickets are created and closed by rules in
  `apps/home/ticket_rules.py`.
- **Master Location** – a reference photo for each camera position.
- **Administration** – site/NVR management, Excel import of the NVR list,
  drag-and-drop ordering, encrypted device passwords with key backup/restore.
- **Desktop control panel** – start/stop the server, live logs, database backup and restore.

## Tech stack

Python · Flask · PostgreSQL · SQLAlchemy · requests · OpenCV · openpyxl ·
Tkinter · Bootstrap 4 · jQuery · PyInstaller · Inno Setup

Full stack table, requirements and build steps: **[BUILD.md](BUILD.md)**.

## Project structure

```
apps/
  authentication/    login, user model, access-control switch
  home/              routes, NVR scanning (ai_report.py, hdd_checker.py),
                     reports, ticket rules, device API paths (device_api.py)
  templates/         Jinja2 pages
  static/            CSS, JavaScript, fonts
run.py               control panel and server entry point
run.spec             PyInstaller spec
smc_installer.iss    Inno Setup script
```

## Credits

Built on the Flask Datta Able template by AppSeed and other open-source
components – see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).


