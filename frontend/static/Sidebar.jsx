/* Sidebar — filters at top, recent in the middle, combined Diagnostics section
   at the bottom (above About). No card chrome — quiet key/value rows. */

const { INTENT_LABEL } = window;

function DiagRow({ k, v, unit, sub }) {
  return (
    <div className="diag-row">
      <span className="k">{k}</span>
      <span>
        <span className="v">{v}{unit && <span className="unit">{unit}</span>}</span>
        {sub && <span className="sub">{sub}</span>}
      </span>
    </div>
  );
}

function DiagnosticsSection({ activeEntry, history }) {
  const hasActive = !!activeEntry;
  const hasHistory = history.length > 0;

  if (!hasHistory) {
    return (
      <div className="diag-empty">
        Diagnostics and session totals will appear here after your first query.
      </div>
    );
  }

  const totalCost = history.reduce((s, h) => s + h.metrics.cost_usd, 0);
  const totalTokens = history.reduce((s, h) => s + h.metrics.tokens_input + h.metrics.tokens_output, 0);
  const avgLatency = history.reduce((s, h) => s + h.metrics.total_ms, 0) / history.length;

  return (
    <div>
      {hasActive && (
        <div className="diag-group">
          <div className="diag-group-label">This query</div>
          <DiagRow k="Latency" v={(activeEntry.metrics.total_ms / 1000).toFixed(2)} unit="s" />
          <DiagRow k="Retrieval" v={(activeEntry.metrics.retrieval_ms / 1000).toFixed(2)} unit="s"
                   sub={`${activeEntry.metrics.retrieval_calls} call${activeEntry.metrics.retrieval_calls > 1 ? "s" : ""}`} />
          <DiagRow k="LLM" v={(activeEntry.metrics.llm_ms / 1000).toFixed(2)} unit="s"
                   sub={`${activeEntry.metrics.llm_calls} call${activeEntry.metrics.llm_calls > 1 ? "s" : ""}`} />
          <DiagRow k="Tokens" v={`${activeEntry.metrics.tokens_input.toLocaleString()} / ${activeEntry.metrics.tokens_output.toLocaleString()}`} sub="in / out" />
          <DiagRow k="Cost" v={`$${activeEntry.metrics.cost_usd.toFixed(5)}`} />
        </div>
      )}
      <div className="diag-group">
        <div className="diag-group-label">Session totals</div>
        <DiagRow k="Queries" v={history.length} />
        <DiagRow k="Total cost" v={`$${totalCost.toFixed(4)}`} />
        <DiagRow k="Total tokens" v={totalTokens.toLocaleString()} />
        <DiagRow k="Avg latency" v={(avgLatency / 1000).toFixed(2)} unit="s" />
      </div>
    </div>
  );
}

function Sidebar({ filters, setFilters, history, activeId, onSelect, onClearHistory, activeEntry }) {
  return (
    <aside className="sidebar">
      {/* Filters */}
      <div className="side-section">
        <div className="label">Filters</div>
        <label className="field-label">Regulator</label>
        <div className="seg" role="tablist">
          {["All", "RBI", "SEBI"].map((r) => (
            <button
              key={r}
              className={filters.regulator === r ? "active" : ""}
              onClick={() => setFilters({ ...filters, regulator: r })}
            >
              {r}
            </button>
          ))}
        </div>
        <label className="field-label" style={{ marginTop: 12 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={filters.useDateRange}
              onChange={(e) => setFilters({ ...filters, useDateRange: e.target.checked })}
              style={{ accentColor: "var(--indigo-500)" }}
            />
            Issue-date range
          </span>
        </label>
        <div className="date-row">
          <input
            className="input"
            type="text"
            value={filters.dateFrom}
            disabled={!filters.useDateRange}
            onChange={(e) => setFilters({ ...filters, dateFrom: e.target.value })}
          />
          <input
            className="input"
            type="text"
            value={filters.dateTo}
            disabled={!filters.useDateRange}
            onChange={(e) => setFilters({ ...filters, dateTo: e.target.value })}
          />
        </div>
      </div>

      {/* Recent queries */}
      <div className="side-section">
        <div className="label label-row">
          <span>Recent · {history.length}</span>
          {history.length > 0 && (
            <button
              onClick={onClearHistory}
              style={{ background: "none", border: 0, color: "var(--ink-500)", cursor: "pointer", fontSize: 10.5, padding: 0, letterSpacing: 0, textTransform: "none", fontWeight: 500 }}
            >
              Clear
            </button>
          )}
        </div>
        {history.length === 0 ? (
          <div className="history-empty">Ask a question to begin — your session history appears here.</div>
        ) : (
          <div className="history">
            {history.map((h, i) => (
              <button
                key={h.id}
                className={`history-item ${h.id === activeId ? "active" : ""}`}
                onClick={() => onSelect(h.id)}
              >
                <span className="num">{String(history.length - i).padStart(2, "0")}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h.q}</div>
                  <div className="meta">{INTENT_LABEL[h.intent]}</div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Diagnostics + session totals — single quiet section */}
      <div className="side-section">
        <div className="label">Diagnostics</div>
        <DiagnosticsSection activeEntry={activeEntry} history={history} />
      </div>

      {/* About */}
      <div className="side-section">
        <div className="label">About</div>
        <div className="about-blurb">
          A LangGraph agent over hybrid retrieval — dense embeddings, BM25, and a cross-encoder reranker. Every answer is verified against retrieved circulars before display.
        </div>
      </div>
    </aside>
  );
}

window.Sidebar = Sidebar;
