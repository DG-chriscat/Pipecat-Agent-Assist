from fastapi import FastAPI

from agent_assist_server.agent_token import router as agent_token_router
from agent_assist_server.browser_call import router as browser_call_router
from agent_assist_server.dialin import router as dialin_router
from agent_assist_server.queue_api import router as queue_router

app = FastAPI(title="Agent Assist Server")
app.include_router(dialin_router)
app.include_router(agent_token_router)
app.include_router(browser_call_router)
app.include_router(queue_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "agent-assist-server"}
