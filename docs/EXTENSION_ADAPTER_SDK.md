# Extension Adapter SDK v1

Status: Verified non-LLM core package 8  
Manifest: `cubeengine.extension-manifest/1.0`  
SDK: `cubeengine.extension-sdk/1.0`  
RPC: `cubeengine.extension-rpc/1.0`  
Host: `cubeengine.extension-host/1.0`

## 1. Purpose

The declarative four-IR core deliberately does not contain arbitrary Python or
JavaScript. Mechanics, importers or presentation behavior outside the core
must therefore enter through one versioned, reviewable Extension boundary.

The SDK supplies that boundary. It separates:

- package identity and immutable code hashes;
- capability identity and typed request/response contracts;
- permissions and trust approval;
- process lifecycle and resource limits;
- deterministic/replay declarations and executable probes;
- Project Manifest pins and runtime capability resolution.

An Extension cannot silently become a fifth game-state authority. The first
integrated capability kind is a pure Rule expression function. Rule Runtime
still owns legality, transactions, state hashes and outcomes.

## 2. Security model: two different execution classes

Python's own documentation explicitly states that Python-level audit hooks are
not a sandbox and can be bypassed by malicious code. CubeEngine therefore does
not advertise its local process host as hostile-code containment:

<https://docs.python.org/3/library/sys.html#sys.addaudithook>

The machine-readable capability profile divides Extensions into:

1. `first_party` or explicitly `designer_reviewed` code may run in the local
   `cooperative_process` host after package verification.
2. `untrusted_generated` code, including newly generated LLM adapters, must
   declare `os_sandbox_required`. The local host refuses to start it. A future
   external sandbox provider must supply an AppContainer/container-equivalent
   boundary before that code can run.

This prevents “subprocess” from being mistaken for a complete sandbox.

## 3. Extension Manifest

Every package contains `extension.json` with:

- exact SDK/manifest versions, extension ID and semantic version;
- canonical manifest hash;
- publisher and trust level;
- entrypoint file/factory;
- complete executable file inventory with byte size and SHA-256;
- one or more versioned capability IDs;
- request/response JSON contracts;
- deterministic, replay-safe and side-effect declarations;
- filesystem/network/process/native/environment/module permissions;
- RPC, timeout, request/response, memory, CPU and process limits;
- exact Extension dependencies;
- provenance and unresolved review items.

Package loading rejects traversal, symlinks, Windows case collisions, altered
bytes, syntax errors, unlisted imports, reflective escape primitives and
unsupported dynamic execution. Manifest/package identity is content-addressed;
changing code requires a new hash and renewed designer approval.

## 4. Registry and dependency resolution

The registry accepts only verified packages. Extension IDs and capability IDs
have one provider. Dependencies pin exact provider ID, semantic version and
manifest hash. Bulk registration resolves dependencies in deterministic
topological order and rejects missing or mismatched providers.

Project Manifest `2.0-alpha.2` pins the complete provider set. An IR document
may request a versioned capability only when its provider is registered and
pinned by that project. Transitive Extension dependencies must also be pinned.

## 5. Process host and limits

One Extension package runs in one child process using:

- an explicit Python executable and argument array with `shell=False`;
- Python isolated/no-site mode;
- a minimal environment and private temporary working directory;
- bounded UTF-8 JSON-lines stdin/stdout RPC;
- captured Extension stdout/stderr so prints cannot corrupt the protocol;
- wall-clock request timeout and bounded protocol output;
- Windows Job Object limits for process memory, CPU time, active process count
  and kill-on-close process-tree cleanup.

Microsoft documents Job Objects as the Windows unit for applying limits and
managing a process tree:

<https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects>

The worker also applies static import checks and Python audit guards against
filesystem writes, network, subprocess and native loading. These are useful
defence-in-depth for reviewed code, but are not presented as an OS security
boundary.

## 6. RPC and lifecycle

The lifecycle is:

```text
hello -> initialize -> invoke* -> snapshot/restore* -> shutdown
```

Every request has a monotonic ID, exact protocol version, operation and JSON
payload. Success/error response fields are exact. Both sides enforce declared
byte limits. Capability inputs and outputs are validated using a bounded JSON
Schema subset with no remote references or executable validators.

An adapter exception becomes a structured RPC error and does not crash the
engine. Protocol corruption, output contract failure, purity violation or
timeout terminates that worker.

## 7. Determinism and replay

Every invocation records:

- Extension ID/version/hash;
- capability ID and sequence;
- canonical request hash;
- canonical response hash.

A `side_effects: none` capability is snapshot-tested before and after each
call. Any hidden adapter-state change terminates the worker. The conformance
kit runs deterministic cases in two fresh processes and compares results.
Replay-safe capabilities can be executed again from recorded requests and
must reproduce the complete invocation audit; divergence is rejected.

## 8. Rule and Project integration

Rule IR dependencies now contain unique versioned Extension capability IDs.
When Rule compilation sees one, it requires an initialized SDK session with
the exact capability list. `rule_function` declarations supply the Rule return
type, argument types and variadic flag, then execute through the process host.

Only pure deterministic Rule functions are integrated in this package. Custom
Rule effects, Scene components, Asset renderers and Input providers have
manifest/RPC vocabulary but remain compiler blockers until their owning
compiler defines a transaction-safe integration protocol.

Project compilation verifies Extension pins before launching a compile probe.
Each Project Session receives fresh Extension processes and closes them with
the Rule Runtime. The full accepted path is:

```text
Input event -> Rule precondition -> Extension rule function
            -> legal Rule transition -> Scene delta
```

## 9. Checked example and acceptance

`srtp/examples/extensions/parity/` is a sealed first-party package exposing the
pure capability `extension:cubeengine.parity/is_even/1`. It is deliberately
small: the example proves the generic SDK boundary, not a game-specific
adapter.

Automated acceptance covers:

- schemas, capabilities, honest drafts and canonical sealing;
- source inventory hashing and tamper rejection;
- static import/dynamic-execution gates;
- JSON request/response contracts;
- approval and untrusted-code gates;
- Windows Job Object host startup and complete lifecycle;
- output capture, errors, wall timeout and process isolation;
- pure snapshot enforcement;
- exact registry pins and dependency ordering;
- fresh-process determinism and replay audit divergence;
- Rule Runtime capability use;
- Project Manifest provider pinning and full Input→Rule→Scene execution.

## 10. Deliberate limits

This package does not claim general hostile-code sandboxing. It does not grant
network, native code, filesystem write, environment or subprocess permissions
to local Extensions. It does not yet integrate `rule_effect`, custom Scene,
Asset or Input capability kinds. Those remain typed and blocked rather than
falling back to arbitrary callbacks.

The downstream AlphaZero General nine-API adapter and final Non-LLM Integration
Gate are now verified. They consume validated target Rule/Project contracts;
they do not load or trust source-game code itself.
