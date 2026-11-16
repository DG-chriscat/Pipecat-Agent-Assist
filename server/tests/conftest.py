"""Server tests: no warm bot pool unless a test asks for one (P5-2).

The app's lifespan starts WARM_BOT_POOL warm bot processes; `with TestClient(app)` in
unit tests would otherwise spawn real bots. Live tests that measure the pool set
WARM_BOT_POOL themselves (monkeypatch) or build their own BotLauncher.
"""

import os

os.environ.setdefault("WARM_BOT_POOL", "0")
