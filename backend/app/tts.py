def _synthesize_uncached(text: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        response = get_client().models.generate_content(
            model=settings.TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                # REMOVED: system_instruction="...", because Flash-Lite-TTS does not support it
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=settings.TTS_VOICE
                        )
                    )
                ),
            ),
        )
        # ... rest of the function
