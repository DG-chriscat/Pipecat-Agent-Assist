from fastapi import FastAPI

from agent_assist_server.dialin import router as dialin_router

app = FastAPI(title="Agent Assist Server")
app.include_router(dialin_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "agent-assist-server"}
