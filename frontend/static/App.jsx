/* App.jsx — top-level shell wired to the FastAPI backend.
   Manages: filters, session history, active query, source panel state. */

const {
  Icon, Topbar, Sidebar, Composer, EmptyState, AnswerCard, SourcePanel,
} = window;
const { useState: useStateApp, useMemo: useMemoApp } = React;

const TODAY = new Date().toLocaleDateString("en-IN", {
  day: "2-digit", month: "short", year: "numeric",
}).replace(/ /g, " ");

function uid() { return Math.random().toString(36).slice(2, 9); }

function markdownToHtml(md) {
  if (window.marked && typeof window.marked.parse === "function") {
    return window.marked.parse(md, { breaks: true, gfm: true });
  }
  // Fallback: paragraph-split with newlines.
  return md.split(/\n\n+/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}

function normalizeSources(rawSources) {
  return (rawSources || []).map((s, i) => ({
    n: i + 1,
    regulator: s.regulator || "Unknown",
    ref: s.reference || s.ref || "—",
    date: s.date || "",
    file: s.source || s.file || "",
    excerpt: s.content || s.excerpt || "",
  }));
}

function buildPayload(question, filters) {
  const payload = { question: question.trim() };
  if (filters.regulator && filters.regulator !== "All") {
    payload.regulator = filters.regulator;
  }
  if (filters.useDateRange) {
    payload.date_from = filters.dateFrom;
    payload.date_to = filters.dateTo;
  }
  return payload;
}

function App() {
  const [filters, setFilters] = useStateApp({
    regulator: "All",
    useDateRange: false,
    dateFrom: "2015-01-01",
    dateTo: new Date().toISOString().slice(0, 10),
  });

  const [composerValue, setComposerValue] = useStateApp("");
  const [history, setHistory] = useStateApp([]);
  const [activeId, setActiveId] = useStateApp(null);
  const [loading, setLoading] = useStateApp(false);
  const [error, setError] = useStateApp(null);
  const [pendingQuestion, setPendingQuestion] = useStateApp("");

  const [sourcesOpen, setSourcesOpen] = useStateApp(false);
  const [highlightedN, setHighlightedN] = useStateApp(null);

  const active = useMemoApp(
    () => history.find((h) => h.id === activeId),
    [history, activeId]
  );

  const ask = async (question) => {
    const q = question.trim();
    if (!q || loading) return;
    setLoading(true);
    setError(null);
    setPendingQuestion(q);
    setSourcesOpen(false);
    setHighlightedN(null);
    try {
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildPayload(q, filters)),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const id = uid();
      const entry = {
        id,
        q,
        intent: data.intent || "simple_lookup",
        productType: data.product_type || null,
        grounded: !!data.grounded,
        guardNotes: data.guard_notes || null,
        html: markdownToHtml(data.answer || ""),
        sources: normalizeSources(data.sources),
        metrics: data.metrics || {
          total_ms: 0, retrieval_ms: 0, llm_ms: 0,
          retrieval_calls: 0, llm_calls: 0,
          tokens_input: 0, tokens_output: 0, cost_usd: 0,
        },
      };
      setHistory((h) => [entry, ...h]);
      setActiveId(id);
      setComposerValue("");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
      setPendingQuestion("");
    }
  };

  const onPickPrompt = (p) => {
    setComposerValue(p.q);
    ask(p.q);
  };

  const reset = () => {
    setActiveId(null);
    setComposerValue("");
    setSourcesOpen(false);
    setHighlightedN(null);
    setError(null);
  };

  const onSelect = (id) => {
    setActiveId(id);
    setSourcesOpen(false);
    setHighlightedN(null);
  };

  const onClearHistory = () => {
    setHistory([]);
    setActiveId(null);
    setSourcesOpen(false);
  };

  const onToggleSources = () => {
    setSourcesOpen((v) => !v);
    setHighlightedN(null);
  };
  const onCite = (n) => {
    setSourcesOpen(true);
    setHighlightedN(n);
  };
  const onCloseSources = () => {
    setSourcesOpen(false);
    setHighlightedN(null);
  };

  const showSourcePanel = sourcesOpen && active && active.sources.length > 0;

  const corpusStats = {
    circulars: "—",
    rbi: "—",
    sebi: "—",
    lastSync: TODAY,
  };

  return (
    <div className="app" data-source-open={showSourcePanel ? "true" : "false"}>
      <Topbar onReset={reset} lastSync={TODAY} />
      <Sidebar
        filters={filters}
        setFilters={setFilters}
        history={history}
        activeId={activeId}
        onSelect={onSelect}
        onClearHistory={onClearHistory}
        activeEntry={active}
      />

      <main className="main">
        <Composer
          value={composerValue}
          onChange={setComposerValue}
          onSubmit={() => ask(composerValue)}
          disabled={loading}
        />

        <div className="body-wrap">
          <div className="body-inner">
            {loading && (
              <div className="answer-wrap fade-in">
                <div className="q-block">
                  <span className="q-prefix">Retrieving</span>
                  <div className="q-text" style={{ color: "var(--ink-500)" }}>{pendingQuestion}</div>
                </div>
                <div className="loading-status">
                  <Icon name="loader-2" size={13} color="var(--indigo-500)" style={{ animation: "spin 1s linear infinite" }} />
                  Hybrid retrieval · BM25 + dense + cross-encoder rerank
                </div>
                <div className="loading-block">
                  <div className="loading-line w-1" />
                  <div className="loading-line w-2" />
                  <div className="loading-line w-3" />
                  <div className="loading-line w-4" />
                </div>
              </div>
            )}

            {error && !loading && (
              <div className="guard-note" style={{ marginBottom: 16 }}>
                <Icon name="alert-triangle" size={16} color="var(--danger-500)" />
                <div>
                  <b>Could not reach the backend.</b> {error}
                </div>
              </div>
            )}

            {!active && !loading && (
              <EmptyState onPick={onPickPrompt} corpusStats={corpusStats} />
            )}

            {active && !loading && (
              <AnswerCard
                key={active.id}
                entry={active}
                sourcesOpen={sourcesOpen}
                onToggleSources={onToggleSources}
                onCite={onCite}
              />
            )}
          </div>
        </div>
      </main>

      {showSourcePanel && (
        <SourcePanel
          sources={active.sources}
          highlightedN={highlightedN}
          onClose={onCloseSources}
        />
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

window.App = App;
