# Meridian ERP Voice Assistant -- Project Context

This file is for Claude Code. Read it before making changes. It captures
decisions already made so they aren't re-litigated, and the exact current
state so work continues rather than restarts.

## What this project is

A voice assistant platform, launched with one persona/workflow --
**AI Sales Agent** for a fictional company, **Meridian ERP Inc.**
(Canadian cloud ERP vendor, SME-focused), grounded on a small markdown
knowledge base. Users reach it through a web page: pick a persona, click
"Start Call," speak into the mic, the assistant transcribes, generates a
grounded reply, and speaks it back -- a browser-based pseudo phone call.

The product roadmap has 15 planned personas/features (AI Sales Agent, AI
Customer Support Agent, Knowledge Base Integration, CRM Integration,
Appointment Scheduling, Lead Capture, Analytics Dashboard, Admin Portal,
Multi-channel Messaging, Voice AI, Conversation Logs, Reporting Dashboard,
Human Escalation, API Integrations, Documentation/Training). They're built
**one at a time**; the persona picker UI shows all 15 but only implemented
ones are selectable. The full list lives in `backend/app/personas.py`, not
duplicated in this file -- check there for current status.

The assistant is also reachable over real channels now, not just the
browser: a real phone call via **Twilio** (Media Streams) and **WhatsApp**
(Business Cloud API) both route into the same conversation engine as the
browser call. Both are functional but **unconfigured by default** --
no credentials are set up yet (see "Environment / config"). WhatsApp Web
browser-automation (e.g. via Playwright) was considered and explicitly
rejected -- see decision #15.

## Repo layout

```
voiceagent/
├── backend/              # FastAPI, Python, managed by uv (NOT the repo root)
│   ├── app/
│   │   ├── main.py           # FastAPI app: /health, /chat, /audio, /personas, includes kb/twilio/whatsapp routers
│   │   ├── config.py         # Settings, loaded from .env
│   │   ├── personas.py       # PERSONA_REGISTRY -- the 15-item roadmap list + which are available
│   │   ├── chat_engine.py    # ChatEngine class -- multi-turn conversation, one instance per call, persona-aware
│   │   ├── call_engine.py    # process_turn() -- transport-agnostic turn logic shared by /audio and Twilio
│   │   ├── rag.py            # load_kb() -- naive full-KB-in-context grounding
│   │   ├── stt.py            # Gemini speech-to-text: transcribe(pcm_bytes) -> str
│   │   ├── tts.py            # Gemini text-to-speech: synthesize(text) -> pcm_bytes
│   │   ├── kb_routes.py       # GET/PUT /kb -- view+edit the KB markdown files from the UI
│   │   ├── integrations/
│   │   │   ├── twilio_voice.py   # /twilio/voice (TwiML) + /twilio/media-stream (websocket), mu-law codec
│   │   │   └── whatsapp.py       # /whatsapp/webhook -- Meta Cloud API, text-based
│   │   └── prompts/
│   │       ├── prompt_manager.py       # PromptManager registry (see "Prompt management" below)
│   │       └── templates/
│   │           └── meridian_assistant.yaml
│   ├── kb/                   # the knowledge base -- 4 markdown files, real content, editable via /kb
│   │   ├── company_profile.md
│   │   ├── pricing_and_plans.md
│   │   ├── faq.md
│   │   └── policies.md
│   ├── pyproject.toml        # uv-managed, dependencies for backend ONLY
│   ├── .env                  # real GEMINI_API_KEY + optional Twilio/WhatsApp keys go here (gitignored)
│   └── .env.example
│
├── frontend/              # Vite + React + TypeScript, managed by npm
│   └── src/
│       ├── types.ts           # CallStatus, Persona, adapter callback signatures
│       ├── CallAdapter.ts     # WebSocket audio transport, barge-in, silence detection, filler playback
│       ├── App.tsx            # single-page 3-column layout: KBPanel | call UI | IntegrationsPanel (no tabs)
│       ├── PersonaPicker.tsx  # landing page (center column) -- fetches /personas, only available ones clickable
│       ├── CallScreen.tsx     # the actual call UI (mic meter, log, Start/End Call, "I'm Done Talking"), persona-scoped
│       ├── KBPanel.tsx        # left sidebar -- scrollable collapsible tiles, view/edit KB files (GET/PUT /kb)
│       ├── IntegrationsPanel.tsx  # right sidebar -- collapsible tiles: call flow, Twilio setup, WhatsApp setup
│       └── main.tsx           # entry point
│
└── README.md
```

## Architecture decisions already made (do not redesign these without being asked)

1. **No multi-agent "factory" pattern.** An earlier design pass explored a
   `agent-factory/` structure with `components/` + `products/` +
   `persona.yaml` configs, meant to support many future agent types
   (sales, support, teaching, document-summarizer, etc.) built from shared
   components. **This was deliberately abandoned as overengineered** for a
   project that currently has exactly one agent. Do not reintroduce it
   unless explicitly asked.
   **Update:** a multi-persona roadmap is now real (see decision #12) --
   that does NOT reverse this decision. The persona registry is one flat
   dict (`app/personas.py`) plus one extra `persona` parameter threaded
   through the existing single-endpoint code path -- not a new
   `components/`+`products/` abstraction layer, not per-persona endpoint
   duplication. If a persona ever needs genuinely different *logic* (not
   just a different prompt/KB), that's the point to revisit this decision
   deliberately -- don't back into a factory pattern by accretion.

2. **`app/` not `src/` for the backend package name.** Matches FastAPI's
   own convention; this is a single deployable app, not a distributable
   library, so `src/`-layout's main benefit (avoiding accidental local
   imports of unpackaged code) doesn't apply here.

3. **Two fully independent toolchains, not one.** `backend/` has its own
   `pyproject.toml` and is `uv`-managed. `frontend/` has its own
   `package.json` and is `npm`-managed. Neither toolchain's root is the
   repo root (`voiceagent/`) -- each lives inside its own subfolder. If
   `uv` ever reports picking up a "workspace" unexpectedly, check for a
   stray `pyproject.toml` one level up.

4. **Prompt management: registry pattern, not hardcoded strings or plain
   `.md`.** `app/prompts/templates/*.yaml` holds prompt content (with
   `description:` + `template:` keys), loaded by
   `app/prompts/prompt_manager.py`'s `PromptManager` class into an
   in-memory registry, retrieved via
   `prompt_manager.get_prompt(key, **kwargs)` with `.format()`-style
   variable interpolation (e.g. `{kb_context}`). Chosen over plain
   hardcoded prompt strings because it supports variable interpolation
   with validation, groups related prompts with metadata, and scales to
   more prompts with zero new loading code. Chosen over bare `.md` files
   because `.md` has no structured place for variables or metadata.

5. **RAG is intentionally naive (V1).** `rag.py`'s `load_kb()` reads every
   `.md` file in `kb/` and concatenates them in full into the system
   prompt -- no embeddings, no vector search. This is correct for now
   (KB is ~4 short files, fits easily in context) and should only be
   replaced with real retrieval once the KB grows large enough that it
   no longer fits in context. Don't add embeddings speculatively.

6. **Audio format: 8kHz / 16-bit / mono PCM, everywhere.** Every
   transport (today's web pseudo-call, any future telephony transport)
   uses this format as the common contract. Gemini TTS natively returns
   24kHz PCM; `tts.py` does a manual 3:1 averaging decimation down to
   8kHz (no `audioop`, since it was removed in Python 3.13).

7. **`stt.transcribe()` and `tts.synthesize()` work on in-memory bytes,
   not file paths.** Earlier prototypes wrote WAV files to disk; the
   current versions take/return raw bytes so the websocket handler in
   `main.py` never touches the filesystem per-turn.

8. **`ChatEngine` is instantiated once per call/session**, not shared
   across callers -- it holds that session's `history` list internally.
   `/chat` (HTTP) currently creates a fresh instance per request (no
   memory across HTTP calls yet -- fine for endpoint testing, not meant
   to be multi-turn over plain HTTP). `/audio` (websocket) creates one
   instance per connection and keeps it for the call's duration.

9. **`/audio` websocket protocol:** client streams raw PCM16 binary
   frames continuously while the user speaks, then sends a text frame
   `{"event": "end_of_turn"}` when the user stops. Server transcribes the
   buffered audio, generates a reply, synthesizes it, and streams PCM16
   binary frames back (640-byte frames = 40ms @ 8kHz), followed by a text
   frame `{"event": "reply_end"}`. **The frontend has not been built yet
   and must implement this exact protocol**, including client-side
   silence detection to trigger `end_of_turn` -- this doesn't exist
   anywhere yet.

10. **CORS is wide open (`allow_origins=["*"]`) in `main.py`** for local
    dev convenience since frontend (Vite, port 5173 by default) and
    backend run on different origins. Tighten before any real deployment.

11. **Backend runs on port 8001, not FastAPI's usual 8000.** Port 8000 on
    this machine is permanently occupied by an unrelated project
    (`briefcast`, a separate local FastAPI app under `/working/AI26/briefcast`)
    that's routinely left running. Always start voiceagent's backend with
    `--port 8001` and hit it at `localhost:8001`. Note `config.py`'s
    `PORT` setting is *not* actually wired to the `uvicorn` CLI invocation
    (`main.py` has no `if __name__ == "__main__"` block) -- the `.env`
    `PORT` value is cosmetic/for-future-use only. The `--port` flag on the
    command line is what actually matters today.

12. **Persona routing: one parameterized endpoint set, keyed registry --
    not per-persona routes.** `app/personas.py` holds `PERSONA_REGISTRY`,
    a flat dict of all 15 roadmap items (`key`, `label`, `description`,
    `available`, `prompt_key`). `/chat` takes a `persona` field, `/audio`
    takes a `?persona=` query param, both default to `"sales"` and reject
    (400 / close 4004) any persona that isn't `available`. `ChatEngine`
    looks up the persona's `prompt_key` in the existing `PromptManager`
    registry (decision #4) -- adding a persona later means one new YAML
    template + one new `Persona(..., available=True)` entry, not a new
    route or duplicated handler. Only `"sales"` is `available` today.
    `GET /personas` exposes the registry so the frontend's persona-picker
    doesn't hardcode the roadmap list independently.

13. **`app/call_engine.py`'s `process_turn()` is transport-agnostic and
    shared.** Originally lived inline in `main.py`'s `audio_ws`; pulled
    out so the Twilio integration (decision #15) doesn't duplicate the
    transcribe -> reply -> synthesize -> stream -> interrupt-check logic.
    It takes plain 8kHz/16-bit PCM plus two callables -- `send_frame`
    (outgoing audio) and `send_event` (out-of-band signaling like
    `reply_end`/`interrupted`/`error`) -- so each transport only has to
    adapt its own wire format at the boundary (e.g. Twilio's mu-law codec
    lives entirely in `twilio_voice.py`, never in `call_engine.py`).

14. **KB is editable from the UI, not just readable.** `app/kb_routes.py`
    exposes `GET /kb` (list all `kb/*.md` files + content) and
    `PUT /kb/{filename}` (overwrite one file's content), used by
    `KBPanel.tsx`. Filename is validated to be a plain `*.md` name
    directly inside `KB_DIR` (no path separators, resolved path must
    stay under `KB_DIR`) to block path traversal. This writes straight to
    the same files `rag.py`'s `load_kb()` reads -- no caching layer, no
    versioning; the next `ChatEngine()` construction (i.e. next call)
    picks up whatever was last saved.

15. **Twilio and WhatsApp Business API integrations are real code, but
    unconfigured by default.** Both route into the same `ChatEngine` /
    `process_turn` core as the browser call:
    - **Twilio** (`app/integrations/twilio_voice.py`): `POST /twilio/voice`
      returns TwiML that connects a Media Stream; `WS /twilio/media-stream`
      bridges that stream through `process_turn`. Twilio streams 8kHz
      mu-law, matching this project's 8kHz contract (decision #6) except
      mu-law-companded -- a small hand-rolled G.711 codec handles that (no
      `audioop`, same reason as `tts.py`'s resampler). Since a real caller
      has no client-side JS, this module does its own RMS-based
      end-of-turn/barge-in detection server-side, mirroring
      `CallAdapter.ts`'s thresholds. Gated on `settings.twilio_configured`
      (`TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + `PUBLIC_BASE_URL` all
      set) -- until then `/twilio/voice` just speaks a "not configured"
      TwiML message.
    - **WhatsApp** (`app/integrations/whatsapp.py`): `GET/POST
      /whatsapp/webhook`, Meta Cloud API, **text only** (no voice notes
      yet). One `ChatEngine` per sender phone number, kept in an in-memory
      dict for the process's lifetime (same "session per caller" model as
      `/audio`, just keyed differently). Gated on
      `settings.whatsapp_configured` (`WHATSAPP_ACCESS_TOKEN` +
      `WHATSAPP_PHONE_NUMBER_ID` set).
    - **WhatsApp Web browser automation (e.g. Playwright) was considered
      and rejected for production use.** Driving the real WhatsApp Web UI
      isn't an official API, breaks WhatsApp's Terms of Service, risks the
      number being banned, and breaks whenever Meta changes the web UI --
      don't build it as a production path. **Update (2026-08-12):** a
      *dev-only* Playwright path for local testing (to skip Meta app
      review/costs while developing) is being discussed but not yet
      scoped or built -- if picked up, keep it fully separate from
      `whatsapp.py`, behind its own opt-in flag, and never pointed at the
      real business number (use a disposable test number so a ban doesn't
      touch production). The tile for it was removed from
      `IntegrationsPanel.tsx` until that's actually built.
    - Neither integration has real credentials yet -- that's intentional,
      deferred until someone actually sets up a Twilio/Meta account. See
      `IntegrationsPanel.tsx` (the frontend's right-sidebar tiles) for the
      exact setup steps, and "Environment / config" below for the env vars.

16. **A cached filler line plays while a turn is being processed.**
    `app/filler_audio.py` synthesizes a small set of "one moment..."
    phrases once (via the existing `tts.synthesize`) and caches them in
    memory; `main.py`'s `lifespan` warms this cache at server startup so
    the first real call never pays synthesis latency for it.
    `call_engine.process_turn` streams a random cached phrase
    (`{"event":"filler_start"}` ... PCM frames ... `{"event":"filler_end"}`)
    as soon as the transcript is confirmed non-empty, *before* the slow
    LLM-reply + TTS-synthesis steps -- covering the ~1-3s gap that used to
    be dead air. On the frontend, `CallAdapter.ts` plays filler and reply
    audio through the same interruptible `AudioBufferSourceNode`
    (`playAudio`/`currentSource`), with a small pending-playback queue so
    a fast real reply arriving before the filler finishes queues behind
    it instead of overlapping. Barge-in during filler playback works
    identically to barge-in during the real reply -- no special-casing.

17. **Manual turn-end button, additive to silence detection.**
    `CallAdapter.endTurn()` sends the same `end_of_turn` signal the
    automatic `SILENCE_DURATION_MS` timer sends, just without waiting --
    wired to CallScreen's "I'm Done Talking" button (shown only while
    `status === "listening"`). Silence-based auto-detection is unchanged;
    this is purely an additional, faster way to trigger the same path.

18. **Persona swap for the Patient Registration coding challenge
    (2026-08-21): `sales` is now `available=False`, `patient_intake` is the
    new default/available persona.** A take-home assessment ("Voice AI
    Agent -- Patient Registration System") asked for a phone agent that
    conversationally collects U.S. patient demographics, persists them,
    and exposes them via a REST API. Rather than a separate project, it
    was built into this codebase, reusing the existing STT/TTS/Twilio/
    browser-call/persona-registry infrastructure (decisions #6-#13) and
    swapping which persona is live -- `sales` stays in `PERSONA_REGISTRY`
    (just unavailable) so it's a one-line revert. `patient_intake` isn't
    part of the original 15-item Meridian roadmap and doesn't need to be;
    it's the demo persona for this specific deliverable.
    New pieces, all backend-only (no frontend protocol changes -- persona
    picker/call screen already work generically off `GET /personas`):
    - `app/patients_db.py` -- SQLite (stdlib `sqlite3`, no ORM), file at
      `backend/data/patients.db`, schema matching the assessment's field
      list, 2 seed records, safe to call `init_db()` on every startup
      (skips seeding if rows already exist).
    - `app/patients_schemas.py` -- pydantic `PatientCreate`/`PatientUpdate`
      (validators shared as plain `_check_*` functions so full-required
      and partial-optional models enforce identical rules) /`PatientOut`.
      Server-side validation is authoritative regardless of what the voice
      agent already checked, per the brief's explicit requirement.
    - `app/patients_service.py` -- the one place that writes to the DB;
      both `app/patients_routes.py` (REST) and `app/patient_tools.py`
      (voice agent) call it directly in-process rather than the agent
      looping back over HTTP to its own server.
    - `app/patients_routes.py` -- `GET/POST/PUT/DELETE /patients[/:id]`,
      `{"data":..., "error":...}` envelope on every response (see
      `main.py`'s `HTTPException`/`RequestValidationError` handlers, which
      wrap every error into the same envelope project-wide). Delete is a
      soft-delete (`deleted_at`), never a hard delete.
    - `app/patient_tools.py` + a per-persona tools dict in
      `chat_engine.py` -- **this is the deliberate revisit flagged in
      decision #1/#12**: `patient_intake` needs genuinely different logic
      (Gemini function-calling, DB writes, structured validation), not
      just a different prompt/KB, so `ChatEngine.generate_reply()` grew an
      optional tool-calling loop (`find_patient_by_phone`,
      `save_patient`), gated by a `{persona_key: (tools, dispatch)}` dict.
      Personas without an entry (e.g. `sales`) are completely unaffected --
      this is one conditional branch inside the existing class, not a new
      `components/`+`products/` abstraction layer. If a third persona ever
      needs tools too, extend this dict; if tool-calling logic diverges
      enough to not fit one class cleanly, *that's* the point to revisit
      again, deliberately.
    - `app/prompts/templates/patient_intake.yaml` -- read-back/confirm
      before saving, spelled-correction handling, opt-in optional fields,
      duplicate-caller handling via `find_patient_by_phone`, "start over"
      handling, plain-language re-prompting on a validation failure
      (never expose raw field names like `zip_code` to the caller).
    Known gap: `GEMINI_API_KEY` in `backend/.env` was found to be invalid/
    expired during this work (fails at server startup on the filler-audio
    TTS warm-cache call, and would fail `/chat`/`/audio` identically) --
    pre-existing, unrelated to this change, but blocks live voice testing
    (including over Twilio) until replaced with a real key.

19. **Primary web testing now happens through a self-contained page served
    by FastAPI itself (`backend/app/static/index.html`, `GET /`), not the
    separate `frontend/` Vite+React app.** The `frontend/`'s installed
    `node_modules` turned out to be missing Windows `.cmd` shims for
    `tsc`/`vite` (`npm run dev`/`build` both failed with "'vite' is not
    recognized"), unrelated to any code change here -- likely installed in
    a different environment originally. Rather than fight that toolchain,
    `backend/app/static/index.html` is a single vanilla-JS/HTML/CSS file
    (no build step) that reimplements `frontend/src/CallAdapter.ts`'s
    exact `/audio` websocket protocol and thresholds (decision #9) plus a
    patients-list panel, served directly at `GET /` by `app/main.py`.
    `frontend/` is untouched and still the right long-term UI once its npm
    install is fixed (`rm -rf node_modules package-lock.json && npm
    install` from a native Windows shell) -- this isn't a decision to
    abandon it, just to unblock testing today. Run `uv run --directory
    backend uvicorn app.main:app --reload --port 8001` and open
    `http://localhost:8001/` -- API and UI are one process, so CORS no
    longer matters for this path (still wide open for `frontend/`, see
    decision #10).
    Also while chasing this: `app/main.py`'s `lifespan` no longer treats a
    failed filler-audio TTS warm-cache (e.g. from the bad `GEMINI_API_KEY`
    noted in decision #18) as fatal -- it logs and continues, so the REST
    API and static UI are reachable even before a working key is in place.
    A real call still needs a valid key; only server *startup* was the
    problem.

20. **Phone-number-first flow with incremental saves, for call-drop
    resilience (2026-08-21).** Originally `save_patient` was called once,
    only after full confirmation at the end of a call -- if the connection
    dropped mid-registration, everything collected was lost. Now:
    - The prompt (`patient_intake.yaml`) asks for the phone number FIRST,
      before anything else, specifically so `find_patient_by_phone` can
      run immediately and branch the conversation: brand-new caller,
      returning caller with a complete record (offer to update), or
      returning caller with an incomplete record from a dropped call
      (ask only for `missing_fields`, never re-ask what's already known).
    - `save_patient` (`app/patient_tools.py`) is now called incrementally
      -- right after the phone number, then again after each subsequent
      field/group -- not just once at the end, using `existing_patient_id`
      to keep updating the same row.
    - This required relaxing `patients` table columns (beyond
      `patient_id`/timestamps) from `NOT NULL` to nullable
      (`app/patients_db.py`) so a partial/"draft" row can exist at all.
      **Required-ness for a *finished* registration didn't go away** --
      it's enforced by `PatientCreate` (still fully required, still the
      only path `POST /patients` accepts -- the REST contract is
      unchanged) and by `patients_service.is_registration_complete()` /
      `missing_required_fields()`, the new single source of truth (backed
      by `patients_schemas.REQUIRED_FIELDS`) for whether a row counts as
      "done." `PatientOut` gained an `is_complete` field and its
      previously-required attributes became `Optional`, since a draft row
      can now legitimately have `None` for most fields and the API needs
      to serialize it without crashing.
    - New service function `create_draft_patient()` (partial insert, no
      validation beyond per-field format) and `upsert_patient_by_phone()`
      (the voice agent's one save primitive: update by
      `existing_patient_id` if given, else find-or-create by phone) --
      both used only by `patient_tools.py`, never by the REST routes.
    - `patient_tools.py`'s `save_patient` now validates with the already-
      lenient `PatientUpdate` (per-field, nothing required) instead of the
      strict `PatientCreate`, and its function-declaration schema dropped
      its `required` list accordingly.
    - Verified directly: a two-`ChatEngine` simulation (representing two
      separate calls) where call 1 stopped after phone+name+DOB, and call
      2 -- a fresh session, same phone number -- correctly resumed by
      asking only for the still-missing address, then completed
      registration (`is_complete` flipped `false` -> `true` on save).
    - Dashboards (`app/static/index.html` and `frontend/src/
      PatientsPanel.tsx`) show a "DRAFT" badge on incomplete records and
      tolerate `null` fields instead of rendering "null null".

21. **Caller ID (ANI) confirmation, mimicked in the browser UI
    (2026-08-21).** A real Twilio call reports the caller's number for
    free as `From` on the `/voice` webhook (standard Caller ID/ANI, no
    add-on) -- so instead of always asking for the phone number cold, the
    agent can open by confirming a number it already has.
    - `ChatEngine.__init__` gained an optional `caller_ani: str | None`
      param. When set, it's formatted into a `caller_ani_note` string
      injected into the prompt via the existing `PromptManager`
      interpolation (`{caller_ani_note}` in `patient_intake.yaml`) --
      instructing the agent to confirm that number instead of asking for
      it fresh. `None` (no Caller ID -- private/blocked number, or a
      transport that doesn't report one) falls back to asking normally.
    - `app/patients_schemas.py` gained `normalize_us_phone()`, shared by
      `_check_phone`, `patient_tools.py`'s phone lookup, and the Twilio/ANI
      wiring below -- strips non-digits and drops a leading country-code
      `1`, so Twilio's E.164 `+15551234567` and a bare 10-digit number
      normalize identically and actually match in the DB.
    - **Twilio** (`app/integrations/twilio_voice.py`): `/voice` reads
      `From` from the webhook's form body and threads it through as a
      `<Parameter name="callerNumber">` inside the TwiML `<Stream>` tag;
      `media_stream`'s websocket handler now constructs `ChatEngine` only
      once the `"start"` event arrives (not immediately on connect), so it
      can read `customParameters.callerNumber` out of that event first.
    - **Browser test UI** has no real ANI, so it mimics one: both
      `app/static/index.html` and `frontend/src/CallScreen.tsx` gained a
      "your phone number" input shown before Start Call (optional --
      blank just means no Caller ID, agent asks normally), sent as
      `?caller_number=` on the `/audio` websocket URL and read by
      `main.py`'s `audio_ws`. `POST /chat`'s `ChatRequest` also gained an
      optional `caller_number` field for text-only testing.
    - Verified directly: with `caller_ani` set, the agent's first line
      confirms the number ("Looks like you're calling from (512)
      555-0100 -- is that the right number for you?") instead of asking
      for it; confirming it moves straight to `find_patient_by_phone` and
      the next question, skipping the phone-number ask entirely.

22. **Settings UI + browser WebRTC test caller for Twilio, no terminal
    needed (2026-08-22).** Getting real Twilio credentials into `.env` by
    hand (and restarting the backend after every change) was the biggest
    source of friction while first wiring up Twilio -- this replaces most
    of that with self-service pages, since a hiring-challenge reviewer (or
    anyone else picking this up) shouldn't need Claude Code in the loop to
    connect their own Twilio account.
    - **`app/gemini_client.py`** -- `chat_engine.py`/`stt.py`/`tts.py` each
      used to build their own `genai.Client` once at import time, so a
      `GEMINI_API_KEY` change at runtime was silently ignored until a
      restart. `get_client()` centralizes this and rebuilds the client
      whenever `settings.GEMINI_API_KEY` no longer matches what it was
      built with -- a Settings-panel save takes effect on the very next
      call, no restart.
    - **`app/env_file.py`** -- `update_env_file()` surgically replaces (or
      appends) just the changed `KEY=VALUE` lines in `backend/.env`,
      leaving every comment and unrelated line untouched. Written because
      a naive "regenerate the whole file" approach would have destroyed
      all the setup comments already in there.
    - **`app/settings_routes.py`** -- `GET/PUT /settings` for
      `GEMINI_API_KEY` + every `TWILIO_*` var. Secrets (API key, Twilio
      Auth Token, Twilio API Key Secret) are never echoed back in full,
      only a last-4-chars preview; non-secret identifiers (Account SID,
      phone number, tunnel URL) are shown in full since they're not
      sensitive on their own. `PUT` updates the in-memory `settings`
      singleton immediately (every Twilio call site already reads
      `settings.X` fresh per-request -- nothing else was cached at import
      time) *and* persists via `env_file.py`, so it survives a restart
      too. `POST /settings/setup-twiml-app` runs the same one-time Twilio
      TwiML-App creation that used to require running
      `scripts/setup_twilio_client_calling.py` from a terminal.
    - **`app/integrations/twilio_client_calling.py`** -- new: a browser
      WebRTC test caller for testing the Twilio path without a real phone
      or a third-party calling app. `POST /twilio/access-token` issues a
      short-lived Access Token (via the Twilio *API Key*, a different
      credential from the Account SID/Auth Token pair) that
      `app/static/twilio_test_call.html` uses with Twilio's Voice SDK
      (loaded from jsDelivr's mirror of the `@twilio/voice-sdk` npm
      package -- Twilio's own `sdk.twilio.com` CDN path guessed from
      memory turned out to be a dead link, caught by directly curling it
      and getting a CloudFront/S3 403). `POST
      /twilio/browser-call-voice` is the TwiML App's Voice Request URL:
      it just `<Dial>`s our own Twilio number, bridging the WebRTC call
      into the exact same `/twilio/voice` inbound path a real phone call
      uses. The page also auto-sends a DTMF "1" ~2.5s after connecting
      (with a manual button as fallback) because Twilio trial accounts
      prepend a "press any key to continue" prompt to every outbound call
      they place -- including this Dial-to-your-own-number bridge -- and
      nothing on the automated backend side can press that key.
    - Caught and fixed one real bug this surfaced: extracting Twilio's
      `From` field via `await request.form()` in `/twilio/voice` (added
      for decision #21's Caller ID work) 500'd every real call with
      `AssertionError: python-multipart must be installed` -- Starlette's
      form parsing needs that package and it wasn't a dependency yet.
      Added it. This is why the very first real Twilio call attempt
      returned Twilio's generic "application error" message.
    - Three static pages now share a small top nav (`/` Web Testing,
      `/twilio-test` Twilio Test Caller, `/settings-ui` Settings) so
      they're discoverable from each other.

23. **`PUBLIC_BASE_URL` is now derived per-request, not hardcoded
    (2026-08-22) -- deployment-proofing ahead of a Cloud Run target.**
    Flagged mid-build: a static `PUBLIC_BASE_URL` in `.env` (a Cloudflare
    tunnel URL locally) would silently break the moment this runs
    somewhere whose URL isn't known ahead of time or can change between
    deploys -- Cloud Run being the explicit motivating case, but the same
    problem hits any redeploy-to-a-new-URL host.
    - `app/request_utils.py`'s `derive_base_url(request)` is the fix:
      whenever a request reaches this server at all (a Twilio webhook, or
      someone loading `/settings-ui`), it necessarily arrived over the
      *correct* public hostname already -- Cloud Run and every tunnel
      we've used (Cloudflare, presumably ngrok too) forward the real
      external `Host` and terminate TLS in front of us, so reading
      `X-Forwarded-Host`/`X-Forwarded-Proto` off that same request
      reconstructs the right base URL with zero configuration, and it
      self-corrects if the host ever changes -- no redeploy-triggered
      config drift possible.
    - `settings.PUBLIC_BASE_URL` still exists as an **explicit override**
      (checked first) for the rare case forwarded headers aren't
      trustworthy, and as the fallback for `scripts/setup_twilio_client_calling.py`,
      which runs from a terminal with no request to derive from. Verified
      directly: cleared `PUBLIC_BASE_URL` entirely, then hit `/settings`
      and `/twilio/voice` through the live Cloudflare tunnel and got back
      the correct `https://<tunnel>.trycloudflare.com` base URL in both;
      hitting `/settings` on bare `localhost:8001` (no tunnel) correctly
      fell back to `http://localhost:8001`.
    - `settings.twilio_configured` no longer requires `PUBLIC_BASE_URL` --
      it only checks `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` now, since
      the base URL is no longer something that needs "configuring" at all
      in the normal case.
    - Touched: `app/integrations/twilio_voice.py` (Media Stream URL),
      `app/settings_routes.py` (the displayed webhook URL, and the base
      URL passed to TwiML App setup), `app/integrations/twilio_client_calling.py`
      (`ensure_twiml_app()` takes `base_url` as a parameter now instead of
      reading `settings.PUBLIC_BASE_URL` internally, so its two callers --
      the request-driven endpoint and the request-less CLI script -- each
      supply the right one).
    - Practical effect for a future Cloud Run deploy: **leave
      `PUBLIC_BASE_URL` unset** in that environment's config: this app
      won't need to know its own URL ahead of time, and it stays correct
      across revisions/rollouts without touching env vars again.

24. **Clearing a credential from the Settings panel writes a
    self-documenting placeholder to `.env`, not a bare `KEY=`
    (2026-08-22).** `app/config.py`'s `PLACEHOLDERS` dict (e.g.
    `TWILIO_ACCOUNT_SID -> "your twilio account sid"`) is written by
    `settings_routes.py` in place of an empty string when a field is
    cleared, so a human glancing at `.env` sees a reminder of what goes
    there instead of an unexplained blank. This only had to touch the
    *write* path plus one *read*-side guard: `config.py`'s new `_env()`
    helper treats a value that exactly matches its field's placeholder as
    unset, so the round-trip is safe -- clear via UI -> placeholder
    written -> restart -> still reads as not-configured, never as a
    literal (wrong) credential. The in-memory `settings` object itself is
    always set to the real empty string on clear, never the placeholder
    text, so `twilio_configured`/`"set"` checks stay correct without a
    restart either. `.env.example` was updated to the same `"your X"`
    convention for consistency (previously `GEMINI_API_KEY=add key`, a
    different one-off style). Verified directly: cleared
    `TWILIO_API_KEY_SID` via `PUT /settings`, confirmed `.env` got the
    placeholder and `GET /settings` correctly reported `"set": false`,
    restored the real value, and independently confirmed a fresh process
    reads a placeholder-valued env var back as `""` while a real-looking
    value passes through untouched.

25. **`GET /` is now a landing page with 3 tiles, not the call UI
    directly (2026-08-22).** The call UI moved to `/web-testing`.
    - `app/static/landing.html` -- three tiles in this order: **Configuration
      & Setup** (`/settings-ui`), **Web Testing** (`/web-testing`),
      **Twilio Testing** (`/twilio-test`). Each tile fetches `GET
      /settings` on load and shows a live status line (Gemini key
      set/needed, Twilio configured/not) so the landing page itself
      answers "am I ready to test yet?" without clicking through.
    - `/settings-ui` gained a "Getting started" card at the top (create a
      Gemini key, Twilio is optional, trial-account signup, tunnel setup,
      paste the webhook URL) -- the "help, how-to" content requested
      alongside the credential fields, not just the fields themselves.
    - All four pages (`landing.html`, `index.html`, `twilio_test_call.html`,
      `settings.html`) now share the same small top nav (Home / Web
      Testing / Twilio Test Caller / Settings) so every page is reachable
      from every other page.
    - `app/main.py`: `GET /` serves `landing.html`; the former `GET /`
      handler (serving `index.html`) moved to `GET /web-testing`, with a
      short docstring noting the move so it isn't mysterious later.

26. **The agent speaks first, instead of sitting in silence waiting for
    the caller (2026-08-22).** Previously every transport only ever
    called `ChatEngine.generate_reply()` in response to caller audio --
    there was no "turn zero," so a caller who didn't know to speak first
    (the normal expectation of a phone agent) would hear nothing.
    - `ChatEngine.generate_greeting()` (`app/chat_engine.py`) seeds
      history with a synthetic, clearly-meta instruction ("The call has
      just connected. Greet the caller now and begin.") -- never shown to
      the caller, just a trigger -- then runs through the exact same
      `_run_loop()` (refactored out of `generate_reply` so both share it)
      including the tool-calling loop and the existing `caller_ani_note`
      already baked into the system prompt. This is why a known Caller ID
      gets confirmed right in the opening line for free, no extra code.
    - `app/call_engine.py`'s new `greet()` mirrors `process_turn()` (no
      transcription step or filler line -- there's nothing to transcribe
      yet, and synthesis alone covers the latency a caller already
      expects when a line connects) and is launched as the *initial*
      `turn_task` in both `main.py`'s `/audio` handler and
      `twilio_voice.py`'s `media_stream` -- reusing the existing
      turn_task-in-flight barge-in detection for free, no special-casing
      needed for "can I interrupt the greeting."
    - Caught and fixed a real race this surfaced: both browser
      `CallAdapter`s (`app/static/index.html` and
      `frontend/src/CallAdapter.ts`) set status to `"listening"`
      immediately on `ws.onopen`, and the `"listening"` mic-forwarding
      path sends every frame to the server unconditionally (no RMS
      gating -- that only exists on the barge-in path for
      `"processing"`/`"speaking"`). With the server now greeting
      immediately, that meant the very first stray mic frame (even
      near-silence) would set `interrupt_event` server-side and cut the
      greeting off before it started. Fix: `onopen` no longer changes
      status at all -- it stays `"connecting"` (where `handleAudioProcess`
      takes no action either way) until the greeting's audio starts
      arriving and `playAudio()` sets `"speaking"`; `resumeListening()`
      already transitions to `"listening"` once it ends, unchanged.
    - Verified directly: connected to `/audio` and sent nothing at all --
      the server proactively generated, synthesized, and streamed a full
      greeting ending in `reply_end`, entirely unprompted. Also verified
      the opening line differs correctly with vs. without a `caller_ani`
      (asks for the phone number cold vs. confirms the known number).

27. **The call-opening moment gets a cached, zero-latency filler too, not
    just mid-conversation turns (2026-08-22).** `call_engine.greet()`
    (decision #26) originally went straight to `generate_greeting()` +
    `synthesize()` on connect -- both real Gemini calls, meaning a caller
    could sit in dead air for several seconds (LLM + TTS round trip)
    before hearing anything at all, the exact opposite of the "agent
    speaks first" goal.
    - `app/filler_audio.py` now has two independent cached phrase sets:
      `_TURN_PHRASES` (unchanged -- "one moment, let me type that up")
      for the gap mid-process_turn, and a new `_OPENING_PHRASES` ("Hi
      there! One moment please.") for the gap right at connect. Kept
      separate because the turn-filler wording doesn't read naturally as
      the very first thing a caller hears with nothing said yet. Both are
      pre-synthesized at server startup by the same `warm_cache()`, so
      neither ever pays TTS latency at call time.
    - `greet()` now mirrors `process_turn()`'s exact shape: stream the
      cached opening filler instantly (`filler_start`/`filler_end`, zero
      Gemini calls involved), *then* run `generate_greeting()` + TTS in
      the background and stream that as the real opening line once ready.
    - Verified directly: a live websocket connection got `filler_start`
      and its first audio byte at **0.00s**, `filler_end` immediately
      after, and the real LLM-generated greeting's `reply_end` at
      **5.82s** -- the caller now hears something within milliseconds
      instead of sitting in silence for the full LLM+TTS round trip.
      Since `twilio_voice.py`'s Twilio path calls this same shared
      `greet()`, real phone calls get this for free with no separate
      change needed there.

28. **The "please wait" cue is now a procedurally-generated tone, not
    spoken text -- and it cuts short the instant the real reply is ready
    (2026-08-22).** The old filler phrases ("One moment, let me type that
    up, please.") read as a mismatch against the actual dialogue -- a
    voice agent narrating "let me type that up" doesn't make sense -- and
    still cost one real Gemini TTS call each to build their cached clips.
    - `app/filler_audio.py` was rewritten to synthesize a soft tone
      procedurally (a quiet C-major-triad pad with a slow amplitude
      swell and fade in/out, pure `math`/`struct`, no external asset, no
      Gemini call at all) instead of calling TTS on spoken phrases. Same
      public API (`warm_cache()`, `get_filler_audio()`,
      `get_opening_filler_audio()`), so nothing downstream had to change
      its calling convention. Side benefit: filler warm-up can no longer
      fail at startup from a bad `GEMINI_API_KEY` (see decision #19) --
      it's not calling Gemini for anything anymore.
    - **Caught a real bug while verifying this**: the client only starts
      playing a filler clip *after* receiving it in full (server sends
      the whole blob near-instantly over the websocket, no real-time
      pacing server-side), and previously had no way to stop it early.
      With a short spoken phrase (~2s) that was mostly harmless; with a
      longer tone meant to safely cover slow LLM+TTS latency, it would
      have made the caller sit through the tone's *entire* fixed length
      even when the real reply was ready in a fraction of that time --
      the opposite of the goal.
    - Fix: a new `filler_stop` event, sent by `call_engine.py`'s
      `greet()`/`process_turn()` right before streaming the real
      audio (after generation+synthesis, right when playback is about to
      start). Both browser `CallAdapter`s (`app/static/index.html`,
      `frontend/src/CallAdapter.ts`) track a `playingFiller` flag and, on
      `filler_stop`, immediately `.stop()` the currently-playing tone
      *only if* it's actually the tone playing (never clobbers a real
      reply) -- exactly like hold music cutting off the moment an agent
      picks up, rather than playing to a fixed length regardless.
    - Tone duration (`_DURATION_S` = 25s) is a safety ceiling, not a
      target, now that it's properly interruptible -- generous margin
      costs nothing (a few hundred KB, generated once) since it'll almost
      always be cut short well before finishing.
    - Verified directly over a live websocket connection: `filler_start`/
      `filler_end` fire near-instantly (tone begins playing client-side),
      then `filler_stop` fires right as the real greeting's audio is
      about to stream (~10.5s later in one real test), immediately
      followed by `reply_end` -- confirming the tone gets cut short at
      exactly the right moment rather than running to completion.

29. **The processing tone became an actual melodic, piano-like phrase
    instead of a flat drone, and the UI stopped mislabeling it as
    "Speaking" (2026-08-22).** Direct feedback on decision #28's first
    cut: three flat sine waves held as a static chord with a slow
    tremolo read as "robotic," a fair critique -- that's a synth pad, not
    anything piano-like.
    - `app/filler_audio.py`'s `_generate_tone()` now synthesizes real
      notes: each has a fast attack (~12ms) and an exponential decay
      envelope (`_NOTE_DECAY_S`), plus 4 harmonics at decreasing
      amplitude (fundamental + 2nd/3rd/4th partials) -- the standard
      cheap approximation of a plucked/struck instrument's timbre.
      `_NOTE_SEQUENCE` plays a short repeating phrase (C5-E5-G5-E5-D5-G5,
      pentatonic, overlapping decay tails for a legato feel) rather than
      one held chord. Verified the per-note envelope directly (isolated
      from phrase overlap and the overall clip fade, which otherwise mask
      it): RMS goes from 0.74 right after onset to 0.02 four seconds
      later, confirming genuine decay rather than a sustained flat tone.
      No clipping (peak ~30% of full scale). Still pure `math`/`struct`,
      no external asset, no Gemini call, generated once in ~1.4s at
      import/warm_cache time.
    - Separately: the client was calling `setStatus("speaking")` for
      *any* played audio, filler tone included -- so the UI showed
      "Speaking" while the tone played, when it should read as waiting,
      not talking. `playAudio()` (both `app/static/index.html` and
      `frontend/src/CallAdapter.ts`) now takes an `isFiller` flag
      (threaded through `pendingPlayback` too, for the queued-behind-
      something-else case) and sets status to `"processing"` instead of
      `"speaking"` when it's the tone. Also relabeled that status's UI
      text from "Thinking..." to **"Waiting..."**, which fits both this
      case and the brief pre-filler gap it already covered.

30. **The agent can now actually hang up, instead of saying goodbye and
    leaving the call open indefinitely (2026-08-22).** Previously
    "Ending the call" in the prompt was just a plain-text reply -- there
    was no mechanism at all for the agent to end the call, so it always
    fell back to sitting in "listening" after the farewell, waiting on a
    caller with nothing left to say.
    - **`app/chat_engine.py`**: a new universal `end_call` tool (added to
      `_END_CALL_TOOLS`, merged into every persona's tool list regardless
      of whether that persona has its own tools like patient_intake's --
      this is a transport-level concern, not persona/domain logic).
      Takes one argument, `farewell_message`, so the model's closing line
      and its intent-to-hang-up are expressed in a single tool call
      instead of relying on fragile text+function-call parsing in the
      same turn. `_run_loop()` (the refactored core of
      `generate_reply`/`generate_greeting`, decision #26) handles it
      inline rather than via the per-persona `self._dispatch` dict, and
      both methods now return a new `EngineReply(text, end_call)`
      dataclass instead of a bare string -- every call site
      (`call_engine.py`, `main.py`'s `/chat`) had to unpack this.
    - **`app/call_engine.py`**: `_end_call_after_playback()` -- called
      from both `greet()` and `process_turn()` right after their
      `reply_end` -- waits `len(audio) / (2 * sample_rate) + 0.5s` (i.e.
      the farewell's actual playback duration, plus a small jitter
      buffer) before sending `{"event": "call_ended_by_agent"}`. This
      matters because `_stream_audio` sends all frames near-instantly
      server-side with no real-time pacing (playback pacing happens
      client-side / on Twilio's relay, same fact that motivated decision
      #28's `filler_stop`) -- closing the connection the moment the last
      byte is *sent* would risk cutting the farewell off mid-sentence,
      especially on a real Twilio call.
    - **Transport layer**: `main.py`'s `/audio` and
      `twilio_voice.py`'s `/twilio/media-stream` both special-case
      `call_ended_by_agent` in their `send_event` closures to call
      `ws.close(code=1000)`. For Twilio specifically, closing the Media
      Stream websocket ends the TwiML's `<Connect><Stream>` verb, and
      since there's nothing after it in the TwiML, Twilio hangs up the
      actual PSTN call -- no separate REST API call needed. Both browser
      `CallAdapter`s already transition to `"ended"` on websocket close
      (pre-existing `onclose` handler), so no client-side event handling
      was strictly required, though a `call_ended_by_agent` debug-log
      line was added to both for a clearer call log.
    - Prompt (`patient_intake.yaml`) updated: "Ending the call" now
      explicitly calls `end_call` with the farewell as its argument
      instead of just replying with a goodbye, with an explicit warning
      against ending via plain text since that leaves the call open.
    - **Also fixed a real bug this surfaced**, unrelated to end_call
      itself: `candidate_content.parts` can be `None` (not just empty) on
      an unusual Gemini finish_reason (safety block, empty response,
      etc) -- caught this from an actual API response during testing,
      where it crashed the whole turn (`TypeError: 'NoneType' object is
      not iterable`) instead of degrading gracefully. Now guards with
      `parts = candidate_content.parts or []` and falls back to "Sorry,
      could you say that again?" if the model's response is empty.
    - Verified in two layers: end-to-end via `generate_reply`/
      `generate_greeting` directly against real Gemini through a full
      multi-turn registration conversation (confirmed `end_call=True`
      fires exactly on the closing turn, `false` everywhere else); and
      the connection-close timing in isolation via a direct in-process
      call to `process_turn()` with a stubbed engine (0.5s fake audio +
      0.5s buffer -- `call_ended_by_agent` fired exactly 1.0s after
      `reply_end`, confirming the wait is neither too early -- cutting
      off the farewell -- nor a fixed/wrong duration).

31. **Twilio's speech-start detection was too sensitive to real phone-line
    noise, causing the waiting tone to fire after the agent's own
    question instead of only while genuinely processing a reply
    (2026-08-22).** Real telephony audio has a genuinely higher noise
    floor than a clean browser mic (mu-law quantization noise, carrier-
    injected comfort noise, line static) -- reported directly from a real
    Twilio call. Root cause: `twilio_voice.py`'s barge-in detection
    already required `BARGE_IN_DURATION_S` (0.3s) of *sustained* RMS
    above threshold before treating it as real voice activity, but
    speech-*start* detection (`has_spoken`) had no such gate at all -- a
    single loud 20ms frame (a click, a pop, a noise spike) was enough to
    flip `has_spoken = True` immediately. Since a turn only fires once
    RMS then drops back below threshold for the full `SILENCE_DURATION_S`
    (1s), an isolated noise spike followed by a second of genuine quiet
    was enough to trigger a whole unwanted turn -- with nothing meaningful
    said, but often *something* non-empty transcribed anyway (Gemini STT
    can produce plausible-sounding text even from near-silence/noise), so
    the waiting tone and a full reply cycle fired for no real caller input.
    - Fix, mirroring the existing barge-in gate: a new `speech_started_at`
      timer requires RMS to stay above threshold continuously for
      `SPEECH_START_DURATION_S` (0.2s) before `has_spoken` is ever set,
      resetting on any dip back below threshold so isolated spikes don't
      accumulate toward that duration across unrelated moments.
    - Also bumped `SILENCE_RMS_THRESHOLD` itself from 650 to 900 (still
      int16-scale, mirroring `frontend/src/CallAdapter.ts`'s browser-mic
      value that 650 was originally derived from) -- a real phone line's
      baseline noise floor genuinely warrants a higher bar than clean
      digital mic input, independent of the duration-gate fix.
    - Verified the state-machine logic directly (no live call needed): a
      simulated 40ms noise click followed by 2s of quiet triggers zero
      turns; simulated sustained real speech (400ms above threshold)
      followed by 2s of quiet still correctly triggers exactly one.
    - Browser transports (`app/static/index.html`,
      `frontend/src/CallAdapter.ts`) were not touched -- their silence-
      to-turn decision already happens client-side against clean mic
      input via the browser's own `end_of_turn` signal, a different
      mechanism than Twilio's server-side RMS detection on the raw PSTN
      stream, so this specific failure mode doesn't apply there.

32. **Project moved to GitHub and prepped for Railway deployment
    (2026-08-22).** Repo pushed to `github.com/aneesahmed/voiceagent1`
    (merged with GitHub's own auto-created README/LICENSE/.gitignore from
    repo creation -- kept this project's versions of the first two,
    GitHub's LICENSE file, `--allow-unrelated-histories` merge since the
    two initial commits shared no history).
    - `backend/railway.toml` -- Railway build/deploy config. Explicit
      `startCommand` binding to Railway's dynamically-assigned `$PORT`
      (`uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`) is the
      one thing Nixpacks can't infer on its own (the entry point is
      `app.main:app` inside `app/`, not a top-level script it would
      auto-detect); Nixpacks does auto-detect `uv` itself from
      `backend/uv.lock`. Also sets `healthcheckPath = "/health"` since
      that endpoint already existed. Root Directory must be set to
      `backend` in Railway's dashboard (this is a monorepo, decision #3)
      for Railway to find this file at all.
    - **Persistent storage flagged, not yet resolved**: Railway's
      container filesystem is ephemeral by default, so the SQLite
      database needs a Railway Volume mounted (e.g. at `/data`) with
      `DB_PATH` pointed at it (`/data/patients.db`) -- otherwise every
      redeploy/restart silently wipes patient data, directly undermining
      decision #20's whole point. This requires action in Railway's
      dashboard (Settings → Volumes) that only the account owner can do;
      documented in README's new "Deploying → Railway" section as an
      explicit numbered step, not left implicit.
    - `PUBLIC_BASE_URL` guidance from decision #23 generalizes cleanly to
      Railway too (same request-forwarding-header mechanism) -- README's
      "Deploying" section was reworded from Cloud-Run-specific to
      platform-general, with Railway now the concretely documented case.

33. **App-wide HTTP Basic Auth, opt-in via `.env`, gating everything except
    the Twilio/WhatsApp webhook endpoints (2026-08-23).** Once a real
    Twilio account and Gemini billing were wired up, the live Railway URL
    became a real cost/abuse surface -- `/settings-ui` alone exposes
    credential previews, `/audio` and `/twilio/access-token` can rack up
    real Gemini/Twilio charges, and none of it required a login.
    - `app/basic_auth_middleware.py` -- a raw ASGI middleware (not
      Starlette's `BaseHTTPMiddleware`, which can't see WebSocket
      handshakes) checked via `app.add_middleware()` in `main.py`, added
      after `CORSMiddleware` so it becomes outermost and rejects
      unauthenticated requests before any route logic runs. No-ops
      entirely (open access, current behavior unchanged) unless *both*
      `BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD` are set --
      `config.py`'s `basic_auth_enabled` property, same "unconfigured
      degrades gracefully" pattern as Twilio/WhatsApp (decision #15).
      `BASIC_AUTH_PASSWORD` uses the same placeholder-on-clear convention
      as decision #24's `PLACEHOLDERS` dict.
    - `PUBLIC_PATHS` is a small explicit allowlist -- `/health` (Railway's
      own healthcheck), `/twilio/voice`, `/twilio/media-stream`,
      `/twilio/browser-call-voice`, `/whatsapp/webhook` -- since Twilio's
      and Meta's servers call these directly and have no way to attach a
      username/password. Everything else (landing/settings/web-testing/
      twilio-test pages, `/personas`, `/chat`, `/kb/*`, `/patients/*`,
      `/settings`, the `/audio` websocket, and `/twilio/access-token`) now
      requires auth. Credentials check via `hmac.compare_digest` (timing-
      attack-safe), not a plain `==`.
    - WebSocket handling required care: the middleware inspects
      `scope["headers"]` directly (already present pre-handshake, no need
      to call `receive()` first) to decide whether to authorize:
      unauthorized closes with `{"type": "websocket.close"}` (after
      consuming the pending `"websocket.connect"` event, per ASGI spec),
      which surfaces to the client as a clean handshake-level rejection
      (verified: 403 on the WebSocket upgrade itself, not an app-level
      error after connecting) -- not a special code path in `main.py`'s
      `audio_ws` handler.
    - Verified directly: unauthenticated `curl` against `/` and `/chat`
      both 401 with a `WWW-Authenticate: Basic` header; correct
      credentials 200; `/health` and `/twilio/voice` stay 200 with zero
      credentials either way; an unauthenticated raw `/audio` WebSocket
      connect attempt fails at the handshake (403) while one with a
      correct `Authorization` header connects and receives the greeting
      normally.
    - Not yet done: `BASIC_AUTH_USERNAME`/`BASIC_AUTH_PASSWORD` need
      adding to Railway's Variables tab (same gap pattern as decision
      #32's `TWILIO_TWIML_APP_SID` -- a value only in the local `.env`
      does not exist on Railway until added there manually).

34. **Removed the filler/hold-music cue entirely -- it was causing real
    problems on Twilio calls, not just a latency nicety anymore
    (2026-08-23).** Root cause diagnosed directly against the live Railway
    deployment: `call_engine._stream_audio` streams a whole clip's frames
    to the client near-instantly (no real-time pacing server-side, by
    design -- see decision #28's original notes). The browser transport
    compensates for this with real client-side logic
    (`CallAdapter.ts`/`static/index.html`'s `currentSource.stop()` on
    `filler_stop`), but the Twilio Media Streams integration never had an
    equivalent -- Twilio just buffers and plays every frame it receives,
    in order, at real speed, with no idea our server had moved on. That
    meant every real phone call sat through the *entire* ~10s hold tone
    before the greeting even started, and barge-in only stopped the
    server sending *more* frames, never what Twilio had already buffered
    -- confirmed by connecting directly to `/twilio/media-stream` and
    counting frames sent before Gemini's response was even ready. A first
    fix attempt added Twilio's `clear` event (discards buffered/unplayed
    audio) on `filler_stop`/`interrupted`, which worked correctly when
    tested directly -- but given the choice between maintaining two
    diverging per-transport playback-control code paths (browser's
    `AudioBufferSourceNode.stop()` vs. Twilio's `clear` event, each with
    its own edge cases) just to cover a few seconds of LLM+TTS latency,
    removing the filler mechanism outright was simpler and more reliable.
    - `app/filler_audio.py` and `app/assets/hold_music.pcm` deleted
      entirely (the module went through several iterations first -- TTS-
      synthesized spoken phrases, a procedural flat drone, a procedural
      piano melody, then a real user-supplied hold-music MP3 -- before
      being removed altogether here).
    - `call_engine.py`'s `greet()`/`process_turn()` no longer stream
      opening/mid-turn filler audio or send `filler_start`/`filler_end`/
      `filler_stop` events -- straight from transcribe/generate to
      synthesize to stream, same as before the filler mechanism existed.
      `_end_call_after_playback`, barge-in, and interrupt handling are
      unchanged.
    - `twilio_voice.py`'s `send_event` still sends Twilio's `clear` event,
      now only on `interrupted` (genuine barge-in) -- still needed and
      still correct, just no longer paired with `filler_stop` since that
      event doesn't exist anymore.
    - `main.py`'s `lifespan` no longer warms a filler cache at startup
      (nothing to warm).
    - Both browser transports (`app/static/index.html`,
      `frontend/src/CallAdapter.ts`) had their filler-tracking state
      (`fillerChunks`/`receivingFiller`/`playingFiller`, the `isFiller`
      flag threaded through `playAudio`/`pendingPlayback`) removed
      entirely -- `playAudio` always sets status `"speaking"` now, since
      there's no other kind of audio it ever plays.
    - Net effect: a caller now hears silence (not dead air with a tone,
      just nothing) for however long the LLM+TTS round trip actually
      takes, then the real reply -- simpler and more predictable than the
      filler mechanism it replaces, at the cost of losing the "something
      is happening" cue during a slow turn.

## Environment / config

`backend/.env` (gitignored, copy from `.env.example`):
```
GEMINI_API_KEY=<real key goes here>
CHAT_MODEL=gemini-2.5-flash
STT_MODEL=gemini-2.5-flash
TTS_MODEL=gemini-2.5-flash-preview-tts
TTS_VOICE=Aoede
HOST=0.0.0.0
PORT=8001

# Optional -- Twilio, blank until a real account exists (decision #15)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
PUBLIC_BASE_URL=

# Optional -- WhatsApp Business Cloud API, blank until configured (decision #15)
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=
```
`config.py` raises `RuntimeError` at import time if `GEMINI_API_KEY` is
missing -- this is intentional fail-fast behavior, not a bug. The
Twilio/WhatsApp vars are NOT fail-fast: they default to `""`, and each
integration checks its own `settings.twilio_configured` /
`settings.whatsapp_configured` property at request time instead, so an
unconfigured integration degrades gracefully rather than crashing the app.

## Current status / where things stand

**As of 2026-08-21, the live persona is Patient Registration
(`patient_intake`), not Sales -- see decision #18.** The `GEMINI_API_KEY`
in `backend/.env` was found to be invalid/expired during that work; `/chat`
and `/audio` cannot complete a real turn until it's replaced. Everything
that doesn't depend on a live Gemini call has been verified directly
(SQLite persistence across process restarts, all five `/patients` REST
endpoints, validation, duplicate-phone detection, both voice-agent tool
dispatch functions) -- see decision #18 for what was built. Run:
```bash
# backend
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8001
# frontend, separate terminal
cd frontend && npm install && npm run dev
```
`pyproject.toml` now also depends on `httpx` (added for the WhatsApp
integration's outbound Graph API calls) -- run `uv sync` again if you
pull code that predates that.

**What's implemented:**
- Browser pseudo-call (`/audio`) with barge-in/interrupt support, verbose
  per-call logging, and a UI showing a live mic-level meter + event log
  (decisions #9, #13).
- Persona-picker landing page showing all roadmap items; only
  **Patient Registration Agent** is selectable today (decisions #12, #18).
- Patient registration: SQLite persistence, full `/patients` REST CRUD
  (soft-delete, filtering, validation, JSON envelope), and voice-agent
  function-calling tools (`find_patient_by_phone`, `save_patient`) wired
  into `ChatEngine` (decision #18).
- Knowledge base viewer/editor in the UI, backed by `GET/PUT /kb`
  (decision #14) -- currently unused by the active persona (grounds the
  disabled `sales` persona only).
- Twilio phone-call bridge and WhatsApp Business API webhook, both fully
  coded but **not configured** (no real Twilio/Meta credentials yet) --
  see decision #15 and the frontend's Integrations sidebar for setup steps.
- A manual "I'm Done Talking" button supplements silence-based turn
  detection (decision #17). No filler/hold-music cue during the
  transcribe/reply/synthesize gap -- that was built (decision #16),
  iterated on several times, then removed entirely (decision #34) after
  it caused real playback problems specifically on Twilio calls.

**Not implemented:** every other persona on the roadmap (support, CRM,
scheduling, etc. -- see `app/personas.py`), real KB retrieval/embeddings
(still intentionally naive, decision #5), WhatsApp voice notes (text
only for now).

## Working conventions from this project's history

- Files were built one at a time in chat, with explicit review-then-paste
  by the user between each. Claude Code can write directly to disk, so
  this specific constraint doesn't need to carry over mechanically -- but
  the underlying intent (small, reviewable, incremental changes; don't
  silently regenerate/rewrite files that already work) should.
- Don't reintroduce complexity that was explicitly rejected (see
  "Architecture decisions" #1) without being asked again.
