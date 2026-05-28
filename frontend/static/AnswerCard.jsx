/* AnswerCard — renders the answer body, badges, and source-panel toggle.
   Live backend always returns markdown prose; intent affects the badge only. */

const {
  Icon, IntentBadge, ProductBadge, GroundedBadge, ComparisonTable,
} = window;
const { useEffect: useEffectAC } = React;

function CitableBody({ html, onCite }) {
  const ref = React.useRef(null);
  useEffectAC(() => {
    if (!ref.current) return;
    const sups = ref.current.querySelectorAll("sup[data-cite]");
    sups.forEach((s) => {
      const n = parseInt(s.getAttribute("data-cite"), 10);
      s.classList.add("cite-ref");
      s.style.cursor = "pointer";
      s.onclick = (e) => {
        e.preventDefault();
        onCite(n);
      };
    });
  }, [html]);
  return <div ref={ref} className="answer-body" dangerouslySetInnerHTML={{ __html: html }} />;
}

function AnswerCard({ entry, sourcesOpen, onToggleSources, onCite }) {
  const { q, intent, productType, grounded, guardNotes, html, sources, comparison } = entry;
  const showComparisonTable = intent === "comparison" && comparison && comparison.rows && comparison.rows.length > 0;

  return (
    <div className="answer-wrap fade-in">
      <div className="q-block">
        <span className="q-prefix">Question</span>
        <div className="q-text">{q}</div>
      </div>

      <div className="badge-row">
        <IntentBadge intent={intent} />
        {productType && <ProductBadge product={productType} />}
        <GroundedBadge state={grounded} />
        {sources.length > 0 && (
          <button
            className={`p-sources ${sourcesOpen ? "active" : ""}`}
            onClick={() => onToggleSources()}
          >
            <Icon name="file-text" size={12} />
            {sources.length} source{sources.length > 1 ? "s" : ""}
            <Icon name={sourcesOpen ? "chevron-right" : "chevron-left"} size={12} />
          </button>
        )}
      </div>

      {guardNotes && (
        <div className="guard-note">
          <Icon name="shield-alert" size={16} color="var(--warn-700)" />
          <div>
            <b>Hallucination guard:</b> {guardNotes}
          </div>
        </div>
      )}

      <div>
        <h3 className="subhead">
          <span>Answer</span>
        </h3>
        {showComparisonTable
          ? <ComparisonTable comparison={comparison} />
          : <CitableBody html={html} onCite={onCite} />}
      </div>
    </div>
  );
}

window.AnswerCard = AnswerCard;
