#!/usr/bin/env python3
"""GERÇEK MCP protokolü üzerinde IPI PoC (opsiyonel — sunum/demo için).

Bu, harness'teki süreç-içi simülasyonun gerçek MCP SDK karşılığıdır. P09 Şekil
11'i gerçek bir MCP host'unda (Claude Desktop, Cursor, MCP Inspector) göstermek
için kullanılır. Harness (run_experiment.py) BUNA ihtiyaç duymaz; nicel ölçüm
süreç-içi simülasyonla yapılır.

Kurulum:
    pip install "mcp[cli]"
Çalıştırma (stdio):
    python mcp_server/server.py
MCP Inspector ile:
    mcp dev mcp_server/server.py

GÜVENLİK: read_private_file tamamen SAHTE bir sabit döndürür. Gerçek dosya/ağ
erişimi yoktur. Bu server'ı yalnızca kendi yerel test host'una bağla.
"""
import json
import sys
import os

# lab/config.py yeniden kullan
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab import config
from lab.mcp_sim import build_issues

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.stderr.write(
        "HATA: 'mcp' paketi yok. Kur:  pip install \"mcp[cli]\"\n"
        "(Bu server opsiyoneldir; nicel deney için gerekmez.)\n"
    )
    sys.exit(1)

# Hangi payload'ın issue'ya gömüleceği (env ile seçilebilir)
PAYLOAD_ID = os.environ.get("IPI_PAYLOAD", "v1_naive")

mcp = FastMCP("ipi-lab-github")


@mcp.tool()
def list_issues(repo: str, state: str = "open") -> str:
    """Bir public GitHub deposundaki issue'ları listeler (laboratuvar/sentetik)."""
    issues = build_issues(PAYLOAD_ID)   # son issue gövdesinde δk var
    return json.dumps({"repo": repo, "state": state, "issues": issues},
                      ensure_ascii=False)


@mcp.tool()
def read_private_file(path: str) -> str:
    """Yerel 'gizli' dosyayı okur. (Laboratuvar: SAHTE sabit döndürür.)"""
    return json.dumps({"path": path, "content": config.FAKE_SECRET},
                      ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()   # stdio transport
