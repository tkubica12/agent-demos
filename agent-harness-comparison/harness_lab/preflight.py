from __future__ import annotations

import asyncio
import traceback

from .cli import briefs
from .core import ResearchRun


async def main() -> None:
    run = ResearchRun("webiq-probe", briefs()[0], "preflight")
    status = "failed"
    try:
        async with run.research_tools():
            result = await run.call(
                "search_web", {"query": "Azure Container Apps Jobs execution timeout Microsoft Learn"}
            )
        print(result)
        if not result.get("ok") or not result.get("sources"):
            raise RuntimeError("WebIQ search returned no usable evidence.")
        status = "passed"
    except BaseException as error:
        traceback.print_exception(error)
        raise
    finally:
        run.complete(status)


if __name__ == "__main__":
    asyncio.run(main())
