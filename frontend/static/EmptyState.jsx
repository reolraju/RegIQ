/* EmptyState — a "card-catalog" index of canonical prompts by intent.
   No hero. Three columns: Lookup / Comparison / Checklist. */

const { Icon } = window;

const LOOKUP_PROMPTS = [
  { q: "What are the KYC requirements for digital lending apps?", key: "kyc-digital-lending" },
  { q: "What is the minimum investment for accredited investors in AIFs?", key: "aif-min" },
  { q: "Cybersecurity reporting timelines for MIIs", key: "cyber-miis" },
];
const COMPARE_PROMPTS = [
  { q: "How do RBI and SEBI differ on outsourcing of financial services?", key: "outsourcing-compare" },
];
const CHECKLIST_PROMPTS = [
  { q: "Compliance checklist for a digital lending app", key: "checklist-dla" },
];

function IntentColumn({ num, name, desc, prompts, onPick }) {
  return (
    <div className="intent-col">
      <div className="intent-head">
        <span className="ihn">{num}</span>
        <span className="iht">{name}</span>
      </div>
      <p className="intent-desc">{desc}</p>
      <div className="intent-prompts">
        {prompts.map((p) => (
          <button key={p.key} className="intent-prompt" onClick={() => onPick(p)}>
            <span style={{ flex: 1 }}>{p.q}</span>
            <span className="arrow">→</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function EmptyState({ onPick, corpusStats }) {
  return (
    <div className="index-hero fade-in">
      <div className="index-eyebrow">
        <span>How to ask</span>
      </div>
      <h2 className="index-headline">
        Three ways to query the corpus. Pick a prompt below or type your own — the agent will route it to the right path.
      </h2>
      <div className="intent-cols">
        <IntentColumn
          num="01."
          name="Simple lookup"
          desc="A direct, sourced answer to a factual question."
          prompts={LOOKUP_PROMPTS}
          onPick={onPick}
        />
        <IntentColumn
          num="02."
          name="RBI ↔ SEBI comparison"
          desc="Side-by-side table comparing the two regulators on the same topic."
          prompts={COMPARE_PROMPTS}
          onPick={onPick}
        />
        <IntentColumn
          num="03."
          name="Compliance checklist"
          desc="A numbered, checkable list of requirements for a product type."
          prompts={CHECKLIST_PROMPTS}
          onPick={onPick}
        />
      </div>
      <div className="index-footer">
        <span className="stat"><b>{corpusStats.circulars}</b> circulars indexed</span>
        <span>·</span>
        <span className="stat"><b>{corpusStats.rbi}</b> RBI</span>
        <span>·</span>
        <span className="stat"><b>{corpusStats.sebi}</b> SEBI</span>
        <span>·</span>
        <span className="stat">last sync <b>{corpusStats.lastSync}</b></span>
      </div>
    </div>
  );
}

window.EmptyState = EmptyState;
