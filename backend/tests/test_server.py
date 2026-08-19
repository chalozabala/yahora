"""Pruebas del servidor: versión, anti-caché y servido del frontend."""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_version_endpoint_matches_version_file(client):
    esperado = main.VERSION_FILE.read_text(encoding="utf-8-sig").strip()
    assert esperado, "el archivo VERSION no puede estar vacio"
    assert client.get("/version").json() == {"version": esperado}


def test_index_stamps_assets_with_the_version(client):
    html = client.get("/").text
    assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css)[^"]*)"', html)
    assert assets, "el index deberia referenciar js/css"
    for a in assets:
        assert f"?v={main.VERSION}" in a, f"sin sello de version: {a}"


def test_index_html_path_is_stamped_too(client):
    # entrar por /index.html no puede saltearse el sellado, o la pagina
    # quedaria sin version y se reportaria como vieja para siempre
    html = client.get("/index.html").text
    assert f"?v={main.VERSION}" in html


def test_index_is_never_cached(client):
    for metodo in ("get", "head"):
        r = getattr(client, metodo)("/")
        assert r.status_code == 200
        # sin esto el navegador sigue sirviendo la pagina vieja, que es
        # justo el problema que estas cabeceras vienen a resolver
        assert "no-store" in r.headers.get("cache-control", "")


def test_stamped_assets_are_served(client):
    for asset in ("app.js", "backtest.js", "style.css"):
        r = client.get(f"/{asset}?v={main.VERSION}")
        assert r.status_code == 200, asset
        assert len(r.content) > 100


def test_health_still_works(client):
    body = client.get("/health").json()
    assert body["ok"] is True
