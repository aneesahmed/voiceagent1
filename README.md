# Voice AI Agent -- Patient Registration

A phone-reachable voice agent that conversationally registers new patients:
collects standard U.S. demographic fields, confirms them back, saves the
record to a database, and exposes it through a REST API. Also reachable as
a browser pseudo-call (no phone needed) and, once configured, over a real
Twilio phone number or WhatsApp.

This started as a Meridian ERP sales-assistant project; the reusable parts
(speech-to-text/text-to-speech pipeline, Twilio Media Streams bridge,
browser call UI, persona registry) were kept, and a new `patient_intake`
persona was swapped in as the live/default persona. See
[`CLAUDE.md`](CLAUDE.md) decision #18 for the full history and every
architecture decision made along the way.

## Architecture

```
Phone call (Twilio) ---\
                         >--> app/call_engine.py (transport-agnostic) --> ChatEngine (Gemini)
Browser call (/audio) --/            |                                        |
                                stt.transcribe()                    function-calling tools
                                tts.synthesize()                    (find_patient_by_phone,
                                                                       save_patient)
                                                                            |
                                                                   app/patients_service.py
                                                                            |
                                                                  SQLite (backend/data/patients.db)
                                                                            |
                                                          app/patients_routes.py (REST API)
```

- **Telephony/voice transport** (`app/main.py`'s `/audio` websocket,
  `app/integrations/twilio_voice.py`) is transport-specific only at the
  wire-format boundary (mu-law codec, silence/barge-in detection for
  Twilio's server-side stream). Both call `app/call_engine.py`'s
  `process_turn()`, which is identical for every transport.
- **LLM logic** (`app/chat_engine.py`) grounds replies in a persona-specific
  system prompt (`app/prompts/templates/*.yaml`, loaded via
  `app/prompts/prompt_manager.py`) and, for `patient_intake` only, runs a
  Gemini function-calling loop against two tools
  (`app/patient_tools.py`): `find_patient_by_phone` (duplicate-caller
  detection) and `save_patient` (validate + persist).
- **Data layer** (`app/patients_db.py`, `app/patients_service.py`) is the
  single place that reads/writes SQLite. Both the REST API and the voice
  agent's tools call the service layer directly (in-process function
  calls, not the agent looping back over HTTP to its own server).
- **API layer** (`app/patients_routes.py`) is a thin FastAPI router over
  the service layer, with its own pydantic validation
  (`app/patients_schemas.py`) independent of whatever the voice agent
  already checked.

## Tech stack (and why)

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python), `uv`-managed | Async-native, matches the existing STT/TTS/websocket code already in this repo |
| LLM | Gemini (`google-genai`) | Already wired up (STT/TTS also use Gemini); native function-calling support for the save/lookup tools |
| Telephony | Twilio Media Streams | Real dialable number + raw audio streaming; existing bridge in `app/integrations/twilio_voice.py` needed no changes for this feature |
| Database | SQLite (stdlib `sqlite3`, no ORM) | Zero setup, a single file, survives restarts -- more than enough for this scale. A deliberate trade-off: Postgres would add a hosted-DB dependency for no real benefit here (see Known Limitations) |
| Frontend | React + TypeScript (Vite) | Already built for the browser pseudo-call; unchanged for this feature beyond a couple of label tweaks |
| Validation | Hand-written pydantic validators (regex-based) | Avoids adding an `email-validator`/phone-number dependency for a handful of simple US-specific formats |

## Setup

### Backend
```bash
cd backend
uv sync
cp .env.example .env   # then fill in GEMINI_API_KEY (see below)
uv run uvicorn app.main:app --reload --port 8001
```
The patients database is created automatically at
`backend/data/patients.db` on first startup (with 2 seed records) --
nothing to run manually.

### Web UI (primary way to test -- no npm/build step)
Once the backend is running, open **http://localhost:8001/** in Chrome or
Edge -- a landing page with three tiles, each with a live "ready?" status:

- **Configuration & Setup** (`/settings-ui`) -- credentials, help/how-to,
  and the Twilio webhook URL + instructions. Start here.
- **Web Testing** (`/web-testing`) -- click Start Call, allow the
  microphone, and talk. A single static page
  (`backend/app/static/index.html`) served directly by FastAPI, mirroring
  the React app's exact call protocol (see "Known limitations" for why
  this exists), with a live patients panel on the left.
- **Twilio Testing** (`/twilio-test`) -- the real Twilio phone path, or a
  browser WebRTC call with no phone needed (see below).

### Frontend (separate Vite+React app, optional)
```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```
This talks to the same backend over the `/audio` websocket and `/chat`/
`/patients` REST endpoints -- functionally equivalent to the page above,
just a fuller React UI. Not required for testing; see "Known limitations."

### Testing without audio
`POST http://localhost:8001/chat` with `{"message": "...", "persona": "patient_intake"}`
drives the same conversation engine (including the save/lookup tools) over
plain text -- useful for testing the intake flow without a microphone.

### Testing the API directly
```bash
curl http://localhost:8001/patients
curl -X POST http://localhost:8001/patients -H 'Content-Type: application/json' -d '{
  "first_name": "Alex", "last_name": "Rivera", "date_of_birth": "1992-04-01",
  "sex": "Other", "phone_number": "5125550100",
  "address_line_1": "100 Congress Ave", "city": "Austin", "state": "TX", "zip_code": "78701"
}'
```

### Settings UI -- credentials without hand-editing .env
Open **http://localhost:8001/settings-ui**. Every credential below (Gemini
key, Twilio SID/tokens, phone number) can be entered there instead of
editing `backend/.env` directly -- saving updates the running server
immediately (no restart) and persists to `.env` for next time. It also
shows the exact Voice webhook URL to paste into Twilio (auto-detected from
whatever host you're reaching this page through -- see "Deploying"), and a
"Create/Update TwiML App" button for the browser test caller below.

### Connecting a real phone number (Twilio)
1. Create a Twilio account and buy a U.S. number (trial accounts work for testing).
2. Run a public tunnel to your local backend, e.g. a Cloudflare quick
   tunnel (`cloudflared tunnel --url http://localhost:8001`, no account
   needed) or `ngrok http 8001`, and open `/settings-ui` **through that
   tunnel URL**, not `localhost` (e.g. `https://<tunnel>/settings-ui`) --
   the webhook URL it shows is auto-detected from whatever host you're
   viewing it through.
3. On `/settings-ui`, fill in `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
   and `TWILIO_PHONE_NUMBER`, then Save -- no restart needed. You do
   *not* need to set `PUBLIC_BASE_URL` (see "Deploying" below for why).
4. Copy the **Voice webhook URL** shown on that page into the Twilio
   console: Phone Numbers → Manage → Active Numbers → your number → Voice
   Configuration → "A call comes in", method **HTTP POST**.
5. Call the number. `app/integrations/twilio_voice.py` returns TwiML that opens a Media
   Stream to `wss://.../twilio/media-stream`, which bridges into the same `process_turn()`
   used by the browser call.

Until `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` are both set, `/twilio/voice`
answers with a TwiML message saying the assistant isn't configured yet,
instead of erroring.

### Testing Twilio without a real phone
Open **http://localhost:8001/twilio-test** -- a browser page that places a
WebRTC call (via Twilio's Voice SDK) straight into your own Twilio number,
bridging into the exact same inbound path a real phone call uses. Needs a
Twilio **API Key** (SID + Secret, from console.twilio.com → Account → Keys
& Credentials → API keys & tokens -- a different credential from the
Account SID/Auth Token pair) entered on `/settings-ui`, plus clicking
"Create/Update TwiML App" there once. Note: Twilio trial accounts prepend
a "press any key to continue" voice prompt to every outbound call
(including this one) -- the page auto-sends that keypress a couple of
seconds after connecting, with a manual button as a fallback.

### Deploying (e.g. Google Cloud Run)
This app never needs to know its own public URL ahead of time.
`app/request_utils.py` derives it from each incoming request's forwarded
host/proto headers instead of a hardcoded `PUBLIC_BASE_URL` -- Cloud Run
(and every tunnel used during development) terminates TLS in front of the
app and forwards the real external host, so this is correct automatically
and stays correct across revisions, redeploys, or a URL that only gets
assigned after the first deploy. Practical effect: **leave
`PUBLIC_BASE_URL` unset** in your Cloud Run service's environment
variables; just set `GEMINI_API_KEY` and whichever `TWILIO_*` vars you
need (or configure Twilio afterward from `/settings-ui`, same as local
dev). See `CLAUDE.md` decision #23 for the full reasoning and how it was
verified.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Yes | LLM (chat), STT, and TTS all go through Gemini. Server fails fast at startup if missing. |
| `CHAT_MODEL`, `STT_MODEL`, `TTS_MODEL`, `TTS_VOICE` | No | Defaults in `.env.example`; override to change models/voice. |
| `DB_PATH` | No | Defaults to `backend/data/patients.db`. |
| `HOST`, `PORT` | No | Server bind address; backend runs on **8001**, not FastAPI's usual 8000 (port 8000 is used by an unrelated local project). |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | No | Blank until a real Twilio number is set up (see above); `/twilio/voice` degrades gracefully until then. Settable from `/settings-ui`. |
| `PUBLIC_BASE_URL` | No | **Leave this unset in most cases, including on Cloud Run.** The app derives its own public URL from each incoming request (`app/request_utils.py`) instead of needing this hardcoded -- self-correcting across a local tunnel, Cloud Run, or any other host, even across redeploys to a new URL. Only set this to force a specific value if request-derivation is ever wrong. |
| `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_TWIML_APP_SID` | No | Only for the browser WebRTC test caller (`/twilio-test`); the App SID is normally set automatically by the "Create/Update TwiML App" button on `/settings-ui`. |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` | No | Same idea for the WhatsApp channel; not required for this assessment. |

## Data model

`patients` (SQLite, `backend/app/patients_db.py`): `patient_id` (UUID,
primary key), `first_name`, `last_name`, `date_of_birth`, `sex` (enum),
`phone_number`, `email` (optional), `address_line_1`, `address_line_2`
(optional), `city`, `state`, `zip_code`, `insurance_provider` (optional),
`insurance_member_id` (optional), `preferred_language` (default
`"English"`), `emergency_contact_name`/`emergency_contact_phone`
(optional), `created_at`/`updated_at` (auto), `deleted_at` (soft-delete
marker, null for active records).

All columns besides `patient_id`/timestamps are nullable at the DB level
(a voice-agent-created "draft" row can be genuinely partial -- see
"Conversation design" below), but `POST /patients` still requires every
`Yes`-required field per the table in "API" -- that contract didn't
change. Every patient response includes a derived `is_complete: bool` so
API/UI consumers can tell a finished registration from an in-progress one.

## API

All responses use `{ "data": ..., "error": ... }`.

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/patients` | Optional `?last_name=`, `?date_of_birth=`, `?phone_number=` filters |
| GET | `/patients/:id` | 404 if not found (or soft-deleted) |
| POST | `/patients` | 201 on success; 409 with the existing record if the phone number already belongs to an active patient; 422 on invalid input |
| PUT | `/patients/:id` | Partial updates -- only send the fields you want to change |
| DELETE | `/patients/:id` | Soft-delete (`deleted_at` set); never a hard delete |

## Conversation design

The full system prompt is
[`backend/app/prompts/templates/patient_intake.yaml`](backend/app/prompts/templates/patient_intake.yaml),
with each design choice commented inline. Highlights:
- **The agent speaks first.** As soon as the call connects, it greets the
  caller and asks for (or confirms, if Caller ID is known) their phone
  number -- no dead air waiting for the caller to speak first, which is
  the opposite of how a real phone agent behaves. See `CLAUDE.md` decision
  #26 for how this reuses the existing barge-in machinery so the greeting
  can be talked over too.
- **Phone number is asked first**, before anything else, so
  `find_patient_by_phone` can run immediately and branch the call:
  brand-new caller, returning caller with a complete record on file (offer
  to update instead of re-registering -- the bonus "duplicate detection"
  behavior), or returning caller with an *incomplete* record from a call
  that got cut off (ask only for what's still missing).
- **Saves happen incrementally**, not just once at the end -- right after
  the phone number, then again after each field/group as it's collected.
  If the call drops mid-registration, the next call's
  `find_patient_by_phone` finds the partial record and resumes from
  exactly where it left off, instead of starting over or losing the data.
  See `CLAUDE.md` decision #20 for how this changed the DB schema (nullable
  columns for a legitimate "draft" state) and the API contract
  (`is_complete` on every patient record).
- Required fields can be given in any order beyond phone number (no rigid
  script); optional fields (insurance, emergency contact, preferred
  language) are offered as one opt-in question once required fields are
  in.
- A final read-back explicitly confirms every field before ending the
  call; corrections update the agent's understanding and get re-saved.
- **Caller ID (ANI) confirmation**: a real Twilio call reports the
  caller's number for free (`From` on the voice webhook) -- so instead of
  asking for it cold, the agent opens by confirming that number. The
  browser test UI has no real ANI, so it asks for your number up front
  (optional) and passes it through the same path, purely to mimic what a
  real phone call provides. See `CLAUDE.md` decision #21.
- A `save_patient` validation failure is translated into plain language
  and re-prompted for just that field -- the caller never hears a raw
  field name or stack trace.
- "Start over" drops everything collected so far rather than merging old
  and new answers.

## Known limitations / trade-offs

- **Credentials are stored in plaintext `.env`**, whether hand-edited or
  saved via `/settings-ui` -- fine for local dev/demo (the file is
  gitignored and never leaves the machine), not a pattern to carry into a
  real deployment (would want a secrets manager instead).
- The server no longer crashes at startup on a bad `GEMINI_API_KEY` (a bad
  key used to crash the filler-audio TTS warm-cache call and take the
  whole app down with it -- see `CLAUDE.md` decision #19); it logs a
  warning and keeps serving the UI/API, only a real conversation turn
  would fail. Most settings (all `TWILIO_*` vars, saved via `/settings-ui`)
  take effect immediately with no restart; `GEMINI_API_KEY` also updates
  live now (see decision #22's `gemini_client.py`).
- **The `frontend/` (Vite+React) app's local `node_modules` was missing
  Windows shims for `tsc`/`vite`** (`npm run dev`/`build` failed with
  "'vite' is not recognized"), unrelated to any code change here. Rather
  than debug that toolchain, `backend/app/static/index.html` -- a
  single-file vanilla-JS page with no build step, served at `GET /` --
  became the primary way to test a call (see decision #19). `frontend/`
  is untouched and should work once reinstalled cleanly (`rm -rf
  node_modules package-lock.json && npm install`, from a native Windows
  shell rather than Git Bash/WSL).
- **SQLite, not Postgres.** Fine for a single-instance demo; would need a
  real connection-pooled DB for concurrent writers or multi-instance
  deployment.
- **No conversation transcript/call recording storage** -- only the final
  collected payload is logged (to stdout), per the assessment's minimum
  observability bar. Linking a full transcript to each patient record is
  listed as a bonus in the assessment and wasn't built.
- **No appointment scheduling or multi-language switching** -- both listed
  as bonus/optional in the assessment; not built here to keep the core
  requirements solid within the time available. (A patients dashboard,
  also a bonus item, *is* built -- the left panel on `/` and
  `frontend/src/PatientsPanel.tsx`.)
- **Twilio trial-account quirk**: every outbound call a trial account
  places (including the browser test caller's bridge-to-your-own-number
  call) gets a "press any key to continue" prompt prepended by Twilio
  itself. `/twilio-test` auto-sends that keypress, but a real caller
  dialing in directly never hits this (it only applies to calls *Twilio*
  places on your behalf, not inbound calls from any phone) -- upgrading
  the Twilio account removes it entirely if it becomes annoying during
  demos.
- **No automated test suite** -- the API and tool-dispatch logic were
  verified manually end-to-end (SQLite persistence across process
  restarts, all 5 REST endpoints, validation, duplicate detection) during
  development; nothing is wired into CI.
- **CORS is wide open (`allow_origins=["*"]`)** -- fine for local dev,
  should be tightened before any real deployment.
- **Not HIPAA-compliant and not meant to be** -- per the assessment's own
  FAQ, this stores fictional/test data only.

## Next steps

- Get a real Twilio trial number + `ngrok` tunnel running and call it live
  end-to-end (blocked on a valid `GEMINI_API_KEY` first).
- A minimal patients-list dashboard panel (bonus item) -- the frontend
  already has a sidebar-tile pattern (`KBPanel.tsx`) that would extend
  cleanly to a read-only patients table.
- Deploy the backend somewhere reachable 24/7 (Railway/Render/Fly.io) so
  the Twilio webhook doesn't depend on a local tunnel staying up.
