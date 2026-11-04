# Architecture

```
   MCP clients: AiScientist · Claude Code · other apps · aiscientist-mcp CLI
                      │  stdio (local) or Streamable HTTP (shared service)
            ┌─────────┴──────────────────────────────┐
            │ <server>-mcp  (packages/<server>)       │  tools: thin, typed, structured results
            │   └ analysis code (tested functions)    │
            │ aiscientist_mcp.common                  │  server factory · paths · jobs · client
            └─────────┬──────────────────────────────┘
                      │  submit → status → result
           Slurm job (sbatch) or local process, optionally inside the server image
```

## Shared runtime (`packages/common`)

| Module | Provides |
| --- | --- |
| `server` | `make_server`, `@tool(server)` (structured output), `quiet()` (stdout to stderr), `run_stdio` |
| `paths` | `safe_id`, `child_dir`: every id or folder argument goes through these |
| `jobs` | `submit` / `status` / `record_done`: Slurm, local or inline executor, resubmission with `AISCI_SLURM_FALLBACK` after a preemption, `AISCI_JOB_PREFIX` (e.g. `apptainer exec ... image.sif`) |
| `client` | `aiscientist-mcp --server <cmd> tools|call`: a stdio MCP client for shells and batch scripts |

## Long-running tools

A tool that needs minutes or hours, such as loading a 19 GB atlas, never blocks the call:
1. `submit_*` validates its input, writes `state.json`, starts the job, and returns an id.
2. The job wraps its work in `record_done`, which writes `done.json` (COMPLETED or FAILED).
3. `*_status` reads `done.json`, or asks Slurm, and resubmits a preempted job.
4. Tools that read the job's output refuse until it is COMPLETED.

## Deployment

- **Local / Claude Code**: stdio, the package in a conda env, jobs submitted with sbatch.
- **Container**: the server image pinned by digest. A persistent server inside a Slurm allocation
  uses `AISCI_EXECUTOR=local`; on a login node, `slurm` with `AISCI_JOB_PREFIX` so jobs run in the
  same image. When each tool call is a fresh container (`apptainer exec` per call, an agent
  sandbox), use `inline`: a detached `local` job would be killed with the container's PID
  namespace.
- **Shared service** (AiScientist, a chatbot): Streamable HTTP behind authentication, on a service
  host that submits jobs. AiScientist needs an MCP client bridge that wraps each tool for its
  planner.
