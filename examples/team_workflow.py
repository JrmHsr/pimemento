"""Team workflow: multiple users sharing memory.

Scenario:
    Alice (engineering) documents stack decisions and blockers.
    Bob (product) reads Alice's notes to plan the sprint.
    Carol (support) searches for deployment info to help a customer.

Each person uses their own user_id. The shared client_id connects them.
"""

import asyncio

from pimemento.backends.json_backend import JsonBackend
from pimemento.config import PimementoConfig
from pimemento import tools as T


async def main():
    config = PimementoConfig(memory_dir="/tmp/pimemento_team_demo")
    backend = JsonBackend(config)

    # Alice (engineering) saves stack context
    result = await T.save_memory(
        backend,
        config,
        None,  # no embedder in JSON mode
        category="project_config",
        type="insight",
        content="stack=React+Node | deploy=Vercel | blocker=auth_migration",
        reason="Documented current stack and main blocker",
        client_id="acme_project",
        user_id="alice",
        namespace="engineering",
        source_mcp="dev_tools",
    )
    print(f"Alice saved: {result}")

    # Alice also notes a decision
    result = await T.save_memory(
        backend,
        config,
        None,
        category="domain_context",
        type="decision",
        content="db=postgres | orm=prisma | cache=redis",
        reason="Team decided on data layer after sprint review",
        client_id="acme_project",
        user_id="alice",
        namespace="engineering",
    )
    print(f"Alice saved: {result}")

    # Bob (product) reads all context for the project
    result = await T.get_memory(
        backend,
        client_id="acme_project",
    )
    print(f"\nBob reads all context:\n{result}")

    # Bob can also filter to see only Alice's notes
    result = await T.get_memory(
        backend,
        client_id="acme_project",
        user_id="alice",
    )
    print(f"\nBob reads Alice's notes:\n{result}")

    # Carol (support) checks status
    result = await T.memory_status(
        backend,
        client_id="acme_project",
    )
    print(f"\nCarol checks status: {result}")

    # Search for deployment info
    result = await T.search_memory(
        backend,
        None,
        query="deploy",
        client_id="acme_project",
    )
    print(f"\nSearch 'deploy': {result}")


if __name__ == "__main__":
    asyncio.run(main())
