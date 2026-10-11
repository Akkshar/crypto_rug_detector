import type { Health } from "../api";

interface Props {
  curView: string;
  onNavigate: (view: string) => void;
  onSelectPreset: (presetKey: string) => void;
  health: Health | null;
}

const NAV_ITEMS = [
  { id: "home", label: "⌂ Home" },
  { id: "scan", label: "⌕ Scan Token" },
  { id: "history", label: "↺ Scan History" },
  { id: "bookmarks", label: "⚑ Bookmarks" },
  { id: "api", label: "‹/› API Docs" },
  { id: "settings", label: "⚙ Settings" },
];

export function Sidebar({ curView, onNavigate, onSelectPreset, health }: Props) {
  const providerLabel = health?.provider
    ? health.provider.charAt(0).toUpperCase() + health.provider.slice(1) + " Provider"
    : "Mock Provider";

  const modelLabel = health?.model
    ? health.model.includes("synthetic")
      ? "Synthetic"
      : health.model
    : "Synthetic";

  return (
    <aside>
      <div className="logo">
        <svg viewBox="0 0 40 40" fill="none">
          <path
            d="M20 3 5 9v10c0 9 6 15 15 18 9-3 15-9 15-18V9L20 3z"
            stroke="#6b7bff"
            strokeWidth="2.5"
          />
          <path d="M13 20l5 5 9-10" stroke="#9ab0ff" strokeWidth="2.5" />
        </svg>
        <div>
          <b>RugScan</b>
          <small>AI-Powered Rug Pull Detection</small>
        </div>
      </div>
      <nav aria-label="Main">
        {NAV_ITEMS.map((item) => (
          <a
            key={item.id}
            className={curView === item.id ? "on" : ""}
            data-v={item.id}
            tabIndex={0}
            role="button"
            onClick={() => onNavigate(item.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onNavigate(item.id);
              }
            }}
          >
            {item.label}
          </a>
        ))}
      </nav>
      <div className="side-card">
        <h4>Quick Links</h4>
        <button className="ql h" data-s="honeypot" onClick={() => onSelectPreset("honeypot")}>
          ⚠ Sample Honeypot
        </button>
        <button className="ql u" data-s="unlocked" onClick={() => onSelectPreset("unlocked")}>
          ◔ Sample Unlocked Rug
        </button>
        <button className="ql b" data-s="benign" onClick={() => onSelectPreset("benign")}>
          ✓ Sample Benign Token
        </button>
      </div>
      <div className="api">
        <i style={health ? undefined : { background: "var(--crit)", boxShadow: "0 0 8px var(--crit)" }}></i>
        {health ? "API Connected" : "API Offline"}<br />
        Backend • {health ? providerLabel : "Disconnected"}<br />
        {health ? `AI Model: ${modelLabel}` : "Start python main.py"}
      </div>
    </aside>
  );
}
