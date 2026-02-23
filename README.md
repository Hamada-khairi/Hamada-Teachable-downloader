<a name="readme-top"></a>

<!-- PROJECT SHIELDS -->

[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]

<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/Hamada-khairi/Hamada-Teachable-downloader">
    <img src="images/logo.png" alt="Logo" width="80" height="80">
  </a>

<h3 align="center">Teachable-dl</h3>

  <p align="center">
    A high-performance downloader for Teachable courses — with parallel downloads, resume support, and an offline HTML viewer.
    <br />
    <br />
    <a href="https://github.com/Hamada-khairi/Hamada-Teachable-downloader.git/issues">Report Bug</a>
    ·
    <a href="https://github.com/Hamada-khairi/Hamada-Teachable-downloader.git/issues">Request Feature</a>
  </p>
</div>

<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li><a href="#about-the-project">About The Project</a></li>
    <li><a href="#features">Features</a></li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
      </ul>
    </li>
    <li><a href="#usage">Usage</a></li>
    <li><a href="#performance">Performance</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>

<!-- ABOUT THE PROJECT -->

## About The Project

Teachable-dl is a Python-based course downloader for the [Teachable](https://teachable.com) platform. It automates login (including SSO flows), scrapes course structures, and downloads all videos, subtitles, attachments, and HTML pages for offline access.

The downloader supports three Teachable course templates (**Simple/Next**, **Classic/Mainbar**, and **Colossal/Block**) and automatically detects which one to use.

⭐ `Star` this repository if you find it valuable and worth maintaining.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Built With

- [![Python][Python.org]][Python-url]
- [![Selenium][Selenium.org]][Selenium-url]
- [![yt-dlp][yt-dlp.org]][yt-dlp-url]

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Features

| Feature | Description |
|---|---|
| **Batch Download (`--all`)** | Download every enrolled course from your dashboard in one command |
| **Parallel Video Downloads** | 2-phase architecture: sequential scraping → parallel yt-dlp downloads (6 workers) |
| **Resume Support** | `.progress.json` tracker per course — interrupted downloads resume where they left off |
| **ffmpeg HLS Acceleration** | Uses ffmpeg as the external downloader with concurrent fragments and auto-reconnect |
| **Subtitle Integration** | English subtitles downloaded in the same yt-dlp pass — no double extraction |
| **Skip Existing Files** | Already-downloaded videos are automatically skipped |
| **Offline HTML Viewer** | Auto-generated dark-mode `index.html` dashboard with course cards and local file links |
| **SSO Login Support** | Handles Teachable's multi-step SSO "Log in with a password" flow |
| **Cloudflare Bypass** | Detects and handles Cloudflare challenge pages |
| **Custom Output Directory** | `--output` flag to save courses outside of OneDrive/cloud-synced folders |
| **OTP / New Device Support** | Prompts for verification code when Teachable detects a new device |

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- GETTING STARTED -->

## Getting Started

### Prerequisites

- **Python 3.8+**
- **Google Chrome** (latest)
- **ffmpeg** — required for high-speed HLS video downloads

#### Installing ffmpeg

**Linux:**
```sh
sudo apt install ffmpeg
```

**Windows:**
```powershell
winget install ffmpeg
```

**macOS:**
```sh
brew install ffmpeg
```

> Make sure `ffmpeg` is available in your system PATH.

### Installation

1. Clone the repo

```sh
git clone https://github.com/Hamada-khairi/Hamada-Teachable-downloader.git
cd Hamada-Teachable-downloader
```

2. Create and activate a virtual environment

```sh
# Linux / macOS
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
.\venv\Scripts\activate
```

3. Install dependencies

```sh
pip install -r requirements.txt
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- USAGE EXAMPLES -->

## Usage

### Download a single course

```sh
python main.py --url <course_url> -e <email> -p <password> -v
```

### Download all enrolled courses from your dashboard

```sh
python main.py --url <dashboard_url> -e <email> -p <password> --all -v
```

### Download from a file of URLs

```sh
python main.py -f urls.txt -e <email> -p <password> -v
```

### Manual login mode

```sh
python main.py --url <course_url> --man_login_url <target_url> -v
```

> Navigate to the URL in the browser tab and check the console for an exact URL match.

### Save to a custom directory (avoid OneDrive)

```sh
python main.py --url <course_url> -e <email> -p <password> -o D:\courses -v
```

### All available options

```
python main.py --help
```

| Flag | Description |
|---|---|
| `--url` | Course URL or dashboard URL (with `--all`) |
| `-e`, `--email` | Account email |
| `-p`, `--password` | Account password |
| `-o`, `--output` | Output directory (default: `~/Downloads/Teachable-dl`) |
| `-f`, `--file` | Path to a text file containing course URLs |
| `--all` | Download all enrolled courses from the dashboard |
| `-v` | Verbosity (`-v` = INFO, `-vv` = DEBUG) |
| `--complete-lecture` | Mark lectures as complete after downloading |
| `--login_url` | Custom SSO login page URL |
| `--man_login_url` | Manual login — waits for you to reach this URL |
| `--user-agent` | Custom browser user agent string |
| `-t`, `--timeout` | Selenium wait timeout in seconds (default: 10) |

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Performance

The downloader uses a **2-phase architecture** for maximum throughput:

| Phase | What happens | Speed |
|---|---|---|
| **Phase 1** (Sequential) | Selenium navigates each lecture page, saves HTML, downloads attachments, extracts video URLs | ~5-15s per lecture |
| **Phase 2** (Parallel) | 6 concurrent yt-dlp workers download videos via ffmpeg with 5 concurrent fragments each | Limited only by your connection |

**Tips for maximum speed:**
- Use `--output` to save to a local drive — cloud-synced folders (OneDrive, Dropbox) throttle I/O
- Install `pycryptodomex` (included in requirements) — speeds up AES-128 encrypted HLS streams by 10-50x
- ffmpeg must be installed and in your PATH for HLS acceleration

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- CONTRIBUTING -->

## Contributing

Contributions are what make the open source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- LICENSE -->

## License

Distributed under the GNU LGPLv3 License. See `LICENSE.txt` for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- ACKNOWLEDGMENTS -->

## Acknowledgments

- [merberich](https://github.com/merberich)
- [Green0Photon](https://github.com/Green0Photon)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- MARKDOWN LINKS & IMAGES -->

[contributors-shield]: https://img.shields.io/github/contributors/Hamada-khairi/Hamada-Teachable-downloader.svg?style=for-the-badge
[contributors-url]: https://github.com/Hamada-khairi/Hamada-Teachable-downloader.git/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/Hamada-khairi/Hamada-Teachable-downloader.svg?style=for-the-badge
[forks-url]: https://github.com/Hamada-khairi/Hamada-Teachable-downloader.git/network/members
[stars-shield]: https://img.shields.io/github/stars/Hamada-khairi/Hamada-Teachable-downloader.svg?style=for-the-badge
[stars-url]: https://github.com/Hamada-khairi/Hamada-Teachable-downloader.git/stargazers
[issues-shield]: https://img.shields.io/github/issues/Hamada-khairi/Hamada-Teachable-downloader.svg?style=for-the-badge
[issues-url]: https://github.com/Hamada-khairi/Hamada-Teachable-downloader.git/issues
[license-shield]: https://img.shields.io/github/license/Hamada-khairi/Hamada-Teachable-downloader.svg?style=for-the-badge
[license-url]: https://github.com/Hamada-khairi/Hamada-Teachable-downloader.git/blob/master/LICENSE.txt
[product-screenshot]: images/screenshot.png
[Python.org]: https://img.shields.io/badge/Python-14354C?style=for-the-badge&logo=python&logoColor=white
[Python-url]: https://www.python.org
[Selenium.org]: https://img.shields.io/badge/Selenium-43B02A?style=for-the-badge&logo=selenium&logoColor=white
[Selenium-url]: https://www.selenium.dev
[yt-dlp.org]: https://img.shields.io/badge/yt--dlp-000000?style=for-the-badge&logo=github&logoColor=white
[yt-dlp-url]: https://github.com/yt-dlp/yt-dlp
