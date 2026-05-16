/* SourcePanel — right-side drawer with full source documents */

const { Icon, RegulatorBadge } = window;
const { useEffect: useEffectSP, useRef: useRefSP } = React;

function SourcePanel({ sources, highlightedN, onClose }) {
  const refs = useRefSP({});

  useEffectSP(() => {
    if (highlightedN != null && refs.current[highlightedN]) {
      const el = refs.current[highlightedN];
      const container = el.closest(".source-panel");
      if (container) {
        const top = el.offsetTop - 60;
        container.scrollTo({ top, behavior: "smooth" });
      }
    }
  }, [highlightedN]);

  return (
    <aside className="source-panel">
      <div className="source-head">
        <span className="title">Sources</span>
        <span className="count">{sources.length} document{sources.length > 1 ? "s" : ""}</span>
        <button className="close" onClick={onClose} aria-label="Close sources">
          <Icon name="x" size={16} />
        </button>
      </div>
      <div className="src-list">
        {sources.map((s) => (
          <div
            key={s.n}
            ref={(el) => (refs.current[s.n] = el)}
            className={`src ${highlightedN === s.n ? "highlight" : ""}`}
          >
            <div className="src-meta">
              <span className="src-num">[{s.n}]</span>
              <RegulatorBadge regulator={s.regulator} />
              <span className="src-date">{s.date}</span>
            </div>
            <div className="src-ref" style={{ marginBottom: 8 }}>{s.ref}</div>
            <div className="src-excerpt" dangerouslySetInnerHTML={{ __html: s.excerpt }} />
            <div className="src-file" style={{ marginTop: 8 }}>{s.file}</div>
          </div>
        ))}
      </div>
    </aside>
  );
}

window.SourcePanel = SourcePanel;
