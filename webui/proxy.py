#!/usr/bin/env python3
"""
Reverse proxy in front of llama-server's bundled WebUI (SvelteKit SPA,
embedded in the binary - no editable source files on disk). Passes every
request straight through to the real server, except the HTML document,
into which it injects a script that adds:

  1. ZafyaLM branding: replaces the llama.cpp logo with the ZafyaLM "Z"
     mark, retitles the app.
  2. A "For Clinicians / For Patients" green toggle pill.
  3. A window.fetch patch that prepends the matching system prompt to
     outgoing chat completion requests.
  4. A green blinking cursor while a response is streaming.

Usage: python3 proxy.py [listen_port] [upstream_port]
Default: listens on 8091, forwards to llama-server on 8080.
"""
import sys
import http.server
import http.client

LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
UPSTREAM_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
UPSTREAM_HOST = "127.0.0.1"

INJECTED_SCRIPT = b"""
<style id="zafya-injected-style">
  #zafya-toggle {
    position: fixed; top: 14px; right: 16px; z-index: 99999;
    display: flex; background: #f0fdf4; border: 1px solid #bbf7d0;
    border-radius: 999px; padding: 3px; gap: 2px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  #zafya-toggle button {
    border: none; background: transparent; padding: 7px 16px;
    border-radius: 999px; font-size: 13px; font-weight: 600;
    color: #166534; cursor: pointer; transition: all 0.15s ease;
  }
  #zafya-toggle button.zafya-active { background: #16a34a; color: white; }
  #zafya-toggle button:not(.zafya-active):hover { background: #dcfce7; }
  .zafya-cursor {
    display: inline-block; width: 8px; height: 16px;
    background: #22c55e; margin-left: 2px; vertical-align: text-bottom;
    animation: zafya-blink 0.9s step-start infinite;
  }
  @keyframes zafya-blink { 50% { opacity: 0; } }
  .zafya-logo { display: inline-flex; align-items: center; gap: 8px; }
  .zafya-logo svg { display: block; }
  .zafya-logo .zafya-name {
    font-weight: 700; font-size: 15px; letter-spacing: -0.01em; color: inherit;
  }
  .zafya-logo .zafya-name em {
    font-style: normal; color: #16a34a;
  }
</style>
<script id="zafya-injected-script">
(function() {
  var SYSTEM_PROMPTS = {
    clinician: "You are Zafya AI, a clinical decision-support assistant. Respond with concise, evidence-based clinical language appropriate for a healthcare professional: relevant differentials, red flags, and next diagnostic/management steps. Be aware of resource-limited settings (limited imaging follow-up, intermittent specialist access, common comorbidities like malaria, TB, HIV, sickle cell disease). Never state a definitive diagnosis or exact medication dosing. Be concise and direct. Keep answers under 250 words.",
    patient: "You are Zafya AI, a friendly health assistant speaking with a PATIENT, not a medical professional. Use simple, warm, honest language, avoid jargon, and explain any medical term you must use. Give practical next steps in plain language. Never give a diagnosis, medication advice, or dosing - always direct to a clinician. Keep answers short (2-4 sentences) unless asked for more detail."
  };
  window.__zafyaMode = window.__zafyaMode || "clinician";

  // ZafyaLM "Z" mark: rounded green tile, bold complete Z whose middle
  // stroke doubles as an ECG/heartbeat line - medical + AI in one glyph.
  var LOGO_SVG =
    '<svg width="30" height="30" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">' +
      '<defs><linearGradient id="zfg" x1="0" y1="0" x2="1" y2="1">' +
        '<stop offset="0" stop-color="#22c55e"/><stop offset="1" stop-color="#15803d"/>' +
      '</linearGradient></defs>' +
      '<rect x="1" y="1" width="46" height="46" rx="12" fill="url(#zfg)"/>' +
      '<path d="M12 12 H36 L15 36 H36" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path d="M15 24 h5 l2.5 -4.5 3 9 2.5 -4.5 h5" fill="none" stroke="#bbf7d0" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>' +
    '</svg>';

  function rebrand() {
    if (document.title.indexOf("ZafyaLM") === -1) {
      document.title = "ZafyaLM - Zafya AI";
    }
    // The app renders the active model's favicon inline as its "logo" -
    // there's no separate branded component, so target that directly by
    // content (src contains "favicon") rather than guessing by position.
    var faviconImgs = document.querySelectorAll('img[src*="favicon"], img[src*="logo"]');
    faviconImgs.forEach(function(img) {
      if (img.dataset.zafyaDone) return;
      img.dataset.zafyaDone = "1";
      var wrap = document.createElement("span");
      wrap.className = "zafya-logo";
      wrap.innerHTML = LOGO_SVG;
      var w = img.getBoundingClientRect().width || 24;
      wrap.querySelector("svg").setAttribute("width", w);
      wrap.querySelector("svg").setAttribute("height", w);
      img.replaceWith(wrap);
    });
    // Fallback: positional heuristic, in case the favicon isn't an <img>
    // (e.g. rendered as inline <svg> or CSS background-image) on some pages.
    if (!document.getElementById("zafya-logo-mark") && faviconImgs.length === 0) {
      var candidates = document.querySelectorAll("aside img, header img, nav img, a img, aside svg, header svg, [class*='sidebar'] img, [class*='sidebar'] svg");
      var target = null;
      for (var i = 0; i < candidates.length; i++) {
        var r = candidates[i].getBoundingClientRect();
        if (r.top < 90 && r.left < 320 && r.width > 12 && r.width < 80) { target = candidates[i]; break; }
      }
      if (target) {
        var wrap2 = document.createElement("span");
        wrap2.className = "zafya-logo";
        wrap2.id = "zafya-logo-mark";
        wrap2.innerHTML = LOGO_SVG + '<span class="zafya-name">Zafya<em>LM</em></span>';
        target.replaceWith(wrap2);
      }
    }
    // Swap visible "llama.cpp" text labels for ZafyaLM.
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    var n;
    while ((n = walker.nextNode())) {
      if (n.nodeValue && n.nodeValue.indexOf("llama.cpp") !== -1 && n.parentElement && n.parentElement.id !== "zafya-logo-mark") {
        n.nodeValue = n.nodeValue.replace(/llama\\.cpp/g, "ZafyaLM");
      }
    }
  }

  function buildToggle() {
    if (document.getElementById("zafya-toggle")) return;
    var bar = document.createElement("div");
    bar.id = "zafya-toggle";
    bar.innerHTML =
      '<button id="zafya-btn-clinician" class="zafya-active">For Clinicians</button>' +
      '<button id="zafya-btn-patient">For Patients</button>';
    document.body.appendChild(bar);
    document.getElementById("zafya-btn-clinician").onclick = function() { setMode("clinician"); };
    document.getElementById("zafya-btn-patient").onclick = function() { setMode("patient"); };
  }

  function setMode(mode) {
    window.__zafyaMode = mode;
    document.getElementById("zafya-btn-clinician").classList.toggle("zafya-active", mode === "clinician");
    document.getElementById("zafya-btn-patient").classList.toggle("zafya-active", mode === "patient");
  }

  var inFlight = 0;
  var cursorEl = null;
  function findLastMessageEl() {
    var candidates = document.querySelectorAll("main, [class*='chat'], [class*='message']");
    var best = null;
    candidates.forEach(function(el) { if (el.children.length > 0) best = el; });
    if (!best) return document.body;
    var last = best;
    while (last.lastElementChild) last = last.lastElementChild;
    return last.parentElement || best;
  }
  function ensureCursor() {
    if (cursorEl) return;
    cursorEl = document.createElement("span");
    cursorEl.className = "zafya-cursor";
    findLastMessageEl().appendChild(cursorEl);
  }
  function removeCursor() {
    if (cursorEl && cursorEl.parentElement) cursorEl.parentElement.removeChild(cursorEl);
    cursorEl = null;
  }

  // Single fetch patch: inject system prompt + track in-flight for cursor.
  var originalFetch = window.fetch;
  window.fetch = function(input, init) {
    var url = typeof input === "string" ? input : ((input && input.url) || "");
    var isChat = /\\/(v1\\/)?chat\\/completions/.test(url);
    if (isChat && init && typeof init.body === "string") {
      try {
        var body = JSON.parse(init.body);
        if (Array.isArray(body.messages)) {
          var sys = { role: "system", content: SYSTEM_PROMPTS[window.__zafyaMode] };
          if (body.messages[0] && body.messages[0].role === "system") {
            body.messages[0] = sys;
          } else {
            body.messages.unshift(sys);
          }
          init = Object.assign({}, init, { body: JSON.stringify(body) });
        }
      } catch (e) { /* pass original body through on parse issues */ }
    }
    if (isChat) { inFlight++; ensureCursor(); }
    var p = originalFetch.call(this, input, init);
    if (isChat) {
      var done = function() { inFlight--; if (inFlight <= 0) removeCursor(); };
      p.then(function(resp) {
        // The SPA reads the stream after fetch resolves; watch for stream end
        // by polling the response bodyUsed/locked state is unreliable, so we
        // simply remove the cursor when DOM stops changing for 3s.
        armIdleWatch();
        return resp;
      }, done);
    }
    return p;
  };

  var idleTimer = null;
  var observer = new MutationObserver(function() {
    if (!cursorEl) return;
    clearTimeout(idleTimer);
    idleTimer = setTimeout(function() { inFlight = 0; removeCursor(); }, 3000);
  });
  function armIdleWatch() {
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    clearTimeout(idleTimer);
    idleTimer = setTimeout(function() { inFlight = 0; removeCursor(); }, 8000);
  }

  function boot() { buildToggle(); rebrand(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
  setInterval(boot, 1200);
})();
</script>
</body>
"""


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self):
        upstream = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=600)
        body = None
        length = self.headers.get("Content-Length")
        if length:
            body = self.rfile.read(int(length))

        # llama-server's static index.html is only served pre-gzipped - it
        # 415s if the request doesn't declare gzip support. Keep gzip, then
        # decompress ourselves before the byte-replace.
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}
        headers["Accept-Encoding"] = "gzip"
        upstream.request(self.command, self.path, body=body, headers=headers)
        resp = upstream.getresponse()
        resp_headers = dict(resp.getheaders())
        content_type = resp_headers.get("Content-Type", "")

        # Never forward hop-by-hop / framing headers: http.client has already
        # decoded chunked transfer encoding, so passing Transfer-Encoding
        # through while writing raw bytes makes browsers hang forever waiting
        # for chunk framing that never arrives (the "no response" bug).
        HOP = ("connection", "transfer-encoding", "keep-alive")

        if "text/html" in content_type:
            raw = resp.read()
            if resp_headers.get("Content-Encoding", "").lower() == "gzip":
                import gzip
                data = gzip.decompress(raw)
            else:
                data = raw
            if b"</body>" in data:
                data = data.replace(b"</body>", INJECTED_SCRIPT, 1)
            self.send_response(resp.status)
            for k, v in resp_headers.items():
                if k.lower() not in HOP + ("content-length", "content-encoding"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif resp_headers.get("Content-Length") is not None:
            # Fixed-length response: pass through as-is.
            self.send_response(resp.status)
            for k, v in resp_headers.items():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.end_headers()
            remaining = int(resp_headers["Content-Length"])
            while remaining > 0:
                chunk = resp.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
        else:
            # Unknown-length (chunked upstream, e.g. SSE token streaming).
            # Re-frame with our own chunked encoding and flush per chunk so
            # streamed tokens reach the browser immediately.
            self.send_response(resp.status)
            for k, v in resp_headers.items():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(("%x\r\n" % len(chunk)).encode("ascii"))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        upstream.close()

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_OPTIONS(self):
        self._proxy()

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), ProxyHandler)
    print(f"Zafya AI proxy: http://127.0.0.1:{LISTEN_PORT}/  ->  upstream http://{UPSTREAM_HOST}:{UPSTREAM_PORT}/")
    server.serve_forever()
