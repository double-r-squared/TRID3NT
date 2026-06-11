"""Live Bedrock adapter smoke test (sprint-14-aws job-0286)."""
import asyncio, os, sys
os.environ["MODEL_PROVIDER"] = "bedrock"

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.adapter import build_tool_declarations, build_contents_from_history
from grace2_agent import bedrock_adapter as ba
from grace2_agent.adapter import FunctionCallEvent, TextDeltaEvent, UsageMetadataEvent


def offline_catalog_conversion():
    decls = build_tool_declarations(TOOL_REGISTRY)
    tools = ba.tool_declarations_to_bedrock_tools(decls)
    bad = []
    for t in tools:
        spec = t["toolSpec"]
        sch = spec["inputSchema"]["json"]
        if sch.get("type") != "object" or not spec.get("name"):
            bad.append(spec.get("name"))
    print(f"[offline] {len(TOOL_REGISTRY)} registry tools -> {len(decls)} decls "
          f"-> {len(tools)} bedrock toolSpecs; malformed={bad}")
    # show one sample
    sample = next((t for t in tools if t['toolSpec']['name'] == 'fetch_administrative_boundaries'), tools[0])
    print(f"[offline] sample toolSpec name={sample['toolSpec']['name']} "
          f"schema_keys={list(sample['toolSpec']['inputSchema']['json'].keys())}")
    return decls


async def live_turn(decls):
    # Small representative subset to keep the live call cheap.
    keep = {"fetch_administrative_boundaries", "compute_hillshade",
            "run_model_flood_scenario", "fetch_era5_reanalysis"}
    subset = [d for d in decls if d.name in keep]
    contents = build_contents_from_history(
        "Fetch the administrative boundary for Boulder County, Colorado.",
        chat_history=None,
    )
    print(f"[live] streaming Bedrock ({ba.bedrock_model_id()}) with {len(subset)} tools...")
    saw_text, saw_call, usage = [], [], None
    async for ev in ba.stream_bedrock(contents, tool_declarations=subset,
                                      system_prompt="You are GRACE, a geospatial hazard assistant. Call the right tool."):
        if isinstance(ev, TextDeltaEvent):
            saw_text.append(ev.delta)
        elif isinstance(ev, FunctionCallEvent):
            saw_call.append((ev.name, ev.call_id, ev.args))
        elif isinstance(ev, UsageMetadataEvent):
            usage = ev
    print(f"[live] text={''.join(saw_text)[:200]!r}")
    print(f"[live] tool_calls={saw_call}")
    print(f"[live] usage prompt={getattr(usage,'prompt_token_count',None)} "
          f"out={getattr(usage,'candidates_token_count',None)} "
          f"total={getattr(usage,'total_token_count',None)}")
    assert saw_call, "FAIL: Bedrock made no tool call"
    assert saw_call[0][0] == "fetch_administrative_boundaries", f"unexpected tool {saw_call[0]}"
    print("[live] PASS — Bedrock streamed a correct tool call with parsed args")


def main():
    decls = offline_catalog_conversion()
    asyncio.run(live_turn(decls))


if __name__ == "__main__":
    main()
