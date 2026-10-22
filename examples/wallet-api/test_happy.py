from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

def test_happy_transfer():
    a = client.post("/accounts", json={"owner": "alice", "initial_balance": 100}).json()
    b = client.post("/accounts", json={"owner": "bob", "initial_balance": 0}).json()
    r = client.post("/transfers",
                    json={"from_account": a["id"], "to_account": b["id"], "amount": 40})
    assert r.status_code == 200
    assert client.get(f"/accounts/{b['id']}/balance").json()["balance"] == 40
