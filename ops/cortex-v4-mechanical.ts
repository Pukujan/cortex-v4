/// <reference types="node" />
/**
 * V4 mechanical session control wire for OpenCode.
 *
 * Routes preflight / search / tool-gate / closeout through
 * cortex_v4.control.mechanical_session (control layer), not raw SSC adapter CLIs.
 * SSC remains the corpus; V4 owns classification, gates, and receipts.
 *
 * Default mode is shadow (log would_have_failed). Set CORTEX_V4_ENFORCE=1 to refuse.
 */
import type { Plugin, PluginInput } from "@opencode-ai/plugin"
import { tool } from "@opencode-ai/plugin"
import { z } from "zod"
import { appendFile, mkdir } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"

const RITUAL_DIR = join(homedir(), ".config", "opencode", "cortex-ritual")
const SHADOW_LOG = join(RITUAL_DIR, "v4-shadow-log.jsonl")
const V4_ROOT = process.env.CORTEX_V4_ROOT || "D:\\claude\\cortex-v4"
const SSC_ROOT = process.env.CORTEX_SSC_ROOT || "D:\\claude\\stupidly-simple-cortex"
const ENFORCE = process.env.CORTEX_V4_ENFORCE === "1"
const TIMEOUT_MS = 60_000

const SSC_MARKERS = [
  "Stupidly Simple Cortex",
  "cortex-canonical-continuation",
  "cortex_session_preflight",
  "cortex_v4",
]

const GATED_TOOLS = new Set([
  "Write", "Edit", "NotebookEdit", "Bash", "PowerShell", "Task", "Agent", "SendMessage",
])

const EXEMPT_TOOLS = new Set([
  "cortex_session_closeout",
  "cortex_search",
  "cortex_scope_pack",
  "cortex_session_preflight",
  "cortex_v4_preflight",
  "cortex_v4_gate",
  "cortex_v4_closeout",
  "cortex_v4_search",
  "cortex_write_log",
  "cortex_ritual_stamp",
  "python",
  "Read",
  "Grep",
  "Glob",
])

type ShadowEntry = {
  timestamp: string
  sessionID: string
  tool: string
  would_have_failed: boolean
  allowed: boolean
  reason: string
  control_layer: string
  enforce: boolean
  pack_hash?: string
  tax_ms: number
}

function isSSCWorkspace(systemPrompt: string): boolean {
  return SSC_MARKERS.some((m) => systemPrompt.includes(m))
}

async function appendShadow(entry: ShadowEntry): Promise<void> {
  try {
    await mkdir(RITUAL_DIR, { recursive: true })
    await appendFile(SHADOW_LOG, JSON.stringify(entry) + "\n")
  } catch {
    // best-effort
  }
}

const CortexV4Mechanical: Plugin = async (input: PluginInput) => {
  const { $ } = input
  const sscCache = new Map<string, boolean>()
  // session -> last known pack / preflight state (plugin-side mirror; V4 controller is source of truth per process)
  const preflightCache = new Map<string, boolean>()

  async function runV4(args: string[]): Promise<string> {
    const pyArgs = [
      "-c",
      "import os,sys; os.environ['CORTEX_SSC_ROOT']=sys.argv[1]; os.environ['CORTEX_V4_ROOT']=sys.argv[2]; "
      + "sys.path.insert(0, sys.argv[2]); "
      + "from cortex_v4.control.mechanical_session import main; "
      + "raise SystemExit(main(sys.argv[3:]))",
      SSC_ROOT,
      V4_ROOT,
      ...args,
    ]
    const timeoutPromise = new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error("v4 mechanical timeout")), TIMEOUT_MS),
    )
    const shellPromise = $`python ${pyArgs}`.text()
    return await Promise.race([shellPromise, timeoutPromise])
  }

  async function v4Json(args: string[]): Promise<Record<string, unknown>> {
    const text = await runV4(args)
    const start = text.indexOf("{")
    const end = text.lastIndexOf("}")
    if (start < 0 || end < start) {
      return { ok: false, reason: "non-json v4 output", raw: text.slice(0, 400) }
    }
    try {
      return JSON.parse(text.slice(start, end + 1)) as Record<string, unknown>
    } catch (e) {
      return { ok: false, reason: `json parse failed: ${e}`, raw: text.slice(0, 400) }
    }
  }

  return {
    "experimental.chat.system.transform": async (input, output) => {
      try {
        const sessionID = input.sessionID
        if (!sessionID) return
        const systemPrompt = output.system.join("\n")
        const isSSC = isSSCWorkspace(systemPrompt)
        sscCache.set(sessionID, isSSC)
        if (isSSC) {
          output.system.push(
            "\n[CORTEX V4 MECHANICAL CONTROL] This session is governed by V4 mechanical methodology gates " +
              "(cortex_v4.control.mechanical_session), not prompt memory alone. " +
              "Before Write/Edit/Bash/Task: call `cortex_v4_search` then `cortex_v4_preflight` " +
              "(or cortex_search + cortex_session_preflight which now route through V4). " +
              "At end call `cortex_v4_closeout` / cortex_session_closeout. " +
              "SSC is the corpus; V4 is the control layer. Cite pack_hash from preflight.",
          )
        }
      } catch (e) {
        console.error("[cortex-v4-mechanical] system.transform error:", e)
      }
    },

    "tool.execute.before": async (input, _output) => {
      try {
        const { tool: toolName, sessionID } = input
        if (!toolName || !sessionID) return
        if (toolName === "cortex_search" || toolName === "cortex_v4_search") {
          return
        }
        if (toolName === "cortex_session_preflight" || toolName === "cortex_v4_preflight") {
          preflightCache.set(sessionID, true)
          return
        }
        if (EXEMPT_TOOLS.has(toolName) || !GATED_TOOLS.has(toolName)) return
        const isSSC = sscCache.get(sessionID) ?? false
        if (!isSSC) return

        const t0 = performance.now()
        const gateArgs = [
          "gate",
          "--session-id", sessionID,
          "--tool", toolName,
          "--workspace", SSC_ROOT,
        ]
        if (ENFORCE) gateArgs.push("--enforce")
        else gateArgs.push("--shadow")

        const decision = await v4Json(gateArgs)
        const allowed = Boolean(decision.allowed)
        const would_have_failed = Boolean(decision.would_have_failed) || !allowed
        const reason = String(decision.reason || decision.code || "")
        const tax_ms = performance.now() - t0

        await appendShadow({
          timestamp: new Date().toISOString(),
          sessionID,
          tool: toolName,
          would_have_failed,
          allowed,
          reason,
          control_layer: String(decision.control_layer || "cortex_v4.control.mechanical_session"),
          enforce: ENFORCE,
          pack_hash: decision.pack_hash ? String(decision.pack_hash) : undefined,
          tax_ms,
        })

        // Narrow enforce: only when CORTEX_V4_ENFORCE=1. Plugin cannot hard-deny via
        // tool.execute.before in all OpenCode versions; shadow is the default rollout.
        if (ENFORCE && !allowed) {
          console.error(`[cortex-v4-mechanical] REFUSE ${toolName}: ${reason}`)
        }
      } catch (e) {
        console.error("[cortex-v4-mechanical] tool.before error:", e)
      }
    },

    tool: {
      cortex_v4_search: tool({
        description:
          "V4 mechanical corpus search (control layer). Prefer this over bare Read/Grep in SSC workspaces.",
        args: {
          query: z.string().describe("search query"),
          session_id: z.string().optional().describe("session id"),
          limit: z.number().optional().default(12),
        },
        execute: async (args, ctx) => {
          try {
            const sid = args.session_id || ctx.sessionID || "opencode"
            const out = await v4Json([
              "search",
              "--session-id", sid,
              "--query", args.query,
              "--limit", String(args.limit ?? 12),
              "--workspace", SSC_ROOT,
            ])
            return JSON.stringify(out, null, 2)
          } catch (e) {
            return `cortex_v4_search error: ${e instanceof Error ? e.message : String(e)}`
          }
        },
      }),

      cortex_v4_preflight: tool({
        description:
          "V4 mechanical M1 preflight — classifies methodologies, freezes evidence pack, records pack_hash.",
        args: {
          task: z.string().describe("task to ground"),
          session_id: z.string().optional(),
          limit: z.number().optional().default(4),
        },
        execute: async (args, ctx) => {
          try {
            const sid = args.session_id || ctx.sessionID || "opencode"
            const out = await v4Json([
              "preflight",
              "--session-id", sid,
              "--task", args.task,
              "--limit", String(args.limit ?? 4),
              "--workspace", SSC_ROOT,
            ])
            if (out.ok) preflightCache.set(sid, true)
            return JSON.stringify(out, null, 2)
          } catch (e) {
            return `cortex_v4_preflight error: ${e instanceof Error ? e.message : String(e)}`
          }
        },
      }),

      cortex_v4_gate: tool({
        description: "V4 mechanical tool gate check (shadow or enforce).",
        args: {
          tool: z.string(),
          session_id: z.string().optional(),
          enforce: z.boolean().optional().default(false),
        },
        execute: async (args, ctx) => {
          try {
            const sid = args.session_id || ctx.sessionID || "opencode"
            const gateArgs = [
              "gate",
              "--session-id", sid,
              "--tool", args.tool,
              "--workspace", SSC_ROOT,
            ]
            if (args.enforce || ENFORCE) gateArgs.push("--enforce")
            else gateArgs.push("--shadow")
            const out = await v4Json(gateArgs)
            return JSON.stringify(out, null, 2)
          } catch (e) {
            return `cortex_v4_gate error: ${e instanceof Error ? e.message : String(e)}`
          }
        },
      }),

      cortex_v4_closeout: tool({
        description: "V4 mechanical M7 closeout through control layer.",
        args: {
          task: z.string(),
          result: z.string(),
          location: z.string().optional(),
          continuation: z.string().optional(),
          session_id: z.string().optional(),
        },
        execute: async (args, ctx) => {
          try {
            const sid = args.session_id || ctx.sessionID || "opencode"
            const cmd = [
              "closeout",
              "--session-id", sid,
              "--task", args.task,
              "--result", args.result,
              "--workspace", SSC_ROOT,
            ]
            if (args.location) cmd.push("--location", args.location)
            if (args.continuation) cmd.push("--continuation", args.continuation)
            const out = await v4Json(cmd)
            return JSON.stringify(out, null, 2)
          } catch (e) {
            return `cortex_v4_closeout error: ${e instanceof Error ? e.message : String(e)}`
          }
        },
      }),

      // Override/alias: keep familiar names but route through V4 when possible.
      cortex_session_preflight: tool({
        description:
          "M1 preflight via V4 mechanical control (SSC corpus under V4 gates).",
        args: {
          task: z.string(),
          limit: z.number().optional().default(4),
        },
        execute: async (args, ctx) => {
          try {
            const sid = ctx.sessionID || "opencode"
            const out = await v4Json([
              "preflight",
              "--session-id", sid,
              "--task", args.task,
              "--limit", String(args.limit ?? 4),
              "--workspace", ctx.worktree || SSC_ROOT,
            ])
            if (out.ok) preflightCache.set(sid, true)
            return JSON.stringify(out, null, 2)
          } catch (e) {
            return `cortex_session_preflight (v4) error: ${e instanceof Error ? e.message : String(e)}`
          }
        },
      }),

      cortex_search: tool({
        description: "Corpus search via V4 mechanical control layer.",
        args: {
          query: z.string(),
          limit: z.number().optional().default(20),
        },
        execute: async (args, ctx) => {
          try {
            const sid = ctx.sessionID || "opencode"
            const out = await v4Json([
              "search",
              "--session-id", sid,
              "--query", args.query,
              "--limit", String(args.limit ?? 20),
              "--workspace", ctx.worktree || SSC_ROOT,
            ])
            return JSON.stringify(out, null, 2)
          } catch (e) {
            return `cortex_search (v4) error: ${e instanceof Error ? e.message : String(e)}`
          }
        },
      }),

      cortex_session_closeout: tool({
        description: "M7 closeout via V4 mechanical control layer.",
        args: {
          task: z.string(),
          result: z.string(),
          location: z.string().optional(),
          continuation: z.string().optional(),
        },
        execute: async (args, ctx) => {
          try {
            const sid = ctx.sessionID || "opencode"
            const cmd = [
              "closeout",
              "--session-id", sid,
              "--task", args.task,
              "--result", args.result,
              "--workspace", ctx.worktree || SSC_ROOT,
            ]
            if (args.location) cmd.push("--location", args.location)
            if (args.continuation) cmd.push("--continuation", args.continuation)
            const out = await v4Json(cmd)
            return JSON.stringify(out, null, 2)
          } catch (e) {
            return `cortex_session_closeout (v4) error: ${e instanceof Error ? e.message : String(e)}`
          }
        },
      }),
    },
  }
}

export default CortexV4Mechanical
