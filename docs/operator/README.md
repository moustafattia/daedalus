# Daedalus Operator Guide

> **Day-to-day operations, installation, and troubleshooting for humans who run Daedalus.**
>
> This section is for the person staring at a terminal wondering why a lane hasn't moved in three hours.

---

## Operator Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       DAEDALUS OPERATOR MAP                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐  │
│  │  INSTALLATION    │─────►│  DAY-TO-DAY      │─────►│  TROUBLESHOOTING │  │
│  │                  │      │  OPERATIONS      │      │                  │  │
│  │  • Install       │      │                  │      │  • Cheat Sheet   │  │
│  │  • Configure     │      │  • Slash Commands  │      │  • HTTP Status   │  │
│  │  • Verify        │      │  • Watch TUI       │      │  • Logs          │  │
│  │                  │      │  • Service Control │      │                  │  │
│  └──────────────────┘      └──────────────────┘      └──────────────────┘  │
│                                                                              │
│  New box ──► Running ──► Monitoring ──► Debugging ──► Fixing ──► Shipping  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Installation

**First time setting up Daedalus?** Start here.

| Doc | What It Covers | Read This If... |
|:---|:---|:---|
| [**Installation**](./installation.md) | Community install path, prerequisites, plugin setup, systemd service registration, first-run verification. | ...you are installing Daedalus on a new machine or rebuilding after a migration. |

**The narrative arc:** *Install* → *Configure* → *Verify* → *Start service* → *Confirm health*.

---

## Day-to-Day Operations

**Running Daedalus daily?** These are your tools.

| Doc | What It Covers | Read This If... |
|:---|:---|:---|
| [**Slash Commands**](./slash-commands.md) | Complete catalog of `/daedalus` commands: `status`, `doctor`, `watch`, `shadow-report`, `active-gate-status`, `service-status`, `get-observability`, and more. | ...you need to check what's happening or poke the system into action. |
| [**HTTP Status Surface**](./http-status.md) | Optional localhost HTTP server (`:8765`) exposing JSON health snapshots for dashboards and external monitoring. | ...you want to monitor Daedalus without SSHing into the box. |

**The narrative arc:** *Check status* → *Watch live* → *Diagnose* → *Fix* → *Confirm*.

---

## Troubleshooting

**Something is wrong?** These docs get you unstuck.

| Doc | What It Covers | Read This If... |
|:---|:---|:---|
| [**Cheat Sheet**](./cheat-sheet.md) | Quick-reference commands, SQL queries for direct DB inspection, common failure patterns, and recovery procedures. | ...you need to debug a stuck lane, find a failed action, or verify lease health. |

**The narrative arc:** *Observe symptoms* → *Query state* → *Identify root cause* → *Apply fix* → *Verify recovery*.

---

## Start Here

**Installing Daedalus for the first time?**

1. [**Installation**](./installation.md) — get it running
2. [**Slash Commands**](./slash-commands.md) — learn the basics
3. [**Cheat Sheet**](./cheat-sheet.md) — bookmark for emergencies

**Operating Daedalus day-to-day?**

- [**Slash Commands**](./slash-commands.md) — your primary interface
- [**Cheat Sheet**](./cheat-sheet.md) — keep open for quick SQL and debugging
- [**HTTP Status Surface**](./http-status.md) — set up monitoring once, check forever

**Debugging a stuck or broken lane?**

1. [**Cheat Sheet**](./cheat-sheet.md) — run diagnostic SQL, check common patterns
2. [**Slash Commands**](./slash-commands.md) — use `doctor` and `watch` for live state
3. [**HTTP Status Surface**](./http-status.md) — pull JSON state for programmatic analysis

---

## How These Connect

```
[Installation] ──► Daedalus is running
       │
       ▼
[Slash Commands] ──► /daedalus status / doctor / watch
       │
       ▼
[HTTP Status] ──► localhost:8765 for dashboards
       │
       ▼
[Cheat Sheet] ──► SQL queries, recovery procedures, common fixes
       │
       ▼
Lane is unstuck, PR is merged, operator goes back to sleep
```

---

## See Also

| Doc | What It Covers |
|---|---|
| [Architecture Overview](../architecture.md) | The big picture — how Daedalus works internally |
| [Concepts](../concepts/README.md) | The mental model — leases, lanes, actions, failures, etc. |
| [Contributing](../contributing.md) | How to contribute to Daedalus |
