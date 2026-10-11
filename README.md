# ZENO

ZENO is a voice-first AI assistant with a team of worker agents. You talk to **ZENO**; for anything
bigger than a quick lookup it hands the job to a **Bit** (BIT-01, BIT-02, …), an AI worker that runs
in its own sandbox while ZENO keeps talking with you, and reports back when it's done.

```
                         ┌──────────────┐
   phone / browser ◄────►│     ZENO     │  voice (WebRTC) · memory · your apps (MCP)
                         └──────┬───────┘
                                │ start_bit · bit_status · stop_bit
              ┌─────────────────┼─────────────────┐
         ┌────┴────┐       ┌────┴────┐       ┌────┴────┐
         │ BIT-01  │       │ BIT-02  │       │ BIT-03  │   each: LLM agent loop + tools
         │ sandbox │       │ sandbox │       │ sandbox │   (Docker container)
         └────┬────┘       └────┬────┘       └────┬────┘
              └─────────────────┼─────────────────┘
                     one 10 GB storage pool per user
                   /workspace/bit-01  bit-02  bit-03  shared
```

| Folder | What it is |
| --- | --- |
| `server/` | FastAPI + Pipecat: voice pipeline, Bits, memory, MCP store, API |
| `web/` | The dashboard (React + Vite + Tailwind). The server serves its build, so one port serves everything |
| `deploy/` | systemd unit and Cloudflare Tunnel config for a VM |
| `~/Desktop/android/ORION` | The Android app (Kotlin + Compose); package `com.example.zeno`, app id `com.example.orion` |

## How it works

**ZENO** (`server/src/zeno_server/bot.py`) is a Pipecat voice pipeline: Silero VAD → Whisper →
LLM → Kokoro/Pocket TTS, over WebRTC. With an ElevenLabs key (Settings → Voice), ZENO can speak
with ElevenLabs voices and listen with ElevenLabs Scribe instead. ZENO does quick things itself (one lookup in an app, sending an
email) and hands everything else to a Bit with `start_bit`: attachments and files, research,
multi-step work, and anything about later ("remind me", "let me know when", "keep an eye on").
App lookups run in the background, so the conversation never stops.

**Bits** (`bits.py`, `bit_models.py`, `sandbox.py`, `gateway.py`) are LLM agent loops with free
rein over how they do a job:

- `run_command` runs bash/Python in the Bit's Docker sandbox (ZENO's own image: Python with
  requests, pandas, pypdf, Pillow, tesseract, ffmpeg, poppler, curl, git, …; `pip install` for more);
- `write_file`, `read_file`, `list_files`, `delete_file`, `share_file`, and `view_file` to look at
  images and PDFs;
- your connected apps: their tools directly, and from code through the gateway (`zeno tool …`,
  `zeno api gmail GET …` for an app's own API signed in as you, e.g. to download attachments);
- web search (Claude's own search for Claude Bits; DuckDuckGo from the sandbox otherwise);
- `save_tool` puts a script it wrote into the user's toolbox (`/workspace/tools`) for every Bit;
- `wait` (timers, reminders) and `watch` (re-runs a check until its output changes) keep a Bit
  going for hours or days without spending tokens; Bits that were waiting resume after a restart;
- `ask_user` asks you something and waits for the answer; `notify_user` tells you something now.

Bits and you talk both ways. A Bit's question is read out by ZENO (or shown on the dashboard and in
the app under **Waiting on you**); ZENO keeps a checklist and brings unanswered questions up again.
Your answer, new details for a working Bit, or a follow-up for a finished one ("now open that
attachment") go to the Bit with `message_bit`; a finished Bit picks up where it left off.

Each user gets one storage pool (`BIT_STORAGE_GB`, 10 GB by default) shared by all of their Bits:

- each Bit's container mounts only its own folder (read-write), `/workspace/shared` and the
  toolbox (read-only), and the user's installed packages;
- a Bit publishes finished files to `shared/` with `share_file`, which refuses to overwrite a file
  another Bit (or you) put there; you can add files there too, for a Bit to work with;
- Bits can chain: `start_bit(…, wait_for=["BIT-01"])` starts after BIT-01 and receives its report;
- containers are capped (`SANDBOX_CPUS`, `SANDBOX_MEMORY`, 256 processes, no capabilities,
  read-only image); the storage cap is enforced by ZENO (writes over it are refused, and a command
  that overfills the pool is stopped).

Every model call's tokens (in, out, cached) and every step are recorded; the dashboard's **Bits**
page shows Bits live, lets you answer, message or stop them, and lists the toolbox and shared files.

**Memory** (`memory.py`): after each voice session the LLM summarizes the conversation and updates
what ZENO knows about you (who you are, preferences, people, what you're working on). You can also
say “remember …” / “forget …”. Everything is visible and editable on the **Memory** page, and
learning can be turned off.

**AI models** (`llm.py`): ZENO and the Bits each have their own model, set on the dashboard under
**Settings → AI models**: Lemonade or Ollama (local), Google Gemini, Anthropic Claude, OpenAI, or any
OpenAI-compatible server. API keys are stored encrypted with `SESSION_SECRET`. A good pairing is a
fast model for ZENO (e.g. Gemini Flash) and a strong one for the Bits (e.g. Claude Opus).

## Run it locally (Mac)

```bash
cd server
cp .env.example .env        # fill in GOOGLE_CLIENT_IDS, SESSION_SECRET, Pipedream keys
uv sync
uv run zeno-server          # http://localhost:7860
```

```bash
cd web
npm install
npm run dev                 # http://localhost:5180 (proxies /api to :7860)
npm run build               # builds web/dist, which the server then serves on :7860
```

Bits need Docker for their sandboxes (Docker Desktop on a Mac). On first start ZENO builds its
sandbox image (a few minutes; Bits use `python:3.12-slim` meanwhile). Without Docker, Bits still
work with files and your apps, but can't run commands or keep watch.

## Ship it to a VM behind a Cloudflare Tunnel

1. **VM**: Ubuntu/Debian with a persistent disk. Install `uv`, Node 20+, Docker, and `cloudflared`.
   Create a `zeno` user and add it to the `docker` group.
2. **Code**: clone to `/opt/zeno`, then `cd server && uv sync` and `cd ../web && npm ci && npm run build`.
   On Linux, speech-to-text uses faster-whisper automatically (`STT_ENGINE=auto`); the first start
   downloads its model.
3. **Config**: `cp server/.env.example server/.env` and set:
   - `SESSION_SECRET` (new random value), `GOOGLE_CLIENT_IDS`, `ALLOWED_EMAILS`, `ADMIN_EMAILS`;
   - `CORS_ORIGINS=https://zeno.example.com`;
   - the LLM settings (or set models later on the dashboard);
   - `BIT_GATEWAY_URL=https://zeno.example.com` (already in `deploy/zeno.service`): code in Bit
     sandboxes reaches ZENO's gateway through the tunnel, since ZENO itself listens on localhost.
   In Google Cloud, add `https://zeno.example.com` to the web OAuth client's authorized JavaScript
   origins.
4. **Service**: `sudo cp deploy/zeno.service /etc/systemd/system/ && sudo systemctl enable --now zeno`
   (data goes to `DATA_DIR=/var/lib/zeno`).
5. **Tunnel**: follow `deploy/cloudflared.yml` to route `zeno.example.com` → `http://localhost:7860`.
6. **Voice through the tunnel**: a tunnel carries HTTP, not WebRTC's UDP audio, so add a TURN relay.
   Easiest: in the Cloudflare dashboard create a TURN key (Realtime → TURN) and set
   `CLOUDFLARE_TURN_KEY_ID` and `CLOUDFLARE_TURN_API_TOKEN`; ZENO hands fresh credentials to the web
   and Android apps for each call. (Or run coturn and set `TURN_URLS`, `TURN_USERNAME`,
   `TURN_CREDENTIAL`.)
7. **Android**: in the app's sign-in screen (or Settings), set the server to `https://zeno.example.com`.

## Growing with users

Everything is per user already: Google sign-in, MCP connectors, Bits, workspaces and storage pools,
memory and conversations. To grow further:

- **More sign-in options**: `auth.py` turns a provider's identity into a ZENO user and session; add
  providers next to Google (`sign_in_with_*`) without touching the rest.
- **Database**: SQLite (`db.py`) is fine for one VM. For several servers, move `db.py` to Postgres;
  all access goes through that module.
- **Sandboxes**: `sandbox.py` talks to Docker through one class; swap in Kubernetes Jobs or a hosted
  sandbox for more isolation or scale. For a hard per-user disk cap, give each user's pool its own
  volume with a filesystem quota.
- **Limits**: `MAX_BITS`, `BIT_STORAGE_GB`, `BIT_MAX_TOKENS` are per user; per-plan limits would
  live next to them.
