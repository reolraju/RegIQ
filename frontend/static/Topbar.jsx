/* Topbar — subtle paper-tinted masthead.
   Text-only RegIQ wordmark, "Last updated …" strap, single primary action. */

const { Icon } = window;

function Topbar({ onReset, lastSync }) {
  return (
    <header className="topbar">
      <a className="topbar-brand wordmark" href="#" onClick={(e) => { e.preventDefault(); onReset && onReset(); }}>
        Reg<span className="iq">IQ</span>
      </a>
      <div className="topbar-strap">
        <Icon name="database" size={14} className="strap-icon" />
        <span className="strap-label">Last updated</span>
        <span className="strap-text">RBI &amp; SEBI circulars on</span>
        <span className="strap-date">{lastSync}</span>
      </div>
      <div className="topbar-actions">
        <button className="btn btn-secondary btn-sm" onClick={onReset}>
          <Icon name="plus" size={13} />
          New question
        </button>
      </div>
    </header>
  );
}

window.Topbar = Topbar;
