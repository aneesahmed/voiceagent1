"""app/chat_engine.py -- multi-turn conversation engine for the voice
assistant. Grounds every reply on the KB via rag.load_kb() and builds its
system instruction from the prompt registry, so neither the KB content nor
the persona wording is hardcoded here.
"""
import logging
from dataclasses import dataclass

from google.genai import types

from app.config import settings
from app.gemini_client import get_client
from app.patient_tools import PATIENT_INTAKE_TOOLS, TOOL_DISPATCH as PATIENT_TOOL_DISPATCH
from app.personas import DEFAULT_PERSONA_KEY, get_persona
from app.prompts.prompt_manager import prompt_manager
from app.rag import load_kb

logger = logging.getLogger(__name__)

# Per-persona function-calling tools. Personas without an entry here (e.g.
# "sales") get only the universal end_call tool below, unchanged from
# before otherwise -- only a persona whose logic genuinely needs to call
# out to a service (like patient_intake saving to the DB) opts into
# additional tools. This is a deliberate, minimal revisit of CLAUDE.md
# decision #12/#1: one dict entry per persona that needs extra tools, not
# a new per-persona class hierarchy or factory abstraction.
_PERSONA_TOOLS: dict[str, tuple[list[types.Tool], dict]] = {
    "patient_intake": (PATIENT_INTAKE_TOOLS, PATIENT_TOOL_DISPATCH),
}

# Universal end_call tool -- available to every persona, not just ones
# with their own tools (see CLAUDE.md decision #30). The model calls this
# as its final action once the conversation is genuinely over, instead of
# just saying goodbye and leaving the call open indefinitely.
_END_CALL_FUNCTION_NAME = "end_call"
_END_CALL_DECLARATION = types.FunctionDeclaration(
    name=_END_CALL_FUNCTION_NAME,
    description=(
        "Call this ONCE, as your final action, when the conversation is genuinely over -- the "
        "caller has nothing more to add and there's nothing left to do (registration finished, or "
        "a returning caller confirmed nothing needs updating). Pass the exact warm closing line to "
        "speak before hanging up. The call disconnects automatically right after that line finishes "
        "playing -- never call this mid-conversation, and never say a goodbye as plain text instead "
        "of through this tool, since a plain-text goodbye would leave the call open indefinitely."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "farewell_message": types.Schema(
                type=types.Type.STRING,
                description='The exact closing line to speak before hanging up, e.g. "You\'re all set, Jane -- we\'ll see you soon!"',
            )
        },
        required=["farewell_message"],
    ),
)
_END_CALL_TOOLS = [types.Tool(function_declarations=[_END_CALL_DECLARATION])]

MAX_TOOL_ITERATIONS = 5


@dataclass
class EngineReply:
    text: str
    end_call: bool = False


class ChatEngine:
    """One instance per call/session -- holds that session's conversation
    history. Create a new instance per call rather than reusing one across
    callers.

    persona selects which prompt template grounds this session (see
    app/personas.py) -- defaults to the sales persona, the only one
    implemented so far. Callers should validate persona.available before
    constructing this.

    caller_ani is the caller's phone number as reported by the transport
    itself -- Twilio's Caller ID (ANI) on a real call, or the number the
    browser test UI asks for up front to mimic ANI for local testing (see
    CLAUDE.md decision #21). None means no caller ID is available (e.g.
    blocked/private number), in which case the agent asks for the number
    normally instead of confirming one.

    call_id is purely for log correlation -- tagging tool-call/result
    lines with the same [call_id] prefix call_engine.py already uses, so
    a pasted log reads as one coherent per-call story instead of
    unattributed tool-call lines interleaved from whichever call happens
    to be active."""

    def __init__(self, persona: str = DEFAULT_PERSONA_KEY, caller_ani: str | None = None, call_id: object = None):
        resolved = get_persona(persona)
        if resolved is None or not resolved.available or resolved.prompt_key is None:
            raise ValueError(f"Persona '{persona}' is not available")
        self.call_id = call_id

        kb_context = load_kb()
        caller_ani_note = (
            f"CALLER ID: the phone system reports this call is from {caller_ani}. "
            "Open by confirming this number rather than asking for it cold -- e.g. "
            '"Looks like you\'re calling from (555) 123-4567 -- is that the right number for you?" '
            "If they say it's wrong, ask for the correct number instead."
            if caller_ani
            else "CALLER ID: not available for this call (blocked/private number, or a transport that "
            "doesn't report one) -- ask for the phone number normally."
        )
        self.system_instruction = prompt_manager.get_prompt(
            resolved.prompt_key, kb_context=kb_context, caller_ani_note=caller_ani_note
        )
        self.history: list[types.Content] = []
        persona_tools, self._dispatch = _PERSONA_TOOLS.get(resolved.key, ([], None))
        self._tools = [*persona_tools, *_END_CALL_TOOLS]

    def generate_greeting(self) -> EngineReply:
        """The agent speaks first, as soon as the call connects, instead of
        sitting in silence waiting for the caller -- callers expect a phone
        agent to greet them, not the other way around. Seeds history with a
        synthetic, clearly-meta instruction (never shown to the caller) so
        the same generate_content/tool-calling loop as generate_reply
        produces a natural opening line -- including confirming a known
        Caller ID (ANI) right away, per the caller_ani_note already baked
        into the system prompt in __init__."""
        self.history.append(
            types.Content(
                role="user",
                parts=[types.Part(text="(The call has just connected. Greet the caller now and begin.)")],
            )
        )
        return self._run_loop()

    def generate_reply(self, user_text: str) -> EngineReply:
        self.history.append(
            types.Content(role="user", parts=[types.Part(text=user_text)])
        )
        return self._run_loop()

    def _run_loop(self) -> EngineReply:
        config = types.GenerateContentConfig(
            system_instruction=self.system_instruction,
            tools=self._tools,
        )

        for _ in range(MAX_TOOL_ITERATIONS):
            response = get_client().models.generate_content(
                model=settings.CHAT_MODEL,
                contents=self.history,
                config=config,
            )
            candidate = response.candidates[0]
            candidate_content = candidate.content
            self.history.append(candidate_content)

            # candidate_content.parts can be None -- not just empty -- on an
            # unusual finish_reason (safety block, recitation, empty
            # response, etc). Rare, but real: caught this from an actual
            # Gemini response during testing, where it crashed the whole
            # turn with an unhandled TypeError instead of degrading
            # gracefully into "ask the caller to repeat themselves".
            parts = candidate_content.parts or []
            function_calls = [p.function_call for p in parts if p.function_call]
            if not function_calls:
                text = (response.text or "").strip()
                if not text:
                    logger.warning(
                        "[%s] empty model response (finish_reason=%s)",
                        self.call_id,
                        getattr(candidate, "finish_reason", None),
                    )
                    text = "Sorry, could you say that again?"
                return EngineReply(text=text)

            response_parts = []
            farewell_message = None
            for call in function_calls:
                logger.info("[%s] tool call: %s(%s)", self.call_id, call.name, dict(call.args))
                if call.name == _END_CALL_FUNCTION_NAME:
                    # Handled inline rather than via self._dispatch -- this
                    # is a transport-level concern (see call_engine.py),
                    # not persona/domain logic like the patient_intake
                    # tools, so it doesn't belong in a per-persona dispatch
                    # dict.
                    farewell_message = dict(call.args).get("farewell_message", "Goodbye!")
                    result = {"success": True}
                else:
                    handler = (self._dispatch or {}).get(call.name)
                    result = (
                        handler(dict(call.args))
                        if handler is not None
                        else {"success": False, "error": f"unknown tool '{call.name}'"}
                    )
                logger.info("[%s] tool result: %s", self.call_id, result)
                response_parts.append(types.Part.from_function_response(name=call.name, response=result))

            self.history.append(types.Content(role="user", parts=response_parts))

            if farewell_message is not None:
                return EngineReply(text=farewell_message, end_call=True)

        logger.warning("[%s] hit MAX_TOOL_ITERATIONS without a final text reply", self.call_id)
        return EngineReply(text="Sorry, I'm having trouble with that -- could you say that again?")