from fastapi import FastAPI

app = FastAPI(title="Agent Assist Server")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "agent-assist-server"}
