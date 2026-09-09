import { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';

const STATUS_LABELS = {
  open: 'Open',
  in_progress: 'In progress',
  awaiting_customer_reply: 'Awaiting customer',
  resolved: 'Resolved',
  closed: 'Closed',
};
const PRIORITIES = ['low', 'normal', 'high', 'urgent'];
const EMPTY_TEMPLATE = {
  name: '', slug: '', subjectTemplate: '', bodyTemplate: '', categoryId: '',
  isActive: true, autoSend: false, resolvesTicket: false,
};

function formatDate(value, withTime = false) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '—' : new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', ...(withTime ? { hour: 'numeric', minute: '2-digit' } : {}),
  }).format(date);
}

function firstLetters(value = '') {
  return value.split(' ').filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || '—';
}

function Badge({ value, kind = 'status' }) {
  const label = kind === 'status' ? STATUS_LABELS[value] || value : value;
  return <span className={`badge ${kind}-${String(value).replaceAll('_', '-')}`}>{label}</span>;
}

function Notice({ notice, onDismiss }) {
  if (!notice) return null;
  return <div className={`notice ${notice.type || 'success'}`} role="status">
    <span>{notice.text}</span><button onClick={onDismiss} aria-label="Dismiss notification">×</button>
  </div>;
}

function Login({ onAuthenticated }) {
  const [email, setEmail] = useState('admin@support.local');
  const [password, setPassword] = useState('Admin@12345');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      onAuthenticated(await api.login(email, password));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return <main className="login-page">
    <section className="login-card">
      <div className="brand brand-large"><span className="brand-mark">S</span><span>SupportPilot</span></div>
      <p className="eyebrow">Customer Email Support Automation</p>
      <h1>Make every customer email accountable.</h1>
      <p className="login-copy">Triage incoming requests, collaborate on replies, and deliver a consistent, auditable customer experience.</p>
      <form onSubmit={submit} className="login-form">
        <label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required /></label>
        <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></label>
        {error && <p className="form-error">{error}</p>}
        <button className="button primary wide" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
      <div className="demo-hint"><strong>Local demo:</strong> admin@support.local / Admin@12345</div>
    </section>
    <aside className="login-aside">
      <div className="orb orb-one" /><div className="orb orb-two" />
      <div className="login-aside-content">
        <p className="eyebrow">Built for confident support teams</p>
        <h2>From inbox noise to a controlled service workflow.</h2>
        <ul><li>Threaded customer conversations</li><li>Safe approved-template automation</li><li>SLA ownership and audit history</li></ul>
      </div>
    </aside>
  </main>;
}

function Sidebar({ view, setView, user, onLogout }) {
  const base = [
    ['dashboard', 'Overview', '▦'], ['queue', 'Ticket queue', '▤'], ['templates', 'Templates', '▧'],
  ];
  const admin = [['reports', 'Reports', '◫'], ['admin', 'Administration', '⚙']];
  const items = user.role === 'admin' ? [...base, ...admin] : base;
  return <aside className="sidebar">
    <div className="brand"><span className="brand-mark">S</span><span>SupportPilot</span></div>
    <nav>{items.map(([id, label, icon]) => <button key={id} className={`nav-item ${view === id ? 'active' : ''}`} onClick={() => setView(id)}><span>{icon}</span>{label}</button>)}</nav>
    <div className="sidebar-bottom">
      <div className="profile-mini"><span className="avatar">{firstLetters(user.fullName)}</span><div><strong>{user.fullName}</strong><small>{user.role}</small></div></div>
      <button className="nav-item muted" onClick={onLogout}><span>↪</span>Sign out</button>
    </div>
  </aside>;
}

function Header({ view, user, onCompose, onConfigureCompany }) {
  const titles = { dashboard: 'Support overview', queue: 'Ticket queue', templates: 'Response templates', reports: 'Performance reports', admin: 'Administration' };
  return <header className="page-header">
    <div><p className="eyebrow">{user.role === 'admin' ? 'Support operations' : 'My support workspace'}</p><h1>{titles[view]}</h1></div>
    <div className="header-actions">{user.role === 'admin' && onConfigureCompany && <button className="button secondary" onClick={onConfigureCompany}>AI setup</button>}<button className="button primary" onClick={onCompose}>+ New ticket</button><span className="avatar header-avatar">{firstLetters(user.fullName)}</span></div>
  </header>;
}

function MetricCard({ label, value, hint, tone = 'default' }) {
  return <article className={`metric-card ${tone}`}><p>{label}</p><strong>{value ?? '—'}</strong>{hint && <small>{hint}</small>}</article>;
}

function Dashboard({ tickets, report, onQueue, user }) {
  const local = useMemo(() => ({
    open: tickets.filter((ticket) => !['resolved', 'closed'].includes(ticket.status)).length,
    overdue: tickets.filter((ticket) => ticket.isOverdue).length,
    urgent: tickets.filter((ticket) => ticket.priority === 'urgent').length,
  }), [tickets]);
  const metrics = report?.metrics;
  const categoryData = report?.byCategory || Object.entries(tickets.reduce((acc, ticket) => {
    const name = ticket.category?.name || 'Uncategorized'; acc[name] = (acc[name] || 0) + 1; return acc;
  }, {})).map(([name, value]) => ({ name, value }));
  const max = Math.max(...categoryData.map((entry) => entry.value), 1);
  return <section className="dashboard">
    <div className="welcome-row"><div><h2>Good work starts with a clear queue.</h2><p>Monitor support health, then focus on the conversations that need your team now.</p></div><button className="button secondary" onClick={onQueue}>Open ticket queue →</button></div>
    <div className="metric-grid">
      <MetricCard label="Open backlog" value={metrics?.openBacklog ?? local.open} hint="Tickets awaiting resolution" />
      <MetricCard label="SLA at risk" value={metrics?.overdueTickets ?? local.overdue} hint="Past their target response" tone="warning" />
      <MetricCard label="Automation rate" value={metrics ? `${metrics.automationRate}%` : '—'} hint="Replies delivered automatically" tone="accent" />
      <MetricCard label="First response" value={metrics?.averageFirstResponseMinutes != null ? `${metrics.averageFirstResponseMinutes}m` : '—'} hint={metrics ? `${metrics.firstResponsesMeasured} measured in 30 days` : `${local.urgent} urgent ticket(s) in queue`} />
    </div>
    <div className="dashboard-grid">
      <article className="panel category-chart"><div className="panel-heading"><div><p className="eyebrow">Incoming demand</p><h3>Tickets by category</h3></div><span className="muted-text">Last 30 days</span></div>
        <div className="bar-chart">{categoryData.slice(0, 7).map((entry) => <div className="bar-row" key={entry.name}><span>{entry.name}</span><div className="bar-track"><i style={{ width: `${(entry.value / max) * 100}%` }} /></div><strong>{entry.value}</strong></div>)}</div>
      </article>
      <article className="panel priority-panel"><div className="panel-heading"><div><p className="eyebrow">Team focus</p><h3>Priority snapshot</h3></div></div>
        <div className="priority-list">{PRIORITIES.slice().reverse().map((priority) => { const count = tickets.filter((ticket) => ticket.priority === priority && !['resolved', 'closed'].includes(ticket.status)).length; return <div key={priority}><Badge kind="priority" value={priority} /><strong>{count}</strong><span>open tickets</span></div>; })}</div>
      </article>
    </div>
    <article className="panel activity-panel"><div className="panel-heading"><div><p className="eyebrow">Recent work</p><h3>Latest tickets</h3></div><button className="text-button" onClick={onQueue}>View all</button></div>
      <div className="table-wrap"><table><thead><tr><th>Ticket</th><th>Customer</th><th>Category</th><th>Priority</th><th>Status</th><th>Updated</th></tr></thead><tbody>{tickets.slice(0, 6).map((ticket) => <tr key={ticket.id}><td><strong>{ticket.reference}</strong><small>{ticket.subject}</small></td><td>{ticket.customer?.fullName || ticket.customer?.email}</td><td>{ticket.category?.name || 'Uncategorized'}</td><td><Badge kind="priority" value={ticket.priority} /></td><td><Badge value={ticket.status} /></td><td>{formatDate(ticket.updatedAt, true)}</td></tr>)}</tbody></table></div>
    </article>
  </section>;
}

function TicketList({ tickets, selectedId, onSelect, filters, setFilters, categories, loading }) {
  return <section className="queue-list panel">
    <div className="queue-toolbar"><input className="search-input" value={filters.search} onChange={(event) => setFilters({ ...filters, search: event.target.value })} placeholder="Search ticket, customer, or reference" />
      <div className="filter-row"><select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">All statuses</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select value={filters.priority} onChange={(event) => setFilters({ ...filters, priority: event.target.value })}><option value="">All priorities</option>{PRIORITIES.map((value) => <option key={value} value={value}>{value}</option>)}</select><select value={filters.categoryId} onChange={(event) => setFilters({ ...filters, categoryId: event.target.value })}><option value="">All categories</option>{categories.map((category) => <option value={category.id} key={category.id}>{category.name}</option>)}</select></div>
    </div>
    <div className="queue-count">{loading ? 'Refreshing tickets…' : `${tickets.length} tickets shown`}</div>
    <div className="ticket-list">{tickets.map((ticket) => <button className={`ticket-row ${selectedId === ticket.id ? 'selected' : ''}`} key={ticket.id} onClick={() => onSelect(ticket.id)}><div className="ticket-row-top"><strong>{ticket.reference}</strong><span>{formatDate(ticket.updatedAt, true)}</span></div><h3>{ticket.subject}</h3><p>{ticket.customer?.fullName || ticket.customer?.email}</p><div className="ticket-row-bottom"><Badge kind="priority" value={ticket.priority} /><Badge value={ticket.status} />{ticket.isOverdue && <span className="sla-alert">SLA breached</span>}</div></button>)}{!loading && !tickets.length && <div className="empty-state"><strong>No tickets match these filters.</strong><span>Try clearing a filter or create a new ticket.</span></div>}</div>
  </section>;
}

function MessageThread({ messages }) {
  return <div className="message-thread">{messages.map((message) => <article className={`message ${message.direction}`} key={message.id}><div className="message-meta"><div><span className="avatar small">{firstLetters(message.sender)}</span><strong>{message.direction === 'inbound' ? message.sender : 'Support team'}</strong></div><span>{formatDate(message.createdAt, true)}</span></div><p className="message-subject">{message.subject}</p><div className="message-body">{message.bodyText}</div>{message.attachments?.length > 0 && <div className="attachments">{message.attachments.map((attachment) => <span className="attachment" key={attachment.id}>{attachment.originalName} <small>{Math.ceil(attachment.sizeBytes / 1024)} KB</small></span>)}</div>}</article>)}</div>;
}

function TicketDetail({ ticket, categories, templates, users, user, onChanged, onError }) {
  const [replyBody, setReplyBody] = useState('');
  const [replySubject, setReplySubject] = useState('');
  const [templateId, setTemplateId] = useState('');
  const [sending, setSending] = useState(false);
  const [drafting, setDrafting] = useState(false);

  useEffect(() => { setReplyBody(''); setReplySubject(''); setTemplateId(''); }, [ticket?.id]);
  if (!ticket) return <section className="ticket-detail-empty panel"><div><span className="empty-illustration">✦</span><h2>Select a ticket</h2><p>Choose a conversation from the queue to view context, manage ownership, and prepare a reply.</p></div></section>;
  const canEdit = user.role === 'admin' || !ticket.assignee || ticket.assignee.id === user.id;

  async function update(changes) {
    try { await api.updateTicket(ticket.id, changes); onChanged(ticket.id); } catch (error) { onError(error.message); }
  }
  async function assign(assigneeId) {
    try { await api.assignTicket(ticket.id, assigneeId || null); onChanged(ticket.id); } catch (error) { onError(error.message); }
  }
  async function createDraft() {
    setDrafting(true);
    try { const response = await api.draft(ticket.id); setReplySubject(response.draft.subject); setReplyBody(response.draft.body); setTemplateId(''); } catch (error) { onError(error.message); } finally { setDrafting(false); }
  }
  async function sendReply(event) {
    event.preventDefault();
    setSending(true);
    try {
      const payload = { subject: replySubject || undefined, body: replyBody || undefined, templateId: templateId || undefined, statusAfterSend: 'awaiting_customer_reply' };
      await api.reply(ticket.id, payload);
      setReplyBody(''); setReplySubject(''); setTemplateId('');
      onChanged(ticket.id, 'Reply queued for delivery.');
    } catch (error) { onError(error.message); } finally { setSending(false); }
  }
  function useTemplate(next) {
    setTemplateId(next); setReplyBody(''); setReplySubject('');
  }

  return <section className="ticket-detail panel">
    <div className="ticket-detail-header"><div><div className="ticket-kicker"><span>{ticket.reference}</span>{ticket.isOverdue && <span className="sla-alert">SLA breached</span>}</div><h2>{ticket.subject}</h2><p>From <strong>{ticket.customer?.fullName || ticket.customer?.email}</strong> · {ticket.customer?.email}</p></div><div className="ticket-state"><Badge kind="priority" value={ticket.priority} /><Badge value={ticket.status} /></div></div>
    <div className="ticket-properties">
      <label>Priority<select disabled={!canEdit} value={ticket.priority} onChange={(event) => update({ priority: event.target.value })}>{PRIORITIES.map((priority) => <option key={priority}>{priority}</option>)}</select></label>
      <label>Status<select disabled={!canEdit} value={ticket.status} onChange={(event) => update({ status: event.target.value })}>{Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label>Category<select disabled={!canEdit} value={ticket.category?.id || ''} onChange={(event) => update({ categoryId: event.target.value || null })}><option value="">Uncategorized</option>{categories.map((category) => <option value={category.id} key={category.id}>{category.name}</option>)}</select></label>
      <label>Owner<select disabled={!canEdit} value={ticket.assignee?.id || ''} onChange={(event) => assign(event.target.value)}><option value="">Unassigned</option>{users.filter((agent) => user.role === 'admin' || agent.id === user.id).map((agent) => <option value={agent.id} key={agent.id}>{agent.fullName}</option>)}</select></label>
    </div>
    <div className="thread-heading"><div><p className="eyebrow">Conversation</p><h3>{ticket.messages?.length || 0} messages</h3></div><span>Due {formatDate(ticket.dueAt, true)}</span></div>
    <MessageThread messages={ticket.messages || []} />
    <form className="reply-box" onSubmit={sendReply}>
      <div className="reply-box-heading"><div><p className="eyebrow">Reply to customer</p><h3>Compose a helpful response</h3></div><button type="button" className="button subtle" onClick={createDraft} disabled={!canEdit || drafting}>{drafting ? 'Drafting…' : 'Generate draft'}</button></div>
      <div className="reply-controls"><select disabled={!canEdit} value={templateId} onChange={(event) => useTemplate(event.target.value)}><option value="">Write a custom reply</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}</select><input disabled={!canEdit} value={replySubject} onChange={(event) => setReplySubject(event.target.value)} placeholder="Subject (defaults to Re: ticket subject)" /></div>
      <textarea disabled={!canEdit} value={replyBody} onChange={(event) => setReplyBody(event.target.value)} placeholder={templateId ? 'Leave blank to send the selected approved template, or edit a custom message here.' : 'Write a response or generate a reviewable draft…'} rows="7" />
      <div className="reply-footer"><span>Replies are placed in the durable outbox before delivery.</span><button className="button primary" disabled={!canEdit || sending || (!replyBody.trim() && !templateId)}>{sending ? 'Queueing…' : 'Queue reply'}</button></div>
    </form>
  </section>;
}

function QueuePage({ tickets, selectedId, setSelectedId, selectedTicket, categories, templates, users, user, filters, setFilters, loading, onChanged, onError }) {
  return <section className="queue-layout"><TicketList tickets={tickets} selectedId={selectedId} onSelect={setSelectedId} filters={filters} setFilters={setFilters} categories={categories} loading={loading} /><TicketDetail ticket={selectedTicket} categories={categories} templates={templates} users={users} user={user} onChanged={onChanged} onError={onError} /></section>;
}

function TemplatesPage({ templates, categories, user, onRefresh, onNotice }) {
  const [selectedId, setSelectedId] = useState(templates[0]?.id || null);
  const [editing, setEditing] = useState(null);
  useEffect(() => { if (!selectedId && templates[0]) setSelectedId(templates[0].id); }, [templates, selectedId]);
  const selected = templates.find((template) => template.id === selectedId) || templates[0];

  async function approve(template, approved) {
    try { await api.approveTemplate(template.id, approved); onNotice(`Template ${approved ? 'approved' : 'returned to draft'}.`); onRefresh(); } catch (error) { onNotice(error.message, 'error'); }
  }
  async function save(payload) {
    try {
      const response = editing?.id ? await api.updateTemplate(editing.id, payload) : await api.createTemplate(payload);
      setSelectedId(response.template.id); setEditing(null); onNotice(editing?.id ? 'Template updated; approval is required.' : 'Template created as a draft.'); onRefresh();
    } catch (error) { onNotice(error.message, 'error'); }
  }

  if (editing) return <TemplateEditor template={editing} categories={categories} onCancel={() => setEditing(null)} onSave={save} />;
  return <section className="templates-layout"><aside className="template-list panel"><div className="panel-heading"><div><p className="eyebrow">Library</p><h3>Response templates</h3></div>{user.role === 'admin' && <button className="button primary small-button" onClick={() => setEditing(EMPTY_TEMPLATE)}>+ Create</button>}</div>{templates.map((template) => <button className={`template-item ${template.id === selected?.id ? 'selected' : ''}`} key={template.id} onClick={() => setSelectedId(template.id)}><strong>{template.name}</strong><span>{template.category?.name || 'General'} · v{template.version}</span><div>{template.isApproved ? <span className="approval approved">Approved</span> : <span className="approval draft">Draft</span>}{template.autoSend && <span className="auto-label">Auto-send</span>}</div></button>)}</aside>
    <section className="template-detail panel">{selected ? <><div className="panel-heading"><div><p className="eyebrow">{selected.category?.name || 'General policy'}</p><h2>{selected.name}</h2><p>{selected.isApproved ? 'This template is approved for agent use.' : 'This template requires administrator approval before agent use.'}</p></div>{user.role === 'admin' && <div className="button-group"><button className="button secondary" onClick={() => setEditing({ ...selected, categoryId: selected.category?.id || '' })}>Edit</button><button className={`button ${selected.isApproved ? 'danger-outline' : 'primary'}`} onClick={() => approve(selected, !selected.isApproved)}>{selected.isApproved ? 'Unapprove' : 'Approve'}</button></div>}</div><div className="template-preview"><label>Subject</label><div>{selected.subjectTemplate}</div><label>Message</label><pre>{selected.bodyTemplate}</pre></div>{user.role === 'admin' && <div className="template-policy"><span>{selected.autoSend ? 'Eligible for automated delivery' : 'Agent-review response only'}</span><span>{selected.resolvesTicket ? 'Marks tickets resolved after send' : 'Keeps tickets awaiting customer reply'}</span></div>}</> : <div className="empty-state">No templates are available.</div>}</section>
  </section>;
}

function TemplateEditor({ template, categories, onCancel, onSave }) {
  const [form, setForm] = useState({ ...EMPTY_TEMPLATE, ...template, categoryId: template.categoryId || template.category?.id || '' });
  const [saving, setSaving] = useState(false);
  function patch(key, value) { setForm((current) => ({ ...current, [key]: value })); }
  async function submit(event) { event.preventDefault(); setSaving(true); await onSave(form); setSaving(false); }
  return <section className="editor-panel panel"><div className="panel-heading"><div><p className="eyebrow">Template policy</p><h2>{template.id ? 'Edit template' : 'Create template'}</h2></div><button className="button secondary" onClick={onCancel}>Cancel</button></div><form onSubmit={submit} className="template-form"><div className="form-grid"><label>Name<input value={form.name} onChange={(event) => patch('name', event.target.value)} required /></label><label>Slug<input value={form.slug} onChange={(event) => patch('slug', event.target.value)} placeholder="e.g. shipping-update" required /></label><label>Category<select value={form.categoryId} onChange={(event) => patch('categoryId', event.target.value)}><option value="">No category</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label><label>Subject<input value={form.subjectTemplate} onChange={(event) => patch('subjectTemplate', event.target.value)} required /></label></div><label>Message body<textarea value={form.bodyTemplate} onChange={(event) => patch('bodyTemplate', event.target.value)} rows="11" required /></label><div className="checkbox-grid"><label><input type="checkbox" checked={form.isActive} onChange={(event) => patch('isActive', event.target.checked)} /> Active</label><label><input type="checkbox" checked={form.autoSend} onChange={(event) => patch('autoSend', event.target.checked)} /> Eligible for auto-send</label><label><input type="checkbox" checked={form.resolvesTicket} onChange={(event) => patch('resolvesTicket', event.target.checked)} /> Resolve ticket after delivery</label></div><p className="muted-text">{'Supported variables: {{ticket_reference}}, {{ticket_subject}}, {{customer_name}}, {{customer_email}}, {{category}}, {{priority}}, {{support_email}}.'}</p><div className="form-actions"><button type="button" className="button secondary" onClick={onCancel}>Cancel</button><button className="button primary" disabled={saving}>{saving ? 'Saving…' : 'Save template'}</button></div></form></section>;
}

function ReportsPage({ report, volume, workload }) {
  const metric = report?.metrics || {};
  const maxVolume = Math.max(...(volume || []).map((item) => item.count), 1);
  return <section className="reports-page"><div className="metric-grid"><MetricCard label="Tickets received" value={metric.ticketsReceived ?? '—'} hint="In the selected 30-day period" /><MetricCard label="Open backlog" value={metric.openBacklog ?? '—'} hint="Across all currently open tickets" /><MetricCard label="Automation rate" value={metric.automationRate != null ? `${metric.automationRate}%` : '—'} hint="Delivered via approved automation" tone="accent" /><MetricCard label="Overdue tickets" value={metric.overdueTickets ?? '—'} hint="Escalate to protect the SLA" tone="warning" /></div><div className="reports-grid"><article className="panel"><div className="panel-heading"><div><p className="eyebrow">Ticket volume</p><h3>Daily incoming requests</h3></div></div><div className="mini-chart">{volume?.length ? volume.map((item) => <div className="column" title={`${item.date}: ${item.count}`} key={item.date}><i style={{ height: `${Math.max(8, item.count / maxVolume * 100)}%` }} /><span>{item.date.slice(5)}</span></div>) : <div className="empty-state">No ticket activity in this period.</div>}</div></article><article className="panel"><div className="panel-heading"><div><p className="eyebrow">Capacity</p><h3>Agent workload</h3></div></div><div className="workload-list">{workload?.map((agent) => <div key={agent.agent}><span className="avatar small">{firstLetters(agent.agent)}</span><strong>{agent.agent}</strong><span>{agent.open} open</span><span className={agent.overdue ? 'overdue-count' : ''}>{agent.overdue} overdue</span></div>) || <div className="empty-state">No active assignments.</div>}</div></article></div><article className="panel"><div className="panel-heading"><div><p className="eyebrow">Routing insight</p><h3>Category distribution</h3></div></div><div className="bar-chart">{report?.byCategory?.map((entry) => <div className="bar-row" key={entry.name}><span>{entry.name}</span><div className="bar-track"><i style={{ width: `${Math.min(100, entry.value / Math.max(...report.byCategory.map((x) => x.value), 1) * 100)}%` }} /></div><strong>{entry.value}</strong></div>)}</div></article></section>;
}

function AdminPage({ users, operations, audit, onRefresh, onNotice }) {
  const [create, setCreate] = useState(false);
  const [form, setForm] = useState({ fullName: '', email: '', password: '', role: 'agent' });
  async function operation(label, operation) { try { const result = await operation(); onNotice(`${label}: ${result.status || result.sent != null ? JSON.stringify(result) : 'complete'}`); onRefresh(); } catch (error) { onNotice(error.message, 'error'); } }
  async function createUser(event) { event.preventDefault(); try { await api.createUser(form); setForm({ fullName: '', email: '', password: '', role: 'agent' }); setCreate(false); onNotice('User created.'); onRefresh(); } catch (error) { onNotice(error.message, 'error'); } }
  return <section className="admin-page"><div className="admin-grid"><article className="panel"><div className="panel-heading"><div><p className="eyebrow">Runtime controls</p><h3>Mail and SLA operations</h3></div><span className={`approval ${operations?.scheduler?.running ? 'approved' : 'draft'}`}>{operations?.scheduler?.running ? 'Scheduler running' : 'Manual mode'}</span></div><div className="operations-info"><div><span>Outbound transport</span><strong>{operations?.mail?.transport || '—'}</strong></div><div><span>IMAP inbound</span><strong>{operations?.mail?.imapEnabled ? 'Enabled' : 'Disabled'}</strong></div><div><span>Last mailbox sync</span><strong>{formatDate(operations?.mailbox?.lastSuccessAt, true)}</strong></div></div><div className="operation-buttons"><button className="button secondary" onClick={() => operation('Mailbox poll', api.pollMailbox)}>Poll mailbox</button><button className="button secondary" onClick={() => operation('Outbox delivery', api.deliverOutbox)}>Deliver outbox</button><button className="button secondary" onClick={() => operation('SLA check', api.checkSla)}>Check SLA</button></div></article><article className="panel"><div className="panel-heading"><div><p className="eyebrow">Audit trail</p><h3>Latest decisions</h3></div></div><div className="audit-list">{audit?.slice(0, 7).map((event) => <div key={event.id}><span>{formatDate(event.createdAt, true)}</span><strong>{event.action.replaceAll('_', ' ')}</strong><small>{event.actor?.fullName || event.actor?.type || event.actorType}</small></div>) || <div className="empty-state">No audit events yet.</div>}</div></article></div><article className="panel user-panel"><div className="panel-heading"><div><p className="eyebrow">Access management</p><h3>Team users</h3></div><button className="button primary" onClick={() => setCreate(!create)}>{create ? 'Close form' : '+ Add user'}</button></div>{create && <form className="inline-form" onSubmit={createUser}><input placeholder="Full name" value={form.fullName} onChange={(event) => setForm({ ...form, fullName: event.target.value })} required /><input type="email" placeholder="Email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} required /><input type="password" placeholder="Initial password (10+ chars)" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} required minLength="10" /><select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}><option value="agent">Agent</option><option value="admin">Admin</option></select><button className="button primary">Create</button></form>}<div className="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last login</th></tr></thead><tbody>{users.map((member) => <tr key={member.id}><td><span className="avatar small">{firstLetters(member.fullName)}</span> <strong>{member.fullName}</strong></td><td>{member.email}</td><td><span className="role-label">{member.role}</span></td><td><span className={member.isActive ? 'state-active' : 'state-disabled'}>{member.isActive ? 'Active' : 'Disabled'}</span></td><td>{formatDate(member.lastLoginAt, true)}</td></tr>)}</tbody></table></div></article></section>;
}

function NewTicketModal({ categories, onClose, onCreated, onError }) {
  const [form, setForm] = useState({ customerName: '', customerEmail: '', subject: '', body: '' });
  const [busy, setBusy] = useState(false);
  async function submit(event) { event.preventDefault(); setBusy(true); try { const result = await api.createTicket(form); onCreated(result.ticket.id); } catch (error) { onError(error.message); } finally { setBusy(false); } }
  return <div className="modal-backdrop"><section className="modal panel"><div className="panel-heading"><div><p className="eyebrow">New customer conversation</p><h2>Create ticket</h2></div><button className="modal-close" onClick={onClose}>×</button></div><form onSubmit={submit} className="template-form"><div className="form-grid"><label>Customer name<input value={form.customerName} onChange={(event) => setForm({ ...form, customerName: event.target.value })} /></label><label>Customer email<input type="email" value={form.customerEmail} onChange={(event) => setForm({ ...form, customerEmail: event.target.value })} required /></label></div><label>Subject<input value={form.subject} onChange={(event) => setForm({ ...form, subject: event.target.value })} required /></label><label>Customer message<textarea rows="7" value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} required /></label><div className="form-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy}>{busy ? 'Creating…' : 'Create ticket'}</button></div></form></section></div>;
}

function CompanyProfileModal({ profile, aiStatus, required, onClose, onRefresh, onSaved }) {
  const [form, setForm] = useState({
    companyName: profile?.companyName || '',
    companyBrief: profile?.companyBrief || '',
    tone: profile?.tone || 'clear and helpful',
    autoReplyEnabled: Boolean(profile?.autoReplyEnabled),
    minimumCustomerConfidence: String(profile?.minimumCustomerConfidence ?? 0.85),
  });
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const aiConfigurationReady = Boolean(aiStatus?.configurationReady);
  const canChangeAutomation = aiConfigurationReady || form.autoReplyEnabled;
  const statusText = aiConfigurationReady
    ? `Groq is configured for ${aiStatus.model}. SupportPilot does not probe the provider here; unavailable requests are routed for human review.`
    : aiStatus?.enabled
      ? 'Groq is enabled but its local configuration is incomplete. Add a fresh server-side key and model before enabling automatic AI replies.'
      : 'Groq is disabled. Save the company brief now; add a newly generated key locally and restart the backend before enabling automatic AI replies.';

  useEffect(() => {
    setForm({
      companyName: profile?.companyName || '',
      companyBrief: profile?.companyBrief || '',
      tone: profile?.tone || 'clear and helpful',
      autoReplyEnabled: Boolean(profile?.autoReplyEnabled),
      minimumCustomerConfidence: String(profile?.minimumCustomerConfidence ?? 0.85),
    });
  }, [profile]);

  // A backend restart changes readiness without changing the mounted page.
  // Refresh from the server whenever this setup dialog opens.
  useEffect(() => { void onRefresh(); }, []);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !required && !saving) {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose, required, saving]);

  function patch(key, value) { setForm((current) => ({ ...current, [key]: value })); }
  function close() { if (!required && !saving) onClose(); }
  async function refreshStatus() {
    setRefreshing(true);
    try { await onRefresh(); } finally { setRefreshing(false); }
  }
  function trapFocus(event) {
    if (event.key !== 'Tab') return;
    const focusable = [...event.currentTarget.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  }
  async function submit(event) {
    event.preventDefault();
    setSaving(true); setError('');
    try {
      const response = await api.updateCompanyProfile({
        ...form,
        minimumCustomerConfidence: Number(form.minimumCustomerConfidence),
      });
      onSaved(response);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  return <div className="modal-backdrop company-profile-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}><section className="modal panel company-profile-modal" role="dialog" aria-modal="true" aria-labelledby="company-profile-title" aria-describedby="company-profile-description" onKeyDown={trapFocus}>
    <div className="panel-heading"><div><p className="eyebrow">AI support setup</p><h2 id="company-profile-title">Tell SupportPilot what your company does</h2><p id="company-profile-description">Describe your products, services, support boundaries, and facts that replies may safely use.</p></div>{!required && <button type="button" className="modal-close" aria-label="Close AI setup" onClick={close} disabled={saving} autoFocus>×</button>}</div>
    <form onSubmit={submit} className="template-form company-profile-form">
      <div className="form-grid"><label>Company name<input value={form.companyName} onChange={(event) => patch('companyName', event.target.value)} maxLength="160" required autoFocus={required} /></label><label>Reply tone<input value={form.tone} onChange={(event) => patch('tone', event.target.value)} maxLength="80" required /></label></div>
      <label>What does your company do?<textarea value={form.companyBrief} onChange={(event) => patch('companyBrief', event.target.value)} rows="8" minLength="20" maxLength="4000" placeholder="For example: We provide… Our support team can help with… We do not…" required /></label>
      <label>Minimum confidence for an automatic AI reply<select value={form.minimumCustomerConfidence} onChange={(event) => patch('minimumCustomerConfidence', event.target.value)}><option value="0.75">75% — more responsive</option><option value="0.85">85% — recommended</option><option value="0.9">90% — most cautious</option></select></label>
      <label className="company-automation-option"><input type="checkbox" checked={form.autoReplyEnabled} disabled={!canChangeAutomation} onChange={(event) => patch('autoReplyEnabled', event.target.checked)} /><span><strong>Enable automatic AI replies</strong><small>Only high-confidence customer requests can receive a grounded reply. Promotions and unclear messages are routed to review. If AI setup later becomes unavailable, you can still turn this setting off.</small></span></label>
      <p className={`ai-setup-status ${aiConfigurationReady ? 'ready' : 'pending'}`} role="status" aria-live="polite">{statusText}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions"><button type="button" className="button secondary" onClick={refreshStatus} disabled={saving || refreshing}>{refreshing ? 'Refreshing…' : 'Refresh AI status'}</button>{!required && <button type="button" className="button secondary" onClick={close} disabled={saving || refreshing}>Cancel</button>}<button className="button primary" disabled={saving || refreshing}>{saving ? 'Saving…' : 'Save company profile'}</button></div>
    </form>
  </section></div>;
}

function Workspace({ session, onLogout }) {
  const user = session.user;
  const [view, setView] = useState('dashboard');
  const [tickets, setTickets] = useState([]);
  const [categories, setCategories] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [users, setUsers] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [report, setReport] = useState(null);
  const [volume, setVolume] = useState([]);
  const [workload, setWorkload] = useState([]);
  const [operations, setOperations] = useState(null);
  const [audit, setAudit] = useState([]);
  const [companyProfile, setCompanyProfile] = useState(null);
  const [aiStatus, setAiStatus] = useState(null);
  const [profileLoadState, setProfileLoadState] = useState(user.role === 'admin' ? 'loading' : 'ready');
  const [companyProfileModalOpen, setCompanyProfileModalOpen] = useState(false);
  const [filters, setFilters] = useState({ search: '', status: '', priority: '', categoryId: '' });
  const [ticketLoading, setTicketLoading] = useState(false);
  const [notice, setNotice] = useState(null);
  const [newTicket, setNewTicket] = useState(false);

  const notify = (text, type = 'success') => setNotice({ text, type });
  async function loadTickets() {
    setTicketLoading(true);
    try {
      const data = await api.tickets({ ...filters, perPage: 100, sort: filters.priority ? 'priority' : 'newest' });
      setTickets(data.items); if (!selectedId && data.items[0]) setSelectedId(data.items[0].id);
    } catch (error) { notify(error.message, 'error'); } finally { setTicketLoading(false); }
  }
  async function loadCore() {
    try {
      const calls = [api.categories(), api.templates(), api.tickets({ perPage: 100 }), api.users().catch(() => ({ items: [] }))];
      if (user.role === 'admin') calls.push(api.reports(), api.volume(), api.workload(), api.operations(), api.audit({ perPage: 25 }));
      const responses = await Promise.all(calls);
      setCategories(responses[0].items); setTemplates(responses[1].items); setTickets(responses[2].items); setUsers(responses[3].items);
      if (user.role === 'admin') { setReport(responses[4]); setVolume(responses[5].items); setWorkload(responses[6].items); setOperations(responses[7]); setAudit(responses[8].items); }
    } catch (error) { notify(error.message, 'error'); }
  }
  async function loadCompanyProfile({ openAfterLoad = false, background = false } = {}) {
    if (user.role !== 'admin') return false;
    if (!background) setProfileLoadState('loading');
    try {
      const response = await api.companyProfile();
      setCompanyProfile(response.profile); setAiStatus(response.ai); setProfileLoadState('ready');
      setCompanyProfileModalOpen((isOpen) => openAfterLoad || isOpen || !response.profile.isComplete);
      return true;
    } catch (error) {
      notify(`Company setup could not be loaded: ${error.message}`, 'error');
      if (!background) {
        setProfileLoadState('failed');
        setCompanyProfileModalOpen(false);
      }
      return false;
    }
  }
  useEffect(() => { void loadCore(); void loadCompanyProfile(); }, []);
  useEffect(() => { const timer = window.setTimeout(loadTickets, 250); return () => window.clearTimeout(timer); }, [filters]);
  useEffect(() => { if (!selectedId) return; api.ticket(selectedId).then((result) => setSelectedTicket(result.ticket)).catch((error) => notify(error.message, 'error')); }, [selectedId]);

  async function ticketChanged(id, message) {
    if (id) { try { const result = await api.ticket(id); setSelectedTicket(result.ticket); } catch (error) { notify(error.message, 'error'); } }
    await loadTickets(); if (message) notify(message);
  }
  function created(id) { setNewTicket(false); setView('queue'); setSelectedId(id); notify('Ticket created and classified.'); void loadTickets(); }
  function refreshedAdmin() { void loadCore(); void loadCompanyProfile(); }
  function openCompanyProfile() {
    if (profileLoadState === 'ready') {
      void loadCompanyProfile({ openAfterLoad: true });
    } else if (profileLoadState === 'failed') {
      void loadCompanyProfile({ openAfterLoad: true });
    } else {
      notify('AI setup is still loading. Please wait a moment.', 'error');
    }
  }
  function companyProfileSaved(response) {
    setCompanyProfile(response.profile); setAiStatus(response.ai); setProfileLoadState('ready'); setCompanyProfileModalOpen(false);
    notify(response.profile.autoReplyEnabled ? 'Company profile saved. AI replies are eligible only when inbound processing, Groq, and outbox delivery are available.' : 'Company profile saved; AI replies remain safely disabled.');
    void loadCore();
  }

  const content = view === 'dashboard' ? <Dashboard tickets={tickets} report={report} user={user} onQueue={() => setView('queue')} />
    : view === 'queue' ? <QueuePage tickets={tickets} selectedId={selectedId} setSelectedId={setSelectedId} selectedTicket={selectedTicket} categories={categories} templates={templates} users={users} user={user} filters={filters} setFilters={setFilters} loading={ticketLoading} onChanged={ticketChanged} onError={(text) => notify(text, 'error')} />
      : view === 'templates' ? <TemplatesPage templates={templates} categories={categories} user={user} onRefresh={loadCore} onNotice={notify} />
        : view === 'reports' && user.role === 'admin' ? <ReportsPage report={report} volume={volume} workload={workload} />
          : view === 'admin' && user.role === 'admin' ? <AdminPage users={users} operations={operations} audit={audit} onRefresh={refreshedAdmin} onNotice={notify} />
            : <Dashboard tickets={tickets} report={report} user={user} onQueue={() => setView('queue')} />;
  const profileRequired = !companyProfile?.isComplete;
  return <div className="app-shell"><Sidebar view={view} setView={setView} user={user} onLogout={onLogout} /><main className="app-main"><Header view={view} user={user} onCompose={() => setNewTicket(true)} onConfigureCompany={openCompanyProfile} /><Notice notice={notice} onDismiss={() => setNotice(null)} />{user.role === 'admin' && profileLoadState === 'failed' && <aside className="company-profile-load-error" role="alert"><div><strong>AI setup could not be loaded.</strong><span>Your saved company profile has not been changed. Retry to load it before editing.</span></div><button className="button secondary" onClick={() => { void loadCompanyProfile(); }}>Retry</button></aside>}{content}</main>{newTicket && <NewTicketModal categories={categories} onClose={() => setNewTicket(false)} onCreated={created} onError={(text) => notify(text, 'error')} />}{user.role === 'admin' && profileLoadState === 'ready' && companyProfileModalOpen && <CompanyProfileModal profile={companyProfile} aiStatus={aiStatus} required={profileRequired} onClose={() => setCompanyProfileModalOpen(false)} onRefresh={() => loadCompanyProfile({ openAfterLoad: true, background: true })} onSaved={companyProfileSaved} />}</div>;
}

export default function App() {
  const [session, setSession] = useState(() => api.getSession());
  useEffect(() => api.subscribeSession(setSession), []);
  if (!session?.user) return <Login onAuthenticated={setSession} />;
  return <Workspace session={session} onLogout={api.logout} />;
}
