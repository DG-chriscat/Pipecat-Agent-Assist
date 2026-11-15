from agent_assist_server.app import app


def test_app_title():
    assert app.title == "Agent Assist Server"
