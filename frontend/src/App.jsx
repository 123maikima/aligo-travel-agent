import React, { useEffect, useRef, useState } from 'react'
import {
  Activity,
  Bot,
  CheckCircle2,
  Clipboard,
  Clock3,
  DoorOpen,
  FileText,
  HeartPulse,
  KeyRound,
  LogOut,
  MessageSquare,
  Plane,
  Plus,
  RefreshCw,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  UserRound,
  XCircle,
} from 'lucide-react'
import {
  chatStream,
  chatSync,
  closeSession,
  createSession,
  getApiBaseUrl,
  getCurrentPrincipal,
  healthCheck,
  listSessions,
  refreshToken,
  requestToken,
} from './api'

const QUICK_PROMPTS = [
  '我想从北京去杭州出差三天',
  '帮我规划上海到成都的三日行程',
  '差旅报销标准是多少',
  '我过去都去哪旅游过？',
]

const TOKEN_KEY = 'aligo.travel_agent.token'
const PRINCIPAL_KEY = 'aligo.travel_agent.principal'

function loadJson(key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function saveJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value))
}

function formatTime() {
  return new Date().toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function makeMessage(role, content, meta = {}) {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    meta,
    time: formatTime(),
  }
}

function StatusBadge({ ok, children }) {
  return (
    <span className={`status-badge ${ok ? 'ok' : 'warn'}`}>
      {ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
      {children}
    </span>
  )
}

function App() {
  const [apiBaseUrl] = useState(getApiBaseUrl())
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '')
  const [principal, setPrincipal] = useState(() => loadJson(PRINCIPAL_KEY))
  const [userForm, setUserForm] = useState({
    user_id: 'default_user',
    session_id: '',
    display_name: '',
    email: '',
  })
  const [sessions, setSessions] = useState([])
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(true)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([
    makeMessage('system', '登录后即可开始多 Agent 旅行问答。'),
  ])
  const [status, setStatus] = useState('等待登录')
  const [lastIntent, setLastIntent] = useState(null)
  const [agentEvents, setAgentEvents] = useState([])
  const [error, setError] = useState('')
  const [rawResult, setRawResult] = useState(null)
  const [currentTraceId, setCurrentTraceId] = useState('')
  const [activeInspector, setActiveInspector] = useState('activity')
  const [showToken, setShowToken] = useState(false)
  const [copied, setCopied] = useState(false)
  const listRef = useRef(null)

  const isAuthenticated = Boolean(token && principal)
  const currentSession = sessions.find((session) => session.id === principal?.session_id)

  useEffect(() => {
    localStorage.setItem(TOKEN_KEY, token)
    if (principal) {
      saveJson(PRINCIPAL_KEY, principal)
    } else {
      localStorage.removeItem(PRINCIPAL_KEY)
    }
  }, [token, principal])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    if (!token) return
    void bootstrapAuth(token)
  }, [])

  async function bootstrapAuth(authToken = token) {
    try {
      const me = await getCurrentPrincipal(authToken)
      setPrincipal(me.principal)
      const sessionList = await listSessions(authToken)
      setSessions(sessionList.sessions || [])
      setStatus(`已连接：${me.principal.user_id}`)
      setError('')
    } catch (err) {
      setError(err.message)
      setToken('')
      setPrincipal(null)
      setSessions([])
      setStatus('等待登录')
    }
  }

  async function handleLogin(event) {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      const payload = {
        ...userForm,
        display_name: userForm.display_name || userForm.user_id,
      }
      const data = await requestToken(payload)
      setToken(data.access_token)
      setPrincipal(data.principal)
      saveJson(PRINCIPAL_KEY, data.principal)
      setMessages([
        makeMessage('system', `已进入会话 ${data.principal.session_id}`),
      ])
      const sessionList = await listSessions(data.access_token)
      setSessions(sessionList.sessions || [])
      setStatus(`已认证：${data.principal.user_id}`)
      void handleHealth()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleRefreshToken() {
    if (!token) return
    setLoading(true)
    setError('')
    try {
      const data = await refreshToken(token)
      setToken(data.access_token)
      setPrincipal(data.principal)
      saveJson(PRINCIPAL_KEY, data.principal)
      setStatus(`Token 已刷新：${data.principal.session_id}`)
      const sessionList = await listSessions(data.access_token)
      setSessions(sessionList.sessions || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleNewSession() {
    if (!token) return
    setLoading(true)
    setError('')
    try {
      const data = await createSession(token)
      setToken(data.access_token)
      setPrincipal(data.principal)
      saveJson(PRINCIPAL_KEY, data.principal)
      setStatus(`新会话已创建：${data.principal.session_id}`)
      const sessionList = await listSessions(data.access_token)
      setSessions(sessionList.sessions || [])
      setMessages((current) => [
        ...current,
        makeMessage('system', `已切换到新会话 ${data.principal.session_id}`),
      ])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleLogout() {
    if (!token) {
      clearAuthState()
      return
    }
    setLoading(true)
    setError('')
    try {
      await closeSession(token)
    } catch (err) {
      setError(err.message)
    } finally {
      clearAuthState()
      setLoading(false)
    }
  }

  function clearAuthState() {
    setToken('')
    setPrincipal(null)
    setSessions([])
    setLastIntent(null)
    setAgentEvents([])
    setRawResult(null)
    setCurrentTraceId('')
    setStatus('等待登录')
    setMessages([makeMessage('system', '已退出登录。')])
  }

  async function handleHealth() {
    setLoading(true)
    setError('')
    try {
      const data = await healthCheck()
      setHealth(data)
      setStatus(data.ok ? '服务健康' : '服务异常')
    } catch (err) {
      setHealth(null)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function sendMessage(messageText) {
    const text = messageText.trim()
    if (!text || !token || !principal) return

    setLoading(true)
    setError('')
    setAgentEvents([])
    setLastIntent(null)
    setRawResult(null)

    setMessages((current) => [
      ...current,
      makeMessage('user', text, { session_id: principal.session_id }),
      makeMessage('assistant', '', { streaming: true }),
    ])

    const appendAssistantChunk = (content) => {
      if (!content) return
      setMessages((current) => {
        const updated = [...current]
        const index = updated.findLastIndex((message) => message.meta.streaming)
        if (index >= 0) {
          updated[index] = {
            ...updated[index],
            content: `${updated[index].content || ''}${content}`,
          }
        }
        return updated
      })
    }

    const replaceStreamingAssistant = (content, meta = {}) => {
      setMessages((current) => {
        const updated = [...current]
        const index = updated.findLastIndex((message) => message.meta.streaming)
        if (index >= 0) {
          updated[index] = makeMessage('assistant', content, meta)
        }
        return updated
      })
    }

    const onEvent = (event, data) => {
      if (event === 'session') {
        if (data.trace_id) setCurrentTraceId(data.trace_id)
        setStatus(`会话 ${data.session_id} 正在执行`)
        return
      }

      if (event === 'status') {
        if (data.trace_id) setCurrentTraceId(data.trace_id)
        const message = data?.message || data?.stage || '处理中'
        setAgentEvents((current) =>
          current.concat({
            event: 'status',
            stage: data.stage,
            summary: message,
          }),
        )
        return
      }

      if (event === 'intent') {
        if (data.trace_id) setCurrentTraceId(data.trace_id)
        setLastIntent(data)
        return
      }

      if (event === 'agent_start') {
        if (data.trace_id) setCurrentTraceId(data.trace_id)
        setAgentEvents((current) =>
          current.concat({
            ...data,
            event: 'start',
            summary: data.reason || '开始执行',
          }),
        )
        setStatus(`${data.agent} 执行中`)
        return
      }

      if (event === 'agent_done') {
        if (data.trace_id) setCurrentTraceId(data.trace_id)
        setAgentEvents((current) =>
          current.concat({
            ...data,
            event: 'done',
          }),
        )
        setStatus(`${data.agent} 完成`)
        return
      }

      if (event === 'agent_error') {
        if (data.trace_id) setCurrentTraceId(data.trace_id)
        setAgentEvents((current) =>
          current.concat({
            ...data,
            event: 'error',
            summary: data.error || '执行失败',
          }),
        )
        return
      }

      if (event === 'agent_summary') {
        if (data.trace_id) setCurrentTraceId(data.trace_id)
        setAgentEvents((current) =>
          current.concat({
            ...data,
            event: 'summary',
          }),
        )
        return
      }

      if (event === 'chunk') {
        if (data.trace_id) setCurrentTraceId(data.trace_id)
        appendAssistantChunk(data.content || '')
        return
      }

      if (event === 'done') {
        const result = data.result || data
        setCurrentTraceId(data.trace_id || result.trace_id || '')
        setRawResult(result)
        setStatus('回答完成')
        const answer = result.human_response || result.response || result.answer || ''
        replaceStreamingAssistant(answer || '已处理完成。', {
          intent: result.intention_data,
          agents: result.agents_executed,
        })
        return
      }

      if (event === 'error') {
        setError(data.detail || data.error || '请求失败')
      }
    }

    try {
      if (streaming) {
        await chatStream(token, { message: text }, { onEvent })
      } else {
        const data = await chatSync(token, { message: text })
        onEvent('done', { result: data })
      }
    } catch (err) {
      setError(err.message)
      setMessages((current) => {
        const updated = [...current]
        const index = updated.findLastIndex((message) => message.meta.streaming)
        if (index >= 0) {
          updated[index] = makeMessage('assistant', `请求失败：${err.message}`)
        }
        return updated
      })
    } finally {
      setLoading(false)
    }
  }

  function handleQuickPrompt(prompt) {
    setInput(prompt)
    void sendMessage(prompt)
  }

  function submitChat(event) {
    event.preventDefault()
    const text = input
    setInput('')
    void sendMessage(text)
  }

  async function handleCopyToken() {
    if (!token) return
    try {
      await navigator.clipboard.writeText(token)
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    } catch {
      setCopied(false)
    }
  }

  if (!isAuthenticated) {
    return (
      <main className="login-shell">
        <section className="login-card">
          <div className="brand-lockup">
            <span className="brand-mark">
              <Plane size={22} />
            </span>
            <div>
              <strong>Aligo Travel Agent</strong>
              <span>多 Agent 旅行助理工作台</span>
            </div>
          </div>

          <form className="login-form" onSubmit={handleLogin}>
            <div className="form-head">
              <KeyRound size={24} />
              <div>
                <h1>登录工作台</h1>
                <p>输入用户标识后自动创建或恢复当前会话。</p>
              </div>
            </div>

            <label>
              用户 ID
              <input
                value={userForm.user_id}
                onChange={(event) => setUserForm({ ...userForm, user_id: event.target.value })}
                placeholder="default_user"
                required
              />
            </label>
            <label>
              显示名称
              <input
                value={userForm.display_name}
                onChange={(event) =>
                  setUserForm({ ...userForm, display_name: event.target.value })
                }
                placeholder="例如：差旅顾问"
              />
            </label>
            <label>
              邮箱
              <input
                value={userForm.email}
                onChange={(event) => setUserForm({ ...userForm, email: event.target.value })}
                placeholder="name@example.com"
                type="email"
              />
            </label>

            <details className="advanced-login">
              <summary>高级选项</summary>
              <label>
                指定 Session ID
                <input
                  value={userForm.session_id}
                  onChange={(event) =>
                    setUserForm({ ...userForm, session_id: event.target.value })
                  }
                  placeholder="留空则自动创建"
                />
              </label>
              <span>API：{apiBaseUrl}</span>
            </details>

            {error && <div className="error-banner">{error}</div>}

            <button className="primary-button" type="submit" disabled={loading}>
              {loading ? <RefreshCw size={18} className="spin" /> : <ShieldCheck size={18} />}
              登录并创建会话
            </button>
          </form>
        </section>
      </main>
    )
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup compact">
          <span className="brand-mark">
            <Plane size={20} />
          </span>
          <div>
            <strong>Aligo Travel Agent</strong>
            <span>{principal.user_id}</span>
          </div>
        </div>
        <div className="topbar-actions">
          <StatusBadge ok={health?.ok !== false}>{health?.ok ? 'API 正常' : 'API 未确认'}</StatusBadge>
          <button className="icon-button" onClick={handleHealth} title="健康检查" type="button">
            <HeartPulse size={18} />
          </button>
          <button className="icon-button" onClick={handleRefreshToken} title="刷新 Token" type="button">
            <RefreshCw size={18} />
          </button>
          <button className="ghost-button" onClick={handleLogout} type="button">
            <LogOut size={17} />
            退出
          </button>
        </div>
      </header>

      {error && <div className="global-error">{error}</div>}

      <section className="workspace">
        <aside className="sidebar">
          <div className="sidebar-card current-session">
            <span className="section-label">当前会话</span>
            <strong>{principal.session_id}</strong>
            <div className="meta-row">
              <Clock3 size={15} />
              {currentSession?.updated_at || '刚刚'}
            </div>
            <button className="secondary-button" onClick={handleNewSession} disabled={loading} type="button">
              <Plus size={17} />
              新建会话
            </button>
          </div>

          <nav className="sidebar-card nav-card">
            <button className="nav-item active" type="button">
              <MessageSquare size={18} />
              旅行问答
            </button>
            <button
              className={`nav-item ${activeInspector === 'activity' ? 'selected' : ''}`}
              onClick={() => setActiveInspector('activity')}
              type="button"
            >
              <Activity size={18} />
              运行事件
            </button>
            <button
              className={`nav-item ${activeInspector === 'settings' ? 'selected' : ''}`}
              onClick={() => setActiveInspector('settings')}
              type="button"
            >
              <Settings size={18} />
              设置
            </button>
          </nav>

          <div className="sidebar-card">
            <div className="card-title">
              <span>快捷入口</span>
              <Sparkles size={16} />
            </div>
            <div className="quick-grid">
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  className="quick-button"
                  disabled={loading}
                  key={prompt}
                  onClick={() => handleQuickPrompt(prompt)}
                  type="button"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>

          <div className="sidebar-card sessions-card">
            <div className="card-title">
              <span>会话记录</span>
              <span className="count">{sessions.length}</span>
            </div>
            <div className="session-list">
              {sessions.length === 0 && <span className="empty-state">暂无历史会话</span>}
              {sessions.map((session) => (
                <div
                  className={`session-row ${session.id === principal.session_id ? 'current' : ''}`}
                  key={session.id}
                >
                  <span>{session.id}</span>
                  <small>{session.message_count || 0} 条消息</small>
                </div>
              ))}
            </div>
          </div>
        </aside>

        <section className="chat-panel">
          <div className="chat-toolbar">
            <div>
              <span className="section-label">对话</span>
              <h1>旅行规划与差旅问答</h1>
            </div>
            <div className="toolbar-controls">
              <label className="switch">
                <input
                  checked={streaming}
                  onChange={(event) => setStreaming(event.target.checked)}
                  type="checkbox"
                />
                <span>SSE 流式</span>
              </label>
              <StatusBadge ok={!loading}>{loading ? '执行中' : status}</StatusBadge>
            </div>
          </div>

          <div className="message-list" ref={listRef}>
            {messages.map((message) => (
              <article className={`message ${message.role}`} key={message.id}>
                <div className="message-icon">
                  {message.role === 'user' && <UserRound size={17} />}
                  {message.role === 'assistant' && <Bot size={17} />}
                  {message.role === 'system' && <TerminalSquare size={17} />}
                </div>
                <div className="message-body">
                  <div className="message-meta">
                    <span>{message.role}</span>
                    <span>{message.time}</span>
                  </div>
                  <p>{message.content}</p>
                </div>
              </article>
            ))}
          </div>

          <form className="composer" onSubmit={submitChat}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  submitChat(event)
                }
              }}
              placeholder="输入旅行计划、差旅政策、历史偏好或天气问题..."
              rows={3}
            />
            <button className="send-button" disabled={loading || !input.trim()} type="submit">
              <Send size={18} />
              发送
            </button>
          </form>
        </section>

        <aside className="inspector">
          <div className="inspector-tabs">
            <button
              className={activeInspector === 'activity' ? 'active' : ''}
              onClick={() => setActiveInspector('activity')}
              type="button"
            >
              <Activity size={16} />
              事件
            </button>
            <button
              className={activeInspector === 'settings' ? 'active' : ''}
              onClick={() => setActiveInspector('settings')}
              type="button"
            >
              <Settings size={16} />
              设置
            </button>
          </div>

          {activeInspector === 'activity' ? (
            <div className="inspector-content">
              <div className="inspector-card">
                <div className="card-title">
                  <span>意图识别</span>
                  <FileText size={16} />
                </div>
                {lastIntent ? (
                  <div className="intent-stack">
                    {(lastIntent.intents || []).map((intent, index) => (
                      <div className="intent-row" key={`${intent.type}-${index}`}>
                        <span>{intent.type}</span>
                        <strong>{Math.round((intent.confidence || 0) * 100)}%</strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <span className="empty-state">等待下一轮问答</span>
                )}
              </div>

              <div className="inspector-card">
                <div className="card-title">
                  <span>Agent 事件</span>
                  <Bot size={16} />
                </div>
                {currentTraceId && (
                  <div className="trace-row">
                    <span>Trace</span>
                    <code>{currentTraceId}</code>
                  </div>
                )}
                <div className="event-list">
                  {agentEvents.length === 0 && <span className="empty-state">暂无事件</span>}
                  {agentEvents.map((event, index) => (
                    <div className="event-row" key={`${event.agent}-${index}`}>
                      <strong>{event.agent || event.name || event.stage || `event-${index + 1}`}</strong>
                      <span>
                        {event.event || event.stage || 'updated'}
                        {typeof event.duration_ms === 'number' ? ` · ${event.duration_ms}ms` : ''}
                      </span>
                      {event.summary && <small>{event.summary}</small>}
                    </div>
                  ))}
                </div>
              </div>

              <div className="inspector-card">
                <div className="card-title">
                  <span>最后结果</span>
                  <Clipboard size={16} />
                </div>
                <pre>{rawResult ? JSON.stringify(rawResult, null, 2) : '暂无结果'}</pre>
              </div>
            </div>
          ) : (
            <div className="inspector-content">
              <div className="inspector-card profile-card">
                <span className="avatar">
                  <UserRound size={22} />
                </span>
                <strong>{principal.display_name || principal.user_id}</strong>
                <span>{principal.email || '未设置邮箱'}</span>
              </div>

              <div className="inspector-card settings-grid">
                <label>
                  API Base URL
                  <input readOnly value={apiBaseUrl} />
                </label>
                <label>
                  User ID
                  <input readOnly value={principal.user_id} />
                </label>
                <label>
                  Session ID
                  <input readOnly value={principal.session_id} />
                </label>
              </div>

              <div className="inspector-card token-card">
                <div className="card-title">
                  <span>访问凭证</span>
                  <button className="mini-button" onClick={() => setShowToken(!showToken)} type="button">
                    {showToken ? '隐藏' : '显示'}
                  </button>
                </div>
                <textarea readOnly rows={showToken ? 6 : 2} value={showToken ? token : '••••••••••••••••'} />
                <button className="secondary-button" onClick={handleCopyToken} type="button">
                  <Clipboard size={16} />
                  {copied ? '已复制' : '复制 Token'}
                </button>
              </div>

              <button className="danger-button" onClick={handleLogout} type="button">
                <DoorOpen size={17} />
                关闭会话并退出
              </button>
            </div>
          )}
        </aside>
      </section>
    </main>
  )
}

export default App
