'use client';

import { useState, useEffect, useRef } from 'react';

// Demo accounts metadata
const DEMO_ACCOUNTS = [
  { username: 'dr.mehta', role: 'doctor', name: 'Dr. Mehta', title: 'Senior Medical Director' },
  { username: 'nurse.priya', role: 'nurse', name: 'Nurse Priya', title: 'ICU Head Nurse' },
  { username: 'billing.ravi', role: 'billing_executive', name: 'Ravi Das', title: 'Insurance Lead' },
  { username: 'tech.anand', role: 'technician', name: 'Anand Sharma', title: 'Biomedical Technician' },
  { username: 'admin.sys', role: 'admin', name: 'System Admin', title: 'IT & Executive Ops' }
];

const BACKEND_URL = 'http://localhost:8000';

export default function Home() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(false);
  const [collections, setCollections] = useState([]);
  const [messages, setMessages] = useState([]);
  const [inputText, setInputText] = useState('');
  const [backendHealthy, setBackendHealthy] = useState(false);
  const messagesEndRef = useRef(null);

  // Check health of backend on mount
  useEffect(() => {
    fetch(`${BACKEND_URL}/health`)
      .then(res => res.json())
      .then(data => {
        if (data.status === 'ok') setBackendHealthy(true);
      })
      .catch(err => {
        console.error("Backend health check failed:", err);
        setBackendHealthy(false);
      });
  }, []);

  // Scroll to bottom on new messages
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, loading]);

  // Handle demo login click
  const handleLogin = async (username) => {
    setLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password: 'password' })
      });
      if (!res.ok) throw new Error("Login failed");
      const userData = await res.json();
      setUser(userData);
      
      // Fetch allowed collections for this role
      const colRes = await fetch(`${BACKEND_URL}/collections/${userData.role}`);
      if (colRes.ok) {
        const colData = await colRes.json();
        setCollections(colData.collections);
      }
      
      // Add initial greeting message
      setMessages([
        {
          sender: 'bot',
          text: `Welcome, ${userData.name}! I am **MediBot**, your clinical and operational assistant. \n\nHow can I help you today? You have access to the following collections: **${ROLE_COLLECTIONS_DESC[userData.role]}**.`
        }
      ]);
    } catch (err) {
      console.error(err);
      alert("Could not login. Please make sure the FastAPI backend is running on http://localhost:8000!");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    setUser(null);
    setCollections([]);
    setMessages([]);
  };

  // Send message to FastAPI
  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!inputText.trim() || !user) return;

    const queryText = inputText;
    setInputText('');
    
    // Add user message to UI
    setMessages(prev => [...prev, { sender: 'user', text: queryText }]);
    setLoading(true);

    try {
      const res = await fetch(`${BACKEND_URL}/chat`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${user.token}`
        },
        body: JSON.stringify({ question: queryText, role: user.role })
      });

      if (!res.ok) throw new Error("Chat request failed");
      const data = await res.json();

      // Check if message was blocked/refused by RBAC
      const isRefusal = data.answer.includes("do not have permissions") || 
                        data.answer.includes("don't have access") ||
                        data.answer.includes("unauthorized");

      setMessages(prev => [...prev, {
        sender: 'bot',
        text: data.answer,
        sources: data.sources || [],
        retrieval_type: data.retrieval_type,
        isBlocked: isRefusal
      }]);
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, {
        sender: 'bot',
        text: "Sorry, I had trouble reaching the server. Please check that the backend service is running on http://localhost:8000.",
        isError: true
      }]);
    } finally {
      setLoading(false);
    }
  };

  const ROLE_COLLECTIONS_DESC = {
    doctor: "Clinical treatment protocols, drug formulary, diagnostic guidelines, nursing care, and general policies",
    nurse: "ICU nursing procedures, infection control, and general staff FAQs",
    billing_executive: "Insurance billing codes, claim submission guides, and general staff handbooks",
    technician: "Medical equipment manual, calibration procedures, and staff handbook",
    admin: "All documents and relational database query access"
  };

  // Helper function to render text with basic markdown styling safely
  const renderFormattedText = (text) => {
    if (!text) return null;

    const formatInline = (str) => {
      if (typeof str !== 'string') return str;
      // Bold **text** (requires non-whitespace boundaries)
      let formatted = str.replace(/\*\*([^\s\*](?:[^\*]*?[^\s\*])?)\*\*/g, '<strong>$1</strong>');
      // Italics *text* (requires non-whitespace boundaries)
      formatted = formatted.replace(/\*([^\s\*](?:[^\*]*?[^\s\*])?)\*/g, '<em>$1</em>');
      // Inline code `code`
      formatted = formatted.replace(/`([^`]+?)`/g, '<code>$1</code>');
      return <span dangerouslySetInnerHTML={{ __html: formatted }} />;
    };
    
    // Check if it's a markdown table
    if (text.includes('|') && text.includes('\n')) {
      const lines = text.split('\n');
      const tableRows = [];
      let isTable = false;
      let headerCols = [];
      
      for (let line of lines) {
        if (line.trim().startsWith('|')) {
          isTable = true;
          const cols = line.split('|').map(c => c.trim()).filter((c, idx, arr) => idx > 0 && idx < arr.length - 1);
          if (line.includes('---')) continue; // Skip delimiter row
          
          if (headerCols.length === 0) {
            headerCols = cols;
          } else {
            tableRows.push(cols);
          }
        }
      }
      
      if (isTable && headerCols.length > 0) {
        return (
          <div className="table-responsive my-2">
            <table>
              <thead>
                <tr>
                  {headerCols.map((col, idx) => <th key={idx}>{formatInline(col)}</th>)}
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row, rowIdx) => (
                  <tr key={rowIdx}>
                    {row.map((col, colIdx) => <td key={colIdx}>{formatInline(col)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
    }

    // Split text into lines for bullet lists or paragraphs
    const lines = text.split('\n');
    let inList = false;
    const elements = [];
    let listItems = [];

    lines.forEach((line, idx) => {
      const trimmed = line.trim();
      
      // Match headings like #, ##, ###
      const headingMatch = trimmed.match(/^(#{1,6})\s+(.*)$/);
      // Match bullet lists starting with *, -, +, or • (requires at least one space)
      const bulletMatch = trimmed.match(/^([\*\-\+•]|&bull;)\s+(.*)$/);
      
      if (headingMatch) {
        if (inList) {
          elements.push(<ul key={`ul-${idx}`}>{listItems}</ul>);
          listItems = [];
          inList = false;
        }
        const level = headingMatch[1].length;
        const HeadingTag = `h${level}`;
        elements.push(
          <HeadingTag key={`h-${idx}`} className={`heading-l${level}`}>
            {formatInline(headingMatch[2])}
          </HeadingTag>
        );
      } else if (bulletMatch) {
        inList = true;
        listItems.push(
          <li key={`li-${idx}`}>
            {formatInline(bulletMatch[2])}
          </li>
        );
      } else {
        if (inList) {
          elements.push(<ul key={`ul-${idx}`}>{listItems}</ul>);
          listItems = [];
          inList = false;
        }
        
        if (trimmed) {
          elements.push(
            <p key={`p-${idx}`} className="mb-2">
              {formatInline(line)}
            </p>
          );
        }
      }
    });

    if (inList) {
      elements.push(<ul key="ul-final">{listItems}</ul>);
    }

    return elements;
  };

  // --- 1. Login View ---
  if (!user) {
    return (
      <div className="login-container">
        <div className="login-card">
          <div className="login-header">
            <h1 className="login-logo">MediBot</h1>
            <p className="login-subtitle">An AI Assistant for MediAssist Network</p>
          </div>
          
          <div className="demo-account-list">
            {DEMO_ACCOUNTS.map((acc) => (
              <button 
                key={acc.username}
                className="demo-account-btn"
                onClick={() => handleLogin(acc.username)}
                disabled={loading}
              >
                <div className="account-info">
                  <span className="account-name">{acc.name}</span>
                  <span className="account-username">{acc.title} ({acc.username})</span>
                </div>
                <span className={`role-badge role-${acc.role}`}>
                  {acc.role.replace('_', ' ')}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // --- 2. Chat Dashboard View ---
  return (
    <div className="app-layout">
      {/* Sidebar - Profile & Access scopes */}
      <div className="sidebar">
        <div className="sidebar-brand">MediBot</div>
        
        <div className="user-profile-card">
          <div className="profile-header">
            <div className="profile-avatar">
              {user.name.split(' ').map(w => w[0]).join('')}
            </div>
            <div className="profile-details">
              <span className="profile-name">{user.name}</span>
              <span className="profile-title">{user.username}</span>
            </div>
          </div>
          <span className={`role-badge role-${user.role} block text-center mt-2`}>
            {user.role.replace('_', ' ')}
          </span>
        </div>

        <div className="access-scope">
          <span className="scope-title">Authorized Document Collections</span>
          <div className="collection-list">
            {['general', 'clinical', 'nursing', 'billing', 'equipment'].map(col => {
              const isAllowed = collections.includes(col);
              return (
                <div 
                  key={col} 
                  className={`collection-item ${isAllowed ? 'active' : ''}`}
                >
                  <span className="collection-dot"></span>
                  <span className="capitalize">{col}</span>
                  {!isAllowed && <span className="ml-auto text-[10px] text-rose-400 uppercase font-semibold">Blocked</span>}
                </div>
              );
            })}
          </div>
        </div>

        <button className="logout-btn" onClick={handleLogout}>
          Sign Out & Switch Profile
        </button>
      </div>

      {/* Main Chat Workspace */}
      <div className="chat-section">
        {/* Header */}
        <div className="chat-header">
          <div className="header-title">Clinical Inquiry Panel</div>
          <div className="system-status">
            <span className="status-indicator"></span>
            <span>MediAssist Central DB Connected</span>
          </div>
        </div>

        {/* Messages */}
        <div className="messages-container">
          {messages.length === 0 ? (
            <div className="empty-state">
              <span className="empty-icon">🏥</span>
              <h2 className="empty-title">How can I help you today?</h2>
              <p>Ask a question about treatment guidelines, billing codes, equipment maintenance schedules, or query relational operational records.</p>
            </div>
          ) : (
            messages.map((msg, index) => (
              <div 
                key={index} 
                className={`message-wrapper ${msg.sender === 'user' ? 'user' : 'bot'}`}
              >
                <div className="message-bubble">
                  {renderFormattedText(msg.text)}

                  {/* Render RBAC rejection alert card for user awareness */}
                  {msg.isBlocked && (
                    <div className="rbac-blocked-card">
                      <span className="rbac-blocked-icon">⚠️</span>
                      <div className="rbac-blocked-text">
                        <strong>Access Restricted:</strong> Your current user profile does not have permission to view this specific clinical, billing, or equipment information. If you require this information, please request access from the hospital IT Helpdesk.
                      </div>
                    </div>
                  )}

                  {/* Message Metadata (badge, citations) */}
                  {msg.sender === 'bot' && (msg.retrieval_type || (msg.sources && msg.sources.length > 0)) && (
                    <div className="response-meta">
                      {msg.retrieval_type && (
                        <span className="retrieval-badge">
                          {msg.retrieval_type.replace('_', ' ')}
                        </span>
                      )}
                      
                      {msg.sources && msg.sources.length > 0 && (
                        <div className="flex-grow">
                          <div className="sources-title">Verified Sources ({msg.sources.length})</div>
                          <div className="citations-list">
                            {msg.sources.map((src, sIdx) => (
                              <span 
                                key={sIdx} 
                                className="citation-tag" 
                                title={`Collection: ${src.collection}`}
                              >
                                📄 {src.source_document} {src.section_title ? `| ${src.section_title}` : ''}
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
          
          {loading && (
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

        {/* Input Bar Form */}
        <div className="chat-input-container">
          <form className="chat-input-form" onSubmit={handleSendMessage}>
            <input 
              type="text" 
              className="chat-input-field"
              placeholder={`Ask MediBot (queries will be logged as ${user.role.replace('_', ' ')})...`}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              disabled={loading}
            />
            <button 
              type="submit" 
              className="chat-send-btn"
              disabled={loading || !inputText.trim()}
            >
              ➔
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
