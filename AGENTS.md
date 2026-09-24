# Branch ownership

- Local development, fixes, and integration updates belong on `SRTP`.
- `llm` belongs to the AI engineer. Use it only to retrieve and test submissions; do not develop or publish local fixes on it.
- Before editing, verify the checkout is `SRTP`. Preserve existing local changes when switching.
- `E:/CubeEngine/work/llm-manual-1feefe6` is a historical test copy. Do not modify its code to deliver fixes.
- Track Workbench / LLM priorities in `docs/WORKBENCH_LLM_TODO.md`. Functional blockers take precedence over error-display polish.
- Use this single checkout for daily testing and development. Fetch `origin`, then merge `origin/llm` into `SRTP`; do not copy code between folders or create per-commit checkouts.
- The initial histories were joined by merge `5f7e637`. Future updates use ordinary merges. Push local development only to `origin/SRTP`; leave `main` and `llm` unchanged.

# Conversion delivery and verification

- Fix reusable capabilities and their composition, not game-name branches or edits to a user's generated Manifest. Preserve original failed responses for replay.
- Validate schema, model-facing contract, builder, runtime, and renderer together. Include neutral/renamed pattern cases and the saved failure; authored test repairs are never live-model acceptance.
- Every delivery must state: what the user can see/play now in Workbench; exact offline checks and their limits; remaining implementation; whether a new API call is necessary; its smallest useful scope, request limit and cost basis for user review.
- Passing offline tests does not establish successful new model generation or arbitrary-game conversion. Do not ask the user to pay for another full Compile merely because unit tests passed.
- Before paid acceptance, prepare the concrete test and reuse validated paid stages. Follow the current session's explicit authorization and budget; absent authorization, report the proposed cost/request scope and wait. Never present a request-count limit as a dollar cap.
