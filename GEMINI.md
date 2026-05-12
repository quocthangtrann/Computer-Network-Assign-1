# Project Guidelines and Rules

These rules must be strictly followed when working on this project. They prioritize assignment constraints, grading script compatibility, and minimal intervention.

## 1. Grading Script Preservation (No API Changes)
*   **Rule:** NEVER alter existing function names, their signatures, or create new top-level helper functions that aren't part of the original template.
*   **Reason:** The professor's grading script relies on specific function names and signatures. Any deviation will cause the tests to fail. If a new mechanism is needed, implement it inline within the existing functions.

## 2. Strict Adherence to Assignment Constraints
*   **Rule:** The architecture MUST utilize non-blocking communication (callbacks/selectors or coroutines/asyncio) for multi-daemon communication.
*   **Rule:** Explicitly AVOID blocking `while` loops tied to OS threads (`threading.Thread`) for handling connections or peer-to-peer sending.
*   **Rule:** DO NOT use external web frameworks (e.g., Flask, Django). Rely exclusively on Python's built-in `socket`, `selectors`, and `asyncio` libraries.

## 3. Minimalist and Surgical Intervention
*   **Rule:** Do not over-optimize or rewrite the entire codebase just to make it "perfect."
*   **Rule:** Fix ONLY what is broken or violates the assignment rules, using the smallest code footprint possible.

## 4. Intent-Preserving Conflict Resolution
*   **Rule:** When resolving merge conflicts, ensure the core logic of the target branch is preserved while intelligently adopting non-conflicting improvements (like docstrings) from the base branch.

## 5. RFC Standards Verification
*   **Rule:** Authentication MUST strictly follow HTTP standards:
    *   RFC 2617/7235 for Basic Auth (must include `WWW-Authenticate` header for 401s).
    *   RFC 6265 for Cookies (must properly use `Set-Cookie` and `Cookie` headers).

## 6. Code Generation Precision and Syntax
*   **Rule:** When generating or replacing code, especially raw HTTP headers, ensure proper byte string formatting (`b"\r\n\r\n"`). Never use literal unescaped line breaks inside a byte string.
*   **Reason:** Raw HTTP requires carriage return + line feed (`\r\n`), and Python syntax forbids literal newlines in single-line strings.

## 7. API Signature Consistency
*   **Rule:** Always ensure all related handler functions mirror the target signature (e.g., `def route(req):`) when refactoring an interface. Do not leave behind old signatures (e.g., `def route(headers="guest", body="anonymous"):`).
*   **Reason:** The web framework expects a uniform handler signature. Mismatched signatures will cause crashes during execution.

## 8. True Non-Blocking Implementation
*   **Rule:** When implementing "callback" or "event-driven" modes via `selectors`, the socket must remain non-blocking (`setblocking(False)`). Never switch the socket back to `setblocking(True)` and execute synchronous `recv()` or `send()` loops.
*   **Reason:** True non-blocking architectures must use the event loop to yield control during partial reads/writes.

## 9. Asynchronous Execution Accuracy
*   **Rule:** Every asynchronous coroutine (`async def`) MUST be properly `await`ed (or scheduled as a task) when called from within another asynchronous function.
*   **Reason:** Calling an async function without `await` produces a coroutine object that is never executed.

## 10. Thread-Safety Exhaustiveness
*   **Rule:** Every single read, write, or iteration over shared global state (e.g., `peer_list`, `messages`, `channels`) MUST be wrapped in a `with _lock:` context manager.
*   **Reason:** The system supports multi-threading mode. Unlocked global access will cause `RuntimeError` (e.g., list changed size during iteration) or race conditions during concurrent use.

## 11. Strict Coding Standards (PEP 8 / PEP 257)
*   **Rule:** Adhere strictly to PEP 8 (snake_case for variables/functions, no camelCase like `isCoFunc`) and PEP 257 (meaningful docstrings, no empty "TODO" docstrings). Remove leftover boilerplate "TODO" comments.
*   **Reason:** Explicit assignment grading rubric for code style and ethics.

## 12. Dynamic Configuration over Hardcoding
*   **Rule:** Core architectural modes (like non-blocking strategies) should be configurable via command-line arguments (e.g., `argparse`) rather than hardcoded global variables.
*   **Reason:** A robust daemon must be configurable at launch for proper demonstration and testing without requiring source code edits.