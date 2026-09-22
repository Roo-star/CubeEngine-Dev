# Branch ownership

- Local development, fixes, and integration updates belong on `SRTP`.
- `llm` belongs to the AI engineer. Use it only to retrieve and test submissions; do not develop or publish local fixes on it.
- Before editing, verify the checkout is `SRTP`. Preserve existing local changes when switching.
- `E:/CubeEngine/work/llm-manual-1feefe6` is a historical test copy. Do not modify its code to deliver fixes.
- Track Workbench / LLM priorities in `docs/WORKBENCH_LLM_TODO.md`. Functional blockers take precedence over error-display polish.
- Use this single checkout for daily testing and development. Fetch `origin`, then merge `origin/llm` into `SRTP`; do not copy code between folders or create per-commit checkouts.
- The initial histories were joined by merge `5f7e637`. Future updates use ordinary merges. Push local development only to `origin/SRTP`; leave `main` and `llm` unchanged.
