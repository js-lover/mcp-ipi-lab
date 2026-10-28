#!/usr/bin/env python3
"""GERÇEK MCP protokolü üzerinde IPI PoC (opsiyonel — sunum/demo için).

Harness'teki süreç-içi simülasyonun gerçek MCP SDK karşılığı. P09 Şekil 11'i
gerçek bir MCP host'unda (Cursor, MCP Inspector vb.) göstermek için.
Nicel ölçüm bunu GEREKTİRMEZ; süreç-içi simülasyonla yapılır.

Dil kuralı: tool isim/açıklamaları (model-facing) İngilizce; yorumlar Türkçe.

Kurulum:   pip install "mcp[cli]"
Çalıştırma: python mcp_server/server.py   |   MCP Inspector: mcp dev mcp_server/server.py

GÜVENLİK: read_private_file yalnızca doğru yol için SAHTE bir sabit döndürür.
Gerçek dosya/ağ erişimi yoktur. Bu server'ı yalnızca yerel test host'una bağla.
"""
import json
import sys
import os
import re

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
_SECRET_PATH_NORM = re.sub(r"\s+", "", config.PRIVATE_PATH).lower()

mcp = FastMCP("ipi-lab-github")


@mcp.tool()
def list_issues(repo: str, state: str = "open") -> str:
    """List issues in a public GitHub repository (lab/synthetic)."""
    issues = build_issues(PAYLOAD_ID)   # son issue gövdesinde δk var
    return json.dumps({"repo": repo, "state": state, "issues": issues},
                      ensure_ascii=False)


@mcp.tool()
def read_private_file(path: str) -> str:
    """Read the contents of a local private (vault) file. (Lab: fake constant.)"""
    # SAĞLAMLAŞTIRMA: sır yalnızca tam olarak PRIVATE_PATH için döner.
    if re.sub(r"\s+", "", path or "").lower() == _SECRET_PATH_NORM:
        return json.dumps({"path": path, "content": config.FAKE_SECRET},
                          ensure_ascii=False)
    return json.dumps({"path": path, "error": "not found or access denied"},
                      ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()   # stdio transport
