/* Small primitives: Icon wrapper (Lucide), Badges, Buttons */

const { useEffect, useRef } = React;

function Icon({ name, size = 16, color, style, className = "", strokeWidth = 1.75 }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current && window.lucide) {
      ref.current.innerHTML = "";
      const el = document.createElement("i");
      el.setAttribute("data-lucide", name);
      ref.current.appendChild(el);
      window.lucide.createIcons({
        attrs: { "stroke-width": strokeWidth, width: size, height: size },
        nameAttr: "data-lucide",
        elements: [el],
      });
    }
  }, [name, size, strokeWidth]);
  return (
    <span
      ref={ref}
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        color: color || "currentColor",
        flex: "0 0 auto",
        ...style,
      }}
    />
  );
}

function RegulatorBadge({ regulator, soft = false }) {
  const cls = soft
    ? regulator === "RBI" ? "p-rbi-soft" : "p-sebi-soft"
    : regulator === "RBI" ? "p-rbi" : "p-sebi";
  return <span className={`pill ${cls}`}>{regulator}</span>;
}

function GroundedBadge({ state }) {
  if (state === true) return <span className="pill p-grounded">✓ Grounded</span>;
  if (state === false) return <span className="pill p-partial">! Partial</span>;
  return <span className="pill p-flagged">✕ Flagged</span>;
}

const INTENT_LABEL = {
  simple_lookup: "Simple lookup",
  comparison: "RBI ↔ SEBI comparison",
  checklist: "Compliance checklist",
};
const INTENT_ICON = {
  simple_lookup: "search",
  comparison: "git-compare-arrows",
  checklist: "list-checks",
};
function IntentBadge({ intent }) {
  return (
    <span className="pill p-intent">
      <Icon name={INTENT_ICON[intent]} size={12} />
      {INTENT_LABEL[intent]}
    </span>
  );
}

function ProductBadge({ product }) {
  if (!product) return null;
  return <span className="pill p-product">{product}</span>;
}

function Button({ children, variant = "primary", size, leadingIcon, trailingIcon, ...rest }) {
  const cls = ["btn", `btn-${variant}`, size && `btn-${size}`].filter(Boolean).join(" ");
  return (
    <button className={cls} {...rest}>
      {leadingIcon && <Icon name={leadingIcon} size={size === "sm" ? 13 : 14} />}
      {children}
      {trailingIcon && <Icon name={trailingIcon} size={size === "sm" ? 13 : 14} />}
    </button>
  );
}

Object.assign(window, {
  Icon, RegulatorBadge, GroundedBadge, IntentBadge, ProductBadge, Button,
  INTENT_LABEL,
});
