# Operating conventions

## No cross-LLM leak of user attachments

Files ingested via bridget inbound or sent via `bridget chat --attach` are
personal/private to Clover unless she explicitly says otherwise. Receiving
agents may Read them with the LLM they are already running on (mayor on
Anthropic, polecats on whatever the harness uses). They MUST NOT upload,
POST, or otherwise transmit these files to any external service — no
Google Vision, no third-party OCR, no comparison LLM tool calls with the
file as payload, no debugging-bug paste to a public site. When unsure,
ask Clover first.
