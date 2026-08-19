"""Pruebas del servidor: versión, anti-caché y servido del frontend."""

import json
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
    d = client.get("/version").json()
    assert d["version"] == esperado
    # el build lleva ademas una huella del frontend, y el servidor informa
    # con que build arranco para poder detectar que quedo desactualizado
    assert d["build"].startswith(esperado + ".")
    assert set(d) == {"version", "build", "process", "server_stale"}


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


def test_version_is_read_per_request_not_frozen_at_import(client):
    """Si el usuario actualiza con el servidor abierto, el servidor tiene
    que notarlo: con la version congelada al importar seguiria sellando
    con la anterior y la app mostraria lo viejo sin avisar."""
    original = main.VERSION_FILE.read_bytes()
    try:
        main.VERSION_FILE.write_bytes(b"9999.99.99-test\n")
        d = client.get("/version").json()
        assert d["version"] == "9999.99.99-test"
        assert d["build"].startswith("9999.99.99-test.")
        assert client.get("/").text.count(f"?v={d['build']}") >= 3
    finally:
        main.VERSION_FILE.write_bytes(original)


def test_frontend_change_moves_the_build_id(client):
    """El id de build incluye una huella de los archivos del frontend, que
    es lo que permite distinguir 'pagina vieja' de 'pagina al dia'."""
    ruta = main.FRONTEND_DIR / "app.js"
    original = ruta.read_bytes()
    antes = client.get("/version").json()["build"]
    try:
        ruta.write_bytes(original + b"\n// cambio\n")
        despues = client.get("/version").json()["build"]
        assert despues != antes
    finally:
        ruta.write_bytes(original)
    assert client.get("/version").json()["build"] == antes


def test_reports_when_the_running_server_is_older_than_the_files(client):
    """El caso que dejaba al usuario actualizando en circulos."""
    ruta = main.FRONTEND_DIR / "app.js"
    original = ruta.read_bytes()
    try:
        ruta.write_bytes(original + b"\n// actualizado con el server abierto\n")
        d = client.get("/version").json()
        assert d["server_stale"] is True
        assert d["build"] != d["process"]
    finally:
        ruta.write_bytes(original)
    assert client.get("/version").json()["server_stale"] is False


def test_every_path_that_resolves_to_the_index_is_stamped(client):
    # "//" y "/index.html/" los resuelve StaticFiles: sin interceptarlos
    # se colaba el index crudo, sin sello y cacheable
    for ruta in ("/", "//", "/index.html", "/index.html/"):
        r = client.get(ruta)
        assert r.status_code == 200, ruta
        assert "?v=" in r.text, ruta
        assert "no-store" in r.headers.get("cache-control", ""), ruta


def test_health_still_works(client):
    body = client.get("/health").json()
    assert body["ok"] is True


def test_diagnostico_reports_what_the_server_actually_sees(client):
    d = client.get("/diagnostico").json()
    assert set(d) == {"carpeta", "archivo_clave", "archivo_clave_existe",
                      "modulo_databento", "clave_configurada",
                      "clave_termina_en"}
    # la ruta del .env es lo que permite notar que datos-reales.bat se
    # corrio en otra copia de la carpeta
    assert d["archivo_clave"].endswith(".env")
    assert d["carpeta"] in d["archivo_clave"]


def test_diagnostico_never_leaks_the_key(client, monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-SUPERSECRETA-1234")
    d = client.get("/diagnostico").json()
    assert d["clave_configurada"] is True
    assert d["clave_termina_en"] == "1234"
    assert "SUPERSECRETA" not in json.dumps(d)


def test_verificar_clave_sin_clave(client, monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    r = client.get("/verificar-clave").json()
    assert r["ok"] is False
    assert r["caso"] in ("sin_clave", "sin_modulo")
    assert r["mensaje"]
