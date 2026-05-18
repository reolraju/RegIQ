/* Composer — slim sticky search bar */

const { Icon, Button } = window;

function Composer({ value, onChange, onSubmit, disabled }) {
  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  };
  return (
    <div className="composer-bar">
      <div className="composer-inner">
        <Icon name="search" size={18} className="search-icon" />
        <textarea
          placeholder="Ask about a circular, compare RBI & SEBI, or request a checklist…"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKey}
          rows={1}
          autoFocus
        />
        <span className="kbd-hint">
          <span className="kbd">↵</span> to ask
        </span>
        <Button
          variant="primary"
          size="sm"
          disabled={disabled || !value.trim()}
          onClick={onSubmit}
          trailingIcon="arrow-right"
        >
          Ask
        </Button>
      </div>
    </div>
  );
}

window.Composer = Composer;
