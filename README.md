# AshuViralCuts

AshuViralCuts is a self-hosted video clipping studio for turning long gaming videos and streams into short, captioned clips. The app has a FastAPI/Python backend, a responsive browser editor, and FFmpeg-based rendering. It can run locally or as a protected Render web service.

## What it can do

- Import YouTube, Twitch, and Kick video links, or upload a local video.
- Optionally upload a private YouTube `cookies.txt` file when YouTube blocks imports with “Sign in to confirm you're not a bot”. Cookies are used only for YouTube yt-dlp requests; Twitch, Kick, and local video workflows do not use them.
- Show preview frames while a link import continues in the background.
- Find candidate moments from transcripts, audio activity, and video motion. Optional local Ollama models can refine rankings and write clip hooks.
- Produce one or many clips in vertical, square, portrait, or landscape formats, with configurable resolution, frame rate, layout, crop regions, and facecam placement.
- Generate Hindi, Hinglish, and English subtitles; import SRT/ASS/VTT; edit words and timings; export subtitle files.
- Add caption styles, opening hooks, social templates, backgrounds, watermarks, credits, outros, brand profiles, and music beds.
- Search full-source transcripts, select passages, review optional local visual observations, and detect facecam areas.
- Re-edit saved clips, package clips and captions as a ZIP, rate clips, and use ratings to tune local highlight ranking.
- Queue or schedule batch jobs, pause waiting work, and retry failed or interrupted jobs.
- Record Twitch or YouTube live sessions and clip chat spikes. Save creators to watch for streams and start clipping automatically.
- Use the phone remote on the same Wi-Fi network for live controls, quick clip jobs, and saved clip playback.

## Requirements

- Python **3.13.17** (pinned in `.python-version`).
- FFmpeg and FFprobe available on `PATH`.
- Windows, macOS, or Linux for local use. Render deploys use Linux.
- Internet access for importing videos and downloading speech models.
- Ollama is optional. Without a compatible local model, highlight detection uses transcript/audio/video heuristics and the visual review feature is unavailable.

## Run locally

From the project directory:

```powershell
py -3.13 -m venv .venv
\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

On macOS or Linux, activate with `source .venv/bin/activate`. Open <http://127.0.0.1:8770>.

Local mode does not require a password unless `NEONCLIPPING_REQUIRE_AUTH=1` is set. If local authentication is enabled, provide `NEONCLIPPING_AUTH_PASSWORD` in the process environment. Do not commit passwords or `.env` files.

## Authentication and security

Public deployments require `NEONCLIPPING_REQUIRE_AUTH=1` and `NEONCLIPPING_AUTH_PASSWORD`. The password must be at least 16 characters. Render reads the secret from its Environment settings; it is intentionally not stored in this repository. The app uses a short-lived, HTTP-only, SameSite session cookie, limits failed sign-in attempts, checks request origins for state-changing requests, and sets browser security headers. Sign-in sessions expire after 12 hours or when the server restarts.

Keep the access password private and unique. HTTPS is provided by Render for its public domain; the app marks session cookies secure when it detects HTTPS. Do not expose a local server directly to the public internet. Phone access is intended for a trusted local Wi-Fi network and retains its separate six-digit PIN.

### YouTube cookies

Open **Bring your video → YouTube cookies**, then choose a Netscape-format `cookies.txt` exported from a browser signed in to YouTube. The upload is limited to 1 MB, validated for YouTube cookie entries, saved only in the private application data folder, and never exposed through a download route. yt-dlp gets a restricted-permission temporary copy for each YouTube job; that copy is deleted when the job exits. The saved cookie file is excluded by `.gitignore`. Remove it from the same dashboard panel when it is no longer needed. Cookie sessions expire, so upload a fresh export if YouTube starts rejecting it again. Do not share the file or commit it to GitHub. On Render Free, re-upload it after a restart/redeploy because the service filesystem is ephemeral.

## Upload and storage limits

- Main video uploads default to **300 MB**. The accepted file extension, content type, FFprobe video stream, dimensions, and duration are checked. Override with `NEONCLIPPING_MAX_UPLOAD_MB` (10–700 MB).
- Image and audio style assets have their own 25 MB and 300 MB limits.
- The app defaults to an **800 MB managed-data budget** with a **96 MB reserve**. These can be changed using `NEONCLIPPING_STORAGE_BUDGET_MB` and `NEONCLIPPING_STORAGE_RESERVE_MB`.
- Before a large operation, the app checks the managed budget, physical free space, and an estimated render output size. Live recordings also stop if they approach the configured cap. Low-space requests fail with a clear message instead of silently removing user files.
- Stale `.part`, `.ytdl`, `.tmp`, upload staging files, temporary YouTube cookie copies, low-resolution analysis copies, and old preview-frame folders are disposable and may be removed after 24 hours. Per-job YouTube cookie copies are normally removed as soon as processing ends. The manual cleanup button only removes those temporary items. Source videos, rendered clips, saved YouTube cookie configuration, captions, style assets, creator settings, and feedback are never automatically deleted.
- Check `/storage` for the current storage estimate. Download or manually remove files when the data folder approaches its configured budget.

## Render deployment (Free tier)

The included `render.yaml` stays on Render's **Free** web-service plan. It configures the native Python service, FFmpeg, a health check, authentication, an 800 MB managed-data budget, and a 300 MB upload limit. It does not request a paid plan, paid add-on, or persistent disk.

**Free-tier storage is temporary.** Render Free web services have an ephemeral filesystem. Files in `NEONCLIPPING_DATA_DIR` can disappear when the service spins down, restarts, or redeploys. That includes uploaded sources, rendered clips, captions, style assets, creator settings, and the local queue database. The 800 MB quota protects the running instance from filling its working space; it does not make files durable. Keep local copies of clips you need. If durable hosted storage is needed later, choose and authorize a storage service separately before changing this configuration.

1. Push this project to a Git repository and create a Render Blueprint from its `render.yaml`.
2. Keep the service on **Free**; the repository configuration does not enable a paid tier.
3. Set `NEONCLIPPING_AUTH_PASSWORD` in the Render Dashboard's Environment page to a unique password of at least 16 characters. `sync: false` asks for the secret during initial Blueprint creation; for an existing service, add it manually in the Dashboard.
4. Deploy from the Render Dashboard. Confirm the service reports healthy at `/healthz`, sign in at the service URL, then test a small upload and export.

Free web services may spin down after inactivity, take time to wake up, and have limited CPU, memory, processing time, and temporary storage. Large FFmpeg renders and transcription can be slow or be interrupted. The app's in-memory live sessions and active job status also do not survive a restart. Render deployment was not initiated as part of this code change.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8770` locally; set by Render | HTTP listen port |
| `NEONCLIPPING_DATA_DIR` | `./data` | Location for sources, clips, assets, queue database, and app settings; ephemeral on Render Free |
| `NEONCLIPPING_REQUIRE_AUTH` | unset | Set to `1` to require the sign-in page |
| `NEONCLIPPING_AUTH_PASSWORD` | unset | Secret password; keep it in the host's secret settings |
| `NEONCLIPPING_LAN` | unset | Set to `1` to enable same-Wi-Fi phone remote on local runs |
| `NEONCLIPPING_MAX_UPLOAD_MB` | `300` | Main video upload limit, clamped to 10–700 MB |
| `NEONCLIPPING_STORAGE_BUDGET_MB` | `800` | Managed data-folder budget, clamped to 128–1024 MB |
| `NEONCLIPPING_STORAGE_RESERVE_MB` | `96` | Free-space safety margin, clamped to 32–512 MB |
| `NEONCLIPPING_DONATE_RAZORPAY` | unset | Optional HTTPS donation URL |
| `NEONCLIPPING_DONATE_PAYPAL` | unset | Optional HTTPS donation URL |

## Main workflow

1. Paste a replay link or upload a video.
2. Select clip count, length, aspect ratio, resolution, frame rate, layout, and caption options.
3. Use **Make clips** for the one-click flow, or import the source and use **Find highlights** to review candidate moments.
4. Adjust the crop boxes and time range. Generate, import, or edit subtitles and hooks.
5. Export the clip. Download the MP4, subtitles, or a ZIP bundle from the library.

For live streams, use **Live clipping** or add a creator to the watcher list. Live capture requires the stream to be accessible through Streamlink/yt-dlp; chat availability and platform changes can affect results.

## Project map

- `app.py` — FastAPI routes, source validation, clip export, and live-session orchestration.
- `standalone.py` — Link import, preview, local analysis, and source validation.
- `app.js`, `extras.js`, `transcripts.js`, `vision.js`, `security.js`, `storage.js` — browser editor behavior and progressive enhancements.
- `index.html`, `style.css`, `mobile.html`, `mobile.js` — desktop dashboard and responsive phone remote.
- `layouts.py`, `captions.py`, `styling.py`, `brands.py`, `hooks.py`, `subtitles.py` — rendering layouts, captions, optional design elements, and subtitle files.
- `smart.py`, `highlights.py`, `action.py`, `curation.py`, `excite.py`, `learn.py`, `vision.py`, `facecam_detect.py`, `translit.py` — moment ranking, optional local models, signal analysis, and learning from ratings.
- `live_session.py`, `creators.py` — live stream recording, chat spike monitoring, and creator watching.
- `jobs.py`, `reedit.py`, `bundles.py`, `transcripts.py`, `aiparts.py`, `previews.py`, `phone.py` — queue, saved-clip editing, ZIP downloads, transcript tools, previews, and phone authentication.
- `storage.py`, `security.py`, `youtube_cookies.py` — disk budget and safe temporary-file cleanup; public-site sign-in protections; private YouTube cookie upload and per-job handling.

## Troubleshooting

- **Upload rejected:** check the file format, size limit, and free space. A valid video needs a readable video stream and duration.
- **Render cannot start:** verify `NEONCLIPPING_AUTH_PASSWORD` exists, has at least 16 characters, and the service is on a plan that supports its disk.
- **Missing FFmpeg:** install FFmpeg/FFprobe locally and ensure both commands are on `PATH`; Render installs FFmpeg during the build.
- **No local visual review:** install Ollama and a compatible Gemma4 model on the same machine. Visual review does not automatically download model weights.
- **Platform import fails:** YouTube/Twitch/Kick may change access behavior. Update the pinned `yt-dlp` or `streamlink` version deliberately, then retest the import flow.
- **YouTube says “Sign in to confirm you're not a bot”:** in **Bring your video → YouTube cookies**, upload a fresh `cookies.txt` exported in Netscape format from a browser signed in to YouTube, then retry. The cookie session must remain valid; Twitch, Kick, and local video clipping do not need this setting.
- **Phone page unavailable:** enable `NEONCLIPPING_LAN=1`, start the local app, connect the phone to the same Wi-Fi, and open the `/m` address shown by the computer. Phone remote is not enabled for public Render access.

## Development checks

Run the repository checks from the project root:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py standalone.py storage.py security.py phone.py styling.py youtube_cookies.py tests
```

The unittest suite covers storage quotas, safe cleanup behavior, and sign-in protections. Manual media tests still require FFmpeg and a small valid video file.
