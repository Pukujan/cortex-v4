# V4 replay observation — first control-layer slice

## V4-A observed failure

The legacy-overlap injector intentionally starts a retry before the timed-out attempt has stopped.
The deterministic provider reports `max_active == 2`, proving concurrent attempts. V4-A can still
return a successful final, so model narration is not an adequate liveness oracle.

## V4-B observed repair

The repaired V4 controller requests cancellation, waits for acknowledgement, advances the
generation fence, creates a new run ID, and starts the retry only after the first attempt exits.
The same provider reports `max_active == 1`; the replay test passes.

## Comparison boundary

This is a control-layer replay, not yet a real LiteLLM V4 call. The next loop must attach the
public LiteLLM route contract and the observation deck projection, then repeat the clean, timeout,
heartbeat, mutation, and hidden-golden checks without exposing SSC-A answers.
