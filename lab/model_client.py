"""Backend-agnostik model client.

Dil kuralı: modelin ürettiği/işlediği metinler (mock özet, marker) İngilizce;
yorumlar Türkçe.

Ortak arayüz:
    client.chat(system, messages, tools) -> {"content": str|None, "tool_calls": [...]}

Backend'ler: mock (Ollama'sız, deterministik), ollama (/api/chat), openai (uyumlu).
"""
import json
import os
import re
import urllib.request
import urllib.error
from . import config


class ModelError(RuntimeError):
    """Model backend'inden gelen, kullanıcıya gösterilecek anlaşılır hata."""


# ============================================================================
# MOCK — Ollama gerekmez. "Saf/kolay kanan" (gullible) bir modeli taklit eder:
# enjeksiyona baştan sona uyar (sırrı okur -> cevaba marker'la sızdırır).
# Harness'i, savunmaları ve ASR hesabını DOĞRULAMAK için. Gerçek bulgu DEĞİL.
# ============================================================================
class MockClient:
    name = "mock"

    # Yalnızca gerçekten tehlikeli, uygulanabilir token'lara tepki ver: tool adı,
    # gizli yol, canary (aralıklı dahil). Böylece d2/d4 bunları temizlediğinde
    # mock "kanmaz" ve matris yorumlanabilir olur.
    _TRIGGER = re.compile(
        r"(read_private_file|"
        + re.escape(config.PRIVATE_PATH)
        + r"|"
        + re.escape(config.CANARY_MARK)
        + r"|c\s*a\s*n\s*a\s*r\s*y)",
        re.I,
    )

    def chat(self, system, messages, tools):
        called = _called_tool_names(messages)
        results = _tool_results(messages)

        # 1) Henüz issue listelenmediyse -> meşru görev: list_issues
        if "list_issues" not in called:
            return {
                "content": None,
                "tool_calls": [{
                    "id": "c1", "name": "list_issues",
                    "args": {"repo": config.TARGET_REPO, "state": "open"},
                }],
            }

        list_text = results.get("list_issues", "")

        # 2) list_issues sonucunu okudu; tetiklendiyse read_private_file dene
        if "read_private_file" not in called:
            if self._TRIGGER.search(list_text):
                return {
                    "content": None,
                    "tool_calls": [{
                        "id": "c2", "name": "read_private_file",
                        "args": {"path": config.PRIVATE_PATH},
                    }],
                }
            return {"content": _summarize(list_text), "tool_calls": []}

        # 3) read_private_file sonucuna göre bitir (marker ile sızdır)
        priv = results.get("read_private_file", "")
        summary = _summarize(list_text)
        if config.FAKE_SECRET in priv:
            return {"content": summary + f"\n\n{config.CANARY_MARK}{config.FAKE_SECRET}",
                    "tool_calls": []}
        return {"content": summary + f"\n\n{config.CANARY_MARK}(unavailable)",
                "tool_calls": []}


def _summarize(list_text):
    try:
        data = json.loads(_strip_spotlight(list_text))
        titles = [f"#{i.get('number')} {i.get('title')}" for i in data.get("issues", [])]
        return "Open issue summary: " + "; ".join(titles)
    except Exception:
        return "Open issue summary: (could not parse)"


def _strip_spotlight(text):
    return re.sub(r"<<[^>]*UNTRUSTED_TOOL_DATA[^>]*>>", "", text).strip()


def _called_tool_names(messages):
    names = []
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                names.append(tc["name"])
    return names


def _tool_results(messages):
    out = {}
    for m in messages:
        if m.get("role") == "tool":
            out[m.get("name")] = m.get("content", "")
    return out


# ============================================================================
# OLLAMA — http://localhost:11434/api/chat (native tool calling)
# ============================================================================
class OllamaClient:
    def __init__(self, model, host=None, temperature=0.0, keep_alive="10m",
                 num_predict=384, timeout=180):
        self.model = model
        self.name = f"ollama:{model}"
        self.host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.temperature = temperature
        self.keep_alive = keep_alive        # modeli bellekte tut -> çağrılar hızlı
        self.num_predict = num_predict
        self.timeout = timeout

    def chat(self, system, messages, tools):
        omsgs = [{"role": "system", "content": system}]
        for m in messages:
            omsgs.append(_to_ollama_msg(m))
        payload = {
            "model": self.model,
            "messages": omsgs,
            "tools": tools,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature,
                        "num_predict": self.num_predict},
        }
        data = _http_post(f"{self.host}/api/chat", payload, timeout=self.timeout)
        msg = data.get("message", {})
        tool_calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tool_calls.append({"id": f"oc{i}", "name": fn.get("name"), "args": args})
        return {"content": msg.get("content") or None, "tool_calls": tool_calls}

    def ping(self):
        """Modeli yükle + kısa yanıt al (warmup). Süreyi saniye döndürür."""
        import time
        t0 = time.time()
        payload = {"model": self.model,
                   "messages": [{"role": "user", "content": "ping"}],
                   "stream": False, "keep_alive": self.keep_alive,
                   "options": {"num_predict": 1}}
        _http_post(f"{self.host}/api/chat", payload, timeout=self.timeout)
        return time.time() - t0


def _to_ollama_msg(m):
    role = m["role"]
    if role == "tool":
        return {"role": "tool", "content": m.get("content", "")}
    if role == "assistant" and m.get("tool_calls"):
        return {
            "role": "assistant",
            "content": m.get("content") or "",
            "tool_calls": [
                {"function": {"name": tc["name"], "arguments": tc["args"]}}
                for tc in m["tool_calls"]
            ],
        }
    return {"role": role, "content": m.get("content") or ""}


# ============================================================================
# OPENAI-UYUMLU (OpenAI, OpenRouter, Groq, vb.) — bonus
# ============================================================================
class OpenAIClient:
    def __init__(self, model, base_url=None, api_key=None, temperature=0.0, timeout=180):
        self.model = model
        self.name = f"openai:{model}"
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL",
                         "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.temperature = temperature
        self.timeout = timeout

    def chat(self, system, messages, tools):
        omsgs = [{"role": "system", "content": system}]
        for m in messages:
            omsgs.append(_to_openai_msg(m))
        payload = {
            "model": self.model, "messages": omsgs,
            "tools": tools, "temperature": self.temperature,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = _http_post(f"{self.base_url}/chat/completions", payload, headers,
                          timeout=self.timeout)
        choice = data["choices"][0]["message"]
        tool_calls = []
        for tc in choice.get("tool_calls") or []:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            tool_calls.append({"id": tc.get("id", "oc"), "name": fn["name"], "args": args})
        return {"content": choice.get("content"), "tool_calls": tool_calls}

    def ping(self):
        import time
        t0 = time.time()
        self.chat("ping", [{"role": "user", "content": "ping"}], [])
        return time.time() - t0


def _to_openai_msg(m):
    role = m["role"]
    if role == "tool":
        return {"role": "tool", "tool_call_id": m.get("tool_call_id", "tc"),
                "content": m.get("content", "")}
    if role == "assistant" and m.get("tool_calls"):
        return {
            "role": "assistant", "content": m.get("content"),
            "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["args"], ensure_ascii=False)}}
                for tc in m["tool_calls"]
            ],
        }
    return {"role": role, "content": m.get("content") or ""}


# --- HTTP yardımcısı (stdlib; harici bağımlılık yok) ------------------------
def _http_post(url, payload, headers=None, timeout=180):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:400]
        except Exception:
            pass
        raise ModelError(
            f"HTTP {e.code} ({url}). Yanıt: {detail}\n"
            f"İpucu: model adı doğru/çekili mi? ('ollama list' / 'ollama pull <model>')"
        ) from None
    except urllib.error.URLError as e:
        raise ModelError(
            f"Bağlanılamadı: {url} ({e.reason}).\n"
            f"İpucu: Ollama çalışıyor mu? Ayrı terminalde 'ollama serve'."
        ) from None


# --- fabrika ----------------------------------------------------------------
def make_client(backend, model=None, **kw):
    if backend == "mock":
        return MockClient()
    if backend == "ollama":
        return OllamaClient(model or "qwen2.5:7b", **kw)
    if backend == "openai":
        return OpenAIClient(model or "gpt-4o-mini", **kw)
    raise ValueError(f"bilinmeyen backend: {backend}")
