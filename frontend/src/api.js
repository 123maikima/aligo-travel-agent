const DEFAULT_BASE_URL = 'http://localhost:8000'

function getBaseUrl() {
  const envUrl = import.meta.env.VITE_API_BASE_URL
  if (envUrl && typeof envUrl === 'string') {
    return envUrl.replace(/\/$/, '')
  }
  return DEFAULT_BASE_URL
}

async function readJson(response) {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    return response.json()
  }
  const text = await response.text()
  return text ? { message: text } : {}
}

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function requestToken(payload) {
  const response = await fetch(`${getBaseUrl()}/api/v1/auth/token`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  const data = await readJson(response)
  if (!response.ok) {
    throw new Error(data.error || '签发 token 失败')
  }
  return data
}

export async function getCurrentPrincipal(token) {
  const response = await fetch(`${getBaseUrl()}/api/v1/auth/me`, {
    headers: authHeaders(token),
  })
  const data = await readJson(response)
  if (!response.ok) {
    throw new Error(data.error || '无法获取当前身份')
  }
  return data
}

export async function refreshToken(token) {
  const response = await fetch(`${getBaseUrl()}/api/v1/auth/refresh`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  const data = await readJson(response)
  if (!response.ok) {
    throw new Error(data.error || '刷新 token 失败')
  }
  return data
}

export async function createSession(token) {
  const response = await fetch(`${getBaseUrl()}/api/v1/sessions/new`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  const data = await readJson(response)
  if (!response.ok) {
    throw new Error(data.error || '创建会话失败')
  }
  return data
}

export async function closeSession(token) {
  const response = await fetch(`${getBaseUrl()}/api/v1/sessions/close`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  const data = await readJson(response)
  if (!response.ok) {
    throw new Error(data.error || '关闭会话失败')
  }
  return data
}

export async function listSessions(token) {
  const response = await fetch(`${getBaseUrl()}/api/v1/sessions`, {
    headers: authHeaders(token),
  })
  const data = await readJson(response)
  if (!response.ok) {
    throw new Error(data.error || '获取会话列表失败')
  }
  return data
}

export async function healthCheck() {
  const response = await fetch(`${getBaseUrl()}/health`)
  const data = await readJson(response)
  if (!response.ok) {
    throw new Error(data.error || '健康检查失败')
  }
  return data
}

function parseSseBlock(block) {
  const lines = block.split(/\r?\n/)
  let event = 'message'
  const dataLines = []

  for (const line of lines) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  const rawData = dataLines.join('\n')
  let data = rawData
  if (rawData) {
    try {
      data = JSON.parse(rawData)
    } catch {
      data = { content: rawData }
    }
  } else {
    data = {}
  }

  return { event, data }
}

export async function chatStream(token, payload, handlers = {}) {
  const response = await fetch(`${getBaseUrl()}/api/v1/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(token),
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const data = await readJson(response)
    throw new Error(data.error || '聊天请求失败')
  }

  if (!response.body) {
    throw new Error('浏览器不支持流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    let boundaryIndex = buffer.indexOf('\n\n')
    while (boundaryIndex !== -1) {
      const block = buffer.slice(0, boundaryIndex).trim()
      buffer = buffer.slice(boundaryIndex + 2)
      if (block) {
        const parsed = parseSseBlock(block)
        if (handlers.onEvent) {
          handlers.onEvent(parsed.event, parsed.data)
        }
      }
      boundaryIndex = buffer.indexOf('\n\n')
    }
  }

  const tail = buffer.trim()
  if (tail) {
    const parsed = parseSseBlock(tail)
    if (handlers.onEvent) {
      handlers.onEvent(parsed.event, parsed.data)
    }
  }
}

export async function chatSync(token, payload) {
  const response = await fetch(`${getBaseUrl()}/api/v1/chat/sync`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(token),
    },
    body: JSON.stringify(payload),
  })

  const data = await readJson(response)
  if (!response.ok) {
    throw new Error(data.error || '聊天请求失败')
  }
  return data
}

export function getApiBaseUrl() {
  return getBaseUrl()
}

