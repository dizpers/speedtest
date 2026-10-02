# AGENTS.md

`speedtest.py` measures download speed with 10 sequential GET requests to one URL.
What exactly is measured is defined in README.md, section «Что измеряется». That section is the spec: keep code and README in sync.

## Commands
- Tests: `python3 -m unittest -v`
- Run: `python3 speedtest.py <url>`

## Rules
- Standard library only, in code and tests.
- Must run on Python 3.9 (macOS system python3): no syntax or stdlib APIs newer than 3.9.
- Tests never use the network: serve bytes from a local `http.server` on 127.0.0.1, port 0.
- TDD, one behavior per cycle: write the test, run it, see it fail for the expected reason, then write the minimal code, then refactor on green.
- A new test that passes right away proves nothing yet: break the code on purpose (mutation) to see it fail, then revert.
- Never weaken or edit a test to make code pass. If a test is wrong, stop and explain why.
- Expected values in tests are hand-computed numbers, never the production formula.
- Done = tests pass on Python 3.9 and the latest Python, plus one real run against a live URL.
