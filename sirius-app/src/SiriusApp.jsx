// SiriusApp.jsx - Interface multiplataforma do S.I.R.I.U.S.
//
// Arquitetura:
//   Qualquer dispositivo (celular, tablet, outro PC)
//     |
//     |  WebSocket ws://IP:5000/ws
//     |  REST      http://IP:5000
//     V
//   sirius_server.py no PC do Carlos
//     ├- cerebro.py   -> processa comandos
//     ├- memoria.py   -> historico, macros
//     ├- controle_pc  -> controla o PC do Carlos
//     ├- aprendizado  -> autodidata, retreino
//     └- proativo     -> lembretes, alertas
//
// O app NAO tem logica local — so envia e exibe.
// Protocolo WebSocket (sirius_server.py):
//   -> { "texto": "comando" }
//   <- { "tipo": "sirius"|"usuario"|"estado"|"proativo"|"log"|"bem_vindo",
//        "texto": "...", "timestamp": "..." }

import { useState, useEffect, useRef, useCallback } from "react"

const K_URL = "sirius_url"
const K_TTS = "sirius_tts"
const K_USER = "sirius_user_id"
const K_DEVICE = "sirius_device_name"
const K_DEVICE_ID = "sirius_device_id"
const MAX_INPUT_LENGTH = 2000
const MAX_CHAT_MESSAGES = 50

const getURL = () => localStorage.getItem(K_URL) || ""
const getTTS = () => localStorage.getItem(K_TTS) !== "false"
const getUserId = () => localStorage.getItem(K_USER) || "guest"
const getDeviceName = () => localStorage.getItem(K_DEVICE) || navigator.platform || "navegador"
const getDeviceId = () => localStorage.getItem(K_DEVICE_ID) || ""

const sanitizeServerUrl = (value) => {
  return String(value || "")
    .trim()
    .replace(/^https?:\/\//, "")
    .replace(/^ws:\/\//, "")
    .replace(/\/$/, "")
}

const sanitizeUserInput = (value) => {
  return String(value || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[\x00-\x09\x0B\x0C\x0E-\x1F\x7F]/g, "")
    .trim()
    .slice(0, MAX_INPUT_LENGTH)
}

const normalizeDeviceId = (text) => {
  return String(text || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim()
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_-]/g, "") || "device"
}

const getDeviceInfo = () => {
  const deviceName = getDeviceName()
  let deviceId = getDeviceId()
  if (!deviceId) {
    deviceId = normalizeDeviceId(deviceName)
    localStorage.setItem(K_DEVICE_ID, deviceId)
  }
  return { deviceId, deviceName }
}

const saveURL = (u) => localStorage.setItem(K_URL, u)
const saveTTS = (v) => localStorage.setItem(K_TTS, String(v))
const saveUserId = (v) => localStorage.setItem(K_USER, v)
const saveDeviceName = (v) => localStorage.setItem(K_DEVICE, v)

function falarLocal(texto) {
  if (!getTTS() || !window.speechSynthesis) return
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(texto)
  u.lang = "pt-BR"
  u.rate = 1.05
  const vozes = window.speechSynthesis.getVoices()
  const ptVoz = vozes.find(v => v.lang.startsWith("pt"))
  if (ptVoz) u.voice = ptVoz
  window.speechSynthesis.speak(u)
}

const C = {
  azul:    "#5DE2FF",
  verde:   "#00FF88",
  amarelo: "#FFD700",
  branco:  "#FFFFFF",
  fundo:   "#000A12",
  borda:   "#1a3a4a",
  painel:  "rgba(0,10,20,0.97)",
}

const ESTADOS = {
  STANDBY:     { cor: C.azul,    label: "standby"      },
  OUVINDO:     { cor: C.verde,   label: "ouvindo"      },
  PROCESSANDO: { cor: C.amarelo, label: "processando"  },
  FALANDO:     { cor: C.branco,  label: "falando"      },
}

export default function SiriusApp() {
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState("")
  const [url, setUrl] = useState(getURL())
  const [urlTemp, setUrlTemp] = useState(getURL())
  const [conectado, setConectado] = useState(false)
  const [estado, setEstado] = useState("STANDBY")
  const [tentativas, setTentativas] = useState(0)
  const [telaCfg, setTelaCfg] = useState(!getURL())
  const [status, setStatus] = useState(null)
  const [tts, setTts] = useState(getTTS())
  const [digitando, setDigitando] = useState(false)
  const [isLoading, setIsLoading] = useState(false)

  const wsRef = useRef(null)
  const chatRef = useRef(null)
  const reconnRef = useRef(null)
  const inputRef = useRef(null)
  const tentRef = useRef(0)

  const pushMsg = useCallback((msg) => {
    setMsgs(prev => [...prev.slice(-MAX_CHAT_MESSAGES + 1), msg])
  }, [])

  const addMsg = useCallback((tipo, texto, ts = null) => {
    const safeText = String(texto || "").trim()
    if (!safeText) return
    pushMsg({ tipo, texto: safeText, ts: ts || new Date().toISOString(), id: Math.random() })
  }, [pushMsg])

  const conectar = useCallback((serverUrl) => {
    const host = sanitizeServerUrl(serverUrl)
    if (!host) {
      addMsg("sistema", "✗ Endereco do servidor invalido.")
      return
    }

    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
    }

    clearTimeout(reconnRef.current)

    const wsUrl = `ws://${host}/ws`
    let ws

    try {
      ws = new WebSocket(wsUrl)
    } catch (error) {
      addMsg("sistema", `✗ URL invalida: ${wsUrl}`)
      return
    }

    wsRef.current = ws

    ws.onopen = () => {
      setConectado(true)
      setEstado("STANDBY")
      tentRef.current = 0
      setTentativas(0)
      addMsg("sistema", `✓ Conectado - ${host}`)
      const userId = getUserId()
      const { deviceId, deviceName } = getDeviceInfo()
      saveUserId(userId)
      saveDeviceName(deviceName)
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ user_id: userId, device_id: deviceId, device_name: deviceName }))
      }
      sendHeartbeat()
      fetch(`http://${host}/status`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setStatus(d) })
        .catch(() => {})
    }

    ws.onmessage = ({ data }) => {
      let d
      try {
        d = JSON.parse(data)
      } catch {
        addMsg("sirius", data)
        return
      }

      const { tipo, texto, estado: est, mensagem, timestamp } = d

      if (tipo === "sirius" || tipo === "proativo") {
        addMsg("sirius", texto, timestamp)
        falarLocal(texto)
        setDigitando(false)
        setEstado("STANDBY")
        setIsLoading(false)
      } else if (tipo === "bem_vindo") {
        addMsg("sistema", mensagem || "Conectado ao Sirius.")
        if (est) setEstado(est)
      } else if (tipo === "estado") {
        setEstado(est || "STANDBY")
        setDigitando(est === "PROCESSANDO")
        if (est === "FALANDO" && texto) falarLocal(texto)
        if (est === "STANDBY" || est === "OUVINDO") {
          setDigitando(false)
          setIsLoading(false)
        }
      } else if (tipo === "log") {
        console.log("[SIRIUS]", texto)
      }
    }

    ws.onclose = () => {
      setConectado(false)
      setEstado("STANDBY")
      setDigitando(false)
      setIsLoading(false)
      tentRef.current += 1
      setTentativas(tentRef.current)
      const delay = Math.min(2000 * 2 ** Math.min(tentRef.current - 1, 4), 30000)
      reconnRef.current = setTimeout(() => conectar(serverUrl), delay)
    }

    ws.onerror = () => {
      setIsLoading(false)
    }
  }, [addMsg])

  const sendHeartbeat = useCallback(() => {
    const ws = wsRef.current
    if (ws?.readyState !== WebSocket.OPEN) return
    const { deviceId, deviceName } = getDeviceInfo()
    ws.send(JSON.stringify({
      tipo: "heartbeat",
      device_id: deviceId,
      device_name: deviceName,
      has_focus: document.hasFocus(),
    }))
  }, [])

  useEffect(() => {
    if (url) conectar(url)
    return () => {
      clearTimeout(reconnRef.current)
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close() }
    }
  }, [url, conectar])

  useEffect(() => {
    if (!conectado) return
    sendHeartbeat()
    const interval = setInterval(sendHeartbeat, 20000)
    const handleVisibilidade = () => sendHeartbeat()
    window.addEventListener("visibilitychange", handleVisibilidade)
    window.addEventListener("focus", handleVisibilidade)
    window.addEventListener("blur", handleVisibilidade)
    return () => {
      clearInterval(interval)
      window.removeEventListener("visibilitychange", handleVisibilidade)
      window.removeEventListener("focus", handleVisibilidade)
      window.removeEventListener("blur", handleVisibilidade)
    }
  }, [conectado, sendHeartbeat])

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight
    }
  }, [msgs, digitando])

  useEffect(() => {
    if (!conectado || !url) return
    const host = sanitizeServerUrl(url)
    const timer = setInterval(() => {
      fetch(`http://${host}/status`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setStatus(d) })
        .catch(() => {})
    }, 15000)
    return () => clearInterval(timer)
  }, [conectado, url])

  const fmtHora = (ts) => {
    try { return new Date(ts).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) }
    catch { return "" }
  }

  const enviar = async () => {
    const txt = sanitizeUserInput(input)
    if (!txt) return

    const msg = { tipo: "usuario", texto: txt, ts: new Date().toISOString(), id: Math.random() }
    pushMsg(msg)

    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      setIsLoading(true)
      try {
        ws.send(JSON.stringify({ texto: txt }))
        setInput("")
      } catch (error) {
        setIsLoading(false)
        addMsg("sistema", "✗ Erro de envio. A mensagem permanece no chat.")
      }
      return
    }

    const host = sanitizeServerUrl(url)
    if (!host) {
      addMsg("sistema", "✗ Endereco do servidor invalido.")
      return
    }

    setIsLoading(true)
    try {
      const response = await fetch(`http://${host}/comando`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto: txt }),
      })
      if (!response.ok) throw new Error("erro de rede")
      const data = await response.json()
      if (data?.resposta) {
        addMsg("sirius", data.resposta)
      } else {
        addMsg("sistema", "✗ Servidor respondeu sem resposta valida.")
      }
      setInput("")
    } catch (error) {
      addMsg("sistema", "Servidor offline ou erro de rede. A mensagem permanece no chat.")
    } finally {
      setIsLoading(false)
    }
  }

  const salvarConfig = () => {
    const u = sanitizeServerUrl(urlTemp)
    if (!u) return
    saveURL(u)
    setUrl(u)
    setTelaCfg(false)
    setMsgs([])
  }

  const toggleTTS = () => {
    const n = !tts
    setTts(n)
    saveTTS(n)
    if (!n) window.speechSynthesis?.cancel()
  }

  const { cor: corEst, label: lblEst } = ESTADOS[estado] || ESTADOS.STANDBY
  const canSend = !isLoading && sanitizeUserInput(input).length > 0

  if (telaCfg) return (
    <div style={S.tela}>
      <div style={S.cfgCentro}>
        <div style={S.cfgBox}>
          <div style={S.cfgLogo}>◆ S.I.R.I.U.S.</div>
          <div style={S.cfgLabel}>ENDERECO DO SERVIDOR</div>
          <div style={S.cfgDica}>
            Ex: <b style={{ color: C.azul }}>192.168.1.10:5000</b><br/>
            Aparece no terminal do PC do Carlos ao iniciar o Sirius.
          </div>
          <input
            style={S.cfgInput}
            value={urlTemp}
            onChange={e => setUrlTemp(e.target.value)}
            onKeyDown={e => e.key === "Enter" && salvarConfig()}
            placeholder="192.168.1.10:5000"
            autoFocus
            maxLength={MAX_INPUT_LENGTH}
          />
          <button style={S.btnP} onClick={salvarConfig}>CONECTAR</button>
          {url && (
            <button style={S.btnS} onClick={() => setTelaCfg(false)}>Cancelar</button>
          )}
          <div style={S.cfgRodape}>
            Memoria · Processamento · Aprendizado<br/>
            executam no PC do servidor
          </div>
        </div>
      </div>
    </div>
  )

  return (
    <div style={S.tela}>
      <div style={S.header}>
        <div style={S.hL}>
          <div style={{ ...S.dot, background: corEst, boxShadow: `0 0 7px ${corEst}66` }}/>
          <span style={S.titulo}>S.I.R.I.U.S.</span>
        </div>
        <div style={S.hR}>
          <span style={{ ...S.estadoTag, color: corEst }}>{lblEst}</span>
          <button style={{ ...S.btnIco, color: tts ? C.azul : "rgba(93,226,255,.2)" }}
                  onClick={toggleTTS} title={tts ? "Voz ativa" : "Voz desativada"}>
            🔊
          </button>
          <button style={S.btnIco} onClick={() => setTelaCfg(true)} title="Configurar">⚙</button>
        </div>
      </div>

      {status && conectado && (
        <div style={S.statusBar}>
          {status.cpu_pct != null && (
            <span style={status.cpu_pct > 80 ? { color: C.amarelo } : {}}>
              CPU {status.cpu_pct}%
            </span>
          )}
          {status.ram_pct != null && (
            <span style={status.ram_pct > 85 ? { color: C.amarelo } : {}}>
              RAM {status.ram_pct}%
            </span>
          )}
          {status.conta && <span style={{ color: C.verde }}>● {status.conta}</span>}
          <span style={{ opacity: .25 }}>|</span>
          <span style={{ opacity: .5 }}>{url.replace(/^https?:\/\//, "")}</span>
          {status.ws_clientes > 1 && (
            <span style={{ opacity: .35 }}>{status.ws_clientes} clientes</span>
          )}
        </div>
      )}

      {!conectado && (
        <div style={S.banner}>
          ⚠ Sem conexao · tentativa {tentativas} ·{" "}
          <span style={{ color: C.amarelo, cursor: "pointer" }}
                onClick={() => { tentRef.current = 0; conectar(url) }}>
            tentar agora
          </span>
        </div>
      )}

      <div style={S.chat} ref={chatRef}>
        {msgs.length === 0 && !digitando && (
          <div style={S.vazio}>
            <div style={{ fontSize: 38, opacity: .15, marginBottom: 10 }}>◆</div>
            <div style={{ opacity: .28, fontSize: 12 }}>
              {conectado ? "Aguardando comandos..." : "Conectando..."}
            </div>
          </div>
        )}

        {msgs.map(m => (
          <div key={m.id} style={{
            ...S.msg,
            ...(m.tipo === "usuario" ? S.mUser   : {}),
            ...(m.tipo === "sirius"  ? S.mSirius : {}),
            ...(m.tipo === "sistema" ? S.mSist   : {}),
          }}>
            {m.tipo === "sirius" && <div style={S.tagSirius}>◆ SIRIUS</div>}
            <div>
              {m.texto.split("\n").map((l, i, a) =>
                <span key={i}>{l}{i < a.length - 1 && <br/>}</span>
              )}
            </div>
            {m.tipo !== "sistema" && <div style={S.hora}>{fmtHora(m.ts)}</div>}
          </div>
        ))}

        {digitando && (
          <div style={{ ...S.msg, ...S.mSirius, opacity: .55 }}>
            <div style={S.tagSirius}>◆ SIRIUS</div>
            <div style={{ letterSpacing: 3, fontSize: 16 }}>• • •</div>
          </div>
        )}
      </div>

      <div style={S.inputArea}>
        <input
          ref={inputRef}
          style={S.input}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); enviar() } }}
          placeholder="Comando ou pergunta..."
          maxLength={MAX_INPUT_LENGTH}
          disabled={isLoading}
        />
        <button style={S.btnEnv} onClick={enviar} disabled={!canSend}>
          {isLoading ? "Enviando..." : "▶"}
        </button>
      </div>
    </div>
  )
}

const S = {
  tela:     { background: C.fundo, color: C.azul, fontFamily: "'Consolas','Courier New',monospace", height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden" },
  header:   { padding: "10px 15px", borderBottom: `1px solid ${C.borda}`, display: "flex", justifyContent: "space-between", alignItems: "center", background: "rgba(0,12,25,.97)", flexShrink: 0 },
  hL:       { display: "flex", alignItems: "center", gap: 9 },
  hR:       { display: "flex", alignItems: "center", gap: 8 },
  dot:      { width: 8, height: 8, borderRadius: "50%", flexShrink: 0, transition: "background .4s, box-shadow .4s" },
  titulo:   { fontSize: 13, fontWeight: "bold", letterSpacing: 4, color: C.azul },
  estadoTag:{ fontSize: 11, letterSpacing: 2, textTransform: "uppercase", transition: "color .3s" },
  btnIco:   { background: "transparent", border: "none", color: "rgba(93,226,255,.3)", cursor: "pointer", fontSize: 16, padding: "2px 5px", borderRadius: 4 },
  statusBar:{ display: "flex", gap: 14, padding: "4px 15px", fontSize: 11, color: "rgba(93,226,255,.4)", background: "rgba(0,7,15,.9)", borderBottom: `1px solid ${C.borda}`, flexShrink: 0, flexWrap: "wrap" },
  banner:   { background: "rgba(255,100,0,.07)", borderBottom: "1px solid rgba(255,150,0,.2)", color: C.amarelo, fontSize: 12, padding: "5px 15px", flexShrink: 0, textAlign: "center" },
  chat:     { flex: 1, overflowY: "auto", padding: "14px 15px", display: "flex", flexDirection: "column", gap: 9 },
  vazio:    { flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" },
  msg:      { maxWidth: "83%", padding: "9px 13px", borderRadius: 10, fontSize: 13, lineHeight: 1.58, wordBreak: "break-word" },
  mUser:    { alignSelf: "flex-end",   background: "rgba(93,226,255,.09)", border: "1px solid rgba(93,226,255,.22)", color: "#fff" },
  mSirius:  { alignSelf: "flex-start", background: "rgba(0,22,40,.88)",   border: `1px solid ${C.borda}`,           color: C.azul },
  mSist:    { alignSelf: "center",     background: "none", border: "none", color: "rgba(93,226,255,.28)", fontSize: 11, padding: "2px 0" },
  tagSirius:{ color: C.azul, fontWeight: "bold", fontSize: 10, marginBottom: 3, letterSpacing: 1 },
  hora:     { fontSize: 10, opacity: .27, marginTop: 4, textAlign: "right" },
  inputArea:{ padding: "10px 14px", borderTop: `1px solid ${C.borda}`, display: "flex", gap: 8, background: C.painel, flexShrink: 0 },
  input:    { flex: 1, background: "rgba(93,226,255,.04)", border: "1px solid rgba(93,226,255,.22)", borderRadius: 8, color: "#fff", padding: "9px 13px", fontFamily: "inherit", fontSize: 13, outline: "none" },
  btnEnv:   { background: "rgba(93,226,255,.09)", border: `1px solid ${C.azul}`, borderRadius: 8, color: C.azul, padding: "0 16px", fontSize: 16, cursor: "pointer", flexShrink: 0 },
  cfgCentro:{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 },
  cfgBox:   { background: "rgba(0,8,18,.99)", border: `1px solid ${C.azul}`, borderRadius: 13, padding: "28px 22px", width: "100%", maxWidth: 360, display: "flex", flexDirection: "column", gap: 12 },
  cfgLogo:  { fontSize: 15, fontWeight: "bold", letterSpacing: 4, color: C.azul, textAlign: "center", marginBottom: 4 },
  cfgLabel: { fontSize: 10, color: "rgba(93,226,255,.4)", letterSpacing: 2 },
  cfgDica:  { fontSize: 11, color: "rgba(93,226,255,.33)", lineHeight: 1.65 },
  cfgInput: { background: "rgba(93,226,255,.04)", border: "1px solid rgba(93,226,255,.28)", borderRadius: 7, color: "#fff", padding: "11px 13px", fontFamily: "inherit", fontSize: 14, outline: "none", width: "100%" },
  btnP:     { background: "rgba(93,226,255,.09)", border: `1px solid ${C.azul}`, borderRadius: 7, color: C.azul, padding: 11, fontSize: 12, fontFamily: "inherit", fontWeight: "bold", letterSpacing: 2, cursor: "pointer", width: "100%" },
  btnS:     { background: "transparent", border: "none", color: "rgba(93,226,255,.28)", cursor: "pointer", fontFamily: "inherit", fontSize: 12, textAlign: "center", padding: 4 },
  cfgRodape:{ fontSize: 10, color: "rgba(93,226,255,.2)", textAlign: "center", lineHeight: 1.7, borderTop: "1px solid rgba(93,226,255,.1)", paddingTop: 10 },
}
