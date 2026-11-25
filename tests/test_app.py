import sqlite3
from pathlib import Path

import pytest

import db
import application as app_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.DB_PATH = tmp_path / "data" / "kitap.db"
    db.init_db()
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as client:
        yield client


def login(client):
    return client.post("/login", data={"username": "test", "password": "123"})


def test_login_required(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.location


def test_login_and_library(client):
    response = login(client)
    assert response.status_code == 302
    assert client.get("/", follow_redirects=True).status_code == 200


def test_manual_page_count_rule(client):
    login(client)
    response = client.post(
        "/ekle",
        data={"title": "Suç ve Ceza", "author": "Dostoyevski", "page_count": "0"},
        follow_redirects=True,
    )
    assert b"sayfa say" in response.data


def test_duplicate_book(client):
    login(client)
    client.post("/ekle", data={"title": "Suç ve Ceza", "author": "Dostoyevski", "page_count": "600"})
    response = client.post(
        "/ekle",
        data={"title": "Suç ve Ceza", "author": "Dostoyevski", "page_count": "600"},
        follow_redirects=True,
    )
    assert "Bu kitap zaten" in response.get_data(as_text=True)


def test_page_progress_cannot_exceed_total(client):
    login(client)
    client.post("/ekle", data={"title": "Suç ve Ceza", "author": "Dostoyevski", "page_count": "600", "status": "okunuyor"})
    conn = db.get_db()
    book_id = conn.execute("SELECT id FROM books").fetchone()["id"]
    conn.close()
    response = client.post(f"/kitap/{book_id}/ilerleme", data={"pages": "601"}, follow_redirects=True)
    assert "toplam sayfa sayısını aşamaz" in response.get_data(as_text=True)


def test_goal_status():
    assert app_module.goal_status(9, 24).endswith("15 kitap gerisindesin")
    assert app_module.goal_status(24, 24).endswith("hedefi 0 kitap geçtin")
