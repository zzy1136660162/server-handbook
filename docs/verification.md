# Verification record

Initial development date: **2026-11-13**, Asia/Shanghai. Results below record the actual initial local checks. The public source is [zzy1136660162/server-handbook](https://github.com/zzy1136660162/server-handbook); current cross-platform CI results are reported separately in [GitHub Actions](https://github.com/zzy1136660162/server-handbook/actions/workflows/ci.yml).

| Check | Actual result |
| --- | --- |
| Default test suite, Windows / Python 3.13.10 | 117 passed, 1 explicitly opt-in Docker test skipped, 19 subtests passed |
| Skill format validator | Passed |
| Real Docker Desktop / Linux containers, Docker 29.4.0 and Compose 5.1.1 | Isolated Compose lifecycle passed; four persisted receipts verified |
| Wheel without optional MCP | Fresh-environment CLI roundtrip passed; no MCP dependency required |
| Wheel with SDK 2.3.0 | Real stdio read/write boundaries, tools/resources, registry update and bundled assets passed |
| Tool answerability | 10 anonymous-example answers verified through actual read-only MCP calls; not an LLM accuracy benchmark |
| Linux / Python 3.11 base wheel | Final wheel passed in an isolated Debian-based container, without network or host volumes; CLI roundtrip, Unicode paths, CRLF preservation, revision conflicts and Skill export passed |
| Native systemd service lifecycle | Simulated command-runner tests only; not executed against a live systemd host |
| GitHub source / CI | Public repository and cross-platform checks workflow; consult linked Actions for each commit's actual result |
| PyPI publication | [0.1.0](https://pypi.org/project/server-handbook/0.1.0/) published on 2026-11-13 through GitHub Actions Trusted Publishing; [build and publish jobs succeeded](https://github.com/zzy1136660162/server-handbook/actions/runs/43378082055), including installed-wheel CLI and real stdio MCP verification on Linux / Python 3.13 |

The real Compose fixture used an anonymous UUID project, one `nginx:alpine` service, a random loopback-only port, no host volumes and no privileged mode. It verified successful HTTP 200, an intentionally wrong expected 201 producing a failed receipt despite a running process, correction and success at the next revision, and a stopped service. All operations refreshed the managed README while retaining hand-written content. Cleanup removed only the fixture's project containers/network; it did not remove volumes, images or other projects.

The nginx image pulled for that check had digest `sha256:713fc2ebfd92e8217db451477182ee91944b123d2b1ee9fd5a8c6e91ca6c3a11`. Image tags in examples are illustrative, not pinned production deployment inputs.

The Linux base-wheel check used `python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535`. It installed the wheel with `--no-index`, ran with network disabled and no host mounts, then removed only its verified test container. It did not run systemd or install the optional MCP SDK inside that container.

Unit/integration cases include real SQLite concurrent compare-and-swap, failed history-write rollback, missing/read-only/unknown database handling, declaration drift, document and secret boundaries, interrupted operation retention/recovery, real loopback HTTP redirects, and managed Markdown preservation. Independent review added regression cases for edits made after initial reads, competing descriptor creation and directory symlinks escaping the service home.

`tools/verify_installed.py` must run from an installed wheel, not an editable checkout. It uses a disposable anonymous Unicode-path workspace, verifies hand-written CRLF preservation, revision conflicts and exported Skill/schema resources, then exercises the actual MCP console process. `--base-only` verifies the standard-library CLI without installing MCP. Local raw receipts are kept under the ignored `verification/` directory and are excluded from distributions.

Native systemd lifecycle execution and real third-party AI-client registration remain unverified. Additional Linux/Windows and Python-version test results belong to the linked CI runs, separately from the original local checks above. The packaging checks did not deploy to or reconfigure any existing server.
