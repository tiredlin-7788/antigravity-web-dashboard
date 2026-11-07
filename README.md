# Antigravity Quota Dashboard

A responsive, lightweight real-time Quota Dashboard for Antigravity (Google Cloud Code Assist API), designed for mobile (iPhone / Tailscale) and desktop browsers.

![Theme](https://img.shields.io/badge/Theme-iOS%20Widget%20%7C%20Cyber%20%7C%20Slate-blue)
![Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Python%20Stdlib)-green)
![Tailscale](https://img.shields.io/badge/Tailscale-Ready-brightgreen)

## Features

- **Mobile First / iOS Widget Look**: Designed specifically to match iOS widgets with smooth rounded cards, progress bars, and high contrast typography.
- **Multiple Themes**: Toggle on-the-fly between **iOS (Dark Glass)**, **Cyberpunk (Neon)**, and **Slate (Clean Modern)**.
- **Three Pool Aggregation**:
  - **Claude 3.5 / 3.7 / 5.5**
  - **Gemini 4 Argon & GPT-OSS**
  - **Gemini 2.5 / 3.x Flash & Pro**
- **Dynamic Countdown**: Real-time ticker showing exactly how much time remains until quota resets (`HH:MM:SS` or relative days/hours).
- **Bottleneck Detection**: Instant status badge highlighting which model pool is the current bottleneck.
- **Collapsible Model Breakdown**: Tap to inspect all individual models under each pool.
- **Zero External Dependencies**: Python backend uses standard libraries (`http.server`, `urllib`, `ctypes`) to read Windows Credential Manager and query Google's Cloud Code Assist API directly.
- **Tailscale Accessible**: Bind to `0.0.0.0:8890` to view smoothly from iPhone via Tailscale (`http://<tailscale-ip>:8890`).

## Architecture

```
iPhone Safari (Tailscale) / Desktop Browser
                  │
                  ▼ HTTP (:8890)
       server.py (Python stdlib)
                  │
        Windows Credential Manager
        (gemini:antigravity OAuth)
                  │
                  ▼
daily-cloudcode-pa.googleapis.com
  (/v1internal:retrieveUserQuota)
```

## Getting Started

### 1. Requirements
- Windows 11 with `antigravity-cli` (agy) logged in (`gemini:antigravity` in Credential Manager)
- Python 3.10+

### 2. Run the Dashboard
```bash
python server.py --port 8890
```

Access from:
- Local: `http://localhost:8890`
- Tailscale (iPhone): `http://100.122.156.90:8890` (or `http://bourbon-win:8890`)

## PWA / Home Screen on iOS
1. Open `http://<your-tailscale-ip>:8890` in iOS Safari.
2. Tap the **Share** button at the bottom.
3. Select **Add to Home Screen**.
4. The dashboard will now launch like a native iOS application.
