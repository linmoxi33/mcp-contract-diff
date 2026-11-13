# mcp-contract-diff

A zero-dependency CLI that tells you, from two captured MCP `tools/list` payloads, **which changes will break existing clients** — and names the exact schema field responsible.

Why now: MCP servers change their tool surface between releases and every change looks identical from the outside — a release note bullet. `homeassistant-ai/ha-mcp` shipped `Breaking change (v7.3.0): ha_config_set_yaml has been moved to beta` at the very top of its README, and it also hides tools behind a search mode when a client caps the tool count ([README L1](https://github.com/homeassistant-ai/ha-mcp/blob/master/README.md#L1), [README L379](https://github.com/homeassistant-ai/ha-mcp/blob/master/README.md#L379)). A client that cached yesterday's catalog keeps sending `ha_config_set_yaml` and gets a tool-not-found it cannot explain.

Upstream: https://github.com/homeassistant-ai/ha-mcp

## What the upstream is, and what is missing

`homeassistant-ai/ha-mcp` is an MCP server that exposes ~87 Home Assistant tools to LLM clients. Its own README documents that the tool surface is not stable (the v7.3.0 line above) and that clients which inline every schema or cap tool counts see a *different* surface than clients using deferred loading.

What is missing is not documentation — it is a **mechanical compatibility check**. Nothing in the repo, and nothing in the MCP spec, defines whether "`detail` lost the `full` enum value" or "`target` became required" is a breaking change for an existing caller. Maintainers find out from bug reports.

## What this tool does

Given a **baseline** and a **candidate** `tools/list` payload (a JSON-RPC response, a `{"tools": [...]}` object, or a bare list), it emits one change record per affected field, each with:

- a severity: `breaking` / `compatible` / `info`
- a witness path such as `tools[ha_get_state].inputSchema.properties.detail.enum`
- a human detail that names the concrete value that disappeared

Classifications:

| Change | Severity | Why |
| --- | --- | --- |
| `TOOL_REMOVED`, `PARAM_REMOVED` | breaking | existing calls now fail |
| `REQUIRED_PARAM_ADDED` | breaking | existing calls omit it |
| `TYPE_NARROWED` (e.g. `["string","array"]` → `string`) | breaking | previously valid values rejected |
| `ENUM_NARROWED`, `ENUM_INTRODUCED` | breaking | the caller's value may no longer be allowed |
| `ADDITIONAL_PROPERTIES_FORBIDDEN` | breaking | previously ignored keys now error |
| `TYPE_WIDENED`, `ENUM_WIDENED`, `PARAM_ADDED` (optional), `REQUIRED_RELAXED` | compatible | old calls still valid |
| `DEFAULT_CHANGED`, `DEFAULT_REMOVED`, `DESCRIPTION_CHANGED` | compatible / info | same call, different behaviour or model choice |

## What it does not do

- It does not run a server, speak JSON-RPC, or install an SDK. You capture the payloads; this compares them. Capturing is a one-liner with any MCP client.
- It does not validate payloads against the JSON Schema spec, resolve `$ref`/`allOf`/`oneOf`, or judge `outputSchema`.
- It does not decide your versioning policy. It reports what breaks; you decide whether that is acceptable.

## Install

Python 3.9+, no third-party packages.

```bash
git clone https://github.com/linmoxi33/mcp-contract-diff.git
cd mcp-contract-diff
python3 --version   # 3.9+
```

There is nothing to install. The tool is a single module plus a thin CLI.

## Usage

```bash
python3 bin/mcp-contract-diff examples/in/home-assistant-7.2.tools.json \
                                  examples/in/home-assistant-7.3.tools.json
```

```
mcp-contract-diff: home-assistant-7.2.tools.json -> home-assistant-7.3.tools.json
tools: 3 -> 3
changes: 3 breaking, 4 compatible, 0 info
verdict: BREAKING

[breaking  ] REQUIRED_PARAM_ADDED         ha_call_service                  tools[ha_call_service].inputSchema.required
    new required argument(s): target
[breaking  ] TOOL_REMOVED                 ha_config_set_yaml               tools[ha_config_set_yaml]
    tool no longer present in tools/list
...
```

Machine-readable output (`--json`) is the same report; the committed expected output is
`examples/out/home-assistant-7.3.diff.json` and a test asserts byte-for-byte equality:

```bash
python3 bin/mcp-contract-diff examples/in/home-assistant-7.2.tools.json \
       examples/in/home-assistant-7.3.tools.json --json | python3 -m json.tool | head
```

Capture a real snapshot with any client that can print `tools/list`, for example:

```bash
# any MCP client that can dump tools/list -> baseline.json / candidate.json
python3 bin/mcp-contract-diff baseline.json candidate.json --json > report.json
```

### Options

| Flag | Meaning |
| --- | --- |
| `--json` | emit the report as JSON |
| `--old-label`, `--new-label` | labels used in the report instead of file names |
| `--fail-on {breaking,compatible,info}` | lowest severity that produces exit code 1 (default `breaking`) |
| `--exit-zero` | parse and report, but always exit 0 |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | parsed; no change at or above `--fail-on` |
| `1` | parsed; at least one change at or above `--fail-on` |
| `2` | usage error, unreadable file, invalid JSON, or malformed `tools/list` |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

22 tests, all stdlib: loading/validation, each severity rule, CLI exit codes 0/1/2, and equality with the committed expected output.

## Limits

- The `examples/` snapshots are **synthetic fixtures** modelled on the v7.3.0 break documented in the upstream README. They are not a captured live `tools/list`: this runner is not allowed to install the MCP SDK, and the upstream README itself flags stdio transport issues. The fixture content is labelled `examples/in/` and is never presented as upstream data.
- Only `inputSchema` is compared; `outputSchema`, annotations, icons and `_meta` are ignored.
- Schema composition (`anyOf`, `oneOf`, `$ref`, `allOf`) is not resolved, so a change hidden behind a `$ref` is reported as a description-only change.
- Enum values are compared by JSON identity; `1` and `true` are treated as distinct.

## 本轮创新

首次把 MCP 工具契约当成**可机械判定的版本兼容性对象**：不是 lint 单个 schema 是否"规范"，而是对两个真实快照做类型格上的子类型判定（enum 子集、type 集合收窄、required 单调性），并为每条 breaking 给出字段级见证路径与 `--fail-on` 阈值退出码。台账里没有 MCP 跨版本兼容分类器；现有同类是付费托管的在线服务（见下），没有离线、确定性、可进 CI 的实现。

## 与同类项目的差异

`gh search repos "mcp tool schema diff"` 返回空；`gh search repos "mcp breaking change"` 只命中
`Agothe-ai/agent-contract-preflight`（0★）。它是**付费托管的远程 MCP 服务**（README 要求连接
`https://mcp-chatgpt.agothe.ai/mcp`、创建 $1 Stripe Checkout、用 credits 调用 `preflight_agent_contract`）。
本项目的差异是逐条的：

1. **离线、零依赖、确定性**：不联网、不装包、无账号，同一输入永远同一输出（可进 CI）。
2. **输入是两个任意快照**：不依赖某个服务的运行时，可以对任意 MCP 服务器在任意两个 release 之间做 diff。
3. **阈值化退出码**：`--fail-on breaking|compatible|info` 让"兼容但行为变了"也能卡住流水线。
4. **每条结论带字段级见证路径**，而不是一个总分或一句"有 breaking change"。

## License

MIT. See `LICENSE`.
