# SMC – Sistem Monitor CCTV

A web dashboard for monitoring CCTV NVRs across multiple sites on a local network.
SMC polls each NVR for camera and HDD status, captures snapshots, keeps a weekly
status history, and opens or closes incident tickets automatically from that data.

Tested with **Uniview (UNV)** and **Hikvision** NVRs.

> **Public version note:** the NVR API endpoint paths in `apps/home/device_api.py`
> are replaced with placeholders (`"API CALL FORMAT"`). The application runs, but
> scans report every NVR as unreachable until real endpoints are supplied.

## Key features

- **Dashboard** – live status of every NVR and camera, grouped by site. A manual scan
  ends with a result popup: unreachable NVRs, cameras that are online but return no
  snapshot, and HDD states the system does not recognise yet.
- **Camera & HDD scanning** – parallel polling of NVR APIs with HTTP Digest auth,
  auto-discovery of new channels, per-camera snapshots, per-slot HDD status with
  capacity, free space and disk brand.
- **Weekly reports** – camera and HDD status history (web view + Excel export with
  embedded snapshots), manual status revision with photo upload.
- **Ticketing** – tickets are created and closed by rules in
  `apps/home/ticket_rules.py` when a report is updated; ticket log, archive with
  search and pagination, and a summary email over SMTP.
- **Master Location** – a reference photo for each camera position.
- **Live View (optional)** – RTSP stream in the browser with OpenCV person detection
  (model files not included).
- **Administration** – site / NVR management, Excel import of the NVR list,
  drag-and-drop ordering, user accounts with optional per-menu permissions,
  encrypted device passwords with key backup / restore.
- **Desktop control panel** (Tkinter) – start / stop the server, live logs,
  one-click database backup and restore.

## Tech stack

Python 3.13 · Flask · PostgreSQL · SQLAlchemy · requests · OpenCV · openpyxl ·
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

## License

This project is not open source. No license is granted.

Third-party components keep their own licenses: see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
