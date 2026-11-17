<a id="top"></a>

<div align="center">

# **SMC – Sistem Monitor CCTV**

<b><ins>A web dashboard that monitors CCTV cameras and NVR hard drives across multiple sites, keeps a daily status history, and opens or closes incident tickets automatically.</ins></b>

<br>

![Status](https://img.shields.io/badge/status-COMPLETED-orange?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-All_Rights_Reserved-lightgrey?style=for-the-badge)

</div>

<br>

<details open>
<summary><b>Table of Contents</b></summary>

1. [Project Overview](#project-overview)
2. [Tech Stack](#tech-stack)
3. [How It Works](#how-it-works)
4. [Skills Learned](#skills-learned)
5. [Challenges and Solutions](#challenges-and-solutions)
6. [Project Structure](#project-structure)
7. [Getting Started](#getting-started)
8. [Extra Things](#extra-things)
9. [Credits and Contact](#credits-and-contact)

</details>

---

## <ins>Project Overview</ins>

> **TL;DR:** SMC polls **Uniview (UNV)** and **Hikvision** NVRs on a local network to check camera and HDD status, captures snapshots, records a daily status history, and turns that history into weekly reports and automatic incident tickets.

### ▸ Problem
Checking hundreds of CCTV cameras across several sites by hand is slow and easy to get wrong: someone has to open each NVR, look at every channel, check the hard drives, and write down its status manually. This manual process can take up to 3 hours for ~400 cameras, which is inefficient for monitoring CCTV status and tracking downtime/errors.

### ▸ Objectives
- *Check every camera and NVR hard drive across all sites from one dashboard*, with parallel scanning instead of one device at a time.
- *Keep a daily status record* for each camera and NVR HDD.
- *Open and close incident tickets automatically* from that record, while being able to manage false alarms for new or uncheckable cameras.
- *Ship it as a Windows installer* with a desktop control panel, so non-developers can run, back up, and restore it.

### ▸ Context
I helped develop, enhance, and finalize SMC during my internship at an Indonesian field-service company, building the middle-stage features, initial requirements, and new workflows established by my supervisor. Throughout the development lifecycle, I leveraged Claude Code for feature planning, code generation, and debugging, while handling all final code reviews, testing, and deployment myself.

<p align="right">(<a href="#top">back to top</a>)</p>

---

## <ins>Tech Stack</ins>

> The tools I **actually used** and what I used them **for**.

| Category | Tool | What I Used It For |
|:---|:---|:---|
| **Language** | `Python` | The web app, NVR scanning, the ticket rules and the desktop control panel |
| **Framework** | `Flask`, `Flask-Login`, `Flask-WTF` | The web app, split into blueprints for authentication and the main pages, with session login, forms and optional per-menu permissions |
| **Frontend** | `Jinja2`, `HTML`, `CSS`, `JavaScript`, `Bootstrap` (Datta Able), `jQuery`, `SortableJS`, `Flatpickr` | Server-rendered dashboard pages, drag-and-drop NVR ordering and time pickers |
| **Data / Storage** | `SQLAlchemy`, `PostgreSQL`, `psycopg2` | Models for sites, NVRs, cameras, daily status history and tickets |
| **Data Processing** | `openpyxl` | Importing the NVR list from Excel and exporting weekly camera and HDD reports with embedded snapshots |
| **AI / ML** | `OpenCV` (MobileNet-SSD) | Optional person detection on the MJPEG live view |
| **Desktop** | `Tkinter` | The control panel: first-run setup, starting and stopping the server, live logs, backup and restore |
| **Infra / Deployment** | `PyInstaller`, Inno Setup, `pg_dump` / `pg_restore` | Packaging the app as a Windows executable and installer, and one-click database backup and restore |
| **Libraries** | `requests`, `ElementTree`, `concurrent.futures`, `cryptography`, `hashlib`, `Pillow`, `smtplib` | Polling NVR APIs in parallel over HTTP Digest auth, encrypting stored NVR and SMTP passwords, hashing user passwords, handling snapshots for the reports and sending ticket summary emails |
| **Tools** | `Git`, Claude | Version control, planning, brainstorming the app design |

<p align="right">(<a href="#top">back to top</a>)</p>

---

## <ins>How It Works</ins>

> Scan the NVRs → review the results → save them to the report → ticket rules open or close incidents.

```
NVRs (UNV / Hikvision) → Parallel Scan → Scan Result → Update to Report → Ticket Rules → Report / Excel / Email
```

1. **Scan.** `ai_report.py` and `hdd_checker.py` poll every NVR in parallel over HTTP Digest auth. They read each channel's status, grab a snapshot per camera, and read each HDD slot's state, capacity, free space, and brand. New channels are discovered automatically.
2. **Scan Result.** A manual scan ends with a pop-up listing what needs attention: NVRs that can't be reached, cameras that are online but return no snapshot, and HDD states the system doesn't recognize yet.
3. **Update to Report.** The results are saved as that day's column in the weekly report. Statuses can be revised by hand, with a photo upload as proof.
4. **Ticket Rules.** Every rule lives in one file, `ticket_rules.py`, and runs only on Update to Report or a manual revision. A scan alone never creates or closes a ticket.
   - A **camera ticket** opens when a camera goes Offline after a real non-offline status. New cameras and earlier days with no data don't count. It closes only when the camera is Online with a snapshot.
   - An **HDD ticket** opens when a slot reports Error and the previous check had none. It closes when every slot reads Normal or No Disk.
5. **Output.** Weekly camera and HDD reports export to Excel with embedded snapshots, and ticket summaries can be emailed over SMTP.

<p align="right">(<a href="#top">back to top</a>)</p>

---

## <ins>Skills Learned</ins>

> What this project taught me, technically and beyond.

### ▸ Skills
- **Integrating with hardware APIs:** Each NVR exposes a RESTful HTTP API, and I learned to work with it from the outside: building request URLs per brand, authenticating with HTTP Digest auth, and parsing two response formats (JSON from Uniview, namespaced XML from Hikvision) into one common camera and HDD status. See apps/home/ai_report.py, apps/home/hdd_checker.py, apps/home/device_api.py.
- **Concurrent programming:** Network calls spend most of their time waiting, so I learned to run them in parallel with a thread pool (up to 15 NVRs at once) and to set separate timeouts for data calls and snapshots, so one dead device can't stall the whole scan. See the ThreadPoolExecutor scanning in apps/home/ai_report.py.
- **Database design with an ORM:** I learned to separate current state from history: one table holds each camera's latest status, while daily snapshots and HDD history are stored separately for the weekly reports and ticket checks. See apps/home/models.py.
- **Application security:** I learned and applied encryption and hashing methods. NVR and SMTP passwords are encrypted with Fernet, user passwords are hashed with PBKDF2-SHA512, and the encryption key is backed up together with the database because one is useless without the other. See apps/home/routes_settings.py, apps/authentication/util.py and apps/backup_manager.py.
- **Packaging a desktop-hosted web app:** I learned to ship a Flask app to non-developers as a normal Windows program: a Tkinter control panel with first-run setup, a PyInstaller build, and an Inno Setup installer. See run.py, run.spec, smc_installer.iss.

<p align="right">(<a href="#top">back to top</a>)</p>

---

## <ins>Challenges and Solutions</ins>

> What went wrong, how I fixed it, and what I took away from the project.

Two NVR brands, two different APIs. Uniview and Hikvision expose the same information in completely different ways: different URL paths, JSON on one side and XML with namespaces on the other, and different ways of naming cameras and channels. I put every API path in one file (device_api.py), wrote a separate parser for each brand that returns the same status format, stripped the XML namespaces before parsing, and cleaned up duplicated NVR and channel names in camera_naming.py. The lesson: keep vendor differences in one thin layer, so the rest of the app never needs to know which brand it is talking to. [ADJUST]

Avoiding false tickets. A camera that was just added, or a day when an NVR couldn't be reached, could easily look like a camera that "went offline" and open a ticket nobody needed. I rewrote the rules so a ticket only opens when a camera goes Offline after a real non-offline status, compared against the last day that could actually be checked. A ticket only closes when the camera is Online with a snapshot, and a scan alone never touches tickets. The lesson: automatic alerts are only useful if people trust them, so preventing false alarms matters as much as catching real ones. [ADJUST: how did you notice the false tickets?]

Slow scans and unreachable devices. Checking many NVRs one by one took too long, and a single NVR that didn't respond could hold up the scan for its whole timeout. Running the requests in parallel with ThreadPoolExecutor, using a shorter timeout for snapshots than for data calls, and recording a clear failure reason for each device fixed both. Unreachable NVRs now show up in the Scan Result popup instead of failing silently. The lesson: with network code, plan for failure first. [ADJUST: add real numbers if you have them, e.g. scan time before and after]

Losing the encryption key means losing every NVR password. Stored NVR passwords are encrypted with a key file, so reinstalling the app without that key would leave every saved password unreadable. I made the backup bundle the database dump and the key into one ZIP file, saved backups outside the installation folder by default so an uninstall can't delete them, and made the restore run as a single transaction. The lesson: security features create new ways to lose data, so recovery has to be designed together with them. [ADJUST]

<p align="right">(<a href="#top">back to top</a>)</p>

---

## <ins>Project Structure</ins>

```
SMC/
├── apps/
│   ├── authentication/   # login, user model, access-control switch
│   ├── home/             # routes, NVR scanning, reports, ticket rules, device API paths
│   ├── templates/        # Jinja2 pages
│   ├── static/           # CSS, JavaScript, fonts
│   └── backup_manager.py # database + key backup and restore
├── run.py                # desktop control panel and server entry point
├── run.spec              # PyInstaller spec
├── smc_installer.iss     # Inno Setup installer script
├── BUILD.md              # full stack table, build steps and configuration
├── requirements.txt
└── README.md
```

<p align="right">(<a href="#top">back to top</a>)</p>

---

## <ins>Getting Started</ins>

```bash
# 1. Clone
git clone https://github.com/hanhansel/SMC.git
cd SMC

# 2. Create the database and install dependencies
createdb -U postgres smc_db
pip install -r requirements.txt

# 3. Run
python run.py
```

On first start, a setup window asks for an admin account and the PostgreSQL password. Then click **Nyalakan Server** in the control panel and open http://localhost:5001.

> **Requirements:** Windows 10/11 (x64), Python 3.13, PostgreSQL 16, and network access to the NVRs. The NVR API paths in `apps/home/device_api.py` are left out of this public version, so scanning needs them filled in. Build steps and `config.ini` options are in [`BUILD.md`](BUILD.md).

<p align="right">(<a href="#top">back to top</a>)</p>

---

## <ins>Extra Things</ins>

<details>
<summary><b>Main Features</b></summary>
<br>

| Page | What It Does |
|:---|:---|
| **Dashboard** | Live status of every NVR and camera by site, manual scan with a result pop-up |
| **Weekly Report** | Daily camera status history, manual revision with photo proof, Excel export |
| **HDD Report** | Per-slot HDD status, capacity, free space and brand, Excel export |
| **Ticket Log / Archive** | Open and resolved tickets, bulk update, manual ticket emails |
| **Master Location** | A reference photo for each camera position |
| **Settings** | Site and NVR management, Excel import of the NVR list, drag-and-drop ordering, encryption key backup and restore |
| **Live View** | Optional MJPEG stream with person detection (model files not included) |
| **Accounts** | (HIDDEN) User management and optional per-menu permissions |

</details>

<p align="right">(<a href="#top">back to top</a>)</p>

---

## <ins>Credits and Contact</ins>

**Acknowledgements:** Built on the **Flask Datta Able** template by AppSeed, plus other open-source components listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). [FILL THIS: anyone else you want to thank, e.g. a supervisor or the company.]

**License:** No license. All rights reserved. This repository is shared to show my work; please ask before reusing any of it.

**Author:** **Hansel Sebastian**, *Data Science Student @ Bina Nusantara University*

[![GitHub](https://img.shields.io/badge/GitHub-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/hanhansel)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://linkedin.com/in/hansel-sebastian-5a19b8245)

<p align="right">(<a href="#top">back to top</a>)</p>
