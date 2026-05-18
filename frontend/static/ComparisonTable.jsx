/* ComparisonTable — RBI ↔ SEBI side-by-side */

const { RegulatorBadge } = window;

function ComparisonTable({ comparison }) {
  return (
    <div>
      {comparison.intro && (
        <p style={{ margin: "0 0 14px", fontSize: 15, lineHeight: 1.6, color: "var(--ink-800)", maxWidth: "72ch" }}>
          {comparison.intro}
        </p>
      )}
      <div className="cmp-table">
        <div className="cmp-head rbi">
          <RegulatorBadge regulator="RBI" />
          <span>Reserve Bank of India</span>
        </div>
        <div className="cmp-head sebi">
          <RegulatorBadge regulator="SEBI" />
          <span>Securities &amp; Exchange Board of India</span>
        </div>
        {comparison.rows.map((row, i) => (
          <React.Fragment key={i}>
            <div className="cmp-rowlabel">{row.label}</div>
            <div className="cmp-row">
              <div className="cmp-cell rbi">{row.rbi}</div>
              <div className="cmp-cell sebi">{row.sebi}</div>
            </div>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

window.ComparisonTable = ComparisonTable;
