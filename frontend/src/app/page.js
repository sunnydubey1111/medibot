'use client';

import { useState, useEffect, useRef } from 'react';

const DEMO_ACCOUNTS = [
  { username: 'dr.mehta',     role: 'doctor',            name: 'Dr. Mehta',     title: 'Senior Medical Director' },
  { username: 'nurse.priya',  role: 'nurse',             name: 'Nurse Priya',   title: 'ICU Head Nurse' },
  { username: 'billing.ravi', role: 'billing_executive', name: 'Ravi Das',      title: 'Insurance Lead' },
  { username: 'tech.anand',   role: 'technician',        name: 'Anand Sharma',  title: 'Biomedical Technician' },
  { username: 'admin.sys',    role: 'admin',             name: 'System Admin',  title: 'IT & Executive Ops' },
];

const ROLE_EMOJIS = {
  doctor:            '🩺',
  nurse:             '💊',
  billing_executive: '💼',
  technician:        '🔧',
  admin:             '🛡',
};

const COLLECTION_EMOJIS = {
  general:   '📋',
  clinical:  '🩺',
  nursing:   '💉',
  billing:   '💰',
  equipment: '⚙️',
};

const SAMPLE_QUESTIONS = {
  doctor:            ['What is the treatment protocol for NSTEMI?', 'What is the standard dosage of amoxicillin?', 'What are the diagnostic criteria for sepsis?'],
  nurse:             ['What is the ICU hand hygiene protocol?', 'How do I prevent patient falls in the ICU?', 'What are the steps for central line insertion care?'],
  billing_executive: ['What documents are needed for a cashless claim?', 'How do I submit a Medicare Part B claim?', 'What is the ICD-10 code for hypertension?'],
  technician:        ['What is the maintenance schedule for SterilPro 3000?', 'How do I troubleshoot DriveFlow IP-200 fault code F-05?', 'What are the calibration steps for the autoclave?'],
  admin:             ['How many claims are pending today?', 'Which department has the highest claimed amount?', 'How many maintenance tickets are open?'],
};

const ROLE_COLLECTIONS_DESC = {
  doctor:            'Clinical treatment protocols, drug formulary, diagnostic guidelines, nursing care, and general policies',
  nurse:             'ICU nursing procedures, infection control, and general staff FAQs',
  billing_executive: 'Insurance billing codes, claim submission guides, and general staff handbooks',
  technician:        'Medical equipment manual, calibration procedures, and staff handbook',
  admin:             'All documents and relational database query access',
};

const BACKEND_URL = 'http://localhost:8000';

export default function Home() {
  const [user, setUser]                   = useState(null);
  const [loading, setLoading]             = useState(false);
  const [collections, setCollections]     = useState([]);
  const [messages, setMessages]           = useState([]);
  const [inputText, setInputText]         = useState('');
  const [backendHealthy, setBackendHealthy] = useState(false);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    fetch(`${BACKEND_URL}/health`)
      .then(r => r.json())
      .then(d => { if (d.status === 'ok') setBackendHealthy(true); })
      .catch(() => setBackendHealthy(false));
  }, []);

  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, loading]);

  // ── Token refresh ─────────────────────────────────────────────────────────
  const refreshAccessToken = async () => {
    const rt = localStorage.getItem('medibot_refresh_token');
    if (!rt) return null;
    try {
      const res = await fetch(`${BACKEND_URL}/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) return null;
      const data = await res.json();
      return data.token;
    } catch {
      return null;
    }
  };

  // ── Login ─────────────────────────────────────────────────────────────────
  const handleLogin = async (username) => {
    setLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password: 'password' }),
      });
      if (!res.ok) throw new Error('Login failed');
      const userData = await res.json();

      localStorage.setItem('medibot_refresh_token', userData.refresh_token);
      setUser(userData);

      const colRes = await fetch(`${BACKEND_URL}/collections/${userData.role}`);
      if (colRes.ok) setCollections((await colRes.json()).collections);

      setMessages([{
        sender: 'bot',
        text: `Welcome, ${userData.name}! I am **MediBot**, your clinical and operational assistant.\n\nHow can I help you today? You have access to: **${ROLE_COLLECTIONS_DESC[userData.role]}**.`,
      }]);
    } catch (err) {
      console.error(err);
      alert('Could not login. Please make sure the FastAPI backend is running on http://localhost:8000!');
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('medibot_refresh_token');
    setUser(null);
    setCollections([]);
    setMessages([]);
  };

  // ── Send message (streaming) ──────────────────────────────────────────────
  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!inputText.trim() || !user) return;

    const queryText = inputText;
    setInputText('');
    setLoading(true);

    // Add user message + empty bot placeholder in one update
    setMessages(prev => [
      ...prev,
      { sender: 'user', text: queryText },
      { sender: 'bot', text: '', streaming: true, sources: [], retrieval_type: null },
    ]);

    const doRequest = async (token) => {
      return fetch(`${BACKEND_URL}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ question: queryText, role: user.role }),
      });
    };

    try {
      let res = await doRequest(user.token);

      // Transparent token refresh on 401
      if (res.status === 401) {
        const newToken = await refreshAccessToken();
        if (newToken) {
          setUser(prev => ({ ...prev, token: newToken }));
          res = await doRequest(newToken);
        } else {
          handleLogout();
          return;
        }
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';

        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(part.slice(6));

            if (event.type === 'chunk') {
              setMessages(prev => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = { ...last, text: last.text + event.text };
                return updated;
              });
            } else if (event.type === 'replace') {
              setMessages(prev => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = { ...last, text: event.text };
                return updated;
              });
            } else if (event.type === 'done') {
              setMessages(prev => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                const isBlocked = last.text.includes('do not have permission') ||
                                  last.text.includes("don't have access") ||
                                  last.text.includes('unauthorized');
                updated[updated.length - 1] = {
                  ...last,
                  streaming: false,
                  sources: event.sources || [],
                  retrieval_type: event.retrieval_type,
                  confidence_score: event.confidence_score,
                  confidence_label: event.confidence_label,
                  isBlocked,
                };
                return updated;
              });
            } else if (event.type === 'error') {
              setMessages(prev => {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  sender: 'bot', text: event.text || 'An error occurred.',
                  streaming: false, isError: true,
                };
                return updated;
              });
            }
          } catch (_) {}
        }
      }
    } catch (err) {
      console.error(err);
      setMessages(prev => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          sender: 'bot',
          text: `Sorry, I had trouble reaching the server. ${err.message}`,
          streaming: false,
          isError: true,
        };
        return updated;
      });
    } finally {
      setLoading(false);
    }
  };

  // ── Markdown renderer ─────────────────────────────────────────────────────
  const renderFormattedText = (text) => {
    if (!text) return null;

    const formatInline = (str) => {
      if (typeof str !== 'string') return str;
      // Escape HTML characters first to prevent XSS
      const escaped = str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
      let f = escaped.replace(/\*\*([^\s*](?:[^*]*?[^\s*])?)\*\*/g, '<strong>$1</strong>');
      f = f.replace(/\*([^\s*](?:[^*]*?[^\s*])?)\*/g, '<em>$1</em>');
      f = f.replace(/`([^`]+?)`/g, '<code>$1</code>');
      return <span dangerouslySetInnerHTML={{ __html: f }} />;
    };

    if (text.includes('|') && text.includes('\n')) {
      const lines = text.split('\n');
      let headerCols = [], tableRows = [], isTable = false;
      for (const line of lines) {
        if (line.trim().startsWith('|')) {
          isTable = true;
          const cols = line.split('|').map(c => c.trim()).filter((_, i, a) => i > 0 && i < a.length - 1);
          if (line.includes('---')) continue;
          if (!headerCols.length) headerCols = cols;
          else tableRows.push(cols);
        }
      }
      if (isTable && headerCols.length) {
        return (
          <div className="table-responsive my-2">
            <table>
              <thead><tr>{headerCols.map((c, i) => <th key={i}>{formatInline(c)}</th>)}</tr></thead>
              <tbody>{tableRows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{formatInline(c)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        );
      }
    }

    const lines = text.split('\n');
    // listType: null | 'ul' | 'ol'
    let listType = null, listItems = [];
    const elements = [];

    const flushList = (idx) => {
      if (!listType) return;
      if (listType === 'ol') elements.push(<ol key={`ol-${idx}`}>{listItems}</ol>);
      else elements.push(<ul key={`ul-${idx}`}>{listItems}</ul>);
      listItems = []; listType = null;
    };

    lines.forEach((line, idx) => {
      const trimmed = line.trim();
      const hMatch  = trimmed.match(/^(#{1,6})\s+(.*)$/);
      const bMatch  = trimmed.match(/^([*\-+•])\s+(.*)$/);
      const nMatch  = trimmed.match(/^(\d+)[.)]\s+(.*)$/);
      const qMatch  = trimmed.match(/^>\s*(.*)/);

      if (hMatch) {
        flushList(idx);
        const Tag = `h${hMatch[1].length}`;
        elements.push(<Tag key={`h-${idx}`} className={`heading-l${hMatch[1].length}`}>{formatInline(hMatch[2])}</Tag>);
      } else if (bMatch) {
        if (listType === 'ol') flushList(idx);
        listType = 'ul';
        listItems.push(<li key={`li-${idx}`}>{formatInline(bMatch[2])}</li>);
      } else if (nMatch) {
        if (listType === 'ul') flushList(idx);
        listType = 'ol';
        listItems.push(<li key={`li-${idx}`}>{formatInline(nMatch[2])}</li>);
      } else if (qMatch) {
        flushList(idx);
        elements.push(<blockquote key={`bq-${idx}`} className="response-blockquote">{formatInline(qMatch[1])}</blockquote>);
      } else {
        flushList(idx);
        if (trimmed) elements.push(<p key={`p-${idx}`} className="mb-2">{formatInline(line)}</p>);
      }
    });
    flushList('final');
    return elements;
  };

  // ── Login screen ──────────────────────────────────────────────────────────
  if (!user) {
    return (
      <div className="login-container">
        <div className="login-card">
          <div className="login-header">
            <h1 className="login-logo">🏥 MediBot</h1>
            <p className="login-subtitle">🔒 Secure AI Clinical Assistant · MediAssist Health Network</p>
          </div>
          <p className="login-select-label">👤 Select your profile to continue</p>
          <div className="demo-account-list">
            {DEMO_ACCOUNTS.map(acc => (
              <button key={acc.username} className="demo-account-btn" onClick={() => handleLogin(acc.username)} disabled={loading}>
                <span className="account-emoji">{ROLE_EMOJIS[acc.role]}</span>
                <div className="account-info">
                  <span className="account-name">{acc.name}</span>
                  <span className="account-username">{acc.title} · {acc.username}</span>
                </div>
                <span className={`role-badge role-${acc.role}`}>{acc.role.replace('_', ' ')}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── Chat dashboard ────────────────────────────────────────────────────────
  return (
    <div className="app-layout">
      <div className="sidebar">
        <div className="sidebar-brand">🏥 MediBot</div>

        <div className="user-profile-card">
          <div className="profile-header">
            <div className="profile-avatar">{ROLE_EMOJIS[user.role]}</div>
            <div className="profile-details">
              <span className="profile-name">{user.name}</span>
              <span className="profile-title">{user.username}</span>
            </div>
          </div>
          <span className={`role-badge role-${user.role} block text-center mt-2`}>{user.role.replace('_', ' ')}</span>
        </div>

        <div className="access-scope">
          <span className="scope-title">📂 Authorized Collections</span>
          <div className="collection-list">
            {['general', 'clinical', 'nursing', 'billing', 'equipment'].map(col => {
              const allowed = collections.includes(col);
              return (
                <div key={col} className={`collection-item ${allowed ? 'active' : ''}`}>
                  <span className="collection-emoji">{COLLECTION_EMOJIS[col]}</span>
                  <span className="capitalize">{col}</span>
                  {allowed
                    ? <span className="ml-auto text-[10px] text-teal-400 uppercase font-semibold">✓</span>
                    : <span className="ml-auto text-[10px] text-rose-400 uppercase font-semibold">🔒</span>}
                </div>
              );
            })}
          </div>
        </div>

        <button className="logout-btn" onClick={handleLogout}>🚪 Sign Out &amp; Switch Profile</button>
      </div>

      <div className="chat-section">
        <div className="chat-header">
          <div className="header-title">💬 Clinical Inquiry Panel</div>
          <div className="system-status">
            <span className="status-indicator"></span>
            <span>🟢 MediAssist Central DB · Online</span>
          </div>
        </div>

        <div className="messages-container">
          {messages.length === 0 ? (
            <div className="empty-state">
              <span className="empty-icon">🩺</span>
              <h2 className="empty-title">How can I help you today?</h2>
              <p className="empty-subtitle">Ask about treatment guidelines, billing codes, equipment maintenance, or operational records.</p>
              <div className="sample-questions">
                <p className="sample-label">✨ Try asking:</p>
                {(SAMPLE_QUESTIONS[user.role] || []).map((q, i) => (
                  <button key={i} className="sample-btn" onClick={() => setInputText(q)}>
                    💬 {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg, index) => (
              <div key={index} className={`message-wrapper ${msg.sender === 'user' ? 'user' : 'bot'}`}>
                {msg.sender === 'bot' && (
                  <div className="bot-avatar" title="MediBot AI">🤖</div>
                )}
                <div className="message-bubble">
                  <div className={msg.streaming ? 'streaming-cursor' : ''}>
                    {renderFormattedText(msg.text)}
                  </div>

                  {msg.isBlocked && (
                    <div className="rbac-blocked-card">
                      <span className="rbac-blocked-icon">🔒</span>
                      <div className="rbac-blocked-text">
                        <strong>Access Restricted:</strong> Your current profile does not have permission to view this information. Contact the hospital IT Helpdesk to request access.
                      </div>
                    </div>
                  )}

                  {msg.sender === 'bot' && !msg.streaming && (msg.retrieval_type || (msg.sources && msg.sources.length > 0)) && (
                    <div className="response-meta">
                      {msg.retrieval_type && (
                        <span className="retrieval-badge">{msg.retrieval_type.replace('_', ' ')}</span>
                      )}

                      {msg.confidence_label && (
                        <span className={`confidence-badge confidence-${msg.confidence_label}`}>
                          {msg.confidence_label === 'high' ? '●' : msg.confidence_label === 'medium' ? '◕' : '○'} {msg.confidence_label}
                        </span>
                      )}

                      {msg.sources && msg.sources.length > 0 && (
                        <div className="flex-grow">
                          <div className="sources-title">📎 Verified Sources ({msg.sources.length})</div>
                          <div className="citations-list">
                            {msg.sources.map((src, si) => (
                              <span key={si} className="citation-tag" title={`Collection: ${src.collection}`}>
                                📄 {src.source_document}{src.section_title ? ` | ${src.section_title}` : ''}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}

          {loading && messages[messages.length - 1]?.streaming !== true && (
            <div className="message-wrapper bot">
              <div className="message-bubble">
                <div className="typing-indicator">
                  <span className="typing-dot"></span>
                  <span className="typing-dot"></span>
                  <span className="typing-dot"></span>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <div className="chat-input-container">
          <form className="chat-input-form" onSubmit={handleSendMessage}>
            <input
              type="text"
              className="chat-input-field"
              placeholder={`💬 Ask MediBot as ${user.role.replace('_', ' ')} — type your question...`}
              value={inputText}
              onChange={e => setInputText(e.target.value)}
              disabled={loading}
            />
            <button type="submit" className="chat-send-btn" disabled={loading || !inputText.trim()}>🚀</button>
          </form>
        </div>
      </div>
    </div>
  );
}
