"""Entry point — builds and starts NapyClaw."""
import asyncio
import sys

from napyclaw.config import Config, ConfigError


async def main() -> None:
    try:
        config = Config.from_infisical()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # NapyClaw orchestrator is wired in Plan 4 (app.py)
    print(f"napyclaw starting with model {config.default_model} on {config.default_provider}")
    print("NapyClaw app not yet wired — see Plan 4.")


if __name__ == "__main__":
    asyncio.run(main())
