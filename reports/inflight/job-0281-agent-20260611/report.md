# job-0281 — persist zoom-to emissions (the writer job-0280's verifier demanded)

job-0280's agent found the cross-seam gap honestly: the web Case-reopen
snap-to-location replays `CaseChatMessage.map_command_emissions`, but the
field (contract since job-0099) never had a server-side writer. Now:
`SessionState.current_turn_map_commands` accumulates the geocode-snap
zoom-to (reset per turn alongside the layer accumulator) and
`_persist_chat_turn` snapshots it onto accumulator rows (tool rows get []).
Test: agent-row persists the zoom-to, tool-row stays empty. Reopening any
Case whose turns ran on this build now snaps the camera back.
