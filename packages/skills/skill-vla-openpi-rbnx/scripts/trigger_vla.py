#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""LLM-free deterministic trigger for the VLA execute skill.

Mirrors what the robonix executor does on a first tool call:
  1. resolve the openvla_client driver + execute endpoints from atlas
  2. send CMD_ACTIVATE(1) to the driver gRPC  -> on_activate connects openpi + ROS
  3. call the execute MCP tool with the instruction

Usage: python3 trigger_vla.py "pick the potato toy and place into box" [timeout_s] [max_steps]
"""
import asyncio
import sys

import grpc
import robonix_contracts_pb2_grpc as G
import lifecycle_pb2 as L

from robonix_api import ATLAS

CMD_INIT = 0
CMD_ACTIVATE = 1
CMD_DEACTIVATE = 2


def _endpoints():
    drv = ATLAS.connect_capability(
        consumer_id="vla-trigger", provider_id="openvla_client",
        contract_id="robonix/skill/vla/driver", transport="grpc")
    exe = ATLAS.connect_capability(
        consumer_id="vla-trigger", provider_id="openvla_client",
        contract_id="robonix/skill/vla/execute", transport="mcp")
    return drv.endpoint, exe.endpoint


def _drive(driver_ep: str, command: int, config_json: str = ""):
    ch = grpc.insecure_channel(driver_ep)
    stub = G.RobonixSkillVlaDriverStub(ch)
    resp = stub.Driver(L.Driver_Request(command=command, config_json=config_json), timeout=90.0)
    ch.close()
    return resp


async def call_execute(mcp_ep: str, instruction: str, timeout_s: float, max_steps: int):
    from fastmcp import Client
    async with Client(mcp_ep) as c:
        tools = [t.name for t in await c.list_tools()]
        print(f"[trigger] mcp tools: {tools}", flush=True)
        res = await c.call_tool("execute", {
            "instruction": instruction,
            "timeout_s": timeout_s,
            "max_steps": max_steps,
        })
        return res


def main():
    instruction = sys.argv[1] if len(sys.argv) > 1 else "pick the potato toy and place into box"
    timeout_s = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    max_steps = int(sys.argv[3]) if len(sys.argv) > 3 else 150
    cfg_path = sys.argv[4] if len(sys.argv) > 4 else "/tmp/vla_cfg.json"

    with open(cfg_path) as f:
        config_json = f.read()

    drv_ep, exe_ep = _endpoints()
    print(f"[trigger] driver={drv_ep} execute={exe_ep}", flush=True)

    # The skill process may already be _active from a prior run, holding a
    # websocket to a now-dead server. CMD_ACTIVATE is a no-op when _active, so
    # it would NOT reconnect. Deactivate first to drop the stale policy client;
    # the following INIT+ACTIVATE then rebuild against the current server.
    print("[trigger] sending CMD_DEACTIVATE (drop any stale connection) ...", flush=True)
    rd = _drive(drv_ep, CMD_DEACTIVATE, "")
    print(f"[trigger] deactivate resp: ok={getattr(rd,'ok',None)} state={getattr(rd,'state','?')} "
          f"error={getattr(rd,'error','')}", flush=True)

    # Executor lifecycle order: CMD_INIT (delivers config -> on_init fills _cfg)
    # THEN CMD_ACTIVATE. Skipping INIT leaves _cfg empty -> KeyError 'action_min'.
    print(f"[trigger] sending CMD_INIT (config from {cfg_path}) ...", flush=True)
    ri = _drive(drv_ep, CMD_INIT, config_json)
    print(f"[trigger] init resp: ok={getattr(ri,'ok',None)} state={getattr(ri,'state','?')} "
          f"error={getattr(ri,'error','')}", flush=True)
    if not getattr(ri, "ok", False):
        print("[trigger] INIT failed — aborting", flush=True)
        return 2

    print("[trigger] sending CMD_ACTIVATE ...", flush=True)
    r = _drive(drv_ep, CMD_ACTIVATE, "")
    print(f"[trigger] activate resp: ok={getattr(r,'ok',None)} state={getattr(r,'state','?')} "
          f"error={getattr(r,'error','')}", flush=True)
    if not getattr(r, "ok", False):
        print("[trigger] ACTIVATE failed — aborting", flush=True)
        return 2

    print(f"[trigger] calling execute(instruction={instruction!r}, "
          f"timeout_s={timeout_s}, max_steps={max_steps}) ...", flush=True)
    res = asyncio.run(call_execute(exe_ep, instruction, timeout_s, max_steps))
    print(f"[trigger] execute result: {res}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
