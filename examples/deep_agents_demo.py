"""Deep-Agents demo — thin end-to-end proof of the LangChain adapter.

Uses the real ``deepagents.backends.BaseSandbox`` surface via
:class:`~forge.langchain.ForgeSandbox`. Exercises what Deep-Agents actually
calls when a LangGraph tool hits the backend:

1. write / execute / read via the sandbox protocol methods.
2. glob + grep via ``BaseSandbox``'s default implementations (they route
   through ``aexecute``, so they exercise the Forge pool implicitly).
3. Snapshot the workspace and restore it into a new one, then run the same
   file in the restored workspace.

This does NOT invoke the LLM — Deep-Agents requires provider creds. Wiring
this backend into ``create_deep_agent(model=..., backend=backend)`` gives an
end-to-end agent.

Run::

    python examples/deep_agents_demo.py
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from forge.client import Forge
from forge.config import make_config
from forge.langchain import ForgeSandbox


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_config(Path(tmp))
        async with Forge.local(config=cfg) as forge:
            sandbox = await ForgeSandbox.afrom_thread(
                forge=forge, thread_id="demo-thread-1"
            )

            # 1. Write a file.
            wr = await sandbox.awrite("main.py", "print('hi from forge')\n")
            assert wr.error is None, wr.error
            print("[demo] wrote main.py")

            # 2. Execute it.
            result = await sandbox.aexecute("python main.py")
            print(
                f"[demo] exec exit={result.exit_code} "
                f"output={result.output.strip()!r} truncated={result.truncated}"
            )
            assert result.exit_code == 0

            # 3. Read it back through the sandbox surface.
            read = await sandbox.aread("main.py")
            assert read.error is None
            assert read.file_data is not None
            print(f"[demo] read back main.py encoding={read.file_data.get('encoding')}")
            assert "hi from forge" in read.file_data["content"]

            # 4. BaseSandbox's `agrep` routes through aexecute — quick sanity check.
            grep = await sandbox.agrep("hi from forge")
            assert grep.matches is not None
            print(f"[demo] grep found {len(grep.matches)} match(es)")

            # 5. Snapshot -> restore -> re-execute in the restored workspace.
            snap = await sandbox.workspace.snapshots.create(name="post-write")
            print(f"[demo] snapshot={snap.id}")

            restored_model = await forge._transport.restore_snapshot(  # noqa: SLF001
                snap.id, name="restored", metadata={}
            )
            restored = await forge.workspaces.get(restored_model.id)
            result2 = await restored.exec(["python", "main.py"])
            print(
                f"[demo] restored exec exit={result2.exit_code} "
                f"output={result2.output.strip()!r}"
            )
            assert result2.exit_code == 0
            print("[demo] all steps succeeded")


if __name__ == "__main__":
    asyncio.run(main())
